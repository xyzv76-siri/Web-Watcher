"""Investigation Worker: background polling and dispatching for automated investigations (K.8)."""

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
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
        max_retries: int = 3,
        base_backoff_sec: float = 1.0,
        metrics: Optional[Any] = None,
    ):
        self.repository = repository
        if adapter is not None:
            self.adapter = adapter
        elif planner is not None and engine is not None:
            self.adapter = EventInvestigationAdapter()
        else:
            self.adapter = None
        self.planner = planner
        self.engine = engine
        self.batch_size = batch_size
        self.poll_interval = poll_interval
        self.max_retries = max_retries
        self.base_backoff_sec = base_backoff_sec
        self.metrics = metrics
        self._running = False

    def _inc(self, name: str, tags: Optional[Dict[str, str]] = None, amount: int = 1) -> None:
        if not self.metrics:
            return
        try:
            self.metrics.increment(name, tags=tags, amount=amount)
        except (OSError, ValueError, TypeError, RuntimeError):
            pass

    def fetch_uninvestigated_events(self, limit: Optional[int] = None) -> List[Event]:
        """Retrieves open events that do not yet have an associated investigation result,
        or have a failed investigation that is eligible for retry."""
        lim = limit or self.batch_size
        conn = getattr(self.repository, "connection", None) or getattr(self.repository, "conn", None)
        if conn is None:
            return []

        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute(
            """
            SELECT e.id FROM events e
            LEFT JOIN investigation_results ir ON CAST(e.id AS TEXT) = ir.event_id
                AND ir.id = (
                    SELECT id FROM investigation_results ir2
                    WHERE CAST(ir2.event_id AS TEXT) = CAST(e.id AS TEXT)
                    ORDER BY ir2.created_at DESC
                    LIMIT 1
                )
            WHERE e.status = ?
              AND (
                  ir.id IS NULL
                  OR (
                      ir.metadata IS NOT NULL
                      AND json_extract(ir.metadata, '$.retry_count') IS NOT NULL
                      AND CAST(json_extract(ir.metadata, '$.retry_count') AS INTEGER) < ?
                      AND (json_extract(ir.metadata, '$.next_retry_after') IS NULL
                           OR json_extract(ir.metadata, '$.next_retry_after') <= ?)
                  )
              )
            GROUP BY e.id
            ORDER BY e.created_at ASC
            LIMIT ?
            """,
            (EventStatus.OPEN.value, self.max_retries, now, lim),
        )
        rows = cursor.fetchall()
        events: List[Event] = []
        for r in rows:
            evt = self.repository.get_event(r["id"])
            if evt:
                events.append(evt)
        return events

    def _should_retry(self, existing: Optional[Dict[str, Any]]) -> bool:
        """Determines whether an event with an existing investigation result should be retried."""
        if existing is None:
            return True

        meta = existing.get("metadata") or {}
        retry_count = int(meta.get("retry_count", 0))
        if retry_count >= self.max_retries:
            return False

        next_retry = meta.get("next_retry_after")
        if next_retry is None:
            return True

        try:
            next_dt = datetime.fromisoformat(next_retry)
            if next_dt.tzinfo is None:
                next_dt = next_dt.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) >= next_dt
        except (ValueError, TypeError):
            return True

    def process_event(self, event: Event) -> bool:
        """Processes a single event through eligibility checks, execution, and persistence."""
        if self.adapter is None or not self.adapter.is_eligible(event):
            return False

        existing = self.repository.get_investigation_result_by_event(event.id)
        if existing is not None and not self._should_retry(existing):
            return False

        task_type = self.adapter.resolve_task_type(event)
        task_type_str = task_type.value if hasattr(task_type, "value") else str(task_type)

        try:
            result = self.adapter.run_for_event(
                event,
                planner=self.planner,
                engine=self.engine,
            )
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            self._record_failed_investigation(event, task_type_str, str(exc))
            return False

        if result is None:
            self._record_failed_investigation(event, task_type_str, "adapter returned no result")
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
        self._inc("investigations_total", {"event_id": str(event.id), "status": status_val})
        return True

    def _record_failed_investigation(self, event: Event, task_type_str: str, error_message: str) -> None:
        """Records a failed investigation attempt with retry metadata."""
        existing = self.repository.get_investigation_result_by_event(event.id)
        meta: Dict[str, Any] = {}
        if existing is not None:
            meta = existing.get("metadata") or {}
            inv_id = existing["id"]
        else:
            inv_id = f"inv_wk_{event.id}"

        retry_count = int(meta.get("retry_count", 0)) + 1
        next_retry_after = None
        if retry_count < self.max_retries:
            backoff = self.base_backoff_sec * (2 ** (retry_count - 1))
            next_retry_dt = datetime.now(timezone.utc).timestamp() + backoff
            next_retry_after = datetime.fromtimestamp(next_retry_dt, tz=timezone.utc).isoformat()

        meta.update({
            "retry_count": retry_count,
            "next_retry_after": next_retry_after,
            "last_error": error_message,
        })

        # Use a new investigation ID for each retry attempt so we can track history
        retry_inv_id = f"{inv_id}_retry_{retry_count}"
        self.repository.save_investigation_result(
            investigation_id=retry_inv_id,
            event_id=event.id,
            task_type=task_type_str,
            status="failed",
            summary=f"Investigation failed for event {event.id}: {error_message}",
            metadata=meta,
            evidence_items=[],
        )
        self._inc("investigations_total", {"event_id": str(event.id), "status": "failed"})

    def run_once(self) -> int:
        """Executes a single batch poll cycle and returns the count of processed investigations."""
        events = self.fetch_uninvestigated_events()
        processed_count = 0

        for event in events:
            try:
                if self.process_event(event):
                    processed_count += 1
            except (OSError, ValueError, TypeError, RuntimeError) as exc:
                logger.error(
                    f"Failed to process investigation for event {event.id}: {exc}",
                    exc_info=True,
                    extra={"event_id": str(event.id)},
                )

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
