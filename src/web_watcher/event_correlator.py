"""Event Correlator: aggregates signals into domain events and manages auto-investigation dispatch (Phase 11-B)."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Dict, List, Optional

from .event_status import EventStatus
from .event_types import EventType
from .importance import Importance
from .investigation_adapter import EventInvestigationAdapter
from .models import Entity, Event, Signal
from .repository import Repository
from .signal_types import SignalType

logger = logging.getLogger(__name__)


@dataclass
class CorrelationConfig:
    """Correlation tuning knobs for the correlator."""

    correlation_window_seconds: int = 24 * 3600
    default_importance: str = "medium"

    def __post_init__(self):
        if self.correlation_window_seconds <= 0:
            raise ValueError("correlation_window_seconds must be > 0")

    @property
    def window(self) -> timedelta:
        return timedelta(seconds=self.correlation_window_seconds)


def _derive_event_type(signal: Signal) -> str:
    """Backward-compatible helper that returns the event type string."""
    correlator = EventCorrelator(repository=None)
    resolved = correlator._resolve_event_type(signal)
    return resolved.value if hasattr(resolved, "value") else str(resolved)


class EventCorrelator:
    """Correlates incoming signals into domain events and optionally triggers investigations."""

    def __init__(
        self,
        repository: Repository,
        auto_investigate: bool = False,
        investigation_adapter: Optional[EventInvestigationAdapter] = None,
        planner: Optional[Any] = None,
        engine: Optional[Any] = None,
        config: Optional[CorrelationConfig] = None,
        now_factory: Optional[Any] = None,
    ):
        self.repository = repository
        self.auto_investigate = auto_investigate
        self.config = config or CorrelationConfig()
        self.now_factory = now_factory or (lambda: datetime.now(timezone.utc))
        self.investigation_adapter = investigation_adapter or (
            EventInvestigationAdapter() if auto_investigate else None
        )
        self.planner = planner
        self.engine = engine

    def _resolve_event_type(self, signal: Signal) -> EventType:
        sig_type = signal.signal_type
        if sig_type == SignalType.STARS_CHANGED:
            return EventType.STARS_CHANGED
        if sig_type == SignalType.RELEASE_PUBLISHED:
            return EventType.RELEASE_PUBLISHED
        return EventType.CONTENT_CHANGE

    def _evaluate_importance(self, signal: Signal, existing_event: Optional[Event] = None) -> Importance:
        sig_type = signal.signal_type
        if sig_type == SignalType.RELEASE_PUBLISHED:
            return Importance.CRITICAL
        if sig_type == SignalType.CONTENT_CHANGE:
            return Importance.IMPORTANT
        return Importance.INTERESTING

    def process_signal(self, signal: Signal) -> Event:
        """Processes an incoming signal, correlates it into an event, and triggers auto-investigation if enabled."""
        entity_id = signal.entity_id
        resolved_type = self._resolve_event_type(signal)
        now = self.now_factory()
        cutoff = now - self.config.window
        open_event = self.repository.find_open_event_for_entity(
            entity_id,
            event_type=resolved_type,
            cutoff=cutoff,
        )

        importance = self._evaluate_importance(signal, open_event)

        if open_event is None:
            event = self.repository.create_event(
                entity_id=entity_id,
                event_type=resolved_type,
                status=EventStatus.OPEN,
                importance=importance,
                created_at=signal.observed_at,
            )
        else:
            event = open_event
            if (
                importance == Importance.CRITICAL
                or (importance == Importance.IMPORTANT and event.importance == Importance.INTERESTING)
            ):
                self.repository.update_event(event.id, importance=importance)
                refreshed = self.repository.get_event(event.id)
                if refreshed:
                    event = refreshed

        persisted_signal = self._persist_signal_if_needed(signal)
        if persisted_signal is not None:
            signal = persisted_signal

        if signal.id is not None:
            self.repository.link_signal_to_event(event.id, signal.id)

        if self.auto_investigate:
            self.dispatch_investigation(event)

        return event

    def _persist_signal_if_needed(self, signal: Signal) -> Optional[Signal]:
        if signal.id is None:
            return None
        existing = self.repository.connection.execute(
            "SELECT id FROM signals WHERE id = ?", (signal.id,)
        ).fetchone()
        if existing:
            return signal
        return self.repository.create_signal(
            entity_id=signal.entity_id,
            signal_type=signal.signal_type,
            observed_at=signal.observed_at,
            value=signal.value,
            fingerprint=signal.fingerprint,
        )

    def correlate(self, signal: Signal) -> Event:
        """Alias for process_signal to maintain backward compatibility."""
        return self.process_signal(signal)

    def close_event(self, event_id: int) -> None:
        """Close an existing event so it no longer accepts new signals."""
        self.repository.update_event(event_id=event_id, status=EventStatus.CLOSED)

    def dispatch_investigation(self, event: Event) -> bool:
        """Dispatches automatic investigation for an event if eligible and not previously investigated."""
        adapter = self.investigation_adapter or EventInvestigationAdapter()
        if not adapter.is_eligible(event):
            return False

        existing = self.repository.get_investigation_result_by_event(event.id)
        if existing is not None:
            return False

        try:
            task_type = adapter.resolve_task_type(event)
            task_type_str = task_type.value if hasattr(task_type, "value") else str(task_type)

            result = adapter.run_for_event(
                event,
                planner=self.planner,
                engine=self.engine,
            )
            if result is None:
                return False

            summary = getattr(result, "summary", None) or f"Auto-investigation completed for event {event.id}"
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

            inv_id = f"inv_auto_{event.id}"
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
        except Exception as exc:
            logger.error(f"Auto investigation dispatch failed for event {event.id}: {exc}", exc_info=True)
            return False
