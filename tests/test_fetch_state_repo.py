"""Tests for Repository fetch_state persistence."""

from datetime import datetime, timezone

import pytest

from web_watcher.models import FetchState
from web_watcher.repository import Repository, _parse_iso_datetime, _serialize_datetime


def _now() -> datetime:
    return datetime(2026, 8, 17, 10, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


class TestDateTimeSerialization:

    def test_serialize_none(self):
        assert _serialize_datetime(None) is None

    def test_serialize_datetime(self):
        dt = _now()
        assert _serialize_datetime(dt) == dt.isoformat()

    def test_parse_none(self):
        assert _parse_iso_datetime(None) is None

    def test_parse_valid_iso(self):
        dt = _parse_iso_datetime("2026-08-17T10:00:00+00:00")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 8

    def test_parse_invalid_returns_none(self):
        assert _parse_iso_datetime("not-a-date") is None


# ---------------------------------------------------------------------------
# Repository fetch_state
# ---------------------------------------------------------------------------


class TestRepositoryFetchState:

    def test_get_fetch_state_missing_returns_none(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        state = repo.get_fetch_state("github:unknown/repo")
        assert state is None
        repo.close()

    def test_upsert_and_get_fetch_state(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")

        state = repo.upsert_fetch_state(
            FetchState(
                target_key="github:openai/gpt",
                etag="w/\"abc123\"",
                last_modified="Wed, 01 Jan 2026 00:00:00 GMT",
                content_hash="deadbeef",
                fetched_at=_now(),
            )
        )

        retrieved = repo.get_fetch_state("github:openai/gpt")

        assert retrieved is not None
        assert retrieved.target_key == "github:openai/gpt"
        assert retrieved.etag == "w/\"abc123\""
        assert retrieved.last_modified == "Wed, 01 Jan 2026 00:00:00 GMT"
        assert retrieved.content_hash == "deadbeef"
        assert retrieved.fetched_at == _now()
        repo.close()

    def test_upsert_is_idempotent_no_duplicates(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        key = "github:dup/repo"

        # Insert 5 times
        for i in range(5):
            repo.upsert_fetch_state(
                FetchState(
                    target_key=key,
                    etag=f"etag-{i}",
                    content_hash=f"hash-{i}",
                    fetched_at=_now(),
                )
            )

        rows = repo.connection.execute(
            "SELECT COUNT(*) as cnt FROM fetch_state WHERE target_key = ?",
            (key,),
        ).fetchone()

        assert rows["cnt"] == 1
        repo.close()

    def test_upsert_replaces_previous_values(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        key = "github:replace/repo"

        repo.upsert_fetch_state(
            FetchState(
                target_key=key,
                etag="old-etag",
                content_hash="old-hash",
                fetched_at=_now(),
            )
        )

        repo.upsert_fetch_state(
            FetchState(
                target_key=key,
                etag="new-etag",
                content_hash="new-hash",
                fetched_at=_now(),
            )
        )

        state = repo.get_fetch_state(key)
        assert state.etag == "new-etag"
        assert state.content_hash == "new-hash"
        repo.close()

    def test_upsert_with_none_optional_fields(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")

        repo.upsert_fetch_state(
            FetchState(
                target_key="github:minimal/repo",
                etag=None,
                last_modified=None,
                content_hash=None,
                fetched_at=None,
            )
        )

        state = repo.get_fetch_state("github:minimal/repo")
        assert state is not None
        assert state.etag is None
        assert state.last_modified is None
        assert state.content_hash is None
        assert state.fetched_at is None
        repo.close()

    def test_fetch_state_key_is_unique(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        key = "github:unique/repo"

        repo.upsert_fetch_state(
            FetchState(target_key=key, etag="etag1")
        )

        # Second upsert should succeed (replace), not raise
        repo.upsert_fetch_state(
            FetchState(target_key=key, etag="etag2")
        )

        state = repo.get_fetch_state(key)
        assert state.etag == "etag2"
        repo.close()

    def test_get_fetch_state_missing_key(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")

        repo.upsert_fetch_state(
            FetchState(target_key="github:exists/repo")
        )

        assert repo.get_fetch_state("github:does-not-exist/repo") is None
        repo.close()
