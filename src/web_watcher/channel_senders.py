"""Notification Channel Senders: Multi-channel delivery adapters (Phase 12-A + 13-B)."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
import json
import logging
import smtplib
import urllib.error
import urllib.request
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Any, Dict, Optional

from .models import Notification
from .card_formatters import (
    BaseCardFormatter,
    SlackBlockKitFormatter,
    LarkCardFormatter,
    DingTalkCardFormatter,
    get_card_formatter,
)

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


class SlackSender(WebhookSender):
    """Delivers notifications using Slack Block Kit payload format."""

    def __init__(self, webhook_url: Optional[str] = None, timeout: float = 10.0):
        super().__init__(webhook_url=webhook_url or "", timeout=timeout)
        self._formatter = SlackBlockKitFormatter()

    def send(self, notification: Notification) -> DeliveryResult:
        payload = notification.payload or {}
        target_url = payload.get("recipient") or self.webhook_url
        if not target_url:
            return DeliveryResult(success=False, error_message="Webhook URL is not configured")

        data = self._formatter.format(notification)
        req = urllib.request.Request(
            target_url,
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status = resp.getcode()
                body = resp.read().decode("utf-8")
                return DeliveryResult(success=200 <= status < 300, status_code=status, response_body=body)
        except urllib.error.HTTPError as exc:
            return DeliveryResult(success=False, status_code=exc.code, error_message=f"HTTPError: {exc.reason}")
        except Exception as exc:
            return DeliveryResult(success=False, error_message=f"NetworkError: {str(exc)}")


class LarkSender(WebhookSender):
    """Delivers notifications using Lark/Feishu interactive card payload format."""

    def __init__(self, webhook_url: Optional[str] = None, timeout: float = 10.0):
        super().__init__(webhook_url=webhook_url or "", timeout=timeout)
        self._formatter = LarkCardFormatter()

    def send(self, notification: Notification) -> DeliveryResult:
        payload = notification.payload or {}
        target_url = payload.get("recipient") or self.webhook_url
        if not target_url:
            return DeliveryResult(success=False, error_message="Webhook URL is not configured")

        data = self._formatter.format(notification)
        req = urllib.request.Request(
            target_url,
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status = resp.getcode()
                body = resp.read().decode("utf-8")
                return DeliveryResult(success=200 <= status < 300, status_code=status, response_body=body)
        except urllib.error.HTTPError as exc:
            return DeliveryResult(success=False, status_code=exc.code, error_message=f"HTTPError: {exc.reason}")
        except Exception as exc:
            return DeliveryResult(success=False, error_message=f"NetworkError: {str(exc)}")


class DingTalkSender(WebhookSender):
    """Delivers notifications using DingTalk Markdown payload format."""

    def __init__(self, webhook_url: Optional[str] = None, timeout: float = 10.0):
        super().__init__(webhook_url=webhook_url or "", timeout=timeout)
        self._formatter = DingTalkCardFormatter()

    def send(self, notification: Notification) -> DeliveryResult:
        payload = notification.payload or {}
        target_url = payload.get("recipient") or self.webhook_url
        if not target_url:
            return DeliveryResult(success=False, error_message="Webhook URL is not configured")

        data = self._formatter.format(notification)
        req = urllib.request.Request(
            target_url,
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status = resp.getcode()
                body = resp.read().decode("utf-8")
                return DeliveryResult(success=200 <= status < 300, status_code=status, response_body=body)
        except urllib.error.HTTPError as exc:
            return DeliveryResult(success=False, status_code=exc.code, error_message=f"HTTPError: {exc.reason}")
        except Exception as exc:
            return DeliveryResult(success=False, error_message=f"NetworkError: {str(exc)}")


class EmailSender(BaseChannelSender):
    """Delivers notifications via SMTP email."""

    def __init__(
        self,
        smtp_host: str = "localhost",
        smtp_port: int = 25,
        smtp_user: Optional[str] = None,
        smtp_password: Optional[str] = None,
        use_tls: bool = False,
        use_ssl: bool = False,
        from_addr: Optional[str] = None,
        to_addrs: Optional[list[str]] = None,
    ):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user
        self.smtp_password = smtp_password
        self.use_tls = use_tls
        self.use_ssl = use_ssl
        self.from_addr = from_addr or smtp_user or "web-watcher@localhost"
        self.to_addrs = to_addrs or []

    def _build_message(self, notification: Notification) -> MIMEMultipart:
        payload = notification.payload or {}
        event_type = payload.get("event_type", "unknown")
        importance = payload.get("importance", "normal").upper()
        subject = f"[{importance}] Web-Watcher: {event_type}"

        body_lines = [
            f"Event: {event_type}",
            f"Notification ID: {notification.id}",
            f"Event ID: {notification.event_id}",
            f"Channel: {notification.channel}",
            f"Status: {notification.status}",
            f"Created At: {notification.created_at}",
            "",
        ]
        if payload.get("has_investigation"):
            inv = payload.get("investigation", {})
            body_lines.append(f"Investigation Summary: {inv.get('summary', 'N/A')}")
            preview = inv.get("evidence_preview", [])
            if preview:
                body_lines.append("Evidence Highlights:")
                for item in preview:
                    evidence_type = item.get("evidence_type")
                    payload_data = item.get("payload", {})
                    body_lines.append(f"- [{evidence_type}] {json.dumps(payload_data)}")

        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"] = self.from_addr
        msg["To"] = ", ".join(self.to_addrs)
        msg.attach(MIMEText("\n".join(body_lines), "plain", "utf-8"))
        return msg

    def _connect_and_send(self, server, msg):
        if self.smtp_user and self.smtp_password:
            server.login(self.smtp_user, self.smtp_password)
        server.sendmail(self.from_addr, self.to_addrs, msg.as_string())

    def send(self, notification: Notification) -> DeliveryResult:
        if not self.to_addrs:
            return DeliveryResult(success=False, error_message="Email recipient addresses are not configured")

        try:
            msg = self._build_message(notification)
            if self.use_ssl:
                with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=10) as server:
                    self._connect_and_send(server, msg)
            else:
                with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10) as server:
                    if self.use_tls:
                        server.starttls()
                    self._connect_and_send(server, msg)
            return DeliveryResult(success=True, status_code=250, response_body="Accepted")
        except smtplib.SMTPException as exc:
            return DeliveryResult(success=False, error_message=f"SMTPError: {str(exc)}")
        except Exception as exc:
            return DeliveryResult(success=False, error_message=f"NetworkError: {str(exc)}")
