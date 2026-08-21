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
        cookies: Optional[Dict[str, str]] = None,
        auth: Optional[tuple] = None,
        proxy: Optional[str] = None,
    ) -> FetchResult:
        timeout = timeout or self.default_timeout
        headers = dict(self.session.headers)
        if custom_headers:
            headers.update(custom_headers)
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        request_kwargs: Dict[str, Any] = {
            "headers": headers,
            "timeout": timeout,
            "allow_redirects": False,
        }
        if cookies:
            request_kwargs["cookies"] = cookies
        if auth:
            request_kwargs["auth"] = auth
        if proxy:
            request_kwargs["proxies"] = {"http": proxy, "https": proxy}

        try:
            response = self.session.get(url, **request_kwargs)

            status_code = response.status_code
            redirect_url = response.headers.get("Location") if status_code in (301, 302, 303, 307, 308) else None
            metadata: Dict[str, Any] = {"headers": dict(response.headers)}
            if redirect_url:
                metadata["redirect_url"] = redirect_url
                metadata["redirect_status"] = status_code

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
                    metadata=metadata,
                )

            if status_code in (301, 302, 303, 307, 308):
                return FetchResult(
                    target_key="",
                    status=FetchStatus.REDIRECT,
                    status_code=status_code,
                    fetched_at=datetime.now(timezone.utc),
                    content=None,
                    content_type=response.headers.get("Content-Type"),
                    etag=response.headers.get("ETag"),
                    last_modified=response.headers.get("Last-Modified"),
                    error=f"HTTP {status_code}: Redirect",
                    metadata=metadata,
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
                    metadata=metadata,
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
                    metadata=metadata,
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
                metadata=metadata,
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
