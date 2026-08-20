from datetime import datetime, timezone
from typing import Dict, Optional, Tuple


def _extract_host(url: str) -> Optional[str]:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.hostname:
        return parsed.hostname.lower()
    return None


class HostRateLimiter:
    """Shared per-host rate-limit authority.

    Tracks the earliest next-allowed time for each host. When multiple
    targets share the same host, the most restrictive next_allowed_at wins.
    """

    def __init__(self) -> None:
        self._host_next_allowed: Dict[str, datetime] = {}

    def prepare_request(self, host: str, now: datetime) -> Tuple[bool, Optional[float], Optional[str]]:
        if not host:
            return True, None, None

        now_dt = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        host_next = self._host_next_allowed.get(host)
        if host_next and now_dt < host_next:
            remaining = (host_next - now_dt).total_seconds()
            return False, remaining, f"Host '{host}' is in rate-limit window ({int(remaining)}s remaining)"
        return True, None, None

    def update_after_response(self, host: str, next_allowed_at: Optional[datetime]) -> None:
        if not host or next_allowed_at is None:
            return
        current = self._host_next_allowed.get(host)
        if current is None or next_allowed_at > current:
            self._host_next_allowed[host] = next_allowed_at
