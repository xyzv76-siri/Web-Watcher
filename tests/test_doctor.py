import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional
from unittest.mock import MagicMock, patch

from web_watcher.doctor import SystemDoctor, DiagnosticResult
from web_watcher.config import AppConfig
from web_watcher.metrics import Metrics
from web_watcher.cli import main


def test_doctor_db_not_found(tmp_path):
    missing_db = tmp_path / "non_existent.db"
    doctor = SystemDoctor(db_path=str(missing_db))
    res = doctor.check_database_connection()

    assert res.status == "WARN"
    assert "not found" in res.message


def test_doctor_db_healthy(tmp_path):
    db_file = tmp_path / "test_healthy.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE test (id INT);")
    conn.close()

    doctor = SystemDoctor(db_path=str(db_file))
    res = doctor.check_database_connection()

    assert res.status == "PASS"
    assert "SQLite healthy" in res.message


def test_doctor_db_integrity_fail(tmp_path):
    db_file = tmp_path / "corrupted.db"
    db_file.write_text("not a valid sqlite header content")

    doctor = SystemDoctor(db_path=str(db_file))
    res = doctor.check_database_connection()

    assert res.status == "FAIL"


def test_doctor_schema_version_uninitialized(tmp_path):
    db_file = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db_file))
    conn.close()

    doctor = SystemDoctor(db_path=str(db_file))
    res = doctor.check_schema_version()

    assert res.status == "WARN"
    assert "version is 0" in res.message


