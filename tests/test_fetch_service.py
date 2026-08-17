"""Tests for FetchService — single-fetch orchestration."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from web_watcher.adapters import AdapterRegistry
from web_watcher.fetch import FetchRequest, FetchResult
from web_watcher.fetch_service import FetchService
from web_watcher.models import FetchState
from web_watcher.repository import Repository
from web_watcher.targets import WatchTarget


def _now() -> datetime:
    return datetime(2026, 8, 17, 10, 0, 0, tzinfo=timezone.utc)


def _mk_target(**overrides):
    defaults = {
        "key": "github:openai/gpt-4o",
        "target_type": "github_repository",
        "name": "GPT-4o",
        "locator": "openai/gpt-4o",
    }
    defaults.update(overrides)
    return WatchTarget(**defaults)


def _mk_result(**overrides):
    defaults = {
        "target_key": "github:openai/gpt-4o",
        "success": True,
        "status_code": 200,
        "fetched_at": _now(),
        "content": '{"name":"gpt-4o"}',
        "content_type": "application/json",
        "etag": "w/\"abc123\"",
        "last_modified": "Wed, 17 Aug 2026 10:00:00 GMT",
        "content_hash": None,
        "error": None,
        "metadata": {"source": "github"},
    }
    defaults.update(overrides)
    return FetchResult(**defaults)


def _mk_registry(adapter):
    return AdapterRegistry([adapter])


def _mk_adapter(calls=None):
    """Create a fake adapter that records calls and returns a result."""
    if calls is None:
        calls = []

    class _FakeAdapter:
        def supports(self, target):
            return True

        def fetch(self, request):
            calls.append(request)
            return _mk_result()

    return _FakeAdapter(), calls


# ---------------------------------------------------------------------------
# fetch_one — happy path
# ---------------------------------------------------------------------------


class TestFetchOneHappyPath:

    def test_returns_result_from_adapter(self, tmp_path):
        adapter, calls = _mk_adapter()
        registry = _mk_registry(adapter)
        repo = Repository(tmp_path / "watcher.db")
        service = FetchService(repository=repo, adapter_registry=registry)

        result = service.fetch_one(_mk_target())

        assert result.success is True
        assert result.target_key == "github:openai/gpt-4o"
        assert len(calls) == 1
        repo.close()

    def test_passes_target_key_in_request(self, tmp_path):
        adapter, calls = _mk_adapter()
        registry = _mk_registry(adapter)
        repo = Repository(tmp_path / "watcher.db")
        service = FetchService(repository=repo, adapter_registry=registry)

        service.fetch_one(_mk_target(key="github:torvalds/linux", locator="torvalds/linux"))

        assert len(calls) == 1
        assert calls[0].target.key == "github:torvalds/linux"
        repo.close()

    def test_persists_fetch_state_on_success(self, tmp_path):
        adapter, calls = _mk_adapter()
        registry = _mk_registry(adapter)
        repo = Repository(tmp_path / "watcher.db")
        service = FetchService(repository=repo, adapter_registry=registry)

        result = service.fetch_one(_mk_target())

        state = repo.get_fetch_state("github:openai/gpt-4o")
        assert state is not None
        assert state.etag == "w/\"abc123\""
        assert state.last_modified == "Wed, 17 Aug 2026 10:00:00 GMT"
        assert state.content_hash is not None
        assert state.fetched_at == _now()
        repo.close()

    def test_computes_sha256_content_hash(self, tmp_path):
        adapter, calls = _mk_adapter()
        registry = _mk_registry(adapter)
        repo = Repository(tmp_path / "watcher.db")
        service = FetchService(repository=repo, adapter_registry=registry)

        service.fetch_one(_mk_target())

        state = repo.get_fetch_state("github:openai/gpt-4o")
        # The content is '{"name":"gpt-4o"}'
        import hashlib

        expected = hashlib.sha256(b'{"name":"gpt-4o"}').hexdigest()
        assert state.content_hash == expected
        repo.close()


# ---------------------------------------------------------------------------
# Cache header pass-through
# ---------------------------------------------------------------------------


class TestCacheHeaderPassThrough:

    def test_passes_cached_etag_and_last_modified(self, tmp_path):
        adapter, calls = _mk_adapter()
        registry = _mk_registry(adapter)
        repo = Repository(tmp_path / "watcher.db")

        # Pre-populate fetch state
        repo.upsert_fetch_state(
            FetchState(
                target_key="github:openai/gpt-4o",
                etag="old-etag",
                last_modified="Mon, 15 Aug 2026 00:00:00 GMT",
                content_hash="prev-hash",
                fetched_at=_now(),
            )
        )

        service = FetchService(repository=repo, adapter_registry=registry)
        service.fetch_one(_mk_target())

        assert len(calls) == 1
        request = calls[0]
        assert request.etag == "old-etag"
        assert request.last_modified == "Mon, 15 Aug 2026 00:00:00 GMT"
        repo.close()

    def test_no_cached_state_no_cache_headers(self, tmp_path):
        adapter, calls = _mk_adapter()
        registry = _mk_registry(adapter)
        repo = Repository(tmp_path / "watcher.db")
        service = FetchService(repository=repo, adapter_registry=registry)

        service.fetch_one(_mk_target())

        assert len(calls) == 1
        request = calls[0]
        assert request.etag is None
        assert request.last_modified is None
        repo.close()


# ---------------------------------------------------------------------------
# 304 Not Modified — no state update
# ---------------------------------------------------------------------------


class Test304NotModified:

    def test_304_does_not_update_fetch_state(self, tmp_path):
        adapter, calls = _mk_adapter()
        registry = _mk_registry(adapter)
        repo = Repository(tmp_path / "watcher.db")

        # Pre-populate with old state
        repo.upsert_fetch_state(
            FetchState(
                target_key="github:openai/gpt-4o",
                etag="old-etag",
                content_hash="old-hash",
                fetched_at=_now(),
            )
        )

        class _Adapter304:
            def supports(self, target):
                return True

            def fetch(self, request):
                calls.append(request)
                return FetchResult(
                    target_key="github:openai/gpt-4o",
                    success=True,
                    status_code=304,
                    fetched_at=_now(),
                    content=None,
                    etag="old-etag",
                    last_modified=None,
                    metadata={"source": "github", "unchanged": "true"},
                )

        registry = _mk_registry(_Adapter304())
        service = FetchService(repository=repo, adapter_registry=registry)
        result = service.fetch_one(_mk_target())

        assert result.success is True
        assert result.status_code == 304

        # FetchState should be UNCHANGED
        state = repo.get_fetch_state("github:openai/gpt-4o")
        assert state.etag == "old-etag"
        assert state.content_hash == "old-hash"
        repo.close()

    def test_304_returns_success_result(self, tmp_path):
        adapter, calls = _mk_adapter()
        registry = _mk_registry(adapter)
        repo = Repository(tmp_path / "watcher.db")
        service = FetchService(repository=repo, adapter_registry=registry)

        # Force 304 by pre-populating
        repo.upsert_fetch_state(
            FetchState(
                target_key="github:openai/gpt-4o",
                etag="cached-etag",
            )
        )

        class _Adapter304:
            def supports(self, target):
                return True

            def fetch(self, request):
                return FetchResult(
                    target_key="github:openai/gpt-4o",
                    success=True,
                    status_code=304,
                    fetched_at=_now(),
                    content=None,
                    etag="cached-etag",
                    metadata={"source": "github"},
                )

        registry = _mk_registry(_Adapter304())
        service = FetchService(repository=repo, adapter_registry=registry)
        result = service.fetch_one(_mk_target())

        assert result.success is True
        assert result.status_code == 304
        repo.close()


# ---------------------------------------------------------------------------
# Failure handling — state not overwritten
# ---------------------------------------------------------------------------


class TestFailureHandling:

    def test_failure_does_not_overwrite_existing_state(self, tmp_path):
        adapter, calls = _mk_adapter()
        registry = _mk_registry(adapter)
        repo = Repository(tmp_path / "watcher.db")

        # Pre-populate with valid state
        repo.upsert_fetch_state(
            FetchState(
                target_key="github:openai/gpt-4o",
                etag="good-etag",
                content_hash="good-hash",
                fetched_at=_now(),
            )
        )

        class _FailingAdapter:
            def supports(self, target):
                return True

            def fetch(self, request):
                return FetchResult(
                    target_key="github:openai/gpt-4o",
                    success=False,
                    status_code=404,
                    fetched_at=_now(),
                    error="Not Found",
                    metadata={"source": "github"},
                )

        registry = _mk_registry(_FailingAdapter())
        service = FetchService(repository=repo, adapter_registry=registry)
        result = service.fetch_one(_mk_target())

        assert result.success is False
        assert result.status_code == 404

        # State should be UNCHANGED
        state = repo.get_fetch_state("github:openai/gpt-4o")
        assert state is not None
        assert state.etag == "good-etag"
        assert state.content_hash == "good-hash"
        repo.close()

    def test_failure_with_no_prior_state(self, tmp_path):
        adapter, calls = _mk_adapter()
        registry = _mk_registry(adapter)
        repo = Repository(tmp_path / "watcher.db")

        class _FailingAdapter:
            def supports(self, target):
                return True

            def fetch(self, request):
                return FetchResult(
                    target_key="github:openai/gpt-4o",
                    success=False,
                    status_code=500,
                    fetched_at=_now(),
                    error="Server Error",
                    metadata={"source": "github"},
                )

        registry = _mk_registry(_FailingAdapter())
        service = FetchService(repository=repo, adapter_registry=registry)
        result = service.fetch_one(_mk_target())

        assert result.success is False

        # No state was created
        state = repo.get_fetch_state("github:openai/gpt-4o")
        assert state is None
        repo.close()


# ---------------------------------------------------------------------------
# No content → no hash
# ---------------------------------------------------------------------------


class TestNoContent:

    def test_success_with_no_content_creates_no_state(self, tmp_path):
        adapter, calls = _mk_adapter()
        registry = _mk_registry(adapter)
        repo = Repository(tmp_path / "watcher.db")

        class _NoContentAdapter:
            def supports(self, target):
                return True

            def fetch(self, request):
                return FetchResult(
                    target_key="github:openai/gpt-4o",
                    success=True,
                    status_code=204,
                    fetched_at=_now(),
                    content=None,
                    metadata={"source": "github"},
                )

        registry = _mk_registry(_NoContentAdapter())
        service = FetchService(repository=repo, adapter_registry=registry)
        service.fetch_one(_mk_target())

        # No state persisted (no content to hash)
        state = repo.get_fetch_state("github:openai/gpt-4o")
        assert state is None
        repo.close()


# ---------------------------------------------------------------------------
# Deterministic repeatable fetch
# ---------------------------------------------------------------------------


class TestDeterministicRepeatable:

    def test_two_fetches_same_content_same_hash(self, tmp_path):
        adapter, calls = _mk_adapter()
        registry = _mk_registry(adapter)
        repo = Repository(tmp_path / "watcher.db")
        service = FetchService(repository=repo, adapter_registry=registry)

        service.fetch_one(_mk_target())
        hash1 = repo.get_fetch_state("github:openai/gpt-4o").content_hash

        service.fetch_one(_mk_target())
        hash2 = repo.get_fetch_state("github:openai/gpt-4o").content_hash

        assert hash1 == hash2
        repo.close()

    def test_different_content_different_hash(self, tmp_path):
        class _AdapterA:
            def supports(self, target):
                return True

            def fetch(self, request):
                return FetchResult(
                    target_key="github:openai/gpt-4o",
                    success=True,
                    status_code=200,
                    fetched_at=_now(),
                    content='{"name":"a"}',
                    metadata={},
                )

        class _AdapterB:
            def supports(self, target):
                return True

            def fetch(self, request):
                return FetchResult(
                    target_key="github:openai/gpt-4o",
                    success=True,
                    status_code=200,
                    fetched_at=_now(),
                    content='{"name":"b"}',
                    metadata={},
                )

        repo = Repository(tmp_path / "watcher.db")
        service = FetchService(
            repository=repo,
            adapter_registry=_mk_registry(_AdapterA()),
        )
        service.fetch_one(_mk_target())
        hash1 = repo.get_fetch_state("github:openai/gpt-4o").content_hash

        service = FetchService(
            repository=repo,
            adapter_registry=_mk_registry(_AdapterB()),
        )
        service.fetch_one(_mk_target())
        hash2 = repo.get_fetch_state("github:openai/gpt-4o").content_hash

        assert hash1 != hash2
        repo.close()


# ---------------------------------------------------------------------------
# Error propagation
# ---------------------------------------------------------------------------


class TestErrorPropagation:

    def test_unsupported_target_raises(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        registry = AdapterRegistry()  # empty
        service = FetchService(repository=repo, adapter_registry=registry)

        with pytest.raises(LookupError, match="no adapter"):
            service.fetch_one(_mk_target())
        repo.close()
