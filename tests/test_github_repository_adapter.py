"""Tests for GitHubRepositoryAdapter and GitHubRepositorySnapshot."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
from urllib.error import HTTPError, URLError

from web_watcher.fetch import FetchRequest, FetchResult
from web_watcher.github_repository_adapter import (
    GitHubRepositoryAdapter,
    _GITHUB_API_BASE,
    _USER_AGENT,
)
from web_watcher.snapshots import GitHubRepositorySnapshot
from web_watcher.targets import WatchTarget


def _now() -> datetime:
    return datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _mk_target(**overrides):
    defaults = {
        "key": "github:openai/gpt-4o",
        "target_type": "github_repository",
        "name": "GPT-4o",
        "locator": "openai/gpt-4o",
    }
    defaults.update(overrides)
    return WatchTarget(**defaults)


_SAMPLE_PAYLOAD = {
    "name": "gpt-4o",
    "full_name": "openai/gpt-4o",
    "description": "OpenAI GPT-4o",
    "html_url": "https://github.com/openai/gpt-4o",
    "stargazers_count": 12345,
    "forks_count": 678,
    "open_issues_count": 90,
    "default_branch": "main",
    "created_at": "2024-05-13T12:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
    "pushed_at": "2026-01-01T00:00:00Z",
    "license": {"spdx_id": "MIT"},
    "archived": False,
    "visibility": "public",
}


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


class TestGitHubRepositorySnapshot:

    def test_snapshot_from_payload(self):
        snap = GitHubRepositoryAdapter._snapshot(_SAMPLE_PAYLOAD)
        assert snap.name == "gpt-4o"
        assert snap.full_name == "openai/gpt-4o"
        assert snap.stars == 12345
        assert snap.forks == 678
        assert snap.open_issues == 90
        assert snap.license_spdx_id == "MIT"
        assert snap.archived is False

    def test_snapshot_license_not_dict(self):
        payload = dict(_SAMPLE_PAYLOAD)
        payload["license"] = "MIT License"
        snap = GitHubRepositoryAdapter._snapshot(payload)
        assert snap.license_spdx_id is None

    def test_snapshot_license_missing(self):
        payload = dict(_SAMPLE_PAYLOAD)
        del payload["license"]
        snap = GitHubRepositoryAdapter._snapshot(payload)
        assert snap.license_spdx_id is None

    def test_snapshot_optional_fields_none(self):
        payload = dict(_SAMPLE_PAYLOAD)
        payload["description"] = None
        payload["default_branch"] = None
        payload["created_at"] = None
        payload["updated_at"] = None
        payload["pushed_at"] = None
        payload["visibility"] = None
        snap = GitHubRepositoryAdapter._snapshot(payload)
        assert snap.description is None
        assert snap.default_branch is None
        assert snap.visibility is None

    def test_snapshot_counts_defaults_zero(self):
        payload = dict(_SAMPLE_PAYLOAD)
        payload["stargazers_count"] = None
        payload["forks_count"] = None
        payload["open_issues_count"] = None
        snap = GitHubRepositoryAdapter._snapshot(payload)
        assert snap.stars == 0
        assert snap.forks == 0
        assert snap.open_issues == 0


# ---------------------------------------------------------------------------
# Helpers for network tests
# ---------------------------------------------------------------------------

class _MockResponse:
    """Mutable mock HTTP response usable as a context manager."""

    def __init__(self, status=200, body=None, headers=None):
        self.status = status
        self.headers = headers or {}
        self._body = (
            json.dumps(body or {}).encode("utf-8")
            if body is not None
            else b""
        )

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _header_value(headers, name):
    """Case-insensitive header lookup (urllib normalizes oddly)."""
    lower_name = name.lower()
    for k, v in headers.items():
        if k.lower() == lower_name:
            return v
    return None


def _configure_success(mock_urlopen, body=None, status=200, headers=None):
    mock_urlopen.return_value = _MockResponse(
        status=status, body=body or _SAMPLE_PAYLOAD, headers=headers
    )


def _configure_http_error(mock_urlopen, code, msg="Error"):
    mock_urlopen.side_effect = HTTPError(
        url="https://api.github.com/repos/openai/gpt-4o",
        code=code,
        msg=msg,
        hdrs={},
        fp=None,
    )


def _configure_url_error(mock_urlopen, msg="connection refused"):
    mock_urlopen.side_effect = URLError(msg)


# ---------------------------------------------------------------------------
# Adapter protocol
# ---------------------------------------------------------------------------


class TestGitHubRepositoryAdapterProtocol:

    def test_supports_github_repository(self):
        adapter = GitHubRepositoryAdapter()
        assert adapter.supports(_mk_target()) is True

    def test_supports_wrong_type(self):
        adapter = GitHubRepositoryAdapter()
        assert adapter.supports(
            _mk_target(
                target_type="official_website",
                locator="https://example.com",
            )
        ) is False

    def test_fetch_delegates_to_fetch_repository(self):
        adapter = GitHubRepositoryAdapter()
        request = FetchRequest(target=_mk_target())
        with patch.object(
            adapter, "fetch_repository", return_value=MagicMock()
        ) as mock_fetch:
            result = adapter.fetch(request)
            mock_fetch.assert_called_once_with(request)
            assert result is mock_fetch.return_value


# ---------------------------------------------------------------------------
# Successful fetch
# ---------------------------------------------------------------------------


class TestFetchRepository:

    @patch("web_watcher.github_repository_adapter.urlopen")
    def test_successful_fetch(self, mock_urlopen):
        _configure_success(mock_urlopen, headers={
            "Content-Type": "application/json",
            "ETag": "w/\"abc123\"",
            "Last-Modified": "Wed, 01 Jan 2026 00:00:00 GMT",
        })

        adapter = GitHubRepositoryAdapter()
        result = adapter.fetch_repository(FetchRequest(target=_mk_target()))

        assert result.success is True
        assert result.status_code == 200
        assert result.target_key == "github:openai/gpt-4o"
        assert result.etag == "w/\"abc123\""
        assert result.last_modified == "Wed, 01 Jan 2026 00:00:00 GMT"
        assert result.content_type == "application/json"
        content = json.loads(result.content)
        assert content["name"] == "gpt-4o"
        assert content["stars"] == 12345
        assert result.metadata["source"] == "github"
        assert "openai/gpt-4o" in result.metadata["endpoint"]

    @patch("web_watcher.github_repository_adapter.urlopen")
    def test_request_uses_correct_url(self, mock_urlopen):
        _configure_success(mock_urlopen)

        adapter = GitHubRepositoryAdapter()
        adapter.fetch_repository(FetchRequest(
            target=_mk_target(locator="torvalds/linux")
        ))

        call_url = mock_urlopen.call_args[0][0].full_url
        assert call_url == "https://api.github.com/repos/torvalds/linux"


# ---------------------------------------------------------------------------
# Cache headers
# ---------------------------------------------------------------------------


class TestCacheHeaders:

    @patch("web_watcher.github_repository_adapter.urlopen")
    def test_sends_etag_header(self, mock_urlopen):
        _configure_success(mock_urlopen)

        adapter = GitHubRepositoryAdapter()
        adapter.fetch_repository(FetchRequest(
            target=_mk_target(),
            etag="w/\"old-etag\"",
        ))

        sent_request = mock_urlopen.call_args[0][0]
        assert _header_value(sent_request.headers, "if-none-match") == "w/\"old-etag\""

    @patch("web_watcher.github_repository_adapter.urlopen")
    def test_sends_last_modified_header(self, mock_urlopen):
        _configure_success(mock_urlopen)

        adapter = GitHubRepositoryAdapter()
        adapter.fetch_repository(FetchRequest(
            target=_mk_target(),
            last_modified="Mon, 30 Dec 2025 00:00:00 GMT",
        ))

        sent_request = mock_urlopen.call_args[0][0]
        assert (
            _header_value(sent_request.headers, "if-modified-since")
            == "Mon, 30 Dec 2025 00:00:00 GMT"
        )

    @patch("web_watcher.github_repository_adapter.urlopen")
    def test_sets_user_agent(self, mock_urlopen):
        _configure_success(mock_urlopen)

        adapter = GitHubRepositoryAdapter()
        adapter.fetch_repository(FetchRequest(target=_mk_target()))

        sent_request = mock_urlopen.call_args[0][0]
        assert _header_value(sent_request.headers, "user-agent") == _USER_AGENT

    @patch("web_watcher.github_repository_adapter.urlopen")
    def test_uses_correct_http_method(self, mock_urlopen):
        _configure_success(mock_urlopen)

        adapter = GitHubRepositoryAdapter()
        adapter.fetch_repository(FetchRequest(target=_mk_target()))

        sent_request = mock_urlopen.call_args[0][0]
        assert sent_request.method == "GET"


# ---------------------------------------------------------------------------
# 304 Not Modified
# ---------------------------------------------------------------------------


class Test304NotModified:

    @patch("web_watcher.github_repository_adapter.urlopen")
    def test_returns_not_modified_result(self, mock_urlopen):
        _configure_http_error(mock_urlopen, 304, "Not Modified")

        adapter = GitHubRepositoryAdapter()
        result = adapter.fetch_repository(FetchRequest(
            target=_mk_target(),
            etag="w/\"old-etag\"",
            last_modified="Mon, 30 Dec 2025 00:00:00 GMT",
        ))

        assert result.success is True
        assert result.status_code == 304
        assert result.content is None
        assert result.etag == "w/\"old-etag\""
        assert result.last_modified == "Mon, 30 Dec 2025 00:00:00 GMT"
        assert result.metadata["unchanged"] == "true"

    @patch("web_watcher.github_repository_adapter.urlopen")
    def test_304_does_not_retry(self, mock_urlopen):
        _configure_http_error(mock_urlopen, 304, "Not Modified")

        adapter = GitHubRepositoryAdapter()
        adapter.fetch_repository(FetchRequest(
            target=_mk_target(),
            etag="w/\"old-etag\"",
        ))
        assert mock_urlopen.call_count == 1


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:

    @patch("web_watcher.github_repository_adapter.urlopen")
    def test_404_returns_failure(self, mock_urlopen):
        _configure_http_error(mock_urlopen, 404, "Not Found")

        adapter = GitHubRepositoryAdapter()
        result = adapter.fetch_repository(FetchRequest(
            target=_mk_target(locator="openai/does-not-exist")
        ))

        assert result.success is False
        assert result.status_code == 404

    @patch("web_watcher.github_repository_adapter.urlopen")
    def test_403_returns_failure(self, mock_urlopen):
        _configure_http_error(mock_urlopen, 403, "Forbidden")

        adapter = GitHubRepositoryAdapter()
        result = adapter.fetch_repository(FetchRequest(target=_mk_target()))

        assert result.success is False
        assert result.status_code == 403

    @patch("web_watcher.github_repository_adapter.urlopen")
    def test_network_error_returns_failure(self, mock_urlopen):
        _configure_url_error(mock_urlopen)

        adapter = GitHubRepositoryAdapter()
        result = adapter.fetch_repository(FetchRequest(target=_mk_target()))

        assert result.success is False
        assert result.status_code is None
        assert "network error" in result.error

    def test_unsupported_target_raises(self):
        adapter = GitHubRepositoryAdapter()
        request = FetchRequest(
            target=_mk_target(
                target_type="official_website",
                locator="https://example.com",
            )
        )
        with pytest.raises(ValueError, match="unsupported target type"):
            adapter.fetch_repository(request)


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------


class TestRetryLogic:

    @patch("web_watcher.github_repository_adapter.urlopen")
    @patch("web_watcher.github_repository_adapter.time")
    def test_retries_on_429(self, mock_time, mock_urlopen):
        _configure_http_error(mock_urlopen, 429, "Too Many Requests")
        # After 429, return success
        mock_urlopen.side_effect = [
            HTTPError(
                url="https://api.github.com/repos/openai/gpt-4o",
                code=429, msg="Too Many Requests", hdrs={}, fp=None,
            ),
            _MockResponse(status=200, body=_SAMPLE_PAYLOAD),
        ]

        adapter = GitHubRepositoryAdapter()
        result = adapter.fetch_repository(FetchRequest(target=_mk_target()))

        assert result.success is True
        assert mock_urlopen.call_count == 2
        mock_time.sleep.assert_called_once_with(1)

    @patch("web_watcher.github_repository_adapter.urlopen")
    @patch("web_watcher.github_repository_adapter.time")
    def test_retries_on_500(self, mock_time, mock_urlopen):
        err500 = HTTPError(
            url="https://api.github.com/repos/openai/gpt-4o",
            code=500, msg="Internal Server Error", hdrs={}, fp=None,
        )
        mock_urlopen.side_effect = [
            err500, err500, _MockResponse(status=200, body=_SAMPLE_PAYLOAD)
        ]

        adapter = GitHubRepositoryAdapter()
        result = adapter.fetch_repository(FetchRequest(target=_mk_target()))

        assert result.success is True
        assert mock_urlopen.call_count == 3
        assert mock_time.sleep.call_count == 2

    @patch("web_watcher.github_repository_adapter.urlopen")
    @patch("web_watcher.github_repository_adapter.time")
    def test_exponential_backoff(self, mock_time, mock_urlopen):
        err503 = HTTPError(
            url="https://api.github.com/repos/openai/gpt-4o",
            code=503, msg="Service Unavailable", hdrs={}, fp=None,
        )
        # 3 errors → 3 retries (attempt 0..2), then final call raises
        # with max_retries=3, the code makes 4 urlopen calls total
        mock_urlopen.side_effect = [err503, err503, err503, err503]

        adapter = GitHubRepositoryAdapter()
        result = adapter.fetch_repository(FetchRequest(target=_mk_target()))

        assert result.success is False
        assert mock_time.sleep.call_count == 3
        calls = [c[0][0] for c in mock_time.sleep.call_args_list]
        assert calls == [1, 2, 4]

    @patch("web_watcher.github_repository_adapter.urlopen")
    @patch("web_watcher.github_repository_adapter.time")
    def test_does_not_retry_on_404(self, mock_time, mock_urlopen):
        _configure_http_error(mock_urlopen, 404, "Not Found")

        adapter = GitHubRepositoryAdapter()
        adapter.fetch_repository(FetchRequest(target=_mk_target()))

        assert mock_urlopen.call_count == 1
        mock_time.sleep.assert_not_called()

    @patch("web_watcher.github_repository_adapter.urlopen")
    def test_retries_on_url_error(self, mock_urlopen):
        mock_urlopen.side_effect = [
            URLError("connection refused"),
            _MockResponse(status=200, body=_SAMPLE_PAYLOAD),
        ]

        adapter = GitHubRepositoryAdapter(sleep=lambda _d: None)
        result = adapter.fetch_repository(FetchRequest(target=_mk_target()))

        assert result.success is True
        assert mock_urlopen.call_count == 2


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class TestConfiguration:

    @patch("web_watcher.github_repository_adapter.urlopen")
    def test_timeout_is_passed(self, mock_urlopen):
        _configure_success(mock_urlopen)

        adapter = GitHubRepositoryAdapter(timeout=30.0)
        adapter.fetch_repository(FetchRequest(target=_mk_target()))

        assert mock_urlopen.call_args[1].get("timeout") == 30.0

    @patch("web_watcher.github_repository_adapter.urlopen")
    def test_injectable_sleep(self, mock_urlopen):
        delays = []
        err500 = HTTPError(
            url="https://api.github.com/repos/openai/gpt-4o",
            code=500, msg="Error", hdrs={}, fp=None,
        )
        # max_retries=2 → 3 urlopen calls (attempt 0,1 retry; attempt 2 raises)
        mock_urlopen.side_effect = [err500, err500, err500]

        adapter = GitHubRepositoryAdapter(
            max_retries=2,
            sleep=lambda d: delays.append(d),
        )
        adapter.fetch_repository(FetchRequest(target=_mk_target()))

        assert delays == [1, 2]
