"""Notification Dispatcher: polling, routing, and exponential backoff retry manager (Phase 12-B + 13-B + 13-D)."""

from datetime import datetime, timezone
import json
import logging
import time
from typing import Any, Dict, List, Optional

from .channel_senders import BaseChannelSender, ConsoleSender, DeliveryResult
from .models import Notification
from .repository import Repository
from .alert_silencer import AlertSilencer
from .config import AppConfig
from .retention import RetentionManager

logger = logging.getLogger(__name__)


class NotificationDispatcher:
    """Dispatches pending notifications across registered channel senders with retry backoff."""

    def __init__(
        self,
        repository: Optional[Repository] = None,
        senders: Optional[Dict[str, BaseChannelSender]] = None,
        default_sender: Optional[BaseChannelSender] = None,
        max_retries: Optional[int] = None,
        base_backoff_sec: Optional[float] = None,
        poll_interval: Optional[float] = None,
        batch_size: Optional[int] = None,
        silencer: Optional[AlertSilencer] = None,
        repo: Optional[Repository] = None,
        config: Optional[AppConfig] = None,
        retention_manager: Optional[RetentionManager] = None,
    ):
        # 兼容旧参数名 repository 与 新参数名 repo
        self.repository = repository or repo
        self.senders: Dict[str, BaseChannelSender] = dict(senders or {})
        self.default_sender = default_sender or ConsoleSender()
        self.config = config
        self.retention_manager = retention_manager
        self.max_retries = max_retries if max_retries is not None else (config.default_max_retries if config else 3)
        self.base_backoff_sec = base_backoff_sec if base_backoff_sec is not None else (config.default_base_backoff_sec if config else 1.0)
        self.poll_interval = poll_interval if poll_interval is not None else (config.default_poll_interval if config else 1.0)
        self.batch_size = batch_size if batch_size is not None else (config.default_batch_size if config else 10)
        self.silencer = silencer
        self._running = False

    def register_sender(self, channel: str, sender: BaseChannelSender) -> None:
        """Registers or overrides a sender for a specific channel."""
        self.senders[channel] = sender

    def resolve_sender(self, channel: str) -> BaseChannelSender:
        """Resolves a sender for the given channel name, falling back to default_sender."""
        return self.senders.get(channel, self.default_sender)

    def dispatch(self, notification: Notification) -> DeliveryResult:
        """Delivers a single notification and records status/retry metadata in repository."""
        return self.dispatch_one(notification)

    def dispatch_one(self, notification: Notification) -> DeliveryResult:
        """Delivers a single notification and records status/retry metadata in repository."""
        # 1. 前置告警静音/冷却拦截
        if self.silencer:
            is_silenced, reason = self.silencer.should_silence(notification)
            if is_silenced:
                if hasattr(self.repository, "mark_notification_suppressed"):
                    self.repository.mark_notification_suppressed(notification.id, reason=reason)
                elif hasattr(self.repository, "mark_notification_delivered"):
                    self.repository.mark_notification_delivered(notification.id)
                return DeliveryResult(success=True, status_code=200, response_body="suppressed")

        sender = self.resolve_sender(notification.channel)
        payload = dict(notification.payload or {})
        retries = payload.get("retry_count", 0)

        try:
            result = sender.send(notification)
        except Exception as exc:
            logger.error(f"Unhandled error in channel sender {notification.channel}: {exc}", exc_info=True)
            result = DeliveryResult(success=False, error_message=str(exc))

        if result.success:
            if self.silencer:
                self.silencer.record_dispatch(notification)
            payload["delivered_at"] = datetime.now(timezone.utc).isoformat()
            payload["delivery_response"] = result.response_body
            self.repository.update_notification_status(
                notification_id=notification.id,
                status="delivered",
                payload=payload,
            )
        else:
            retries += 1
            payload["retry_count"] = retries
            payload["last_error"] = result.error_message or f"Status code {result.status_code}"

            if retries >= self.max_retries:
                self.repository.update_notification_status(
                    notification_id=notification.id,
                    status="failed",
                    payload=payload,
                )
            else:
                backoff = self.base_backoff_sec * (2 ** (retries - 1))
                payload["next_retry_after"] = backoff
                self.repository.update_notification_status(
                    notification_id=notification.id,
                    status="retry_pending",
                    payload=payload,
                )

        return result

    def fetch_pending(self, limit: int = 10) -> List[Notification]:
        """Fetches pending or retry_pending notifications from repository."""
        if hasattr(self.repository, "get_pending_notifications"):
            return self.repository.get_pending_notifications(limit=limit)
        cursor = self.repository.connection.execute(
            "SELECT * FROM notifications WHERE status IN ('pending', 'retry_pending') ORDER BY created_at ASC LIMIT ?",
            (limit,),
        )
        rows = cursor.fetchall()
        return [
            Notification(
                id=r["id"],
                event_id=r["event_id"],
                channel=r["channel"],
                status=r["status"],
                payload=json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def run_once(self, trigger_retention: bool = False) -> int:
        """Processes a single batch of pending notifications. Returns count of dispatched items."""
        notifications = self.fetch_pending(limit=self.batch_size)
        processed = 0
        for notif in notifications:
            self.dispatch(notif)
            processed += 1

        if trigger_retention and self.retention_manager:
            self.retention_manager.cleanup_old_records()

        return processed

    def run_forever(self) -> None:
        """Runs continuous polling loop until stopped."""
        self._running = True
        while self._running:
            self.run_once()
            time.sleep(self.poll_interval)

    def stop(self) -> None:
        """Signals the polling loop to stop gracefully."""
        self._running = False
