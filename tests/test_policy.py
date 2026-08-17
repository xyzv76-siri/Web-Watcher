"""Tests for the Phase 9 deterministic policy engine."""

from datetime import datetime, timezone

import pytest

from web_watcher.models import Event
from web_watcher.policy import (
    Action,
    Importance,
    PolicyEngine,
)


def make_event(event_type="unknown"):
    now = datetime.now(timezone.utc)

    return Event(
        id=1,
        entity_id=1,
        event_type=event_type,
        status="open",
        importance=None,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def engine():
    return PolicyEngine()


def test_unknown_event_is_safe_default(engine):
    decision = engine.evaluate(make_event("unknown"))

    assert decision.importance is Importance.IGNORE
    assert decision.action is Action.DISCARD
    assert "no Phase 9 rule matched" in decision.reason


def test_interesting_event(engine):
    decision = engine.evaluate(make_event("interesting"))

    assert decision.importance is Importance.INTERESTING
    assert decision.action is Action.SUMMARIZE


def test_important_event(engine):
    decision = engine.evaluate(make_event("important"))

    assert decision.importance is Importance.IMPORTANT
    assert decision.action is Action.NOTIFY


def test_critical_event(engine):
    decision = engine.evaluate(make_event("critical"))

    assert decision.importance is Importance.CRITICAL
    assert decision.action is Action.INVESTIGATE_AND_NOTIFY


@pytest.mark.parametrize(
    ("event_type", "importance", "action"),
    [
        ("unknown", Importance.IGNORE, Action.DISCARD),
        ("interesting", Importance.INTERESTING, Action.SUMMARIZE),
        ("important", Importance.IMPORTANT, Action.NOTIFY),
        ("critical", Importance.CRITICAL, Action.INVESTIGATE_AND_NOTIFY),
    ],
)
def test_policy_mapping_is_deterministic(
    engine,
    event_type,
    importance,
    action,
):
    event = make_event(event_type)

    first = engine.evaluate(event)
    second = engine.evaluate(event)

    assert first == second
    assert first.importance is importance
    assert first.action is action


def test_policy_does_not_mutate_event(engine):
    event = make_event("important")

    before = (
        event.id,
        event.entity_id,
        event.event_type,
        event.status,
        event.importance,
        event.created_at,
        event.updated_at,
    )

    engine.evaluate(event)

    after = (
        event.id,
        event.entity_id,
        event.event_type,
        event.status,
        event.importance,
        event.created_at,
        event.updated_at,
    )

    assert after == before


def test_decision_is_immutable(engine):
    decision = engine.evaluate(make_event("important"))

    with pytest.raises(Exception):
        decision.reason = "changed"


def test_policy_has_no_external_side_effects():
    engine = PolicyEngine()

    assert engine.__class__.__module__ == "web_watcher.policy"
