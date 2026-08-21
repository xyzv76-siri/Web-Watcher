"""Unit tests for notification retry/stats CLI behavior."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from web_watcher.event_types import EventType
from web_watcher.models import NotificationStatus, Importance
from web_watcher.repository import Repository
from web_watcher.cli import handle_notify
from web_watcher.config import AppConfig


def _make_args(**overrides):
    args = MagicMock()
    args.db_path = ":memory:"
    args.batch_size = 10
    args.interval = 1.0
    args.webhook_url = None
    args.once = True
    args.notify_history = False
    args.notify_history_limit = 20
    args.notify_history_status = None
    args.notify_history_channel = None
    args.notify_retry = False
    args.notify_retry_limit = 10
    args.notify_stats = False
    args.smtp_password = None
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def test_notify_stats_outputs_aggregated_rows():
    repo = Repository(":memory:")
    entity = repo.create_entity(canonical_key="stats-entity", name="Stats", entity_type="service")
    event = repo.create_event(entity_id=entity.id, event_type=EventType.CONTENT_CHANGE, importance=Importance.IMPORTANT)
    repo.create_notification(event_id=event.id, channel="console", status="sent")
    repo.create_notification(event_id=event.id, channel="webhook", status="failed")

    args = _make_args(notify_stats=True)
    config = AppConfig()

    rc = handle_notify(args, config)
    assert rc == 0


def test_notify_stats_empty_db_returns_zero():
    repo = Repository(":memory:")
    args = _make_args(notify_stats=True, db_path=":memory:")
    config = AppConfig()

    # Patch Repository used inside handle_notify
    import web_watcher.cli as cli_mod
    original_repo = cli_mod.Repository
    cli_mod.Repository = lambda db_path: repo
    try:
        rc = handle_notify(args, config)
    finally:
        cli_mod.Repository = original_repo
    assert rc == 0
