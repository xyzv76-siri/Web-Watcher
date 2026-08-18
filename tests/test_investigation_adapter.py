"""Unit tests for EventInvestigationAdapter (Phase 11A Bridge)."""

from unittest.mock import MagicMock
from web_watcher.event_status import EventStatus
from web_watcher.event_types import EventType
from web_watcher.importance import Importance
from web_watcher.investigation_adapter import EventInvestigationAdapter
from web_watcher.investigation_contract import InvestigationTask
from web_watcher.models import Event


def _build_test_event(
    event_type: EventType = EventType.CONTENT_CHANGE,
    status: EventStatus = EventStatus.OPEN,
    importance: Importance = Importance.IMPORTANT,
) -> Event:
    return Event(
        id="evt-adapter-1",
        entity_id=1,
        event_type=event_type,
        status=status,
        importance=importance,
        created_at="2026-08-18T04:00:00Z",
        updated_at="2026-08-18T04:00:00Z",
    )


def test_is_eligible_by_status_and_importance():
    adapter = EventInvestigationAdapter(min_importance=Importance.IMPORTANT)

    # Eligible: OPEN + IMPORTANT
    assert adapter.is_eligible(_build_test_event(status=EventStatus.OPEN, importance=Importance.IMPORTANT)) is True

    # Eligible: OPEN + CRITICAL
    assert adapter.is_eligible(_build_test_event(status=EventStatus.OPEN, importance=Importance.CRITICAL)) is True

    # Ineligible: OPEN + INTERESTING (below threshold)
    assert adapter.is_eligible(_build_test_event(status=EventStatus.OPEN, importance=Importance.INTERESTING)) is False

    # Ineligible: CLOSED + CRITICAL
    assert adapter.is_eligible(_build_test_event(status=EventStatus.CLOSED, importance=Importance.CRITICAL)) is False


def test_resolve_task_type_returns_task_enum():
    adapter = EventInvestigationAdapter()
    for evt_type in EventType:
        event = _build_test_event(event_type=evt_type)
        task = adapter.resolve_task_type(event)
        assert isinstance(task, InvestigationTask)


def test_build_context_contains_event_fields():
    adapter = EventInvestigationAdapter()
    event = _build_test_event()
    ctx = adapter.build_context(event)

    assert ctx["event_id"] == "evt-adapter-1"
    assert ctx["entity_id"] == 1
    assert ctx["event_type"] == "content_change"
    assert ctx["importance"] == "important"
    assert "created_at" in ctx


def test_build_policy_scales_for_critical_events():
    adapter = EventInvestigationAdapter(default_timeout_seconds=20.0)
    important_event = _build_test_event(importance=Importance.IMPORTANT)
    critical_event = _build_test_event(importance=Importance.CRITICAL)

    pol_imp = adapter.build_policy(important_event)
    pol_crit = adapter.build_policy(critical_event)

    if hasattr(pol_imp, "timeout_seconds") and hasattr(pol_crit, "timeout_seconds"):
        assert pol_crit.timeout_seconds > pol_imp.timeout_seconds


def test_run_for_event_skips_ineligible():
    adapter = EventInvestigationAdapter(min_importance=Importance.IMPORTANT)
    ineligible_event = _build_test_event(importance=Importance.IGNORE)

    mock_planner = MagicMock()
    mock_engine = MagicMock()

    result = adapter.run_for_event(ineligible_event, mock_planner, mock_engine)
    assert result is None
    mock_planner.plan.assert_not_called()
    mock_engine.execute.assert_not_called()


def test_run_for_event_executes_eligible():
    adapter = EventInvestigationAdapter(min_importance=Importance.IMPORTANT)
    eligible_event = _build_test_event(importance=Importance.IMPORTANT)

    mock_planner = MagicMock()
    mock_engine = MagicMock()
    mock_plan = MagicMock()
    mock_result = MagicMock()

    mock_planner.plan.return_value = mock_plan
    mock_engine.execute.return_value = mock_result

    result = adapter.run_for_event(eligible_event, mock_planner, mock_engine)
    assert result == mock_result
    mock_planner.plan.assert_called_once()
    mock_engine.execute.assert_called_once()
