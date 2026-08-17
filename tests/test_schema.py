from datetime import datetime, timezone

from web_watcher.storage import open_database, init_schema
from web_watcher.models import Entity, Signal, Event, Notification, FetchState


def test_schema_creates_all_tables(tmp_path):
    db_path = tmp_path / "test.db"
    with open_database(db_path) as conn:
        init_schema(conn)

        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = sorted(row["name"] for row in tables if row["name"] != "sqlite_sequence")

        expected = sorted(["entities", "signals", "events", "event_signals", "notifications", "fetch_state"])
        assert table_names == expected


def test_schema_indexes_exist(tmp_path):
    db_path = tmp_path / "test.db"
    with open_database(db_path) as conn:
        init_schema(conn)

        indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%' ORDER BY name"
        ).fetchall()
        index_names = sorted(row["name"] for row in indexes)

        assert len(index_names) >= 4


def test_foreign_keys_enabled(tmp_path):
    db_path = tmp_path / "test.db"
    with open_database(db_path) as conn:
        init_schema(conn)
        fk = conn.execute("PRAGMA foreign_keys").fetchone()
        assert fk[0] == 1


def test_entity_unique_constraint(tmp_path):
    db_path = tmp_path / "test.db"
    with open_database(db_path) as conn:
        init_schema(conn)

        conn.execute("INSERT INTO entities (canonical_key, name, entity_type, created_at) VALUES (?, ?, ?, ?)",
                     ("repo:x/y", "Test", "github_repo", datetime.now(timezone.utc).isoformat()))

        try:
            conn.execute("INSERT INTO entities (canonical_key, name, entity_type, created_at) VALUES (?, ?, ?, ?)",
                         ("repo:x/y", "Test2", "github_repo", datetime.now(timezone.utc).isoformat()))
            conn.commit()
            assert False, "Should have raised IntegrityError"
        except Exception:
            pass


def test_event_signal_junction(tmp_path):
    db_path = tmp_path / "test.db"
    with open_database(db_path) as conn:
        init_schema(conn)
        dt = datetime.now(timezone.utc).isoformat()

        conn.execute("INSERT INTO entities (canonical_key, name, entity_type, created_at) VALUES (?,?,?,?)",
                     ("repo:a/b", "Repo", "github_repo", dt))
        conn.execute("INSERT INTO signals (entity_id, signal_type, observed_at, fingerprint, created_at) VALUES (?,?,?,?,?)",
                     (1, "push", dt, "fp1", dt))
        conn.execute("INSERT INTO events (entity_id, event_type, status, importance, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                     (1, "new_release", "detected", "low", dt, dt))

        conn.execute("INSERT INTO event_signals (event_id, signal_id) VALUES (?, ?)", (1, 1))
        conn.commit()

        row = conn.execute("SELECT COUNT(*) as cnt FROM event_signals WHERE event_id=1").fetchone()
        assert row["cnt"] == 1


def test_notification_unique_per_event_channel(tmp_path):
    db_path = tmp_path / "test.db"
    with open_database(db_path) as conn:
        init_schema(conn)
        dt = datetime.now(timezone.utc).isoformat()

        conn.execute("INSERT INTO entities (canonical_key, name, entity_type, created_at) VALUES (?,?,?,?)",
                     ("repo:a/b", "Repo", "github_repo", dt))
        conn.execute("INSERT INTO events (entity_id, event_type, status, importance, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                     (1, "test", "detected", "low", dt, dt))

        conn.execute("INSERT INTO notifications (event_id, channel, status, created_at) VALUES (?,?,?,?)",
                     (1, "telegram", "pending", dt))

        try:
            conn.execute("INSERT INTO notifications (event_id, channel, status, created_at) VALUES (?,?,?,?)",
                         (1, "telegram", "pending", dt))
            conn.commit()
            assert False, "Should have raised IntegrityError"
        except Exception:
            pass


def test_fetch_state_content_hash_index(tmp_path):
    db_path = tmp_path / "test.db"
    with open_database(db_path) as conn:
        init_schema(conn)

        conn.execute("INSERT INTO fetch_state (target_key, content_hash, fetched_at) VALUES (?,?,?)",
                     ("https://example.com", "sha256:abc", datetime.now(timezone.utc).isoformat()))
        conn.commit()

        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM fetch_state WHERE content_hash='sha256:abc'"
        ).fetchone()
        assert row["cnt"] == 1
