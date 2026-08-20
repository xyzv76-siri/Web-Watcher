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
        now = now or datetime.now(timezone.utc)
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
                outcome=ExecutionOutcome.POLICY_BLOCKED,
                transition=transition_for(
                    ExecutionOutcome.POLICY_BLOCKED,
                    target=self.target,
                    now=now,
                ),
            )

        meta = dict(self.target.metadata or {})
        signals: List[Any] = []
        is_any_304 = False
        last_status_code = 200
        fetch_error: Optional[str] = None

        # Track outcomes from each sub-fetch to derive the composite outcome.
        release_outcome = ExecutionOutcome.SUCCESS_UNCHANGED
        repo_outcome = ExecutionOutcome.SUCCESS_UNCHANGED
        release_eval = None
        repo_eval = None

        # 2. 检查 Releases
        if "releases" in self.watch_types:
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

            rel_etag = meta.get("release_etag")
            res = fetcher.fetch(rel_url, custom_headers=self._build_headers(rel_etag), timeout=self.timeout)

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
        if "stars" in self.watch_types and self.target.status == TargetStatus.NORMAL:
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

            repo_etag = meta.get("repo_etag")
            res = fetcher.fetch(repo_url, custom_headers=self._build_headers(repo_etag), timeout=self.timeout)

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

        # 4. Determine composite outcome
        if signals:
            outcome = ExecutionOutcome.SUCCESS_CHANGED
        elif is_any_304:
            # Any checked watch type returned 304 → overall NOT_MODIFIED.
            # Unchecked watch types keep their initial SUCCESS_UNCHANGED and must not block this.
            outcome = ExecutionOutcome.NOT_MODIFIED
        elif release_outcome == ExecutionOutcome.POLICY_COOLDOWN or repo_outcome == ExecutionOutcome.POLICY_COOLDOWN:
            outcome = ExecutionOutcome.POLICY_COOLDOWN
        elif release_outcome == ExecutionOutcome.TIMEOUT or repo_outcome == ExecutionOutcome.TIMEOUT:
            outcome = ExecutionOutcome.TIMEOUT
        elif release_outcome == ExecutionOutcome.FETCH_FAILED or repo_outcome == ExecutionOutcome.FETCH_FAILED:
            outcome = ExecutionOutcome.FETCH_FAILED
        elif release_outcome == ExecutionOutcome.TRANSFORM_ERROR or repo_outcome == ExecutionOutcome.TRANSFORM_ERROR:
            outcome = ExecutionOutcome.TRANSFORM_ERROR
        else:
            outcome = ExecutionOutcome.SUCCESS_UNCHANGED

        # Use the most "severe" evaluation for transition state.
        # Priority: POLICY_COOLDOWN > TIMEOUT > FETCH_FAILED > TRANSFORM_ERROR > SUCCESS_CHANGED > NOT_MODIFIED > SUCCESS_UNCHANGED
        if release_eval and repo_eval:
            if release_eval.new_status == TargetStatus.COOLDOWN or repo_eval.new_status == TargetStatus.COOLDOWN:
                observed_status = TargetStatus.COOLDOWN
                observed_consecutive_failures = max(release_eval.consecutive_failures, repo_eval.consecutive_failures)
                observed_next_allowed_at = release_eval.next_allowed_at or repo_eval.next_allowed_at
            elif release_eval.new_status == TargetStatus.BACKOFF or repo_eval.new_status == TargetStatus.BACKOFF:
                observed_status = TargetStatus.BACKOFF
                observed_consecutive_failures = max(release_eval.consecutive_failures, repo_eval.consecutive_failures)
                observed_next_allowed_at = release_eval.next_allowed_at or repo_eval.next_allowed_at
            else:
                observed_status = TargetStatus.NORMAL
                observed_consecutive_failures = max(release_eval.consecutive_failures, repo_eval.consecutive_failures)
                observed_next_allowed_at = release_eval.next_allowed_at or repo_eval.next_allowed_at
        elif release_eval:
            observed_status = release_eval.new_status
            observed_consecutive_failures = release_eval.consecutive_failures
            observed_next_allowed_at = release_eval.next_allowed_at
        elif repo_eval:
            observed_status = repo_eval.new_status
            observed_consecutive_failures = repo_eval.consecutive_failures
            observed_next_allowed_at = repo_eval.next_allowed_at

        # 5. Build transition
        combined_etag = meta.get("release_etag") or meta.get("repo_etag")
        transition = transition_for(
            outcome,
            target=self.target,
            now=now,
            etag=combined_etag,
            last_modified=None,
            metadata=meta if signals else None,
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
