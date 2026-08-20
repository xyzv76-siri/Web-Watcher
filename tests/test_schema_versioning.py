"""Tests for schema versioning and migration (FR-05)."""

import sqlite3
from pathlib import Path

from web_watcher.storage import open_database, initialize_schema
from web_watcher.repository import Repository, SCHEMA_VERSION, utc_now_iso


def test_schema_version_table_exists(tmp_path):
    db_path = tmp_path / "test.db"
    with open_database(db_path) as conn:
        initialize_schema(conn)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'")
        assert cursor.fetchone() is not None


def test_initial_schema_version_is_zero(tmp_path):
    db_path = tmp_path / "test.db"
    with open_database(db_path) as conn:
        initialize_schema(conn)
        cursor = conn.execute("SELECT MAX(version) as v FROM schema_version")
        row = cursor.fetchone()
        assert row["v"] == 0


def test_repository_applies_migrations(tmp_path):
    db_path = tmp_path / "test.db"
    repo = Repository(db_path)

    cursor = repo.connection.execute("SELECT MAX(version) as v FROM schema_version")
    row = cursor.fetchone()
    assert row["v"] == SCHEMA_VERSION


def test_repository_migration_adds_notification_columns(tmp_path):
    db_path = tmp_path / "test.db"
    repo = Repository(db_path)

    cols = [c[1] for c in repo.connection.execute("PRAGMA table_info(notifications)").fetchall()]
    assert "dispatch_owner" in cols
    assert "dispatch_until" in cols
    assert "dispatch_token" in cols
    assert "updated_at" in cols


def test_repository_idempotent_on_existing_db(tmp_path):
    db_path = tmp_path / "test.db"
    # First initialization via Repository
    Repository(db_path)

    # Second initialization via Repository should not fail
    Repository(db_path)

    # Verify version is still correct
    repo = Repository(db_path)
    cursor = repo.connection.execute("SELECT MAX(version) as v FROM schema_version")
    row = cursor.fetchone()
    assert row["v"] == SCHEMA_VERSION


def test_repository_migration_registry_complete(tmp_path):
    from web_watcher.repository import Repository
    registry = Repository.migration_registry()
    assert set(registry.keys()) == {1, 2, 3}
    for migration in registry.values():
        assert migration.name
        assert migration.up is not None
        assert migration.checksum is not None


def test_repository_migration_checksum_stable(tmp_path):
    from web_watcher.repository import Repository
    registry = Repository.migration_registry()
    checksums = [m.compute_checksum() for m in registry.values()]
    assert len(checksums) == len(set(checksums))


def test_repository_migration_idempotent_on_partial(tmp_path):
    db_path = tmp_path / "partial.db"
    conn = open_database(db_path)
    initialize_schema(conn)
    conn.execute("INSERT INTO schema_version (version, applied_at) VALUES (1, ?)", (utc_now_iso(),))
    conn.commit()
    conn.close()

    repo = Repository(db_path)
    cursor = repo.connection.execute("SELECT MAX(version) as v FROM schema_version")
    row = cursor.fetchone()
    assert row["v"] == SCHEMA_VERSION
