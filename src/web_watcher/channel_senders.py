"""Notification Channel Senders: Multi-channel delivery adapters (Phase 12-A)."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
import json
import logging
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from .models import Notification

logger = logging.getLogger(__name__)


@dataclass
class DeliveryResult:
    """Outcome of a notification delivery attempt."""
    success: bool
    status_code: Optional[int] = None
    response_body: Optional[str] = None
    error_message: Optional[str] = None


class BaseChannelSender(ABC):
    """Abstract base class for all notification channel senders."""

    @abstractmethod
    def send(self, notification: Notification) -> DeliveryResult:
        """Delivers a notification to the destination channel."""
        raise NotImplementedError


class ConsoleSender(BaseChannelSender):
    """Delivers notifications by pretty-printing to stdout/stderr."""

    def __init__(self, stream=None):
        self.stream = stream

    def format_message(self, notification: Notification) -> str:
        payload = notification.payload or {}
        event_type = payload.get("event_type", "unknown")
        importance = payload.get("importance", "normal").upper()
        lines = [
            f"🔔 [NOTIFICATION] [{importance}] Event: {event_type} (ID: {notification.event_id})",
            f"   Channel: {notification.channel} | Status: {notification.status}",
        ]
        if payload.get("has_investigation"):
            inv = payload.get("investigation", {})
            summary_text = inv.get("summary", "N/A")
            lines.append(f"   🔍 Investigation: {summary_text}")
            preview = inv.get("evidence_preview", [])
            if preview:
                lines.append("   📋 Evidence Highlights:")
                for item in preview:
                    evidence_type = item.get("evidence_type")
                    payload_data = item.get("payload", {})
                    lines.append(f"      - [{evidence_type}] {json.dumps(payload_data)}")
        return "\n".join(lines)

    def send(self, notification: Notification) -> DeliveryResult:
        try:
            msg = self.format_message(notification)
            print(msg, file=self.stream)
            return DeliveryResult(success=True, status_code=200, response_body="Printed to console")
        except Exception as exc:
            logger.error(f"ConsoleSender failed: {exc}", exc_info=True)
            return DeliveryResult(success=False, error_message=str(exc))


class WebhookSender(BaseChannelSender):
    """Delivers notifications via HTTP POST to a webhook endpoint."""

    def __init__(self, webhook_url: str, timeout: float = 5.0, headers: Optional[Dict[str, str]] = None):
        self.webhook_url = webhook_url
        self.timeout = timeout
        self.headers = headers or {"Content-Type": "application/json"}

    def send(self, notification: Notification) -> DeliveryResult:
        if not self.webhook_url:
            return DeliveryResult(success=False, error_message="Webhook URL is not configured")

        data = json.dumps({
            "notification_id": notification.id,
            "event_id": notification.event_id,
            "channel": notification.channel,
            "created_at": notification.created_at,
            "payload": notification.payload,
        }).encode("utf-8")

        req = urllib.request.Request(self.webhook_url, data=data, headers=self.headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status = resp.getcode()
                body = resp.read().decode("utf-8")
                return DeliveryResult(success=200 <= status < 300, status_code=status, response_body=body)
        except urllib.error.HTTPError as exc:
            return DeliveryResult(success=False, status_code=exc.code, error_message=f"HTTPError: {exc.reason}")
        except Exception as exc:
            return DeliveryResult(success=False, error_message=f"NetworkError: {str(exc)}")
