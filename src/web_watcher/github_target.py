import os
import re
import json
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List, Tuple
from web_watcher.models import Target, TargetStatus
from web_watcher.fetch_policy import FetchPolicy, FetchDecision, FetchEvaluation
from web_watcher.fetcher import SmartFetcher, FetchResult
from web_watcher.fetch import FetchStatus
from web_watcher.execution_semantics import ExecutionOutcome, transition_for
from web_watcher.signal_types import SignalType
from web_watcher.host_rate_limiter import _extract_host
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
    # observation-only durable-state fields for scheduler commit
    updated_metadata: Optional[Dict[str, Any]] = None
    consecutive_failures: int = 0
    next_allowed_at: Optional[datetime] = None
    last_fetched_at: Optional[datetime] = None
    outcome: Any = None
    transition: Any = None


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
        rule_status: str = "enabled",
    ):
        self.target = target
        self.owner, self.repo_name = parse_github_repo(target.url)
        self.watch_types = set(watch_types or ["releases", "stars", "tags", "commits", "prs", "issues"])
        self.token = token or os.getenv("GITHUB_TOKEN")
        self.star_delta_threshold = max(1, star_delta_threshold)
        self.timeout = timeout
        self.rule_status = rule_status

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

    def _compute_signal_fingerprint(self, signal_type: SignalType, payload: Dict[str, Any]) -> str:
        """Compute a deterministic fingerprint for idempotency checks."""
        if signal_type == SignalType.RELEASE_PUBLISHED:
            raw = f"{self.owner}/{self.repo_name}:{payload.get('tag_name', '')}:{payload.get('published_at', '')}"
        elif signal_type == SignalType.STARS_CHANGED:
            raw = f"{self.owner}/{self.repo_name}:stars:{payload.get('new_stars', '')}"
        else:
            raw = f"{self.owner}/{self.repo_name}:{signal_type.value}:{json.dumps(payload, sort_keys=True)}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _is_duplicate_signal(self, signal_type: SignalType, payload: Dict[str, Any], meta: Dict[str, Any]) -> bool:
        """Check if this signal was already emitted (idempotency guard)."""
        fp = self._compute_signal_fingerprint(signal_type, payload)
        emitted = meta.get("emitted_signal_fingerprints", [])
        return fp in emitted

    def _record_signal_fingerprint(self, signal_type: SignalType, payload: Dict[str, Any], meta: Dict[str, Any]):
        """Record that this signal was emitted."""
        fp = self._compute_signal_fingerprint(signal_type, payload)
        emitted = list(meta.get("emitted_signal_fingerprints", []))
        emitted.append(fp)
        # Keep bounded: only remember last 100 fingerprints
        meta["emitted_signal_fingerprints"] = emitted[-100:]

    def _create_signal(self, signal_type: SignalType, payload: Dict[str, Any], now: datetime) -> Any:
        if Signal is not None:
            try:
                return Signal(
                    id=f"sig_{self.target.id}_{signal_type.value}_{int(now.timestamp())}",
                    entity_id=self.target.id,
                    signal_type=signal_type,
                    value=json.dumps(payload, ensure_ascii=False),
                    observed_at=now,
                    fingerprint=self._compute_signal_fingerprint(signal_type, payload),
                )
            except (TypeError, ValueError) as exc:
                logger.debug("Signal construction failed for %s: %s", self.target.id, exc)
                try:
                    return Signal(
                        entity_id=self.target.id,
                        signal_type=signal_type,
                        value=json.dumps(payload, ensure_ascii=False),
                        observed_at=now,
                        fingerprint=self._compute_signal_fingerprint(signal_type, payload),
                    )
                except (TypeError, ValueError):
                    return payload
        return payload

    def execute(
        self,
        fetcher: Optional[SmartFetcher] = None,
        policy: Optional[FetchPolicy] = None,
        repo: Optional[Any] = None,
        now: Optional[datetime] = None,
    ) -> GitHubTargetExecutionResult:
        now = now or datetime.now(timezone.utc)
        fetcher = fetcher or SmartFetcher(default_timeout=self.timeout)
        policy = policy or FetchPolicy()
        meta = dict(self.target.metadata or {})
        meta["observation_timestamp"] = now.isoformat()
        cookies = meta.get("cookies") or {}
        basic_auth = meta.get("basic_auth")
        proxy = meta.get("proxy")

        if self.rule_status == "disabled":
            return GitHubTargetExecutionResult(
                target_id=self.target.id,
                allowed=False,
                status_code=None,
                new_status=self.target.status,
                signals_emitted=[],
                reason="Rule disabled",
                outcome=ExecutionOutcome.POLICY_BLOCKED,
                transition=transition_for(
                    ExecutionOutcome.POLICY_BLOCKED,
                    target=self.target,
                    now=now,
                    reason="Rule disabled",
                ),
            )

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
                outcome=ExecutionOutcome.POLICY_BLOCKED,
                transition=transition_for(
                    ExecutionOutcome.POLICY_BLOCKED,
                    target=self.target,
                    now=now,
                ),
            )

        meta = dict(self.target.metadata or {})
        meta["observation_timestamp"] = now.isoformat()
        signals: List[Any] = []
        is_any_304 = False
        last_status_code = 200
        fetch_error: Optional[str] = None
        claimed_hosts: List[str] = []

        def _claim(host: Optional[str]) -> None:
            if host:
                claimed_hosts.append(host)

        def _release_claims() -> None:
            for host in claimed_hosts:
                if policy.host_rate_limiter:
                    policy.host_rate_limiter.release_request(host)

        def _should_skip_subresource(name: str, now_dt: datetime) -> bool:
            states = meta.get("subresource_states", {})
            state = states.get(name, {})
            status = state.get("status")
            next_allowed_iso = state.get("next_allowed_at")
            if status in ("COOLDOWN", "BACKOFF") and next_allowed_iso:
                try:
                    next_allowed = datetime.fromisoformat(next_allowed_iso)
                    if now_dt.tzinfo is None:
                        now_dt = now_dt.replace(tzinfo=timezone.utc)
                    if next_allowed > now_dt:
                        return True
                except (ValueError, TypeError):
                    pass
            return False

        # Track outcomes from each sub-fetch to derive the composite outcome.
        release_outcome = ExecutionOutcome.SUCCESS_UNCHANGED
        repo_outcome = ExecutionOutcome.SUCCESS_UNCHANGED
        release_eval = None
        repo_eval = None

        try:
            # 2. 检查 Releases
            if "releases" in self.watch_types and not _should_skip_subresource("releases", now):
                rel_url = f"{self.BASE_API}/repos/{self.owner}/{self.repo_name}/releases/latest"
                rel_host = _extract_host(rel_url)
                if rel_host and policy.host_rate_limiter:
                    allowed, _, wait_seconds, _ = policy.host_rate_limiter.prepare_request(rel_host, now)
                    if not allowed:
                        return GitHubTargetExecutionResult(
                            target_id=self.target.id,
                            allowed=False,
                            status_code=None,
                            new_status=self.target.status,
                            signals_emitted=[],
                            reason=f"Host '{rel_host}' rate-limited ({int(wait_seconds or 0)}s remaining)",
                            outcome=ExecutionOutcome.POLICY_BLOCKED,
                            transition=transition_for(
                                ExecutionOutcome.POLICY_BLOCKED,
                                target=self.target,
                                now=now,
                            ),
                        )
                    _claim(rel_host)

                rel_etag = meta.get("release_etag")
                res = fetcher.fetch(
                    rel_url,
                    custom_headers=self._build_headers(rel_etag),
                    timeout=self.timeout,
                    cookies=cookies or None,
                    auth=tuple(basic_auth.values()) if basic_auth and isinstance(basic_auth, dict) else None,
                    proxy=proxy,
                )

                headers_map = {}
                if isinstance(res.metadata, dict):
                    headers_map = {k.lower(): v for k, v in res.metadata.get("headers", {}).items()}
                release_eval = policy.evaluate_response(self.target, res.status_code, headers=headers_map, error=res.error, now=now)

                if res.error:
                    fetch_error = res.error

                if res.status == FetchStatus.NOT_MODIFIED:
                    is_any_304 = True
                    release_outcome = ExecutionOutcome.NOT_MODIFIED
                elif res.status_code == 200 and res.content:
                    try:
                        rel_data = json.loads(res.content)
                    except (json.JSONDecodeError, ValueError):
                        release_outcome = ExecutionOutcome.TRANSFORM_ERROR
                    else:
                        tag_name = rel_data.get("tag_name")
                        prev_tag = meta.get("last_release_tag")

                        meta["release_etag"] = res.etag
                        meta["last_release_tag"] = tag_name

                        if prev_tag is not None and prev_tag != tag_name and tag_name is not None:
                            payload = {
                                "owner": self.owner,
                                "repo": self.repo_name,
                                "tag_name": tag_name,
                                "release_name": rel_data.get("name"),
                                "html_url": rel_data.get("html_url"),
                                "published_at": rel_data.get("published_at"),
                                "body": rel_data.get("body", "")[:500],
                                "source": rel_url,
                                "fetched_at": now.isoformat(),
                            }
                            if not self._is_duplicate_signal(SignalType.RELEASE_PUBLISHED, payload, meta):
                                sig = self._create_signal(
                                    SignalType.RELEASE_PUBLISHED,
                                    payload,
                                    now,
                                )
                                signals.append(sig)
                                self._record_signal_fingerprint(SignalType.RELEASE_PUBLISHED, payload, meta)
                                release_outcome = ExecutionOutcome.SUCCESS_CHANGED
                            else:
                                release_outcome = ExecutionOutcome.SUCCESS_UNCHANGED
                        else:
                            release_outcome = ExecutionOutcome.SUCCESS_UNCHANGED
                else:
                    # Non-200, non-304 release response
                    if release_eval and release_eval.new_status == TargetStatus.COOLDOWN:
                        release_outcome = ExecutionOutcome.POLICY_COOLDOWN
                    elif res.status == FetchStatus.TIMEOUT or (res.status_code is not None and res.status_code == 0):
                        release_outcome = ExecutionOutcome.TIMEOUT
                    elif res.error or (res.status_code is not None and res.status_code >= 400):
                        release_outcome = ExecutionOutcome.FETCH_FAILED
                    else:
                        release_outcome = ExecutionOutcome.SUCCESS_UNCHANGED
                last_status_code = res.status_code

            # 3. 检查 Repo Meta (Stars)
            if "stars" in self.watch_types and self.target.status == TargetStatus.NORMAL and not _should_skip_subresource("stars", now):
                repo_url = f"{self.BASE_API}/repos/{self.owner}/{self.repo_name}"
                repo_host = _extract_host(repo_url)
                if repo_host and policy.host_rate_limiter:
                    allowed, _, wait_seconds, _ = policy.host_rate_limiter.prepare_request(repo_host, now)
                    if not allowed:
                        return GitHubTargetExecutionResult(
                            target_id=self.target.id,
                            allowed=False,
                            status_code=None,
                            new_status=self.target.status,
                            signals_emitted=[],
                            reason=f"Host '{repo_host}' rate-limited ({int(wait_seconds or 0)}s remaining)",
                            outcome=ExecutionOutcome.POLICY_BLOCKED,
                            transition=transition_for(
                                ExecutionOutcome.POLICY_BLOCKED,
                                target=self.target,
                                now=now,
                            ),
                        )
                    _claim(repo_host)

                repo_etag = meta.get("repo_etag")
                res = fetcher.fetch(
                    repo_url,
                    custom_headers=self._build_headers(repo_etag),
                    timeout=self.timeout,
                    cookies=cookies or None,
                    auth=tuple(basic_auth.values()) if basic_auth and isinstance(basic_auth, dict) else None,
                    proxy=proxy,
                )

                headers_map = {}
                if isinstance(res.metadata, dict):
                    headers_map = {k.lower(): v for k, v in res.metadata.get("headers", {}).items()}
                repo_eval = policy.evaluate_response(self.target, res.status_code, headers=headers_map, error=res.error, now=now)

                if res.error:
                    fetch_error = res.error

                if res.status == FetchStatus.NOT_MODIFIED:
                    is_any_304 = True
                    repo_outcome = ExecutionOutcome.NOT_MODIFIED
                elif res.status_code == 200 and res.content:
                    try:
                        repo_data = json.loads(res.content)
                        stars = int(repo_data.get("stargazers_count") or 0)
                        prev_stars = meta.get("last_stars")

                        meta["repo_etag"] = res.etag
                        meta["last_stars"] = stars

                        if prev_stars is not None:
                            delta = stars - prev_stars
                            if abs(delta) >= self.star_delta_threshold:
                                payload = {
                                    "owner": self.owner,
                                    "repo": self.repo_name,
                                    "old_stars": prev_stars,
                                    "new_stars": stars,
                                    "delta": delta,
                                    "source": repo_url,
                                    "fetched_at": now.isoformat(),
                                }
                                if not self._is_duplicate_signal(SignalType.STARS_CHANGED, payload, meta):
                                    sig = self._create_signal(
                                        SignalType.STARS_CHANGED,
                                        payload,
                                        now,
                                    )
                                    signals.append(sig)
                                    self._record_signal_fingerprint(SignalType.STARS_CHANGED, payload, meta)
                                    repo_outcome = ExecutionOutcome.SUCCESS_CHANGED
                                else:
                                    repo_outcome = ExecutionOutcome.SUCCESS_UNCHANGED
                            else:
                                repo_outcome = ExecutionOutcome.SUCCESS_UNCHANGED
                        else:
                            # First observation: establish baseline
                            repo_outcome = ExecutionOutcome.SUCCESS_UNCHANGED
                    except (json.JSONDecodeError, ValueError, TypeError):
                        repo_outcome = ExecutionOutcome.TRANSFORM_ERROR
                else:
                    if repo_eval and repo_eval.new_status == TargetStatus.COOLDOWN:
                        repo_outcome = ExecutionOutcome.POLICY_COOLDOWN
                    elif res.status == FetchStatus.TIMEOUT or (res.status_code is not None and res.status_code == 0):
                        repo_outcome = ExecutionOutcome.TIMEOUT
                    elif res.error or (res.status_code is not None and res.status_code >= 400):
                        repo_outcome = ExecutionOutcome.FETCH_FAILED
                    else:
                        repo_outcome = ExecutionOutcome.SUCCESS_UNCHANGED
                last_status_code = res.status_code

            # 3b. 检查 Commits
            commits_outcome = ExecutionOutcome.SUCCESS_UNCHANGED
            commits_eval = None
            if "commits" in self.watch_types and self.target.status == TargetStatus.NORMAL and not _should_skip_subresource("commits", now):
                commits_url = f"{self.BASE_API}/repos/{self.owner}/{self.repo_name}/commits?per_page=1"
                commits_host = _extract_host(commits_url)
                if commits_host and policy.host_rate_limiter:
                    allowed, _, wait_seconds, _ = policy.host_rate_limiter.prepare_request(commits_host, now)
                    if not allowed:
                        return GitHubTargetExecutionResult(
                            target_id=self.target.id,
                            allowed=False,
                            status_code=None,
                            new_status=self.target.status,
                            signals_emitted=[],
                            reason=f"Host '{commits_host}' rate-limited ({int(wait_seconds or 0)}s remaining)",
                            outcome=ExecutionOutcome.POLICY_BLOCKED,
                            transition=transition_for(
                                ExecutionOutcome.POLICY_BLOCKED,
                                target=self.target,
                                now=now,
                            ),
                        )
                    _claim(commits_host)

                commits_etag = meta.get("commits_etag")
                res = fetcher.fetch(
                    commits_url,
                    custom_headers=self._build_headers(commits_etag),
                    timeout=self.timeout,
                    cookies=cookies or None,
                    auth=tuple(basic_auth.values()) if basic_auth and isinstance(basic_auth, dict) else None,
                    proxy=proxy,
                )

                headers_map = {}
                if isinstance(res.metadata, dict):
                    headers_map = {k.lower(): v for k, v in res.metadata.get("headers", {}).items()}
                commits_eval = policy.evaluate_response(self.target, res.status_code, headers=headers_map, error=res.error, now=now)

                if res.error:
                    fetch_error = res.error

                if res.status == FetchStatus.NOT_MODIFIED:
                    is_any_304 = True
                    commits_outcome = ExecutionOutcome.NOT_MODIFIED
                elif res.status_code == 200 and res.content:
                    try:
                        commits_data = json.loads(res.content)
                        if isinstance(commits_data, list) and commits_data:
                            latest_sha = commits_data[0].get("sha")
                            prev_sha = meta.get("last_commit_sha")

                            meta["commits_etag"] = res.etag
                            meta["last_commit_sha"] = latest_sha

                            if prev_sha is not None and prev_sha != latest_sha and latest_sha is not None:
                                payload = {
                                    "owner": self.owner,
                                    "repo": self.repo_name,
                                    "sha": latest_sha,
                                    "message": (commits_data[0].get("commit") or {}).get("message", ""),
                                    "author": ((commits_data[0].get("commit") or {}).get("author") or {}).get("name"),
                                    "url": commits_data[0].get("html_url"),
                                    "source": commits_url,
                                    "fetched_at": now.isoformat(),
                                }
                                if not self._is_duplicate_signal(SignalType.COMMIT_PUSHED, payload, meta):
                                    sig = self._create_signal(SignalType.COMMIT_PUSHED, payload, now)
                                    signals.append(sig)
                                    self._record_signal_fingerprint(SignalType.COMMIT_PUSHED, payload, meta)
                                    commits_outcome = ExecutionOutcome.SUCCESS_CHANGED
                                else:
                                    commits_outcome = ExecutionOutcome.SUCCESS_UNCHANGED
                            else:
                                commits_outcome = ExecutionOutcome.SUCCESS_UNCHANGED
                        else:
                            commits_outcome = ExecutionOutcome.SUCCESS_UNCHANGED
                    except (json.JSONDecodeError, ValueError, TypeError):
                        commits_outcome = ExecutionOutcome.TRANSFORM_ERROR
                else:
                    if commits_eval and commits_eval.new_status == TargetStatus.COOLDOWN:
                        commits_outcome = ExecutionOutcome.POLICY_COOLDOWN
                    elif res.status == FetchStatus.TIMEOUT or (res.status_code is not None and res.status_code == 0):
                        commits_outcome = ExecutionOutcome.TIMEOUT
                    elif res.error or (res.status_code is not None and res.status_code >= 400):
                        commits_outcome = ExecutionOutcome.FETCH_FAILED
                    else:
                        commits_outcome = ExecutionOutcome.SUCCESS_UNCHANGED
                last_status_code = res.status_code

            # 3c. 检查 Pull Requests
            prs_outcome = ExecutionOutcome.SUCCESS_UNCHANGED
            prs_eval = None
            if "prs" in self.watch_types and self.target.status == TargetStatus.NORMAL and not _should_skip_subresource("prs", now):
                prs_url = f"{self.BASE_API}/repos/{self.owner}/{self.repo_name}/pulls?state=all&per_page=5&sort=updated&direction=desc"
                prs_host = _extract_host(prs_url)
                if prs_host and policy.host_rate_limiter:
                    allowed, _, wait_seconds, _ = policy.host_rate_limiter.prepare_request(prs_host, now)
                    if not allowed:
                        return GitHubTargetExecutionResult(
                            target_id=self.target.id,
                            allowed=False,
                            status_code=None,
                            new_status=self.target.status,
                            signals_emitted=[],
                            reason=f"Host '{prs_host}' rate-limited ({int(wait_seconds or 0)}s remaining)",
                            outcome=ExecutionOutcome.POLICY_BLOCKED,
                            transition=transition_for(
                                ExecutionOutcome.POLICY_BLOCKED,
                                target=self.target,
                                now=now,
                            ),
                        )
                    _claim(prs_host)

                prs_etag = meta.get("prs_etag")
                res = fetcher.fetch(
                    prs_url,
                    custom_headers=self._build_headers(prs_etag),
                    timeout=self.timeout,
                    cookies=cookies or None,
                    auth=tuple(basic_auth.values()) if basic_auth and isinstance(basic_auth, dict) else None,
                    proxy=proxy,
                )

                headers_map = {}
                if isinstance(res.metadata, dict):
                    headers_map = {k.lower(): v for k, v in res.metadata.get("headers", {}).items()}
                prs_eval = policy.evaluate_response(self.target, res.status_code, headers=headers_map, error=res.error, now=now)

                if res.error:
                    fetch_error = res.error

                if res.status == FetchStatus.NOT_MODIFIED:
                    is_any_304 = True
                    prs_outcome = ExecutionOutcome.NOT_MODIFIED
                elif res.status_code == 200 and res.content:
                    try:
                        prs_data = json.loads(res.content)
                        if isinstance(prs_data, list):
                            latest_pr = prs_data[0] if prs_data else None
                            latest_pr_number = str(latest_pr.get("number")) if latest_pr else None
                            latest_pr_state = latest_pr.get("state") if latest_pr else None
                            prev_snapshot = meta.get("last_pr_snapshot")

                            meta["prs_etag"] = res.etag
                            meta["last_pr_snapshot"] = {"number": latest_pr_number, "state": latest_pr_state, "updated_at": latest_pr.get("updated_at")} if latest_pr else None

                            if prev_snapshot and latest_pr_number:
                                prev_state = prev_snapshot.get("state")
                                if prev_state != latest_pr_state:
                                    payload = {
                                        "owner": self.owner,
                                        "repo": self.repo_name,
                                        "pr_number": latest_pr_number,
                                        "state": latest_pr_state,
                                        "title": latest_pr.get("title"),
                                        "html_url": latest_pr.get("html_url"),
                                        "source": prs_url,
                                        "fetched_at": now.isoformat(),
                                    }
                                    if not self._is_duplicate_signal(SignalType.PR_STATUS_CHANGED, payload, meta):
                                        sig = self._create_signal(SignalType.PR_STATUS_CHANGED, payload, now)
                                        signals.append(sig)
                                        self._record_signal_fingerprint(SignalType.PR_STATUS_CHANGED, payload, meta)
                                        prs_outcome = ExecutionOutcome.SUCCESS_CHANGED
                                    else:
                                        prs_outcome = ExecutionOutcome.SUCCESS_UNCHANGED
                                else:
                                    prs_outcome = ExecutionOutcome.SUCCESS_UNCHANGED
                            else:
                                prs_outcome = ExecutionOutcome.SUCCESS_UNCHANGED
                        else:
                            prs_outcome = ExecutionOutcome.SUCCESS_UNCHANGED
                    except (json.JSONDecodeError, ValueError, TypeError):
                        prs_outcome = ExecutionOutcome.TRANSFORM_ERROR
                else:
                    if prs_eval and prs_eval.new_status == TargetStatus.COOLDOWN:
                        prs_outcome = ExecutionOutcome.POLICY_COOLDOWN
                    elif res.status == FetchStatus.TIMEOUT or (res.status_code is not None and res.status_code == 0):
                        prs_outcome = ExecutionOutcome.TIMEOUT
                    elif res.error or (res.status_code is not None and res.status_code >= 400):
                        prs_outcome = ExecutionOutcome.FETCH_FAILED
                    else:
                        prs_outcome = ExecutionOutcome.SUCCESS_UNCHANGED
                last_status_code = res.status_code

            # 3d. 检查 Issues
            issues_outcome = ExecutionOutcome.SUCCESS_UNCHANGED
            issues_eval = None
            if "issues" in self.watch_types and self.target.status == TargetStatus.NORMAL and not _should_skip_subresource("issues", now):
                issues_url = f"{self.BASE_API}/repos/{self.owner}/{self.repo_name}/issues?state=all&per_page=5&sort=updated&direction=desc"
                issues_host = _extract_host(issues_url)
                if issues_host and policy.host_rate_limiter:
                    allowed, _, wait_seconds, _ = policy.host_rate_limiter.prepare_request(issues_host, now)
                    if not allowed:
                        return GitHubTargetExecutionResult(
                            target_id=self.target.id,
                            allowed=False,
                            status_code=None,
                            new_status=self.target.status,
                            signals_emitted=[],
                            reason=f"Host '{issues_host}' rate-limited ({int(wait_seconds or 0)}s remaining)",
                            outcome=ExecutionOutcome.POLICY_BLOCKED,
                            transition=transition_for(
                                ExecutionOutcome.POLICY_BLOCKED,
                                target=self.target,
                                now=now,
                            ),
                        )
                    _claim(issues_host)

                issues_etag = meta.get("issues_etag")
                res = fetcher.fetch(issues_url, custom_headers=self._build_headers(issues_etag), timeout=self.timeout)

                headers_map = {}
                if isinstance(res.metadata, dict):
                    headers_map = {k.lower(): v for k, v in res.metadata.get("headers", {}).items()}
                issues_eval = policy.evaluate_response(self.target, res.status_code, headers=headers_map, error=res.error, now=now)

                if res.error:
                    fetch_error = res.error

                if res.status == FetchStatus.NOT_MODIFIED:
                    is_any_304 = True
                    issues_outcome = ExecutionOutcome.NOT_MODIFIED
                elif res.status_code == 200 and res.content:
                    try:
                        issues_data = json.loads(res.content)
                        if isinstance(issues_data, list):
                            latest_issue = issues_data[0] if issues_data else None
                            latest_issue_number = str(latest_issue.get("number")) if latest_issue else None
                            latest_issue_state = latest_issue.get("state") if latest_issue else None
                            prev_issue_snapshot = meta.get("last_issue_snapshot")

                            meta["issues_etag"] = res.etag
                            meta["last_issue_snapshot"] = {"number": latest_issue_number, "state": latest_issue_state, "updated_at": latest_issue.get("updated_at")} if latest_issue else None

                            if prev_issue_snapshot and latest_issue_number:
                                prev_state = prev_issue_snapshot.get("state")
                                if prev_state != latest_issue_state:
                                    payload = {
                                        "owner": self.owner,
                                        "repo": self.repo_name,
                                        "issue_number": latest_issue_number,
                                        "state": latest_issue_state,
                                        "title": latest_issue.get("title"),
                                        "html_url": latest_issue.get("html_url"),
                                        "source": issues_url,
                                        "fetched_at": now.isoformat(),
                                    }
                                    if not self._is_duplicate_signal(SignalType.ISSUE_UPDATED, payload, meta):
                                        sig = self._create_signal(SignalType.ISSUE_UPDATED, payload, now)
                                        signals.append(sig)
                                        self._record_signal_fingerprint(SignalType.ISSUE_UPDATED, payload, meta)
                                        issues_outcome = ExecutionOutcome.SUCCESS_CHANGED
                                    else:
                                        issues_outcome = ExecutionOutcome.SUCCESS_UNCHANGED
                                else:
                                    issues_outcome = ExecutionOutcome.SUCCESS_UNCHANGED
                            else:
                                issues_outcome = ExecutionOutcome.SUCCESS_UNCHANGED
                        else:
                            issues_outcome = ExecutionOutcome.SUCCESS_UNCHANGED
                    except (json.JSONDecodeError, ValueError, TypeError):
                        issues_outcome = ExecutionOutcome.TRANSFORM_ERROR
                else:
                    if issues_eval and issues_eval.new_status == TargetStatus.COOLDOWN:
                        issues_outcome = ExecutionOutcome.POLICY_COOLDOWN
                    elif res.status == FetchStatus.TIMEOUT or (res.status_code is not None and res.status_code == 0):
                        issues_outcome = ExecutionOutcome.TIMEOUT
                    elif res.error or (res.status_code is not None and res.status_code >= 400):
                        issues_outcome = ExecutionOutcome.FETCH_FAILED
                    else:
                        issues_outcome = ExecutionOutcome.SUCCESS_UNCHANGED
                last_status_code = res.status_code
        finally:
            _release_claims()

        # 4. Determine composite outcome
        if signals:
            outcome = ExecutionOutcome.SUCCESS_CHANGED
        elif is_any_304:
            outcome = ExecutionOutcome.NOT_MODIFIED
        elif release_outcome == ExecutionOutcome.POLICY_COOLDOWN or repo_outcome == ExecutionOutcome.POLICY_COOLDOWN or commits_outcome == ExecutionOutcome.POLICY_COOLDOWN or prs_outcome == ExecutionOutcome.POLICY_COOLDOWN or issues_outcome == ExecutionOutcome.POLICY_COOLDOWN:
            outcome = ExecutionOutcome.POLICY_COOLDOWN
        elif release_outcome == ExecutionOutcome.TIMEOUT or repo_outcome == ExecutionOutcome.TIMEOUT or commits_outcome == ExecutionOutcome.TIMEOUT or prs_outcome == ExecutionOutcome.TIMEOUT or issues_outcome == ExecutionOutcome.TIMEOUT:
            outcome = ExecutionOutcome.TIMEOUT
        elif release_outcome == ExecutionOutcome.FETCH_FAILED or repo_outcome == ExecutionOutcome.FETCH_FAILED or commits_outcome == ExecutionOutcome.FETCH_FAILED or prs_outcome == ExecutionOutcome.FETCH_FAILED or issues_outcome == ExecutionOutcome.FETCH_FAILED:
            outcome = ExecutionOutcome.FETCH_FAILED
        elif release_outcome == ExecutionOutcome.TRANSFORM_ERROR or repo_outcome == ExecutionOutcome.TRANSFORM_ERROR or commits_outcome == ExecutionOutcome.TRANSFORM_ERROR or prs_outcome == ExecutionOutcome.TRANSFORM_ERROR or issues_outcome == ExecutionOutcome.TRANSFORM_ERROR:
            outcome = ExecutionOutcome.TRANSFORM_ERROR
        else:
            outcome = ExecutionOutcome.SUCCESS_UNCHANGED

        # 4-1. Persist per-subresource state in metadata so next run can resume independently.
        # Priority: COOLDOWN > BACKOFF > NORMAL
        subresource_states: Dict[str, Dict[str, Any]] = {}
        if release_eval is not None:
            next_allowed_iso = None
            if release_eval.next_allowed_at is not None:
                next_allowed_iso = release_eval.next_allowed_at.isoformat()
            subresource_states["releases"] = {
                "status": release_eval.new_status.value.upper() if hasattr(release_eval.new_status, "value") else str(release_eval.new_status).upper(),
                "consecutive_failures": release_eval.consecutive_failures,
                "next_allowed_at": next_allowed_iso,
            }
        if repo_eval is not None:
            next_allowed_iso = None
            if repo_eval.next_allowed_at is not None:
                next_allowed_iso = repo_eval.next_allowed_at.isoformat()
            subresource_states["stars"] = {
                "status": repo_eval.new_status.value.upper() if hasattr(repo_eval.new_status, "value") else str(repo_eval.new_status).upper(),
                "consecutive_failures": repo_eval.consecutive_failures,
                "next_allowed_at": next_allowed_iso,
            }
        if commits_eval is not None:
            next_allowed_iso = None
            if commits_eval.next_allowed_at is not None:
                next_allowed_iso = commits_eval.next_allowed_at.isoformat()
            subresource_states["commits"] = {
                "status": commits_eval.new_status.value.upper() if hasattr(commits_eval.new_status, "value") else str(commits_eval.new_status).upper(),
                "consecutive_failures": commits_eval.consecutive_failures,
                "next_allowed_at": next_allowed_iso,
            }
        if prs_eval is not None:
            next_allowed_iso = None
            if prs_eval.next_allowed_at is not None:
                next_allowed_iso = prs_eval.next_allowed_at.isoformat()
            subresource_states["prs"] = {
                "status": prs_eval.new_status.value.upper() if hasattr(prs_eval.new_status, "value") else str(prs_eval.new_status).upper(),
                "consecutive_failures": prs_eval.consecutive_failures,
                "next_allowed_at": next_allowed_iso,
            }
        if issues_eval is not None:
            next_allowed_iso = None
            if issues_eval.next_allowed_at is not None:
                next_allowed_iso = issues_eval.next_allowed_at.isoformat()
            subresource_states["issues"] = {
                "status": issues_eval.new_status.value.upper() if hasattr(issues_eval.new_status, "value") else str(issues_eval.new_status).upper(),
                "consecutive_failures": issues_eval.consecutive_failures,
                "next_allowed_at": next_allowed_iso,
            }
        if subresource_states:
            meta["subresource_states"] = subresource_states

        # 4-2. Merge subresource states into composite target state.
        # This is only for scheduling/backoff semantics; signals are independent.
        evals = [e for e in [release_eval, repo_eval, commits_eval, prs_eval, issues_eval] if e is not None]
        if evals:
            severity = {
                TargetStatus.COOLDOWN: 0,
                TargetStatus.BACKOFF: 1,
                TargetStatus.NORMAL: 2,
            }
            worst = evals[0]
            for e in evals[1:]:
                if severity[e.new_status] <= severity[worst.new_status]:
                    worst = e
            observed_status = worst.new_status
            observed_consecutive_failures = worst.consecutive_failures
            observed_next_allowed_at = worst.next_allowed_at
        else:
            observed_status = TargetStatus.NORMAL
            observed_consecutive_failures = 0
            observed_next_allowed_at = None

        # 5. Build transition
        combined_etag = meta.get("release_etag") or meta.get("repo_etag") or meta.get("commits_etag") or meta.get("prs_etag") or meta.get("issues_etag")
        transition = transition_for(
            outcome,
            target=self.target,
            now=now,
            etag=combined_etag,
            last_modified=None,
            metadata=meta if signals or subresource_states else None,
            consecutive_failures=observed_consecutive_failures,
            next_allowed_at=observed_next_allowed_at,
            emit_signal=bool(signals),
            reason=f"Emitted {len(signals)} GitHub signals" if signals else "No new events",
        )

        # 5. Return observation-only result; scheduler/repo owns durable persistence.
        return GitHubTargetExecutionResult(
            target_id=self.target.id,
            allowed=True,
            status_code=last_status_code,
            new_status=transition.status,
            signals_emitted=signals,
            is_304=is_any_304 and len(signals) == 0,
            reason=f"Emitted {len(signals)} GitHub signals" if signals else "No new events",
            updated_metadata=meta,
            consecutive_failures=observed_consecutive_failures,
            next_allowed_at=observed_next_allowed_at,
            last_fetched_at=now,
            outcome=outcome,
            transition=transition,
        )
