"""Unit tests for InvestigationWorker (K.8)."""

from unittest.mock import MagicMock
from web_watcher.event_status import EventStatus
from web_watcher.event_types import EventType
from web_watcher.importance import Importance
from web_watcher.investigation_adapter import EventInvestigationAdapter
from web_watcher.investigation_worker import InvestigationWorker
from web_watcher.repository import Repository


def test_fetch_uninvestigated_events_returns_only_open_unprocessed():
    repo = Repository(":memory:")
    ent_id = repo.create_entity(
        canonical_key="test:app-1",
        name="app-1",
        entity_type="test_entity",
    ).id

    e1 = repo.create_event(entity_id=ent_id, event_type=EventType.CONTENT_CHANGE, status=EventStatus.OPEN)
    e2 = repo.create_event(entity_id=ent_id, event_type=EventType.STARS_CHANGED, status=EventStatus.OPEN)
    repo.create_event(entity_id=ent_id, event_type=EventType.RELEASE_PUBLISHED, status=EventStatus.CLOSED)

    # Mark e1 as already investigated
    repo.save_investigation_result(
        investigation_id="inv_existing_1",
        event_id=e1.id,
        task_type="diff_analysis",
        status="completed",
    )

    worker = InvestigationWorker(repository=repo)
    unprocessed = worker.fetch_uninvestigated_events()

    assert len(unprocessed) == 1
    assert unprocessed[0].id == e2.id


def test_process_event_eligible_and_persists():
    repo = Repository(":memory:")
    ent_id = repo.create_entity(
        canonical_key="test:app-2",
        name="app-2",
        entity_type="test_entity",
    ).id
    event = repo.create_event(
        entity_id=ent_id,
        event_type=EventType.CONTENT_CHANGE,
        status=EventStatus.OPEN,
        importance=Importance.IMPORTANT,
    )

    mock_planner = MagicMock()
    mock_engine = MagicMock()
    mock_result = MagicMock()
    mock_result.summary = "Worker verified change"
    mock_result.metadata = {"speed": "fast"}
    mock_result.evidence = [{"evidence_type": "delta", "payload": {"k": "v"}}]
    mock_result.status = "completed"

    mock_planner.plan.return_value = MagicMock()
    mock_engine.execute.return_value = mock_result

    adapter = EventInvestigationAdapter(min_importance=Importance.IMPORTANT)
    worker = InvestigationWorker(
        repository=repo,
        adapter=adapter,
        planner=mock_planner,
        engine=mock_engine,
    )

    processed = worker.process_event(event)
    assert processed is True

    saved = repo.get_investigation_result_by_event(event.id)
    assert saved is not None
    assert saved["event_id"] == str(event.id)
    assert saved["summary"] == "Worker verified change"
    assert len(saved["evidence"]) == 1


def test_process_event_ineligible_skipped():
    repo = Repository(":memory:")
    ent_id = repo.create_entity(
        canonical_key="test:app-3",
        name="app-3",
        entity_type="test_entity",
    ).id
    event = repo.create_event(
        entity_id=ent_id,
        event_type=EventType.CONTENT_CHANGE,
        status=EventStatus.OPEN,
        importance=Importance.INTERESTING,
    )

    worker = InvestigationWorker(repository=repo)
    assert worker.process_event(event) is False
    assert repo.get_investigation_result_by_event(event.id) is None


def test_run_once_batch_execution():
    repo = Repository(":memory:")
    ent_id = repo.create_entity(
        canonical_key="test:app-4",
        name="app-4",
        entity_type="test_entity",
    ).id
    repo.create_event(entity_id=ent_id, event_type=EventType.CONTENT_CHANGE, status=EventStatus.OPEN, importance=Importance.IMPORTANT)
    repo.create_event(entity_id=ent_id, event_type=EventType.STARS_CHANGED, status=EventStatus.OPEN, importance=Importance.CRITICAL)

    mock_planner = MagicMock()
    mock_engine = MagicMock()
    mock_engine.execute.return_value = MagicMock(summary="Done", metadata={}, evidence=[], status="completed")

    worker = InvestigationWorker(
        repository=repo,
        planner=mock_planner,
        engine=mock_engine,
    )

    count = worker.run_once()
    assert count == 2


def test_run_forever_iteration_limit_and_stop():
    repo = Repository(":memory:")
    worker = InvestigationWorker(repository=repo, poll_interval=0.01)

    worker.run_forever(max_iterations=3)
    assert worker._running is True
    worker.stop()
    assert worker._running is False
