"""HTTP fetcher with polite caching and timeout handling."""

from dataclasses import dataclass
from typing import Optional, Dict, Any
import requests
from requests.exceptions import RequestException


@dataclass
class FetchResult:
    """Result of a fetch operation."""
    status_code: Optional[int]
    content: Optional[str] = None
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    is_304_not_modified: bool = False
    error: Optional[str] = None
    headers: Optional[Dict[str, str]] = None


class SmartFetcher:
    """Polite web fetcher with ETag/Last-Modified support and timeout handling."""

    def __init__(self, default_timeout: float = 10.0):
        self.default_timeout = default_timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "WebWatcher/1.0 (https://github.com/xyzv76-siri/Web-Watcher)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        })

    def fetch(
        self,
        url: str,
        custom_headers: Optional[Dict[str, str]] = None,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> FetchResult:
        timeout = timeout or self.default_timeout
        headers = dict(self.session.headers)
        if custom_headers:
            headers.update(custom_headers)
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        try:
            response = self.session.get(
                url,
                headers=headers,
                timeout=timeout,
                allow_redirects=True,
            )
            is_304 = response.status_code == 304
            return FetchResult(
                status_code=response.status_code,
                content=response.text if not is_304 else None,
                etag=response.headers.get("ETag"),
                last_modified=response.headers.get("Last-Modified"),
                is_304_not_modified=is_304,
                error=None,
                headers=dict(response.headers),
            )
        except RequestException as e:
            return FetchResult(
                status_code=None,
                content=None,
                etag=None,
                last_modified=None,
                is_304_not_modified=False,
                error=str(e),
                headers=None,
            )

    def close(self) -> None:
        """Close the underlying session."""
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
