"""HTTP fetcher with polite caching, timeout handling, and retry support."""

import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Callable, Type
import requests
from requests.exceptions import RequestException, Timeout, ConnectionError, HTTPError

from .fetch import FetchResult, FetchStatus

RequestError = RequestException


def retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 10.0,
    backoff_factor: float = 2.0,
    retryable_exceptions: tuple = (Timeout, ConnectionError),
) -> Callable:
    """Retry decorator with exponential backoff for network operations."""
    def decorator(func: Callable) -> Callable:
        def wrapper(*args, **kwargs):
            last_exception = None
            delay = base_delay
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as exc:
                    last_exception = exc
                    if attempt == max_attempts:
                        break
                    time.sleep(delay)
                    delay = min(delay * backoff_factor, max_delay)
            raise last_exception  # type: ignore[misc]
        return wrapper
    return decorator


def _fetch_with_playwright(
    url: str,
    timeout: float,
    cookies: Optional[Dict[str, str]] = None,
    proxy: Optional[str] = None,
) -> FetchResult:
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    except ImportError as exc:
        return FetchResult(
            target_key="",
            status=FetchStatus.INVALID_RESPONSE,
            status_code=None,
            fetched_at=datetime.now(timezone.utc),
            content=None,
            error=f"js_render requested but playwright is not installed: {exc}",
            metadata={"renderer": "playwright_unavailable"},
        )

    metadata: Dict[str, Any] = {"renderer": "playwright"}
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
        try:
            from playwright.sync_api import __version__ as playwright_version
        except ImportError:
            try:
                import playwright
                playwright_version = getattr(playwright, "__version__", None) or getattr(playwright.sync_api, "__version__", None) or "unknown"
            except (ImportError, AttributeError, TypeError):
                playwright_version = "unknown"
        metadata["playwright_version"] = playwright_version
    except ImportError as exc:
        return FetchResult(
            target_key="",
            status=FetchStatus.INVALID_RESPONSE,
            status_code=None,
            fetched_at=datetime.now(timezone.utc),
            content=None,
            error=f"js_render requested but playwright is not installed: {exc}",
            metadata={"renderer": "playwright_unavailable"},
        )

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="WebWatcher/1.0 (https://github.com/xyzv76-siri/Web-Watcher)",
                proxy={"server": proxy} if proxy else None,
            )
            if cookies:
                context.add_cookies([{"name": k, "value": v, "url": url} for k, v in cookies.items()])
            page = context.new_page()
            try:
                page.goto(url, timeout=int(timeout * 1000), wait_until="domcontentloaded")
                content = page.content()
                status_code = 200
            except PlaywrightTimeout:
                browser.close()
                return FetchResult(
                    target_key="",
                    status=FetchStatus.TIMEOUT,
                    status_code=None,
                    fetched_at=datetime.now(timezone.utc),
                    content=None,
                    error="Playwright navigation timeout",
                    metadata=metadata,
                )
            browser.close()
            return FetchResult(
                target_key="",
                status=FetchStatus.SUCCESS,
                status_code=status_code,
                fetched_at=datetime.now(timezone.utc),
                content=content,
                content_type="text/html",
                error=None,
                metadata=metadata,
            )
    except (RequestException, Timeout, ConnectionError, HTTPError, ValueError, TypeError) as exc:  # pragma: no cover - defensive fallback
        return FetchResult(
            target_key="",
            status=FetchStatus.NETWORK_ERROR,
            status_code=None,
            fetched_at=datetime.now(timezone.utc),
            content=None,
            error=f"Playwright error: {exc}",
            metadata=metadata,
        )


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
        js_render: bool = False,
    ) -> FetchResult:
        if js_render:
            return _fetch_with_playwright(url, timeout or self.default_timeout, cookies=cookies, proxy=proxy)
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
            @retry(max_attempts=3, base_delay=1.0, max_delay=5.0, retryable_exceptions=(Timeout, ConnectionError))
            def _do_request():
                return self.session.get(url, **request_kwargs)

            response = _do_request()

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
