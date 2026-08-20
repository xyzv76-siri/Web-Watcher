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


@dataclass
class EventToCreate:
    entity_id: str
    event_type: str
    status: str
    importance: str
    created_at: datetime
    updated_at: datetime


@dataclass
class EventToUpdate:
    event_id: int
    status: str
    importance: str
    updated_at: datetime


@dataclass
class SignalToPersist:
    entity_id: str
    signal_type: str
    observed_at: datetime
    value: Any
    fingerprint: str


@dataclass
class LinkToCreate:
    event_id: int
    signal_id: int


@dataclass
class CorrelationPlan:
    events_to_create: List[EventToCreate] = field(default_factory=list)
    events_to_update: List[EventToUpdate] = field(default_factory=list)
    signals_to_persist: List[SignalToPersist] = field(default_factory=list)
    links: List[LinkToCreate] = field(default_factory=list)
    merged_event_id: Optional[int] = None

    @property
    def signals(self) -> List[SignalToPersist]:
        """Backward-compatible alias for signals_to_persist."""
        return self.signals_to_persist

    @signals.setter
    def signals(self, value: List[SignalToPersist]) -> None:
        self.signals_to_persist = value


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
        if investigation_adapter is not None:
            self.investigation_adapter = investigation_adapter
        elif auto_investigate and planner is not None and engine is not None:
            self.investigation_adapter = EventInvestigationAdapter()
        else:
            self.investigation_adapter = None
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

    def process_signal(self, signal: Signal) -> CorrelationPlan:
        """Process a signal and return a CorrelationPlan.

        This method does NOT persist anything. It only makes domain decisions.
        The caller is responsible for atomic finalization.
        """
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

        plan = CorrelationPlan()

        if open_event is None:
            plan.events_to_create.append(EventToCreate(
                entity_id=entity_id,
                event_type=resolved_type.value if hasattr(resolved_type, "value") else str(resolved_type),
                status=EventStatus.OPEN.value,
                importance=importance.value if hasattr(importance, "value") else str(importance),
                created_at=signal.observed_at,
                updated_at=now,
            ))
            target_event_id = None
        else:
            target_event = open_event
            if (
                importance == Importance.CRITICAL
                or (importance == Importance.IMPORTANT and open_event.importance == Importance.INTERESTING)
            ):
                plan.events_to_update.append(EventToUpdate(
                    event_id=open_event.id,
                    status=open_event.status.value,
                    importance=importance.value if hasattr(importance, "value") else str(importance),
                    updated_at=now,
                ))
                target_event_id = open_event.id
            else:
                target_event_id = open_event.id

        plan.merged_event_id = target_event_id

        # Queue signal for persistence
        plan.signals_to_persist.append(SignalToPersist(
            entity_id=entity_id,
            signal_type=signal.signal_type.value if hasattr(signal.signal_type, "value") else str(signal.signal_type),
            observed_at=signal.observed_at,
            value=signal.value,
            fingerprint=signal.fingerprint,
        ))

        # Queue link creation (event_id may be resolved during finalization)
        plan.links.append(LinkToCreate(
            event_id=target_event_id,
            signal_id=-1,  # placeholder, resolved during finalization
        ))

        return plan

    def correlate(self, signal: Signal) -> CorrelationPlan:
        """Alias for process_signal to maintain backward compatibility."""
        return self.process_signal(signal)

    def build_plans(self, signals: List[Signal]) -> List[CorrelationPlan]:
        """Build correlation plans for a batch of signals.

        Returns one CorrelationPlan per distinct entity, aggregated from all signals.
        This method does NOT persist anything.
        """
        entity_plans: Dict[str, CorrelationPlan] = {}
        for signal in signals:
            plan = self.process_signal(signal)
            entity_id = signal.entity_id
            if entity_id not in entity_plans:
                entity_plans[entity_id] = CorrelationPlan()
            existing = entity_plans[entity_id]
            existing.events_to_create.extend(plan.events_to_create)
            existing.events_to_update.extend(plan.events_to_update)
            existing.signals_to_persist.extend(plan.signals_to_persist)
            existing.links.extend(plan.links)
        return list(entity_plans.values())

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
