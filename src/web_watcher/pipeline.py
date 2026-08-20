"""Unified observation-to-notification pipeline enforcing the causal chain:

    Fetch → Observation → Signal → Event → Investigation → Policy → Notification

This module is the single entrypoint for turning raw fetch/observation results
into notifications. No adapter may bypass it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from .event_correlator import CorrelationPlan, EventCorrelator
from .event_status import EventStatus
from .event_types import EventType
from .importance import Importance
from .investigation_adapter import EventInvestigationAdapter
from .models import Entity, Event, Notification, Signal, Target
from .notification_dispatcher import NotificationDispatcher
from .notification_enricher import NotificationEnricher
from .repository import Repository
from .signal_types import SignalType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pipeline input / output contracts
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Observation:
    """Raw observation result from a target execution.

    This is the output of the Fetch + Adapter layer, before Signal creation.
    """
    target_id: str
    target_type: str
    status_code: Optional[int]
    observed_at: datetime
    outcome: str
    signals: List[Signal] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def has_signals(self) -> bool:
        return bool(self.signals)


@dataclass(frozen=True)
class PipelineResult:
    """Final outcome of processing a single observation through the pipeline."""
    target_id: str
    signals_emitted: List[Signal]
    event: Optional[Event]
    notification: Optional[Notification]
    investigation_dispatched: bool
    suppressed: bool
    suppression_reason: Optional[str]
    delivery_result: Optional[Any] = None


# ---------------------------------------------------------------------------
# Unified Pipeline
# ---------------------------------------------------------------------------

class UnifiedPipeline:
    """Enforces the canonical causal chain from observation to notification.

    Steps:
        1. Receive Observation (from adapter execution)
        2. Persist raw signals
        3. Correlate signals → events (with suppression window)
        4. Dispatch investigation for eligible events
        5. Enrich notification with investigation + observation evidence
        6. Optionally deliver notification

    No step may be skipped by adapters or callers.
    """

    def __init__(
        self,
        repository: Repository,
        correlator: Optional[EventCorrelator] = None,
        enricher: Optional[NotificationEnricher] = None,
        dispatcher: Optional[NotificationDispatcher] = None,
        investigation_adapter: Optional[EventInvestigationAdapter] = None,
        auto_investigate: bool = True,
        auto_notify: bool = True,
        auto_deliver: bool = False,
        notify_channel: str = "webhook",
        suppression_window_seconds: float = 3600.0,
        planner: Optional[Any] = None,
        engine: Optional[Any] = None,
        config: Optional[Any] = None,
    ):
        self.repository = repository
        self.config = config
        self.correlator = correlator or EventCorrelator(
            repository=repository,
            auto_investigate=auto_investigate,
            investigation_adapter=investigation_adapter,
            planner=planner,
            engine=engine,
        )
        self.enricher = enricher or NotificationEnricher(repository=repository)
        self.dispatcher = dispatcher or (NotificationDispatcher(repository=repository) if auto_deliver else None)
        self.auto_investigate = auto_investigate
        self.auto_notify = auto_notify
        self.auto_deliver = auto_deliver
        self.notify_channel = notify_channel
        self.suppression_window = timedelta(seconds=suppression_window_seconds)

    def _is_suppressed_for_signal(self, signal: Signal, event_type: EventType, now: datetime) -> Tuple[bool, Optional[str]]:
        """Check if this signal's entity has an open event within the suppression window."""
        if signal.entity_id is None:
            return False, None
        cutoff = now - self.suppression_window
        open_event = self.repository.find_open_event_for_entity(
            signal.entity_id,
            event_type=event_type,
            cutoff=cutoff,
        )
        if open_event is not None:
            return True, f"Suppressed: open event {open_event.id} within window"
        return False, None

    def process_observation(self, observation: Observation) -> PipelineResult:
        """Process a single observation through the full pipeline."""
        now = observation.observed_at
        target_id = observation.target_id
        signals = list(observation.signals)
        suppressed = False
        suppression_reason: Optional[str] = None
        event: Optional[Event] = None
        notification: Optional[Notification] = None
        investigation_dispatched = False

        # Step 1: No signals → no event, no notification
        if not signals:
            return PipelineResult(
                target_id=target_id,
                signals_emitted=[],
                event=None,
                notification=None,
                investigation_dispatched=False,
                suppressed=False,
                suppression_reason="No signals emitted from observation",
            )

        # Step 2: Check suppression window per signal
        signals_by_type: Dict[EventType, List[Signal]] = {}
        for sig in signals:
            evt_type = self._signal_to_event_type(sig.signal_type)
            signals_by_type.setdefault(evt_type, []).append(sig)

        non_suppressed_signals: List[Signal] = []
        for evt_type, sigs in signals_by_type.items():
            for sig in sigs:
                is_supp, reason = self._is_suppressed_for_signal(sig, evt_type, now)
                if is_supp:
                    suppressed = True
                    suppression_reason = reason
                    logger.info(f"Suppressing signal {sig.id} for {sig.entity_id} / {evt_type}: {reason}")
                else:
                    non_suppressed_signals.append(sig)

        if not non_suppressed_signals:
            return PipelineResult(
                target_id=target_id,
                signals_emitted=signals,
                event=None,
                notification=None,
                investigation_dispatched=False,
                suppressed=True,
                suppression_reason=suppression_reason,
            )

        # Step 3: Correlate non-suppressed signals → events
        # Use only non-suppressed signals for correlation
        filtered_observation = Observation(
            target_id=observation.target_id,
            target_type=observation.target_type,
            status_code=observation.status_code,
            observed_at=observation.observed_at,
            outcome=observation.outcome,
            signals=non_suppressed_signals,
            evidence=observation.evidence,
            metadata=observation.metadata,
        )

        # Process each signal through correlator
        for sig in non_suppressed_signals:
            plan = self.correlator.process_signal(sig)
            persisted = self.repository.commit_plan(correlation_plan=plan)
            if not persisted:
                logger.error(f"Failed to persist correlation plan for signal {sig.id}")
                continue

            # Retrieve merged event
            if plan.merged_event_id is not None:
                event = self.repository.get_event(plan.merged_event_id)
            elif plan.events_to_create:
                first = plan.events_to_create[0]
                open_event = self.repository.find_open_event_for_entity(
                    entity_id=first.entity_id,
                    event_type=first.event_type,
                )
                if open_event:
                    event = open_event

            if event is None:
                continue

            # Step 4: Dispatch investigation if eligible
            if self.auto_investigate:
                dispatched = self.correlator.dispatch_investigation(event)
                investigation_dispatched = investigation_dispatched or dispatched

            # Step 5: Create enriched notification
            if self.auto_notify:
                notification = self.enricher.create_enriched_notification(
                    event=event,
                    channel=self.notify_channel,
                    status="pending",
                    base_payload={
                        "observation": {
                            "target_id": target_id,
                            "status_code": observation.status_code,
                            "outcome": observation.outcome,
                            "observed_at": observation.observed_at.isoformat(),
                            "evidence": observation.evidence,
                            "metadata": observation.metadata,
                        },
                        "signals": [
                            {
                                "signal_type": sig.signal_type.value if hasattr(sig.signal_type, "value") else str(sig.signal_type),
                                "observed_at": sig.observed_at.isoformat() if sig.observed_at else None,
                                "fingerprint": sig.fingerprint,
                            }
                            for sig in non_suppressed_signals
                        ],
                    },
                )

                # Step 6: Optionally deliver
                if self.auto_deliver and self.dispatcher and notification:
                    delivery_result = self.dispatcher.dispatch(notification)
                    refreshed = self.repository.get_notification(notification.id)
                    if refreshed:
                        notification = refreshed
                    return PipelineResult(
                        target_id=target_id,
                        signals_emitted=signals,
                        event=event,
                        notification=notification,
                        investigation_dispatched=investigation_dispatched,
                        suppressed=suppressed,
                        suppression_reason=suppression_reason,
                        delivery_result=delivery_result,
                    )

        return PipelineResult(
            target_id=target_id,
            signals_emitted=signals,
            event=event,
            notification=notification,
            investigation_dispatched=investigation_dispatched,
            suppressed=suppressed,
            suppression_reason=suppression_reason,
        )

    def _signal_to_event_type(self, signal_type: SignalType) -> EventType:
        """Map signal type to event type."""
        if signal_type == SignalType.STARS_CHANGED:
            return EventType.STARS_CHANGED
        if signal_type == SignalType.RELEASE_PUBLISHED:
            return EventType.RELEASE_PUBLISHED
        return EventType.CONTENT_CHANGE

    def run_batch(self, observations: List[Observation]) -> List[PipelineResult]:
        """Process a batch of observations."""
        return [self.process_observation(obs) for obs in observations]
