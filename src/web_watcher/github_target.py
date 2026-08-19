import os
import re
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from web_watcher.models import Target, TargetStatus
from web_watcher.fetch_policy import FetchPolicy, FetchDecision, FetchEvaluation
from web_watcher.fetcher import SmartFetcher, FetchResult
from web_watcher.fetch import FetchStatus
try:
    from web_watcher.models import Signal
except ImportError:
    Signal = None


def parse_github_repo(url_or_slug: str) -> Tuple[str, str]:
    """从 URL 或 slug 中解析 owner 与 repo 名称"""
    s = url_or_slug.strip().rstrip("/")
    m = re.search(r"(?:github\.com/|api\.github\.com/repos/)?([^/]+)/([^/]+?)(?:\.git|/)?$", s)
    if m:
        return m.group(1), m.group(2)
    raise ValueError(f"Invalid GitHub repository identifier: '{url_or_slug}'")


@dataclass
class GitHubTargetExecutionResult:
    target_id: str
    allowed: bool
    status_code: Optional[int]
    new_status: TargetStatus
    signals_emitted: List[Any]
    is_304: bool = False
    rate_limit_remaining: Optional[int] = None
    reason: str = ""


class GitHubTarget:
    """GitHub 官方 REST API 专有监控适配器：Releases / Tags / Stars 语义化信号生成与 ETag 缓存"""

    BASE_API = "https://api.github.com"

    def __init__(
        self,
        target: Target,
        watch_types: Optional[List[str]] = None,
        token: Optional[str] = None,
        star_delta_threshold: int = 1,
        timeout: float = 10.0,
    ):
        self.target = target
        self.owner, self.repo_name = parse_github_repo(target.url)
        self.watch_types = set(watch_types or ["releases", "stars", "tags"])
        self.token = token or os.getenv("GITHUB_TOKEN")
        self.star_delta_threshold = max(1, star_delta_threshold)
        self.timeout = timeout

    def _build_headers(self, etag: Optional[str] = None) -> Dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "web-watcher/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if etag:
            headers["If-None-Match"] = etag
        return headers

    def _create_signal(self, signal_type: str, payload: Dict[str, Any], now: datetime) -> Any:
        if Signal is not None:
            try:
                return Signal(
                    id=f"sig_{self.target.id}_{signal_type.lower()}_{int(now.timestamp())}",
                    entity_id=self.target.id,
                    signal_type=signal_type,
                    payload=payload,
                    created_at=now,
                )
            except Exception:
                try:
                    return Signal(
                        entity_id=self.target.id,
                        signal_type=signal_type,
                        payload=payload,
                    )
                except Exception:
                    return payload
        return payload

    def execute(
        self,
        fetcher: Optional[SmartFetcher] = None,
        policy: Optional[FetchPolicy] = None,
        repo: Optional[Any] = None,
        now: Optional[datetime] = None,
    ) -> GitHubTargetExecutionResult:
        now = now or datetime.utcnow()
        fetcher = fetcher or SmartFetcher(default_timeout=self.timeout)
        policy = policy or FetchPolicy()

        # 1. 策略前置检查
        decision = policy.prepare_request(self.target, now=now)
        if not decision.allowed:
            return GitHubTargetExecutionResult(
                target_id=self.target.id,
                allowed=False,
                status_code=None,
                new_status=self.target.status,
                signals_emitted=[],
                reason=decision.reason or "Skipped by policy",
            )

        meta = dict(self.target.metadata or {})
        signals: List[Any] = []
        is_any_304 = False
        last_status_code = 200

        # 2. 检查 Releases
        if "releases" in self.watch_types:
            rel_url = f"{self.BASE_API}/repos/{self.owner}/{self.repo_name}/releases/latest"
            rel_etag = meta.get("release_etag")
            res = fetcher.fetch(rel_url, custom_headers=self._build_headers(rel_etag), timeout=self.timeout)

            eval_res = policy.evaluate_response(self.target, res.status_code, error=res.error, now=now)
            self.target.status = eval_res.new_status

            if res.status == FetchStatus.NOT_MODIFIED:
                is_any_304 = True
            elif res.status_code == 200 and res.content:
                try:
                    rel_data = json.loads(res.content)
                    tag_name = rel_data.get("tag_name")
                    prev_tag = meta.get("last_release_tag")

                    meta["release_etag"] = res.etag
                    meta["last_release_tag"] = tag_name

                    if prev_tag is not None and prev_tag != tag_name:
                        sig = self._create_signal(
                            "GITHUB_RELEASE_PUBLISHED",
                            {
                                "owner": self.owner,
                                "repo": self.repo_name,
                                "tag_name": tag_name,
                                "release_name": rel_data.get("name"),
                                "html_url": rel_data.get("html_url"),
                                "published_at": rel_data.get("published_at"),
                                "body": rel_data.get("body", "")[:500],
                            },
                            now,
                        )
                        signals.append(sig)
                except Exception:
                    pass
            last_status_code = res.status_code

        # 3. 检查 Repo Meta (Stars)
        if "stars" in self.watch_types and self.target.status == TargetStatus.NORMAL:
            repo_url = f"{self.BASE_API}/repos/{self.owner}/{self.repo_name}"
            repo_etag = meta.get("repo_etag")
            res = fetcher.fetch(repo_url, custom_headers=self._build_headers(repo_etag), timeout=self.timeout)

            if res.status_code == 200 and res.content:
                try:
                    repo_data = json.loads(res.content)
                    stars = repo_data.get("stargazers_count", 0)
                    prev_stars = meta.get("last_stars")

                    meta["repo_etag"] = res.etag
                    meta["last_stars"] = stars

                    if prev_stars is not None:
                        delta = stars - prev_stars
                        if abs(delta) >= self.star_delta_threshold:
                            sig = self._create_signal(
                                "GITHUB_STARS_CHANGED",
                                {
                                    "owner": self.owner,
                                    "repo": self.repo_name,
                                    "old_stars": prev_stars,
                                    "new_stars": stars,
                                    "delta": delta,
                                },
                                now,
                            )
                            signals.append(sig)
                except Exception:
                    pass
            elif res.status == FetchStatus.NOT_MODIFIED:
                is_any_304 = True
            last_status_code = res.status_code

        # 4. 同步更新状态与元数据
        self.target.metadata = meta
        self.target.last_fetched_at = now
        if repo and hasattr(repo, "save_target"):
            repo.save_target(self.target)

        # 5. 持久化 signals
        if repo and hasattr(repo, "save_signal"):
            for s in signals:
                try:
                    repo.save_signal(s)
                except Exception:
                    pass

        return GitHubTargetExecutionResult(
            target_id=self.target.id,
            allowed=True,
            status_code=last_status_code,
            new_status=self.target.status,
            signals_emitted=signals,
            is_304=is_any_304 and len(signals) == 0,
            reason=f"Emitted {len(signals)} GitHub signals" if signals else "No new events",
        )
