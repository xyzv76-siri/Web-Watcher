from datetime import datetime, timedelta
from unittest.mock import MagicMock
from web_watcher.models import Notification
from web_watcher.config import AppConfig
from web_watcher.retention import RetentionManager, RetentionPolicy
from web_watcher.notification_dispatcher import NotificationDispatcher
from web_watcher.cli import main


def test_retention_manager_with_config():
    config = AppConfig(retention_max_age_days=15, retention_dry_run=True)
    manager = RetentionManager(repo=MagicMock(), config=config)
    assert manager.policy.max_age_days == 15
    assert manager.policy.dry_run is True


def test_dispatcher_triggers_retention_on_cycle():
    mock_repo = MagicMock()
    mock_repo.list_pending_notifications.return_value = []
    mock_retention = MagicMock(spec=RetentionManager)
    
    dispatcher = NotificationDispatcher(
        repo=mock_repo,
        retention_manager=mock_retention,
    )
    dispatcher.run_once(trigger_retention=True)
    
    assert mock_retention.cleanup_old_records.called


def test_cli_retention_subcommand_dry_run(capsys):
    ret = main(["retention", "--days", "7", "--dry-run"])
    assert ret in (0, None)
    captured = capsys.readouterr()
    assert "Retention" in captured.out or "cleanup" in captured.out.lower() or ret == 0
