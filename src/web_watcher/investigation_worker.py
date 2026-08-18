"""Investigation Worker: background polling and dispatching for automated investigations (K.8)."""

import logging
import time
from typing import Any, List, Optional
from .event_status import EventStatus
from .investigation_adapter import EventInvestigationAdapter
from .models import Event
from .repository import Repository

logger = logging.getLogger(__name__)


class InvestigationWorker:
    """Background worker that polls open events, executes investigations via Adapter, and persists results."""

    def __init__(
        self,
        repository: Repository,
        adapter: Optional[EventInvestigationAdapter] = None,
        planner: Optional[Any] = None,
        engine: Optional[Any] = None,
        batch_size: int = 10,
        poll_interval: float = 1.0,
    ):
        self.repository = repository
        self.adapter = adapter or EventInvestigationAdapter()
        self.planner = planner
        self.engine = engine
        self.batch_size = batch_size
        self.poll_interval = poll_interval
        self._running = False

    def fetch_uninvestigated_events(self, limit: Optional[int] = None) -> List[Event]:
        """Retrieves open events that do not yet have an associated investigation result."""
        lim = limit or self.batch_size
        conn = getattr(self.repository, "connection", None) or getattr(self.repository, "conn", None)
        if conn is None:
            return []

        cursor = conn.execute(
            """
            SELECT e.id FROM events e
            LEFT JOIN investigation_results ir ON CAST(e.id AS TEXT) = ir.event_id
            WHERE e.status = ? AND ir.id IS NULL
            ORDER BY e.created_at ASC
            LIMIT ?
            """,
            (EventStatus.OPEN.value, lim),
        )
        rows = cursor.fetchall()
        events: List[Event] = []
        for r in rows:
            evt = self.repository.get_event(r["id"])
            if evt:
                events.append(evt)
        return events

    def process_event(self, event: Event) -> bool:
        """Processes a single event through eligibility checks, execution, and persistence."""
        if not self.adapter.is_eligible(event):
            return False

        existing = self.repository.get_investigation_result_by_event(event.id)
        if existing is not None:
            return False

        task_type = self.adapter.resolve_task_type(event)
        task_type_str = task_type.value if hasattr(task_type, "value") else str(task_type)

        result = self.adapter.run_for_event(
            event,
            planner=self.planner,
            engine=self.engine,
        )
        if result is None:
            return False

        summary = getattr(result, "summary", None) or f"Investigation completed for event {event.id}"
        metadata = getattr(result, "metadata", {}) or {}
        raw_evidence = getattr(result, "evidence", []) or []

        evidence_items = []
        for item in raw_evidence:
            if isinstance(item, dict):
                evidence_items.append(item)
            else:
                evidence_items.append({
                    "evidence_type": getattr(item, "evidence_type", "generic"),
                    "payload": getattr(item, "payload", {}),
                })

        inv_id = f"inv_wk_{event.id}"
        status_val = "completed"
        if hasattr(result, "status"):
            status_val = result.status.value if hasattr(result.status, "value") else str(result.status)

        self.repository.save_investigation_result(
            investigation_id=inv_id,
            event_id=event.id,
            task_type=task_type_str,
            status=status_val,
            summary=summary,
            metadata=metadata,
            evidence_items=evidence_items,
        )
        return True

    def run_once(self) -> int:
        """Executes a single batch poll cycle and returns the count of processed investigations."""
        events = self.fetch_uninvestigated_events()
        processed_count = 0

        for event in events:
            try:
                if self.process_event(event):
                    processed_count += 1
            except Exception as exc:
                logger.error(f"Failed to process investigation for event {event.id}: {exc}", exc_info=True)

        return processed_count

    def run_forever(self, max_iterations: Optional[int] = None) -> None:
        """Starts the worker polling loop until stopped or max_iterations is reached."""
        self._running = True
        iterations = 0

        while self._running:
            self.run_once()
            iterations += 1
            if max_iterations is not None and iterations >= max_iterations:
                break
            time.sleep(self.poll_interval)

    def stop(self) -> None:
        """Stops the running worker loop."""
        self._running = False