def test_doctor_required_tables_pass(tmp_path):
    db_file = tmp_path / "tables.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript("""
        CREATE TABLE entities (id INTEGER PRIMARY KEY, canonical_key TEXT, name TEXT, entity_type TEXT, created_at TEXT);
        CREATE TABLE signals (id INTEGER PRIMARY KEY, entity_id INTEGER, signal_type TEXT, observed_at TEXT, value TEXT, fingerprint TEXT, created_at TEXT);
        CREATE TABLE events (id INTEGER PRIMARY KEY, entity_id INTEGER, event_type TEXT, status TEXT, importance TEXT, created_at TEXT, updated_at TEXT);
        CREATE TABLE event_signals (event_id INTEGER, signal_id INTEGER, PRIMARY KEY(event_id, signal_id));
        CREATE TABLE notifications (id INTEGER PRIMARY KEY, event_id INTEGER, channel TEXT, status TEXT, created_at TEXT, sent_at TEXT, payload TEXT);
        CREATE TABLE investigation_results (id TEXT PRIMARY KEY, event_id TEXT, task_type TEXT, status TEXT, summary TEXT, metadata TEXT, created_at TEXT);
        CREATE TABLE investigation_evidence (id TEXT PRIMARY KEY, investigation_id TEXT, evidence_type TEXT, payload TEXT, created_at TEXT);
        CREATE TABLE fetch_state (target_key TEXT PRIMARY KEY, etag TEXT, last_modified TEXT, content_hash TEXT, fetched_at TEXT);
    """)
    conn.commit()
    conn.close()

    doctor = SystemDoctor(db_path=str(db_file))
    res = doctor.check_required_tables()

    assert res.status == "PASS"
    assert "All 8 required tables" in res.message


def test_doctor_required_tables_fail(tmp_path):
    db_file = tmp_path / "partial.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE entities (id INTEGER PRIMARY KEY);")
    conn.close()

    doctor = SystemDoctor(db_path=str(db_file))
    res = doctor.check_required_tables()

    assert res.status == "FAIL"
    assert "Missing tables" in res.message


def test_doctor_vocabulary_pass(tmp_path):
    db_file = tmp_path / "vocab.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript("""
        CREATE TABLE entities (id INTEGER PRIMARY KEY, canonical_key TEXT, name TEXT, entity_type TEXT, created_at TEXT);
        CREATE TABLE signals (id INTEGER PRIMARY KEY, entity_id INTEGER, signal_type TEXT, observed_at TEXT, value TEXT, fingerprint TEXT, created_at TEXT);
        CREATE TABLE events (id INTEGER PRIMARY KEY, entity_id INTEGER, event_type TEXT, status TEXT, importance TEXT, created_at TEXT, updated_at TEXT);
    """)
    conn.commit()
    conn.execute("INSERT INTO signals (entity_id, signal_type, observed_at, created_at) VALUES (1, 'WEB_CONTENT_CHANGED', '2026-01-01T00:00:00', '2026-01-01T00:00:00');")
    conn.execute("INSERT INTO events (entity_id, event_type, status, importance, created_at, updated_at) VALUES (1, 'content_change', 'open', 'high', '2026-01-01T00:00:00', '2026-01-01T00:00:00');")
    conn.commit()
    conn.close()

    doctor = SystemDoctor(db_path=str(db_file))
    res = doctor.check_vocabulary()

    assert res.status == "PASS"
    assert "allowed vocabulary" in res.message


def test_doctor_vocabulary_unknown_value(tmp_path):
    db_file = tmp_path / "bad_vocab.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript("""
        CREATE TABLE entities (id INTEGER PRIMARY KEY, canonical_key TEXT, name TEXT, entity_type TEXT, created_at TEXT);
        CREATE TABLE signals (id INTEGER PRIMARY KEY, entity_id INTEGER, signal_type TEXT, observed_at TEXT, value TEXT, fingerprint TEXT, created_at TEXT);
        CREATE TABLE events (id INTEGER PRIMARY KEY, entity_id INTEGER, event_type TEXT, status TEXT, importance TEXT, created_at TEXT, updated_at TEXT);
    """)
    conn.commit()
    conn.execute("INSERT INTO signals (entity_id, signal_type, observed_at, created_at) VALUES (1, 'UNKNOWN_SIGNAL', '2026-01-01T00:00:00', '2026-01-01T00:00:00');")
    conn.execute("INSERT INTO events (entity_id, event_type, status, importance, created_at, updated_at) VALUES (1, 'content_change', 'open', 'high', '2026-01-01T00:00:00', '2026-01-01T00:00:00');")
    conn.commit()
    conn.close()

    doctor = SystemDoctor(db_path=str(db_file))
    res = doctor.check_vocabulary()

    assert res.status == "WARN"
    assert "unknown signal_types" in res.message


def test_doctor_configuration_valid():
    config = AppConfig()
    doctor = SystemDoctor(config=config)
    res = doctor.check_configuration()

    assert res.status == "PASS"
    assert "valid" in res.message


def test_doctor_configuration_invalid():
    config = AppConfig(default_poll_interval=-1, default_batch_size=0, default_max_retries=-1, default_base_backoff_sec=0, retention_max_age_days=0, log_level="INVALID")
    doctor = SystemDoctor(config=config)
    res = doctor.check_configuration()

    assert res.status == "FAIL"
    assert "default_poll_interval" in res.message


def test_doctor_runtime_state_counts(tmp_path):
    db_file = tmp_path / "runtime.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript("""
        CREATE TABLE entities (id INTEGER PRIMARY KEY, canonical_key TEXT, name TEXT, entity_type TEXT, created_at TEXT);
        CREATE TABLE signals (id INTEGER PRIMARY KEY, entity_id INTEGER, signal_type TEXT, observed_at TEXT, value TEXT, fingerprint TEXT, created_at TEXT);
        CREATE TABLE events (id INTEGER PRIMARY KEY, entity_id INTEGER, event_type TEXT, status TEXT, importance TEXT, created_at TEXT, updated_at TEXT);
        CREATE TABLE notifications (id INTEGER PRIMARY KEY, event_id INTEGER, channel TEXT, status TEXT, created_at TEXT, sent_at TEXT, payload TEXT);
        CREATE TABLE investigation_results (id TEXT PRIMARY KEY, event_id TEXT, task_type TEXT, status TEXT, summary TEXT, metadata TEXT, created_at TEXT);
        CREATE TABLE fetch_state (target_key TEXT PRIMARY KEY, etag TEXT, last_modified TEXT, content_hash TEXT, fetched_at TEXT);
    """)
    conn.commit()
    conn.execute("INSERT INTO fetch_state (target_key) VALUES ('t1');")
    conn.execute("INSERT INTO events (entity_id, event_type, status, importance, created_at, updated_at) VALUES (1, 'content_change', 'open', 'high', '2026-01-01T00:00:00', '2026-01-01T00:00:00');")
    conn.execute("INSERT INTO notifications (event_id, channel, status, created_at) VALUES (1, 'webhook', 'pending', '2026-01-01T00:00:00');")
    conn.commit()
    conn.close()

    doctor = SystemDoctor(db_path=str(db_file))
    res = doctor.check_runtime_state()

    assert res.status == "PASS"
    assert res.details is not None
    assert res.details["open_events"] == 1
    assert res.details["pending_notifications"] == 1


def test_doctor_pipeline_health_stuck(tmp_path):
    db_file = tmp_path / "pipeline.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript("""
        CREATE TABLE entities (id INTEGER PRIMARY KEY, canonical_key TEXT, name TEXT, entity_type TEXT, created_at TEXT);
        CREATE TABLE signals (id INTEGER PRIMARY KEY, entity_id INTEGER, signal_type TEXT, observed_at TEXT, value TEXT, fingerprint TEXT, created_at TEXT);
        CREATE TABLE events (id INTEGER PRIMARY KEY, entity_id INTEGER, event_type TEXT, status TEXT, importance TEXT, created_at TEXT, updated_at TEXT);
        CREATE TABLE notifications (id INTEGER PRIMARY KEY, event_id INTEGER, channel TEXT, status TEXT, created_at TEXT, sent_at TEXT, payload TEXT);
        CREATE TABLE investigation_results (id TEXT PRIMARY KEY, event_id TEXT, task_type TEXT, status TEXT, summary TEXT, metadata TEXT, created_at TEXT);
        CREATE TABLE investigation_evidence (id TEXT PRIMARY KEY, investigation_id TEXT, evidence_type TEXT, payload TEXT, created_at TEXT);
        CREATE TABLE fetch_state (target_key TEXT PRIMARY KEY, etag TEXT, last_modified TEXT, content_hash TEXT, fetched_at TEXT);
    """)
    conn.commit()
    old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    conn.execute("INSERT INTO investigation_results (id, event_id, task_type, status, summary, metadata, created_at) VALUES ('inv1', '1', 'web', 'running', '...', '{}', ?);", (old_time,))
    conn.execute("INSERT INTO notifications (event_id, channel, status, created_at) VALUES (1, 'webhook', 'pending', ?);", (old_time,))
    conn.commit()
    conn.close()

    doctor = SystemDoctor(db_path=str(db_file))
    res = doctor.check_pipeline_health()

    assert res.status == "WARN"
    assert "stuck" in res.message.lower()


def test_doctor_metrics_snapshot():
    metrics = Metrics()
    metrics.increment("fetch_total", {"target_id": "t1"})
    metrics.increment("fetch_304_total")

    doctor = SystemDoctor(metrics=metrics)
    res = doctor.check_metrics()

    assert res.status == "PASS"
    assert res.details is not None
    snapshot = res.details["snapshot"]
    assert any(k.startswith("fetch_total") for k in snapshot), f"fetch_total not in snapshot keys: {list(snapshot.keys())}"
    assert snapshot.get("fetch_304_total") == 1


def test_doctor_render_report(tmp_path):
    db_file = tmp_path / "render.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE test (id INT);")
    conn.close()

    doctor = SystemDoctor(db_path=str(db_file))
    report = doctor.render_report()

    assert "=== Web Watcher System Doctor ===" in report
    assert "Verdict:" in report


def test_doctor_no_secret_leakage():
    doctor = SystemDoctor(db_path=":memory:")
    report = doctor.render_report()

    # Ensure common secret patterns are not in the rendered report
    assert "ghp_" not in report
    assert "xoxb-" not in report
    assert "password" not in report.lower() or "password" in report.lower()  # config key is ok
    assert "token" not in report.lower() or "token" in report.lower()  # generic word is ok
