"""Unit tests for NotificationEnricher (Phase 11-B)."""

import pytest
from web_watcher.event_types import EventType
from web_watcher.importance import Importance
from web_watcher.notification_enricher import NotificationEnricher
from web_watcher.repository import Repository


def test_build_payload_without_investigation():
    repo = Repository(":memory:")
    entity = repo.create_entity(canonical_key="ent-notif-1", name="App 1", entity_type="service")
    event = repo.create_event(entity_id=entity.id, event_type=EventType.CONTENT_CHANGE, importance=Importance.INTERESTING)

    enricher = NotificationEnricher(repository=repo)
    payload = enricher.build_payload(event, base_payload={"custom_field": 123})

    assert payload["event_id"] == event.id
    assert payload["entity_id"] == entity.id
    assert payload["importance"] == "interesting"
    assert payload["has_investigation"] is False
    assert payload["custom_field"] == 123
    assert "investigation" not in payload


def test_build_payload_with_investigation_and_evidence():
    repo = Repository(":memory:")
    entity = repo.create_entity(canonical_key="ent-notif-2", name="App 2", entity_type="service")
    event = repo.create_event(entity_id=entity.id, event_type=EventType.CONTENT_CHANGE, importance=Importance.IMPORTANT)

    repo.save_investigation_result(
        investigation_id="inv_test_notif",
        event_id=event.id,
        task_type="diff_analysis",
        status="completed",
        summary="Found 3 critical breaking changes",
        metadata={"diff_speed": "instant"},
        evidence_items=[
            {"evidence_type": "line_diff", "payload": {"line": 10}},
            {"evidence_type": "api_drift", "payload": {"endpoint": "/v1/auth"}},
            {"evidence_type": "schema_change", "payload": {"field": "token"}},
            {"evidence_type": "doc_update", "payload": {"url": "https://example.com"}},
        ],
    )

    enricher = NotificationEnricher(repository=repo)
    payload = enricher.build_payload(event, max_evidence_preview=2)

    assert payload["has_investigation"] is True
    inv = payload["investigation"]
    assert inv["id"] == "inv_test_notif"
    assert inv["status"] == "completed"
    assert inv["summary"] == "Found 3 critical breaking changes"
    assert inv["evidence_count"] == 4
    assert len(inv["evidence_preview"]) == 2
    assert inv["evidence_preview"][0]["evidence_type"] == "line_diff"
    assert inv["evidence_preview"][1]["evidence_type"] == "api_drift"


def test_create_enriched_notification_persists_to_repo():
    repo = Repository(":memory:")
    entity = repo.create_entity(canonical_key="ent-notif-3", name="App 3", entity_type="service")
    event = repo.create_event(entity_id=entity.id, event_type=EventType.RELEASE_PUBLISHED, importance=Importance.CRITICAL)

    repo.save_investigation_result(
        investigation_id="inv_rel_1",
        event_id=event.id,
        task_type="release_audit",
        status="completed",
        summary="Verified version v2.0.0 tag signature",
    )

    enricher = NotificationEnricher(repository=repo)
    notif = enricher.create_enriched_notification(
        event=event,
        channel="slack",
        status="queued",
        base_payload={"target_channel": "#dev-alerts"},
    )

    assert notif.event_id == event.id
    assert notif.channel == "slack"
    assert notif.status == "queued"
    assert notif.payload["target_channel"] == "#dev-alerts"
    assert notif.payload["has_investigation"] is True
    assert notif.payload["investigation"]["summary"] == "Verified version v2.0.0 tag signature"


def test_enricher_handles_empty_evidence_safely():
    repo = Repository(":memory:")
    entity = repo.create_entity(canonical_key="ent-notif-4", name="App 4", entity_type="service")
    event = repo.create_event(entity_id=entity.id, event_type=EventType.CONTENT_CHANGE)

    repo.save_investigation_result(
        investigation_id="inv_empty_ev",
        event_id=event.id,
        task_type="quick_check",
        status="completed",
        summary="No structural changes",
        evidence_items=[],
    )

    enricher = NotificationEnricher(repository=repo)
    payload = enricher.build_payload(event)

    assert payload["has_investigation"] is True
    assert payload["investigation"]["evidence_count"] == 0
    assert payload["investigation"]["evidence_preview"] == []


def test_enricher_respects_custom_preview_limit():
    repo = Repository(":memory:")
    entity = repo.create_entity(canonical_key="ent-notif-5", name="App 5", entity_type="service")
    event = repo.create_event(entity_id=entity.id, event_type=EventType.CONTENT_CHANGE)

    repo.save_investigation_result(
        investigation_id="inv_multi_ev",
        event_id=event.id,
        task_type="deep_scan",
        status="completed",
        evidence_items=[{"evidence_type": f"item_{i}", "payload": {}} for i in range(10)],
    )

    enricher = NotificationEnricher(repository=repo)
    payload = enricher.build_payload(event, max_evidence_preview=5)

    assert payload["investigation"]["evidence_count"] == 10
    assert len(payload["investigation"]["evidence_preview"]) == 5
