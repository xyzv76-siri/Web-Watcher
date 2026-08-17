"""Tests for FetchRequest, FetchResult, Fetcher, SourceAdapter, and select_adapter."""

from datetime import datetime, timezone

import pytest

from web_watcher.fetch import (
    FetchRequest,
    FetchResult,
    Fetcher,
    SourceAdapter,
    select_adapter,
)
from web_watcher.targets import WatchTarget


def _now() -> datetime:
    return datetime(2026, 1, 1, tzinfo=timezone.utc)


def _mk_target(**overrides):
    defaults = {
        "key": "github:example/project",
        "target_type": "github_repository",
        "name": "Example Project",
        "locator": "example/project",
    }
    defaults.update(overrides)
    return WatchTarget(**defaults)


# ---------------------------------------------------------------------------
# FetchRequest
# ---------------------------------------------------------------------------


class TestFetchRequest:

    def test_minimal_request(self):
        target = _mk_target()
        req = FetchRequest(target=target)
        assert req.target is target
        assert req.etag is None
        assert req.last_modified is None

    def test_request_with_etag_and_last_modified(self):
        req = FetchRequest(
            target=_mk_target(),
            etag="abc123",
            last_modified="Wed, 01 Jan 2026 00:00:00 GMT",
        )
        assert req.etag == "abc123"
        assert req.last_modified.startswith("Wed")

    def test_request_is_frozen(self):
        req = FetchRequest(target=_mk_target())
        with pytest.raises(Exception):
            req.target = _mk_target(key="other")


# ---------------------------------------------------------------------------
# FetchResult
# ---------------------------------------------------------------------------


class TestFetchResult:

    def test_success_result_minimal(self):
        result = FetchResult(
            target_key="github:example/project",
            success=True,
            status_code=200,
            fetched_at=_now(),
        )
        assert result.success is True
        assert result.status_code == 200
        assert result.error is None

    def test_success_result_with_content(self):
        result = FetchResult(
            target_key="k",
            success=True,
            status_code=200,
            fetched_at=_now(),
            content="<html></html>",
            content_type="text/html",
        )
        assert result.content == "<html></html>"
        assert result.content_type == "text/html"

    def test_failure_result(self):
        result = FetchResult(
            target_key="k",
            success=False,
            status_code=404,
            fetched_at=_now(),
            error="not found",
        )
        assert result.success is False
        assert result.error == "not found"
        assert result.status_code == 404

    def test_result_with_etag_and_last_modified(self):
        result = FetchResult(
            target_key="k",
            success=True,
            status_code=304,
            fetched_at=_now(),
            etag="new-etag",
            last_modified="Wed, 02 Jan 2026 00:00:00 GMT",
        )
        assert result.etag == "new-etag"
        assert result.last_modified.startswith("Wed")

    def test_result_with_content_hash(self):
        result = FetchResult(
            target_key="k",
            success=True,
            status_code=200,
            fetched_at=_now(),
            content_hash="sha256:abc",
        )
        assert result.content_hash == "sha256:abc"

    def test_result_metadata_default_empty(self):
        result = FetchResult(
            target_key="k",
            success=True,
            status_code=200,
            fetched_at=_now(),
        )
        assert result.metadata == {}

    def test_result_metadata_custom(self):
        result = FetchResult(
            target_key="k",
            success=True,
            status_code=200,
            fetched_at=_now(),
            metadata={"source": "test"},
        )
        assert result.metadata["source"] == "test"

    def test_result_is_frozen(self):
        result = FetchResult(
            target_key="k",
            success=True,
            status_code=200,
            fetched_at=_now(),
        )
        with pytest.raises(Exception):
            result.success = False


# ---------------------------------------------------------------------------
# select_adapter
# ---------------------------------------------------------------------------


class _GitHubAdapter:
    def supports(self, target):
        return target.target_type == "github_repository"

    def fetch(self, request):
        raise NotImplementedError


class _WebsiteAdapter:
    def supports(self, target):
        return target.target_type == "official_website"

    def fetch(self, request):
        raise NotImplementedError


class _NewsAdapter:
    def supports(self, target):
        return target.target_type == "news_source"

    def fetch(self, request):
        raise NotImplementedError


class _WildcardAdapter:
    """Matches everything — used to test ambiguity detection."""

    def supports(self, target):
        return True

    def fetch(self, request):
        raise NotImplementedError


class TestSelectAdapter:

    def test_selects_github_adapter(self):
        adapters: list[SourceAdapter] = [
            _GitHubAdapter(), _WebsiteAdapter(), _NewsAdapter()
        ]
        adapter = select_adapter(_mk_target(), adapters)
        assert isinstance(adapter, _GitHubAdapter)

    def test_selects_website_adapter(self):
        adapters: list[SourceAdapter] = [
            _GitHubAdapter(), _WebsiteAdapter(), _NewsAdapter()
        ]
        adapter = select_adapter(
            _mk_target(
                target_type="official_website",
                locator="https://example.com",
            ),
            adapters,
        )
        assert isinstance(adapter, _WebsiteAdapter)

    def test_selects_news_adapter(self):
        adapters: list[SourceAdapter] = [
            _GitHubAdapter(), _WebsiteAdapter(), _NewsAdapter()
        ]
        adapter = select_adapter(
            _mk_target(
                target_type="news_source",
                locator="https://example.com/feed",
            ),
            adapters,
        )
        assert isinstance(adapter, _NewsAdapter)

    def test_raises_when_no_match(self):
        adapters: list[SourceAdapter] = [_WebsiteAdapter()]
        with pytest.raises(LookupError, match="no adapter available"):
            select_adapter(_mk_target(), adapters)

    def test_raises_when_multiple_match(self):
        adapters: list[SourceAdapter] = [
            _GitHubAdapter(), _WildcardAdapter()
        ]
        with pytest.raises(LookupError, match="multiple adapters"):
            select_adapter(_mk_target(), adapters)

    def test_empty_adapter_list_raises(self):
        with pytest.raises(LookupError):
            select_adapter(_mk_target(), [])
