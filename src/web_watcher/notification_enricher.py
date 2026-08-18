"""Notification Enricher: enriches domain event notifications with investigation results and evidence (Phase 11-B)."""

import logging
from typing import Any, Dict, List, Optional

from .models import Event, Notification
from .repository import Repository

logger = logging.getLogger(__name__)


class NotificationEnricher:
    """Enriches event notification payloads with investigation findings and structured evidence."""

    def __init__(self, repository: Repository):
        self.repository = repository

    def build_payload(
        self,
        event: Event,
        base_payload: Optional[Dict[str, Any]] = None,
        max_evidence_preview: int = 3,
    ) -> Dict[str, Any]:
        """Constructs an enriched notification payload including investigation findings if available."""
        payload: Dict[str, Any] = dict(base_payload or {})
        payload.setdefault("event_id", event.id)
        payload.setdefault("entity_id", event.entity_id)
        payload.setdefault("event_type", event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type))
        payload.setdefault("importance", event.importance.value if hasattr(event.importance, "value") else str(event.importance))

        inv_result = self.repository.get_investigation_result_by_event(event.id)
        if not inv_result:
            payload["has_investigation"] = False
            return payload

        evidence_items = inv_result.get("evidence", []) or []
        preview_evidence: List[Dict[str, Any]] = []
        for item in evidence_items[:max_evidence_preview]:
            preview_evidence.append({
                "id": item.get("id"),
                "evidence_type": item.get("evidence_type"),
                "payload": item.get("payload", {}),
            })

        payload["has_investigation"] = True
        payload["investigation"] = {
            "id": inv_result.get("id"),
            "task_type": inv_result.get("task_type"),
            "status": inv_result.get("status"),
            "summary": inv_result.get("summary"),
            "metadata": inv_result.get("metadata", {}),
            "evidence_count": len(evidence_items),
            "evidence_preview": preview_evidence,
        }
        return payload

    def create_enriched_notification(
        self,
        event: Event,
        channel: str = "webhook",
        status: str = "pending",
        base_payload: Optional[Dict[str, Any]] = None,
    ) -> Notification:
        """Builds the enriched payload and persists the notification to the repository."""
        payload = self.build_payload(event, base_payload=base_payload)
        return self.repository.create_notification(
            event_id=event.id,
            channel=channel,
            status=status,
            payload=payload,
        )
