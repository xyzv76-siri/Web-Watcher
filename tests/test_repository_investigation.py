"""Unit tests for Investigation persistence in Repository (Phase 11A Path 2)."""

from web_watcher.repository import Repository
from web_watcher.event_types import EventType
from web_watcher.event_status import EventStatus
from web_watcher.importance import Importance


def test_investigation_result_crud_lifecycle():
    repo = Repository(":memory:")

    entity = repo.create_entity(
        canonical_key="repo:x/y",
        name="Test Repo",
        entity_type="github_repo",
    )

    event = repo.create_event(
        entity_id=entity.id,
        event_type=EventType.CONTENT_CHANGE,
        status=EventStatus.OPEN,
        importance=Importance.IMPORTANT,
    )

    inv_id = "inv-res-001"
    repo.save_investigation_result(
        investigation_id=inv_id,
        event_id=str(event.id),
        task_type="diff_analysis",
        status="completed",
        summary="Detected 3 key DOM additions",
        metadata={"confidence": 0.95, "duration_ms": 120},
        evidence_items=[
            {"evidence_type": "dom_delta", "payload": {"added_nodes": 3}},
            {"evidence_type": "text_diff", "payload": {"lines_changed": 12}},
        ],
    )

    res = repo.get_investigation_result(inv_id)
    assert res is not None
    assert res["id"] == inv_id
    assert res["event_id"] == str(event.id)
    assert res["task_type"] == "diff_analysis"
    assert res["status"] == "completed"
    assert res["metadata"]["confidence"] == 0.95
    assert len(res["evidence"]) == 2
    assert res["evidence"][0]["evidence_type"] == "dom_delta"
    assert res["evidence"][0]["payload"]["added_nodes"] == 3

    res_by_event = repo.get_investigation_result_by_event(event.id)
    assert res_by_event is not None
    assert res_by_event["id"] == inv_id


def test_get_nonexistent_investigation_result_returns_none():
    repo = Repository(":memory:")
    assert repo.get_investigation_result("nonexistent-id") is None
    assert repo.get_investigation_result_by_event("nonexistent-event") is None
