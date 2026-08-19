"""HTTP fetcher with polite caching and timeout handling."""

from datetime import datetime, timezone
from typing import Optional, Dict, Any
import requests
from requests.exceptions import RequestException, Timeout, ConnectionError, HTTPError

from .fetch import FetchResult, FetchStatus


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

            status_code = response.status_code
            if status_code == 304:
                return FetchResult(
                    target_key="",
                    status=FetchStatus.NOT_MODIFIED,
                    status_code=status_code,
                    fetched_at=datetime.now(timezone.utc),
                    content=None,
                    content_type=response.headers.get("Content-Type"),
                    etag=response.headers.get("ETag"),
                    last_modified=response.headers.get("Last-Modified"),
                    error=None,
                    metadata={"headers": dict(response.headers)},
                )

            if 200 <= status_code < 300:
                return FetchResult(
                    target_key="",
                    status=FetchStatus.SUCCESS,
                    status_code=status_code,
                    fetched_at=datetime.now(timezone.utc),
                    content=response.text,
                    content_type=response.headers.get("Content-Type"),
                    etag=response.headers.get("ETag"),
                    last_modified=response.headers.get("Last-Modified"),
                    error=None,
                    metadata={"headers": dict(response.headers)},
                )

            if status_code == 429:
                return FetchResult(
                    target_key="",
                    status=FetchStatus.RATE_LIMITED,
                    status_code=status_code,
                    fetched_at=datetime.now(timezone.utc),
                    content=None,
                    content_type=response.headers.get("Content-Type"),
                    etag=response.headers.get("ETag"),
                    last_modified=response.headers.get("Last-Modified"),
                    error=f"HTTP {status_code}: Too Many Requests",
                    metadata={"headers": dict(response.headers)},
                )

            # Other HTTP errors (4xx, 5xx)
            return FetchResult(
                target_key="",
                status=FetchStatus.HTTP_ERROR,
                status_code=status_code,
                fetched_at=datetime.now(timezone.utc),
                content=None,
                content_type=response.headers.get("Content-Type"),
                etag=response.headers.get("ETag"),
                last_modified=response.headers.get("Last-Modified"),
                error=f"HTTP {status_code}",
                metadata={"headers": dict(response.headers)},
            )

        except Timeout:
            return FetchResult(
                target_key="",
                status=FetchStatus.TIMEOUT,
                status_code=None,
                fetched_at=datetime.now(timezone.utc),
                content=None,
                error="Request timed out",
            )
        except ConnectionError:
            return FetchResult(
                target_key="",
                status=FetchStatus.NETWORK_ERROR,
                status_code=None,
                fetched_at=datetime.now(timezone.utc),
                content=None,
                error="Connection error",
            )
        except HTTPError as e:
            status_code = getattr(e.response, "status_code", None)
            if status_code == 429:
                status = FetchStatus.RATE_LIMITED
            else:
                status = FetchStatus.HTTP_ERROR
            return FetchResult(
                target_key="",
                status=status,
                status_code=status_code,
                fetched_at=datetime.now(timezone.utc),
                content=None,
                error=str(e),
            )
        except RequestException as e:
            return FetchResult(
                target_key="",
                status=FetchStatus.NETWORK_ERROR,
                status_code=None,
                fetched_at=datetime.now(timezone.utc),
                content=None,
                error=str(e),
            )

    def close(self) -> None:
        """Close the underlying session."""
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
