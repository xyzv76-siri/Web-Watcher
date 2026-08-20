"""Integration tests for EventCorrelator auto-investigation dispatch (Phase 11-B)."""

from datetime import datetime, timezone
from unittest.mock import MagicMock
import pytest

from web_watcher.event_correlator import EventCorrelator
from web_watcher.event_status import EventStatus
from web_watcher.event_types import EventType
from web_watcher.importance import Importance
from web_watcher.investigation_adapter import EventInvestigationAdapter
from web_watcher.repository import Repository
from web_watcher.signal_types import SignalType


def test_correlator_auto_investigate_disabled_by_default():
    repo = Repository(":memory:")
    entity = repo.create_entity(canonical_key="repo-test-1", name="Test App", entity_type="service")
    now = datetime.now(timezone.utc)
    signal = repo.create_signal(entity.id, SignalType.CONTENT_CHANGE, observed_at=now, value="diff")

    correlator = EventCorrelator(repository=repo, auto_investigate=False)
    plan = correlator.process_signal(signal)
    repo.commit_plan(plan)

    # Verify the plan was created correctly
    assert plan is not None
    assert len(plan.events_to_create) == 1
    assert plan.events_to_create[0].importance == Importance.IMPORTANT.value

    # No investigation should exist (auto_investigate=False means process_signal does not dispatch)
    evt = repo.find_open_event_for_entity(entity.id, event_type="content_change")
    assert evt is not None
    assert repo.get_investigation_result_by_event(evt.id) is None


def test_correlator_auto_investigate_triggers_on_important_event():
    repo = Repository(":memory:")
    entity = repo.create_entity(canonical_key="repo-test-2", name="Test App 2", entity_type="service")
    now = datetime.now(timezone.utc)
    signal = repo.create_signal(entity.id, SignalType.CONTENT_CHANGE, observed_at=now, value="critical diff")

    mock_planner = MagicMock()
    mock_engine = MagicMock()
    mock_result = MagicMock()
    mock_result.summary = "Automated pipeline investigation verified changes"
    mock_result.metadata = {"pipeline": "live"}
    mock_result.evidence = [{"evidence_type": "delta_analysis", "payload": {"lines": 42}}]
    mock_result.status = "completed"

    mock_planner.plan.return_value = MagicMock()
    mock_engine.execute.return_value = mock_result

    correlator = EventCorrelator(
        repository=repo,
        auto_investigate=True,
        planner=mock_planner,
        engine=mock_engine,
    )
    plan = correlator.process_signal(signal)
    repo.commit_plan(plan)

    # Get the created event
    evt = repo.find_open_event_for_entity(entity.id, event_type="content_change")
    assert evt is not None

    # Dispatch investigation manually (in new architecture, this is done out-of-band)
    dispatched = correlator.dispatch_investigation(evt)
    assert dispatched is True

    saved_inv = repo.get_investigation_result_by_event(evt.id)
    assert saved_inv is not None
    assert saved_inv["summary"] == "Automated pipeline investigation verified changes"
    assert len(saved_inv["evidence"]) == 1
    assert saved_inv["evidence"][0]["evidence_type"] == "delta_analysis"


def test_correlator_auto_investigate_skips_ineligible_event():
    repo = Repository(":memory:")
    entity = repo.create_entity(canonical_key="repo-test-3", name="Test App 3", entity_type="service")
    now = datetime.now(timezone.utc)
    signal = repo.create_signal(entity.id, SignalType.STARS_CHANGED, observed_at=now, value="105")

    correlator = EventCorrelator(repository=repo, auto_investigate=True)
    plan = correlator.process_signal(signal)
    repo.commit_plan(plan)

    evt = repo.find_open_event_for_entity(entity.id, event_type="stars_changed")
    assert evt is not None
    assert evt.importance == Importance.INTERESTING.value

    # Even with auto_investigate=True, dispatch_investigation should skip ineligible events
    dispatched = correlator.dispatch_investigation(evt)
    assert dispatched is False
    assert repo.get_investigation_result_by_event(evt.id) is None


def test_correlator_auto_investigate_idempotent_on_multi_signals():
    repo = Repository(":memory:")
    entity = repo.create_entity(canonical_key="repo-test-4", name="Test App 4", entity_type="service")
    now = datetime.now(timezone.utc)

    mock_planner = MagicMock()
    mock_engine = MagicMock()
    mock_engine.execute.return_value = MagicMock(summary="Executed once", metadata={}, evidence=[], status="completed")

    correlator = EventCorrelator(
        repository=repo,
        auto_investigate=True,
        planner=mock_planner,
        engine=mock_engine,
    )

    sig1 = repo.create_signal(entity.id, SignalType.CONTENT_CHANGE, observed_at=now, value="change1")
    sig2 = repo.create_signal(entity.id, SignalType.CONTENT_CHANGE, observed_at=now, value="change2")

    plan1 = correlator.process_signal(sig1)
    repo.commit_plan(plan1)
    plan2 = correlator.process_signal(sig2)
    repo.commit_plan(plan2)

    # Both signals should resolve to the same event
    evt = repo.find_open_event_for_entity(entity.id, event_type="content_change")
    assert evt is not None
    assert plan2.merged_event_id == evt.id

    # Dispatch investigation manually
    dispatched = correlator.dispatch_investigation(evt)
    assert dispatched is True

    assert mock_engine.execute.call_count == 1
    assert repo.get_investigation_result_by_event(evt.id) is not None
