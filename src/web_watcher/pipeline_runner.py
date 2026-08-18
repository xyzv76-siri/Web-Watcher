"""Pipeline Runner: end-to-end orchestration of signals, events, auto-investigation, and enriched notifications (Phase 12-C)."""

import logging
from typing import Any, Dict, List, Optional

from .channel_senders import BaseChannelSender, ConsoleSender, DeliveryResult, WebhookSender
from .event_correlator import EventCorrelator
from .models import Entity, Event, Notification, Signal
from .notification_dispatcher import NotificationDispatcher
from .notification_enricher import NotificationEnricher
from .repository import Repository

logger = logging.getLogger(__name__)


class PipelineRunner:
    """Orchestrates end-to-end execution across signals, event correlation, investigation, notifications, and delivery."""

    def __init__(
        self,
        repository: Repository,
        correlator: Optional[EventCorrelator] = None,
        enricher: Optional[NotificationEnricher] = None,
        dispatcher: Optional[NotificationDispatcher] = None,
        auto_investigate: bool = False,
        auto_notify: bool = True,
        auto_deliver: bool = False,
        notify_channel: str = "webhook",
        investigation_adapter: Optional[Any] = None,
        planner: Optional[Any] = None,
        engine: Optional[Any] = None,
    ):
        self.repository = repository
        self.correlator = correlator or EventCorrelator(
            repository=repository,
            auto_investigate=auto_investigate,
            investigation_adapter=investigation_adapter,
            planner=planner,
            engine=engine,
        )
        self.enricher = enricher or NotificationEnricher(repository=repository)
        self.dispatcher = dispatcher or (NotificationDispatcher(repository=repository) if auto_deliver else None)
        self.auto_notify = auto_notify
        self.auto_deliver = auto_deliver
        self.notify_channel = notify_channel

    def process_signal(self, signal: Signal) -> Dict[str, Any]:
        """Processes a single signal through correlation, investigation, enriched notification creation, and optional delivery."""
        event = self.correlator.process_signal(signal)
        notification: Optional[Notification] = None
        delivery_result: Optional[DeliveryResult] = None

        if self.auto_notify:
            notification = self.enricher.create_enriched_notification(
                event=event,
                channel=self.notify_channel,
                status="pending",
            )
            if self.auto_deliver and self.dispatcher:
                delivery_result = self.dispatcher.dispatch(notification)
                refreshed = self.repository.get_notification(notification.id)
                if refreshed:
                    notification = refreshed

        return {
            "signal_id": signal.id,
            "event": event,
            "notification": notification,
            "delivery_result": delivery_result,
        }

    def run_batch_signals(self, signals: List[Signal]) -> List[Dict[str, Any]]:
        """Processes a batch of signals through the end-to-end pipeline."""
        results = []
        for sig in signals:
            res = self.process_signal(sig)
            results.append(res)
        return results
