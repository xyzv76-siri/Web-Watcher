"""Unit tests for NotificationDispatcher (Phase 12-B)."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import pytest

from web_watcher.channel_senders import BaseChannelSender, DeliveryResult
from web_watcher.event_types import EventType
from web_watcher.models import Notification
from web_watcher.notification_dispatcher import NotificationDispatcher
from web_watcher.repository import Repository


class DummySuccessSender(BaseChannelSender):
    def send(self, notification: Notification) -> DeliveryResult:
        return DeliveryResult(success=True, status_code=200, response_body="ok")


class DummyFailingSender(BaseChannelSender):
    def send(self, notification: Notification) -> DeliveryResult:
        return DeliveryResult(success=False, status_code=500, error_message="Internal Error")


def test_dispatch_success_updates_delivered():
    repo = Repository(":memory:")
    entity = repo.create_entity(canonical_key="ent-disp-1", name="App 1", entity_type="service")
    event = repo.create_event(entity_id=entity.id, event_type=EventType.CONTENT_CHANGE)
    notif = repo.create_notification(event_id=event.id, channel="dummy", status="pending", payload={"msg": "hello"})

    dispatcher = NotificationDispatcher(repository=repo, default_sender=DummySuccessSender())
    res = dispatcher.dispatch(notif)

    assert res.success is True
    updated = repo.get_notification(notif.id)
    assert updated is not None
    assert updated.status == "delivered"
    assert "delivered_at" in updated.payload
    assert updated.payload["delivery_response"] == "ok"


def test_dispatch_resolves_registered_sender():
    repo = Repository(":memory:")
    entity = repo.create_entity(canonical_key="ent-disp-2", name="App 2", entity_type="service")
    event = repo.create_event(entity_id=entity.id, event_type=EventType.CONTENT_CHANGE)
    notif = repo.create_notification(event_id=event.id, channel="custom", status="pending")

    success_sender = DummySuccessSender()
    dispatcher = NotificationDispatcher(repository=repo, default_sender=DummyFailingSender())
    dispatcher.register_sender("custom", success_sender)

    res = dispatcher.dispatch(notif)
    assert res.success is True
    updated = repo.get_notification(notif.id)
    assert updated.status == "delivered"


def test_dispatch_failure_marks_failed_after_max_retries():
    repo = Repository(":memory:")
    entity = repo.create_entity(canonical_key="ent-disp-3", name="App 3", entity_type="service")
    event = repo.create_event(entity_id=entity.id, event_type=EventType.CONTENT_CHANGE)
    notif = repo.create_notification(event_id=event.id, channel="dummy", status="pending", payload={"retry_count": 2})

    dispatcher = NotificationDispatcher(repository=repo, default_sender=DummyFailingSender(), max_retries=3)
    res = dispatcher.dispatch(notif)

    assert res.success is False
    updated = repo.get_notification(notif.id)
    assert updated.status == "failed"
    assert updated.payload["retry_count"] == 3
    assert "last_error" in updated.payload


def test_dispatch_failure_schedules_retry_pending():
    repo = Repository(":memory:")
    entity = repo.create_entity(canonical_key="ent-disp-4", name="App 4", entity_type="service")
    event = repo.create_event(entity_id=entity.id, event_type=EventType.CONTENT_CHANGE)
    notif = repo.create_notification(event_id=event.id, channel="dummy", status="pending", payload={"retry_count": 0})

    dispatcher = NotificationDispatcher(repository=repo, default_sender=DummyFailingSender(), max_retries=3, base_backoff_sec=1.0)
    res = dispatcher.dispatch(notif)

    assert res.success is False
    updated = repo.get_notification(notif.id)
    assert updated.status == "retry_pending"
    assert updated.payload["retry_count"] == 1
    assert updated.payload["next_retry_after"] == 1.0


def test_fetch_pending_returns_ordered_notifications():
    repo = Repository(":memory:")
    entity = repo.create_entity(canonical_key="ent-disp-5", name="App 5", entity_type="service")
    event = repo.create_event(entity_id=entity.id, event_type=EventType.CONTENT_CHANGE)
    repo.create_notification(event_id=event.id, channel="ch1", status="pending")
    repo.create_notification(event_id=event.id, channel="ch2", status="retry_pending")
    repo.create_notification(event_id=event.id, channel="ch3", status="delivered")

    dispatcher = NotificationDispatcher(repository=repo)
    pending = dispatcher.fetch_pending(limit=10)

    assert len(pending) == 2
    assert pending[0].status in ("pending", "retry_pending")
    assert pending[1].status in ("pending", "retry_pending")


def test_run_once_processes_batch():
    repo = Repository(":memory:")
    entity = repo.create_entity(canonical_key="ent-disp-6", name="App 6", entity_type="service")
    event = repo.create_event(entity_id=entity.id, event_type=EventType.CONTENT_CHANGE)
    repo.create_notification(event_id=event.id, channel="ch1", status="pending")
    repo.create_notification(event_id=event.id, channel="ch2", status="pending")

    dispatcher = NotificationDispatcher(repository=repo, default_sender=DummySuccessSender(), batch_size=2)
    count = dispatcher.run_once()

    assert count == 2
    pending = dispatcher.fetch_pending(limit=10)
    assert len(pending) == 0
