import sqlite3
from unittest.mock import MagicMock, patch
from web_watcher.doctor import SystemDoctor, DiagnosticResult
from web_watcher.cli import main


def test_doctor_db_not_found(tmp_path):
    missing_db = tmp_path / "non_existent.db"
    doctor = SystemDoctor(db_path=str(missing_db))
    res = doctor.check_database()

    assert res.status == "WARN"
    assert "not found" in res.message


def test_doctor_db_healthy(tmp_path):
    db_file = tmp_path / "test_healthy.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE test (id INT);")
    conn.close()

    doctor = SystemDoctor(db_path=str(db_file))
    res = doctor.check_database()

    assert res.status == "OK"
    assert "SQLite healthy" in res.message


def test_doctor_db_integrity_fail(tmp_path):
    db_file = tmp_path / "corrupted.db"
    db_file.write_text("not a valid sqlite header content")

    doctor = SystemDoctor(db_path=str(db_file))
    res = doctor.check_database()

    assert res.status == "FAIL"


def test_doctor_queue_healthy():
    mock_repo = MagicMock()
    mock_repo.list_pending_notifications.return_value = []
    doctor = SystemDoctor(repo=mock_repo)
    res = doctor.check_notification_queue()

    assert res.status == "OK"
    assert "Queue healthy" in res.message


def test_doctor_queue_backlog_warn():
    mock_repo = MagicMock()
    mock_repo.list_pending_notifications.return_value = [MagicMock()] * 60
    doctor = SystemDoctor(repo=mock_repo)
    res = doctor.check_notification_queue(max_lag_warn=50)

    assert res.status == "WARN"
    assert "High pending queue backlog" in res.message


def test_doctor_render_report_and_cli(tmp_path, capsys):
    db_file = tmp_path / "cli_doctor.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE test (id INT);")
    conn.close()

    ret = main(["doctor", "--db", str(db_file)])
    assert ret == 0

    captured = capsys.readouterr()
    assert "=== Web Watcher System Doctor ===" in captured.out
    assert "Verdict: ALL CHECKS PASSED" in captured.out
