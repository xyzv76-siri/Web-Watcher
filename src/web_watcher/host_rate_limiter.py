from datetime import datetime, timezone
from typing import Dict, Optional, Tuple


def _extract_host(url: str) -> Optional[str]:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.hostname:
        return parsed.hostname.lower()
    return None


class HostRateLimiter:
    """Shared per-host rate-limit authority backed by Repository.

    Provides atomic acquire/release semantics so that multiple workers
    cannot send concurrent requests to the same host.

    A repository instance is required; without it the limiter cannot
    provide cross-process safety and will raise at construction time.
    """

    def __init__(self, repository: object) -> None:
        if repository is None:
            raise RuntimeError("HostRateLimiter requires a Repository for cross-process safety")
        self._repository = repository
        self._active_claims: Dict[str, str] = {}  # host -> claim_token

    def prepare_request(
        self,
        host: str,
        now: datetime,
        lease_seconds: float = 300.0,
    ) -> Tuple[bool, Optional[str], Optional[float], Optional[str]]:
        if not host:
            return True, None, None, None

        now_dt = now if now.tzinfo else now.replace(tzinfo=timezone.utc)

        # Renew an existing claim from the same worker/process.
        existing_token = self._active_claims.get(host)
        if existing_token is not None:
            renewed = self._repository.renew_host_request(host, existing_token, now_dt, lease_seconds=lease_seconds)
            if renewed:
                return True, existing_token, None, None

        allowed, claim_token, wait_seconds = self._repository.acquire_host_request(host, now_dt, lease_seconds=lease_seconds)
        if allowed and claim_token:
            self._active_claims[host] = claim_token
            return True, claim_token, None, None

        if wait_seconds is not None and wait_seconds > 0:
            return False, None, wait_seconds, f"Host '{host}' is in rate-limit window ({int(wait_seconds)}s remaining)"
        return False, None, wait_seconds, f"Host '{host}' is rate-limited"

    def release_request(self, host: str) -> None:
        claim_token = self._active_claims.pop(host, None)
        if claim_token and self._repository is not None:
            try:
                self._repository.release_host_request(host, claim_token)
            except Exception:
                pass

    def update_after_response(self, host: str, next_allowed_at: Optional[datetime]) -> None:
        """Update host rate limit after a response. Only updates DB for actual rate-limit responses."""
        if not host:
            return
        if self._repository is not None and next_allowed_at is not None:
            self._repository.update_host_next_allowed(host, next_allowed_at)
        self.release_request(host)

    def reap_stale_claims(self, older_than: Optional[datetime] = None) -> int:
        """Clear expired claims from the repository."""
        if self._repository is None:
            return 0
        return self._repository.reap_stale_claims(older_than=older_than)
