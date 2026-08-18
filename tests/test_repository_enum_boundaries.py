"""Unit tests for repository enum boundary normalization and deserialization (K.7-B-2)."""

from datetime import datetime, timezone
import pytest
from web_watcher.event_status import EventStatus
from web_watcher.event_types import EventType
from web_watcher.importance import Importance
from web_watcher.models import Entity, Event, Signal
from web_watcher.repository import (
    Repository,
    _deserialize_event_status,
    _deserialize_event_type,
    _deserialize_importance,
    _deserialize_signal_type,
    _normalize_event_status,
    _normalize_event_type,
    _normalize_importance,
    _normalize_signal_type,
)
from web_watcher.signal_types import SignalType


def test_normalization_helpers():
    assert _normalize_signal_type(SignalType.CONTENT_CHANGE) == "content_change"
    assert _normalize_signal_type("content_change") == "content_change"
    assert _normalize_signal_type("CONTENT_CHANGE") == "content_change"
    assert _normalize_signal_type(None) is None

    assert _normalize_event_type(EventType.STARS_CHANGED) == "stars_changed"
    assert _normalize_event_type("stars_changed") == "stars_changed"
    assert _normalize_event_type(None) is None

    assert _normalize_event_status(EventStatus.OPEN) == "open"
    assert _normalize_event_status("new") == "open"
    assert _normalize_event_status(EventStatus.CLOSED) == "closed"
    assert _normalize_event_status("processed") == "closed"
    assert _normalize_event_status(None) is None

    assert _normalize_importance(Importance.CRITICAL) == "critical"
    assert _normalize_importance("critical") == "critical"
    assert _normalize_importance(None) is None


def test_deserialization_helpers_with_fallbacks():
    assert _deserialize_signal_type("content_change") == SignalType.CONTENT_CHANGE
    assert _deserialize_signal_type("unknown_junk") == SignalType.CONTENT_CHANGE

    assert _deserialize_event_type("stars_changed") == EventType.STARS_CHANGED
    assert _deserialize_event_type("invalid_event") == EventType.CONTENT_CHANGE

    assert _deserialize_event_status("open") == EventStatus.OPEN
    assert _deserialize_event_status("new") == EventStatus.OPEN
    assert _deserialize_event_status("closed") == EventStatus.CLOSED

    assert _deserialize_importance("critical") == Importance.CRITICAL
    assert _deserialize_importance("invalid_imp") == Importance.INTERESTING


def test_signal_crud_enum_boundaries():
    repo = Repository(":memory:")
    entity = repo.create_entity(canonical_key="ent-sig-test", name="Signal Test", entity_type="service")
    now = datetime.now(timezone.utc)

    sig1 = repo.create_signal(
        entity_id=entity.id,
        signal_type=SignalType.CONTENT_CHANGE,
        observed_at=now,
        value="10 lines",
    )
    assert isinstance(sig1.signal_type, SignalType)
    assert sig1.signal_type == SignalType.CONTENT_CHANGE

    sig2 = repo.create_signal(
        entity_id=entity.id,
        signal_type="STARS_CHANGED",
        observed_at=now,
        value="50",
    )
    assert isinstance(sig2.signal_type, SignalType)
    assert sig2.signal_type == SignalType.STARS_CHANGED

    # create_signal returns the Signal directly; verify it round-trips through count helpers
    assert repo.count_signals_for_entity(entity.id) == 2
    assert repo.count_signals_for_entity(entity.id, signal_type=SignalType.CONTENT_CHANGE) == 1
    assert repo.count_signals_for_entity(entity.id, signal_type="STARS_CHANGED") == 1


def test_event_crud_enum_boundaries():
    repo = Repository(":memory:")
    entity = repo.create_entity(canonical_key="ent-evt-test", name="Event Test", entity_type="service")

    event1 = repo.create_event(
        entity_id=entity.id,
        event_type="content_change",
        status="new",
        importance="CRITICAL",
    )
    assert isinstance(event1.event_type, EventType)
    assert isinstance(event1.status, EventStatus)
    assert isinstance(event1.importance, Importance)
    assert event1.status == EventStatus.OPEN
    assert event1.importance == Importance.CRITICAL

    repo.update_event(
        event_id=event1.id,
        status="processed",
        importance=Importance.IMPORTANT,
    )
    updated = repo.get_event(event1.id)
    assert updated is not None
    assert updated.status == EventStatus.CLOSED
    assert updated.importance == Importance.IMPORTANT

    assert repo.find_open_event_for_entity(entity.id) is None

    event2 = repo.create_event(
        entity_id=entity.id,
        event_type=EventType.STARS_CHANGED,
        status=EventStatus.OPEN,
        importance=Importance.INTERESTING,
    )
    found = repo.find_open_event_for_entity(entity.id)
    assert found is not None
    assert found.id == event2.id
    assert isinstance(found.event_type, EventType)


def test_get_event_signals_deserialization():
    repo = Repository(":memory:")
    entity = repo.create_entity(canonical_key="ent-link-test", name="Link Test", entity_type="service")
    now = datetime.now(timezone.utc)

    sig1 = repo.create_signal(entity.id, SignalType.CONTENT_CHANGE, observed_at=now, value="k1")
    sig2 = repo.create_signal(entity.id, "STARS_CHANGED", observed_at=now, value="k2")

    event = repo.create_event(entity.id, EventType.CONTENT_CHANGE)
    repo.attach_signal_to_event(event.id, sig1.id)
    repo.attach_signal_to_event(event.id, sig2.id)

    signals = repo.get_event_signals(event.id)
    assert len(signals) == 2
    assert all(isinstance(s.signal_type, SignalType) for s in signals)
    assert signals[0].signal_type == SignalType.CONTENT_CHANGE
    assert signals[1].signal_type == SignalType.STARS_CHANGED
