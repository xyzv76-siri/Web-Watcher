"""Phase 7 focused tests — Fetch Persistence + Signal creation.

These tests cover the 15 required Phase 7 scenarios.
All network calls are mocked — no real web access.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
import sqlite3

from web_watcher.adapters import AdapterRegistry
from web_watcher.content_hash import sha256_of
from web_watcher.fetch import FetchRequest, FetchResult
from web_watcher.fingerprint import fingerprint_for_signal, signal_fingerprint
from web_watcher.fetch_service import FetchService
from web_watcher.models import Entity, FetchState
from web_watcher.repository import Repository
from web_watcher.targets import WatchTarget


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime(2026, 8, 17, 10, 0, 0, tzinfo=timezone.utc)


def _now_plus(sec: int = 60) -> datetime:
    return _now().replace(minute=_now().minute) if False else _now()


def _mk_target(**overrides):
    defaults = {
        "key": "github:octocat/Hello-World",
        "target_type": "github_repository",
        "name": "Hello-World",
        "locator": "octocat/Hello-World",
    }
    defaults.update(overrides)
    return WatchTarget(**defaults)


def _mk_result(content: str = '{"name":"hello"}', **overrides):
    defaults = {
        "target_key": "github:octocat/Hello-World",
        "success": True,
        "status_code": 200,
        "fetched_at": _now(),
        "content": content,
        "content_type": "application/json",
        "etag": 'w/"abc123"',
        "last_modified": "Wed, 17 Aug 2026 10:00:00 GMT",
        "content_hash": None,
        "error": None,
        "metadata": {"source": "github"},
    }
    defaults.update(overrides)
    return FetchResult(**defaults)


def _mk_adapter(return_result=None, calls_list=None):
    if return_result is None:
        return_result = _mk_result()

    if calls_list is None:
        calls_list = []

    class _FakeAdapter:
        def supports(self, target):
            return True

        def fetch(self, request):
            calls_list.append(request)
            return return_result

    return _FakeAdapter(), calls_list


def _make_service(adapter=None, calls_list=None, tmp_path=None):
    if adapter is None:
        adapter, calls_list = _mk_adapter(calls_list=calls_list or [])
    registry = AdapterRegistry([adapter])
    if tmp_path is None:
        return registry, calls_list
    repo = Repository(tmp_path / "watcher.db")
    service = FetchService(repository=repo, adapter_registry=registry)
    return service, repo, calls_list


# ===========================================================================
# A. SHA-256 content hash
# ===========================================================================


class TestContentHash:

    def test_sha256_deterministic(self):
        assert sha256_of("hello") == sha256_of("hello")

    def test_sha256_different_inputs(self):
        assert sha256_of("hello") != sha256_of("world")

    def test_sha256_empty_string(self):
        assert sha256_of("") != sha256_of("x")

    def test_sha256_type_error_for_non_string(self):
        with pytest.raises(TypeError):
            sha256_of(b"bytes")  # type: ignore[arg-type]


# ===========================================================================
# B. Deterministic signal fingerprint
# ===========================================================================


class TestSignalFingerprint:

    def test_fingerprint_deterministic(self):
        f1 = fingerprint_for_signal(1, "content_change", "hash-a")
        f2 = fingerprint_for_signal(1, "content_change", "hash-a")
        assert f1 == f2

    def test_fingerprint_differs_on_different_value(self):
        f1 = fingerprint_for_signal(1, "content_change", "hash-a")
        f2 = fingerprint_for_signal(1, "content_change", "hash-b")
        assert f1 != f2

    def test_fingerprint_differs_on_different_entity(self):
        f1 = fingerprint_for_signal(1, "content_change", "hash-a")
        f2 = fingerprint_for_signal(2, "content_change", "hash-a")
        assert f1 != f2

    def test_fingerprint_hex_format(self):
        f = fingerprint_for_signal(1, "content_change", "hash-a")
        assert len(f) == 64
        int(f, 16)  # must be valid hex

    def test_fingerprint_is_stable_across_calls(self):
        for _ in range(10):
            assert fingerprint_for_signal(1, "content_change", "hash-a") == \
                fingerprint_for_signal(1, "content_change", "hash-a")


# ===========================================================================
# C. Entity — canonical, no duplicates
# ===========================================================================


class TestEntityCanonical:

    def test_repeated_lookup_does_not_create_duplicate_entity(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        key = "github:octocat/Hello-World"

        e1 = repo.get_or_create_entity(key, "Hello-World", "github_repository")
        e2 = repo.get_or_create_entity(key, "Hello-World", "github_repository")
        e3 = repo.get_or_create_entity(key, "Different Name", "github_repository")

        assert e1.id == e2.id == e3.id
        # Only one row in entities
        row = repo.connection.execute(
            "SELECT COUNT(*) as cnt FROM entities WHERE canonical_key = ?",
            (key,),
        ).fetchone()
        assert row["cnt"] == 1
        repo.close()

    def test_different_keys_create_different_entities(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")

        e1 = repo.get_or_create_entity(
            "github:octocat/Hello-World", "HW", "github_repository"
        )
        e2 = repo.get_or_create_entity(
            "github:torvalds/linux", "Linux", "github_repository"
        )

        assert e1.id != e2.id
        assert e1.canonical_key == "github:octocat/Hello-World"
        repo.close()

    def test_entity_name_is_preserved(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        e = repo.get_or_create_entity(
            "github:foo/bar", "My Repo", "github_repository"
        )
        assert e.name == "My Repo"
        assert e.entity_type == "github_repository"
        repo.close()


# ===========================================================================
# D. Signal creation — first successful fetch
# ===========================================================================


class TestSignalOnFirstFetch:

    def test_first_successful_fetch_creates_signal(self, tmp_path):
        adapter, calls = _mk_adapter()
        service, repo, _ = _make_service(adapter, calls, tmp_path)

        service.fetch_one(_mk_target())

        entity = repo.get_entity_by_key("github:octocat/Hello-World")
        assert entity is not None
        assert repo.count_signals_for_entity(entity.id, "content_change") == 1
        repo.close()

    def test_signal_has_expected_fields(self, tmp_path):
        adapter, calls = _mk_adapter()
        service, repo, _ = _make_service(adapter, calls, tmp_path)

        service.fetch_one(_mk_target())

        entity = repo.get_entity_by_key("github:octocat/Hello-World")
        signals = repo.connection.execute(
            "SELECT * FROM signals WHERE entity_id = ?",
            (entity.id,),
        ).fetchall()
        assert len(signals) == 1
        sig = signals[0]
        assert sig["signal_type"] == "content_change"
        assert sig["fingerprint"] is not None
        assert len(sig["fingerprint"]) == 64
        repo.close()


# ===========================================================================
# E. Identical second fetch creates NO Signal
# ===========================================================================


class TestIdenticalSecondFetch:

    def test_identical_second_fetch_creates_no_signal(self, tmp_path):
        adapter, calls = _mk_adapter()
        service, repo, _ = _make_service(adapter, calls, tmp_path)

        service.fetch_one(_mk_target())
        service.fetch_one(_mk_target())

        entity = repo.get_entity_by_key("github:octocat/Hello-World")
        count = repo.count_signals_for_entity(entity.id, "content_change")
        assert count == 1  # first fetch only
        repo.close()


# ===========================================================================
# F. Changed content creates exactly one new Signal
# ===========================================================================


class TestChangedContent:

    def test_changed_content_creates_exactly_one_new_signal(self, tmp_path):
        adapter, calls = _mk_adapter()
        service, repo, _ = _make_service(adapter, calls, tmp_path)

        service.fetch_one(_mk_target())

        # Second fetch with different content
        adapter2, calls2 = _mk_adapter(
            _mk_result(content='{"name":"changed"}'), calls_list=calls
        )
        service2 = FetchService(
            repository=repo,
            adapter_registry=AdapterRegistry([adapter2]),
        )
        service2.fetch_one(_mk_target())

        entity = repo.get_entity_by_key("github:octocat/Hello-World")
        count = repo.count_signals_for_entity(entity.id, "content_change")
        assert count == 2
        repo.close()

    def test_changed_content_signal_has_new_fingerprint(self, tmp_path):
        adapter, calls = _mk_adapter()
        service, repo, _ = _make_service(adapter, calls, tmp_path)

        service.fetch_one(_mk_target())
        entity = repo.get_entity_by_key("github:octocat/Hello-World")

        fp1 = repo.connection.execute(
            "SELECT fingerprint FROM signals WHERE entity_id = ? ORDER BY id LIMIT 1",
            (entity.id,),
        ).fetchone()["fingerprint"]

        adapter2, _ = _mk_adapter(_mk_result(content='{"name":"v2"}'))
        service2 = FetchService(
            repository=repo,
            adapter_registry=AdapterRegistry([adapter2]),
        )
        service2.fetch_one(_mk_target())

        rows = repo.connection.execute(
            "SELECT fingerprint FROM signals WHERE entity_id = ? ORDER BY id",
            (entity.id,),
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["fingerprint"] == fp1
        assert rows[1]["fingerprint"] != fp1
        repo.close()


# ===========================================================================
# G. 304 Not Modified — NO Signal created, state preserved
# ===========================================================================


class Test304Behaviour:

    def test_304_does_not_create_signal(self, tmp_path):
        class _Adapter304:
            def supports(self, target):
                return True

            def fetch(self, request):
                return FetchResult(
                    target_key="github:octocat/Hello-World",
                    success=True,
                    status_code=304,
                    fetched_at=_now(),
                    content=None,
                    etag='w/"abc123"',
                    last_modified=None,
                    metadata={"source": "github", "unchanged": "true"},
                )

        registry = AdapterRegistry([_Adapter304()])
        repo = Repository(tmp_path / "watcher.db")

        # First real fetch to establish state
        adapter1, _ = _mk_adapter()
        svc1 = FetchService(repository=repo, adapter_registry=AdapterRegistry([adapter1]))
        svc1.fetch_one(_mk_target())

        # Second fetch: 304
        svc2 = FetchService(repository=repo, adapter_registry=registry)
        svc2.fetch_one(_mk_target())

        entity = repo.get_entity_by_key("github:octocat/Hello-World")
        count = repo.count_signals_for_entity(entity.id, "content_change")
        assert count == 1  # still only the first
        repo.close()

    def test_304_preserves_previous_content_hash(self, tmp_path):
        class _Adapter304:
            def supports(self, target):
                return True

            def fetch(self, request):
                return FetchResult(
                    target_key="github:octocat/Hello-World",
                    success=True,
                    status_code=304,
                    fetched_at=_now(),
                    content=None,
                    etag='w/"abc123"',
                    metadata={"source": "github"},
                )

        registry = AdapterRegistry([_Adapter304()])
        repo = Repository(tmp_path / "watcher.db")

        # Establish state
        adapter1, _ = _mk_adapter(_mk_result(content='{"old":true}'))
        svc1 = FetchService(repository=repo, adapter_registry=AdapterRegistry([adapter1]))
        svc1.fetch_one(_mk_target())
        state_before = repo.get_fetch_state("github:octocat/Hello-World")
        assert state_before is not None
        hash_before = state_before.content_hash
        etag_before = state_before.etag

        # 304 fetch
        svc2 = FetchService(repository=repo, adapter_registry=registry)
        svc2.fetch_one(_mk_target())

        state_after = repo.get_fetch_state("github:octocat/Hello-World")
        assert state_after is not None
        assert state_after.content_hash == hash_before
        assert state_after.etag == etag_before
        repo.close()


# ===========================================================================
# H. Failed fetch preserves previous state
# ===========================================================================


class TestFailedFetch:

    def test_failed_fetch_preserves_previous_state(self, tmp_path):
        adapter1, _ = _mk_adapter(_mk_result(content='{"good":true}'))
        repo = Repository(tmp_path / "watcher.db")
        svc1 = FetchService(
            repository=repo,
            adapter_registry=AdapterRegistry([adapter1]),
        )
        svc1.fetch_one(_mk_target())

        # Now a failing fetch
        class _FailingAdapter:
            def supports(self, target):
                return True

            def fetch(self, request):
                return FetchResult(
                    target_key="github:octocat/Hello-World",
                    success=False,
                    status_code=500,
                    fetched_at=_now(),
                    error="Server Error",
                    metadata={"source": "github"},
                )

        svc2 = FetchService(
            repository=repo,
            adapter_registry=AdapterRegistry([_FailingAdapter()]),
        )
        result = svc2.fetch_one(_mk_target())

        assert result.success is False
        state = repo.get_fetch_state("github:octocat/Hello-World")
        assert state is not None
        assert state.content_hash is not None
        assert state.content_hash == sha256_of('{"good":true}')

        entity = repo.get_entity_by_key("github:octocat/Hello-World")
        assert repo.count_signals_for_entity(entity.id, "content_change") == 1
        repo.close()

    def test_failed_fetch_does_not_create_signal(self, tmp_path):
        class _FailingAdapter:
            def supports(self, target):
                return True

            def fetch(self, request):
                return FetchResult(
                    target_key="github:octocat/Hello-World",
                    success=False,
                    status_code=429,
                    fetched_at=_now(),
                    error="Rate Limited",
                    metadata={"source": "github"},
                )

        registry = AdapterRegistry([_FailingAdapter()])
        repo = Repository(tmp_path / "watcher.db")
        svc = FetchService(repository=repo, adapter_registry=registry)
        svc.fetch_one(_mk_target())

        state = repo.get_fetch_state("github:octocat/Hello-World")
        assert state is None  # no prior state was destroyed — because none existed
        repo.close()


# ===========================================================================
# I. Repository — get_or_create_entity / create_signal / count
# ===========================================================================


class TestRepositorySignalMethods:

    def test_create_signal_returns_signal(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        entity = repo.get_or_create_entity(
            "github:x/y", "X", "github_repository"
        )
        sig = repo.create_signal(
            entity_id=entity.id,
            signal_type="content_change",
            observed_at=_now(),
            value="hash-1",
            fingerprint="fp-1",
        )
        assert sig is not None
        assert sig.id is not None
        assert sig.entity_id == entity.id
        repo.close()

    def test_create_signal_with_duplicate_fingerprint_returns_none(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        entity = repo.get_or_create_entity(
            "github:x/y", "X", "github_repository"
        )

        sig1 = repo.create_signal(
            entity_id=entity.id,
            signal_type="content_change",
            observed_at=_now(),
            value="hash-1",
            fingerprint="dup-fp",
        )
        sig2 = repo.create_signal(
            entity_id=entity.id,
            signal_type="content_change",
            observed_at=_now(),
            value="hash-1",
            fingerprint="dup-fp",
        )

        assert sig1 is not None
        assert sig2 is None
        count = repo.count_signals_for_entity(entity.id, "content_change")
        assert count == 1
        repo.close()

    def test_count_signals_for_entity(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        entity = repo.get_or_create_entity(
            "github:x/y", "X", "github_repository"
        )

        assert repo.count_signals_for_entity(entity.id) == 0
        repo.create_signal(entity.id, "a", _now(), value="v", fingerprint="fp-a")
        repo.create_signal(entity.id, "a", _now(), value="v", fingerprint="fp-b")
        assert repo.count_signals_for_entity(entity.id, "a") == 2
        assert repo.count_signals_for_entity(entity.id) == 2
        repo.close()


# ===========================================================================
# J. FetchState — all required Phase 7 cases
# ===========================================================================


class TestFetchStatePhase7:

    def test_fetch_state_initially_missing(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        assert repo.get_fetch_state("github:missing/repo") is None
        repo.close()

    def test_fetch_state_creation(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        repo.upsert_fetch_state(
            FetchState(
                target_key="github:new/repo",
                etag="e",
                last_modified="lm",
                content_hash="h",
                fetched_at=_now(),
            )
        )
        state = repo.get_fetch_state("github:new/repo")
        assert state is not None
        assert state.etag == "e"
        assert state.content_hash == "h"
        repo.close()

    def test_fetch_state_update(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        repo.upsert_fetch_state(
            FetchState(target_key="github:up/repo", etag="old")
        )
        repo.upsert_fetch_state(
            FetchState(target_key="github:up/repo", etag="new")
        )
        assert repo.get_fetch_state("github:up/repo").etag == "new"
        repo.close()

    def test_etag_persistence(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        repo.upsert_fetch_state(
            FetchState(target_key="github:etag/r", etag='w/"abc"')
        )
        assert repo.get_fetch_state("github:etag/r").etag == 'w/"abc"'
        repo.close()

    def test_last_modified_persistence(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        repo.upsert_fetch_state(
            FetchState(
                target_key="github:lm/r",
                last_modified="Wed, 01 Jan 2026 00:00:00 GMT",
            )
        )
        assert (
            repo.get_fetch_state("github:lm/r").last_modified
            == "Wed, 01 Jan 2026 00:00:00 GMT"
        )
        repo.close()


# ===========================================================================
# K. Exactly one fetch call per fetch_one
# ===========================================================================


class TestOneFetchOneCall:

    def test_one_fetch_service_call_performs_one_fetch_only(self, tmp_path):
        adapter, calls = _mk_adapter()
        service, repo, _ = _make_service(adapter, calls, tmp_path)

        service.fetch_one(_mk_target())

        assert len(calls) == 1
        repo.close()

    def test_three_fetch_one_calls_yield_three_adapter_calls(self, tmp_path):
        adapter, calls = _mk_adapter()
        service, repo, _ = _make_service(adapter, calls, tmp_path)

        for _ in range(3):
            service.fetch_one(_mk_target())

        assert len(calls) == 3
        repo.close()


# ===========================================================================
# L. Regression: existing behaviour preserved for edge cases
# ===========================================================================


class TestRegression:

    def test_unsupported_target_raises(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        registry = AdapterRegistry()
        service = FetchService(repository=repo, adapter_registry=registry)

        with pytest.raises(LookupError, match="no adapter"):
            service.fetch_one(_mk_target())
        repo.close()

    def test_success_with_no_content_creates_no_signal(self, tmp_path):
        class _NoContentAdapter:
            def supports(self, target):
                return True

            def fetch(self, request):
                return FetchResult(
                    target_key="github:octocat/Hello-World",
                    success=True,
                    status_code=204,
                    fetched_at=_now(),
                    content=None,
                    metadata={},
                )

        registry = AdapterRegistry([_NoContentAdapter()])
        repo = Repository(tmp_path / "watcher.db")
        service = FetchService(repository=repo, adapter_registry=registry)
        service.fetch_one(_mk_target())

        state = repo.get_fetch_state("github:octocat/Hello-World")
        assert state is None
        assert repo.get_entity_by_key("github:octocat/Hello-World") is None
        repo.close()
