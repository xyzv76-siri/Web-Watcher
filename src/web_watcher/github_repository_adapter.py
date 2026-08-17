"""Concrete GitHub repository adapter.

Fetches repository metadata via the GitHub REST API (no-auth, rate-limited).
Uses only stdlib (urllib.request) — no external HTTP client dependencies.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Optional, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .fetch import FetchRequest, FetchResult, SourceAdapter
from .snapshots import GitHubRepositorySnapshot
from .targets import WatchTarget


_GITHUB_API_BASE = "https://api.github.com/repos"
_USER_AGENT = "QwenPaw-WebWatcher/1.0"


class GitHubRepositoryAdapter:
    """Fetches GitHub repository metadata.

    Implements the SourceAdapter protocol.
    Supports only targets whose locator looks like 'owner/repo'.
    """

    def __init__(
        self,
        timeout: float = 15.0,
        max_retries: int = 3,
        sleep: Optional[callable] = None,
    ):
        self.timeout = timeout
        self.max_retries = max_retries
        # Injectable for deterministic tests
        self._sleep: callable = (sleep if sleep is not None else time.sleep)

    # ------------------------------------------------------------------
    # SourceAdapter protocol
    # ------------------------------------------------------------------

    def supports(self, target: WatchTarget) -> bool:
        return target.target_type == "github_repository"

    def fetch(self, request: FetchRequest) -> FetchResult:
        return self.fetch_repository(request)

    # ------------------------------------------------------------------
    # Public fetch API
    # ------------------------------------------------------------------

    def fetch_repository(
        self,
        request: FetchRequest,
    ) -> FetchResult:
        target = request.target

        if not self.supports(target):
            raise ValueError(
                f"unsupported target type: {target.target_type}"
            )

        url = self._repository_url(target.locator)
        fetched_at = datetime.now(timezone.utc)

        try:
            response = self._request(
                url,
                etag=request.etag,
                last_modified=request.last_modified,
            )

            with response:
                raw = response.read().decode("utf-8")
                payload = json.loads(raw)
                snapshot = self._snapshot(payload)

                headers = response.headers
                return FetchResult(
                    target_key=target.key,
                    success=True,
                    status_code=response.status,
                    fetched_at=fetched_at,
                    content=json.dumps(snapshot.__dict__, sort_keys=True),
                    content_type=headers.get("Content-Type"),
                    etag=headers.get("ETag"),
                    last_modified=headers.get("Last-Modified"),
                    metadata={
                        "source": "github",
                        "endpoint": url,
                    },
                )

        except HTTPError as exc:
            if exc.code == 304:
                return FetchResult(
                    target_key=target.key,
                    success=True,
                    status_code=304,
                    fetched_at=fetched_at,
                    content=None,
                    etag=request.etag,
                    last_modified=request.last_modified,
                    metadata={
                        "source": "github",
                        "endpoint": url,
                        "unchanged": "true",
                    },
                )

            return FetchResult(
                target_key=target.key,
                success=False,
                status_code=exc.code,
                fetched_at=fetched_at,
                error=f"HTTP {exc.code}: {exc.reason}",
                metadata={
                    "source": "github",
                    "endpoint": url,
                },
            )

        except (URLError, TimeoutError, OSError) as exc:
            return FetchResult(
                target_key=target.key,
                success=False,
                status_code=None,
                fetched_at=fetched_at,
                error=f"network error: {exc}",
                metadata={
                    "source": "github",
                    "endpoint": url,
                },
            )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _snapshot(payload: dict) -> GitHubRepositorySnapshot:
        license_data = payload.get("license")
        if license_data is not None and not isinstance(license_data, dict):
            license_data = None

        return GitHubRepositorySnapshot(
            name=str(payload["name"]),
            full_name=str(payload["full_name"]),
            description=payload.get("description"),
            html_url=str(payload["html_url"]),
            stars=int(payload.get("stargazers_count") or 0),
            forks=int(payload.get("forks_count") or 0),
            open_issues=int(payload.get("open_issues_count") or 0),
            default_branch=payload.get("default_branch"),
            created_at=payload.get("created_at"),
            updated_at=payload.get("updated_at"),
            pushed_at=payload.get("pushed_at"),
            license_spdx_id=(
                license_data.get("spdx_id") if license_data else None
            ),
            archived=bool(payload.get("archived", False)),
            visibility=payload.get("visibility"),
        )

    def _repository_url(self, locator: str) -> str:
        return f"{_GITHUB_API_BASE}/{locator}"

    def _headers(
        self,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
    ) -> dict[str, str]:
        headers: dict[str, str] = {
            "User-Agent": _USER_AGENT,
            "Accept": "application/vnd.github.v3+json",
        }
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        return headers

    def _request(
        self,
        url: str,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
    ):
        request = Request(
            url,
            headers=self._headers(
                etag=etag,
                last_modified=last_modified,
            ),
            method="GET",
        )

        attempt = 0

        while True:
            try:
                return urlopen(
                    request,
                    timeout=self.timeout,
                )

            except HTTPError as exc:
                retryable = exc.code == 429 or exc.code >= 500
                if retryable and attempt < self.max_retries:
                    self._sleep(2**attempt)
                    attempt += 1
                    continue
                raise

            except URLError:
                if attempt < self.max_retries:
                    self._sleep(2**attempt)
                    attempt += 1
                    continue
                raise
