"""Unit tests for data retention policy (Phase 16-A)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from web_watcher.retention import RetentionManager, RetentionPolicy
from web_watcher.repository import Repository


def _make_repo_with_counts(events: int = 0, notifications: int = 0):
    repo = MagicMock(spec=Repository)
    repo.delete_old_events.return_value = events
    repo.delete_old_notifications.return_value = notifications
    return repo


def test_retention_enforce_dry_run():
    repo = _make_repo_with_counts()
    policy = RetentionPolicy(max_age_days=30, dry_run=True)
    manager = RetentionManager(repo=repo, policy=policy)
    summary = manager.enforce()

    assert summary["dry_run"] is True
    assert summary["deleted_events"] == 0
    assert summary["deleted_notifications"] == 0
    repo.delete_old_events.assert_not_called()
    repo.delete_old_notifications.assert_not_called()


def test_retention_enforce_cuts_off_old_data():
    repo = _make_repo_with_counts(events=5, notifications=12)
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    policy = RetentionPolicy(max_age_days=30)
    manager = RetentionManager(repo=repo, policy=policy)
    summary = manager.enforce()

    assert summary["dry_run"] is False
    assert summary["deleted_events"] == 5
    assert summary["deleted_notifications"] == 12
    repo.delete_old_events.assert_called_once()
    repo.delete_old_notifications.assert_called_once()
