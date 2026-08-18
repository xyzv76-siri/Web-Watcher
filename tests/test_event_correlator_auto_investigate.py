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
    event = correlator.process_signal(signal)

    assert event is not None
    assert event.importance == Importance.IMPORTANT
    assert repo.get_investigation_result_by_event(event.id) is None


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
    event = correlator.process_signal(signal)

    assert event is not None
    saved_inv = repo.get_investigation_result_by_event(event.id)
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
    event = correlator.process_signal(signal)

    assert event.importance == Importance.INTERESTING
    assert repo.get_investigation_result_by_event(event.id) is None


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

    event1 = correlator.process_signal(sig1)
    event2 = correlator.process_signal(sig2)

    assert event1.id == event2.id
    assert mock_engine.execute.call_count == 1
    assert repo.get_investigation_result_by_event(event1.id) is not None
