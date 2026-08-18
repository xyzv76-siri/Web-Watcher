"""Integration tests for AppConfig wiring into CLI, Dispatcher, PipelineRunner, and SystemDoctor (Phase 15-B)."""

import os
from unittest.mock import MagicMock, patch
from web_watcher.config import AppConfig, get_config
from web_watcher.cli import main
from web_watcher.notification_dispatcher import NotificationDispatcher
from web_watcher.pipeline_runner import PipelineRunner
from web_watcher.doctor import SystemDoctor


def test_get_config_returns_app_config():
    cfg = get_config()
    assert isinstance(cfg, AppConfig)


@patch.dict(os.environ, {
    "WEB_WATCHER_DB": "integration.db",
    "WEB_WATCHER_COOLDOWN": "180",
    "WEB_WATCHER_BATCH_SIZE": "25",
})
def test_cli_uses_config_defaults(tmp_path, capsys):
    db_file = tmp_path / "integration.db"
    db_file.write_text("")

    ret = main(["doctor", "--db", str(db_file)])
    assert ret == 0
    captured = capsys.readouterr()
    assert "System Doctor" in captured.out or "ALL CHECKS PASSED" in captured.out


def test_dispatcher_accepts_config():
    mock_repo = MagicMock()
    cfg = AppConfig(db_path=":memory:", default_max_retries=5, default_base_backoff_sec=0.5)
    dispatcher = NotificationDispatcher(repository=mock_repo, config=cfg)
    assert dispatcher.max_retries == 5
    assert dispatcher.base_backoff_sec == 0.5


def test_pipeline_runner_accepts_config():
    mock_repo = MagicMock()
    cfg = AppConfig(db_path=":memory:")
    runner = PipelineRunner(repository=mock_repo, config=cfg)
    assert runner.config is cfg


def test_doctor_uses_config_db_path():
    cfg = AppConfig(db_path=":memory:")
    doctor = SystemDoctor(config=cfg)
    assert doctor.db_path == ":memory:"
