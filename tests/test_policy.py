"""Tests for the Phase 9 deterministic policy engine.

No network access, no LLM calls, no Telegram, no scheduling,
no external side effects — pure function behaviour only.
"""

from datetime import datetime, timezone

from web_watcher.models import Event
from web_watcher.policy import (
    Action,
    Importance,
    PolicyDecision,
    PolicyEngine,
)


def _make_event(event_type="unknown", importance="low") -> Event:
    dt = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
    return Event(
        id=None,
        entity_id=1,
        event_type=event_type,
        status="detected",
        importance=importance,
        created_at=dt,
        updated_at=dt,
    )


# --- evaluate() end-to-end for each Importance tier ---


def test_evaluate_critical_event():
    engine = PolicyEngine()
    event = _make_event("critical")
    decision = engine.evaluate(event)

    assert isinstance(decision, PolicyDecision)
    assert decision.importance == Importance.CRITICAL
    assert decision.action == Action.INVESTIGATE_AND_NOTIFY
    assert "critical" in decision.reason


def test_evaluate_important_event():
    engine = PolicyEngine()
    event = _make_event("important")
    decision = engine.evaluate(event)

    assert decision.importance == Importance.IMPORTANT
    assert decision.action == Action.NOTIFY
    assert "important" in decision.reason


def test_evaluate_interesting_event():
    engine = PolicyEngine()
    event = _make_event("interesting")
    decision = engine.evaluate(event)

    assert decision.importance == Importance.INTERESTING
    assert decision.action == Action.SUMMARIZE
    assert "interesting" in decision.reason


def test_evaluate_unknown_event_defaults_to_ignore():
    engine = PolicyEngine()
    event = _make_event("new_release")
    decision = engine.evaluate(event)

    assert decision.importance == Importance.IGNORE
    assert decision.action == Action.DISCARD
    assert "new_release" in decision.reason


def test_evaluate_empty_event_type_defaults_to_ignore():
    engine = PolicyEngine()
    event = _make_event("")
    decision = engine.evaluate(event)

    assert decision.importance == Importance.IGNORE
    assert decision.action == Action.DISCARD
    assert "" in decision.reason


# --- _importance() static method ---


def test_importance_critical_event_type():
    assert PolicyEngine._importance(_make_event("critical")) == Importance.CRITICAL


def test_importance_important_event_type():
    assert PolicyEngine._importance(_make_event("important")) == Importance.IMPORTANT


def test_importance_interesting_event_type():
    assert PolicyEngine._importance(_make_event("interesting")) == Importance.INTERESTING


def test_importance_unknown_event_type():
    assert PolicyEngine._importance(_make_event("deployment_failure")) == Importance.IGNORE


def test_importance_empty_event_type():
    assert PolicyEngine._importance(_make_event("")) == Importance.IGNORE


# --- PolicyDecision immutability ---


def test_policy_decision_is_frozen():
    engine = PolicyEngine()
    decision = engine.evaluate(_make_event("critical"))
    try:
        decision.importance = Importance.IGNORE
        assert False, "PolicyDecision should be immutable"
    except Exception:
        pass


# --- Importance and Action enums ---


def test_importance_enum_values():
    assert Importance.IGNORE.value == "ignore"
    assert Importance.INTERESTING.value == "interesting"
    assert Importance.IMPORTANT.value == "important"
    assert Importance.CRITICAL.value == "critical"


def test_action_enum_values():
    assert Action.DISCARD.value == "discard"
    assert Action.SUMMARIZE.value == "summarize"
    assert Action.NOTIFY.value == "notify"
    assert Action.INVESTIGATE_AND_NOTIFY.value == "investigate_and_notify"


# --- PolicyDecision is a dataclass with expected fields ---


def test_policy_decision_fields():
    engine = PolicyEngine()
    decision = engine.evaluate(_make_event("important"))
    assert hasattr(decision, "importance")
    assert hasattr(decision, "action")
    assert hasattr(decision, "reason")
    assert len(decision.reason) > 0
