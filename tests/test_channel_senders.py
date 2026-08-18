"""Unit tests for Notification Channel Senders (Phase 12-A)."""

import io
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import urllib.error
import pytest

from web_watcher.channel_senders import ConsoleSender, DeliveryResult, WebhookSender
from web_watcher.models import Notification


def _now():
    return datetime.now(timezone.utc).isoformat()


def test_console_sender_basic_format():
    buf = io.StringIO()
    sender = ConsoleSender(stream=buf)
    notif = Notification(
        id="notif_1",
        event_id=101,
        channel="console",
        status="pending",
        created_at=_now(),
        payload={"event_type": "content_change", "importance": "important", "has_investigation": False},
    )

    res = sender.send(notif)
    assert res.success is True
    assert res.status_code == 200
    out = buf.getvalue()
    assert "[IMPORTANT] Event: content_change" in out
    assert "ID: 101" in out


def test_console_sender_with_investigation_evidence():
    buf = io.StringIO()
    sender = ConsoleSender(stream=buf)
    notif = Notification(
        id="notif_2",
        event_id=102,
        channel="console",
        status="pending",
        created_at=_now(),
        payload={
            "event_type": "release_published",
            "importance": "critical",
            "has_investigation": True,
            "investigation": {
                "summary": "Verified tag v1.0",
                "evidence_preview": [{"evidence_type": "git_tag", "payload": {"tag": "v1.0"}}],
            },
        },
    )

    res = sender.send(notif)
    assert res.success is True
    out = buf.getvalue()
    assert "CRITICAL" in out
    assert "Verified tag v1.0" in out
    assert "[git_tag]" in out


@patch("urllib.request.urlopen")
def test_webhook_sender_success(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.getcode.return_value = 200
    mock_resp.read.return_value = b"{\"ok\": true}"
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    sender = WebhookSender(webhook_url="https://api.example.com/webhook")
    notif = Notification(id="notif_3", event_id=103, channel="webhook", status="pending", created_at=_now(), payload={"k": "v"})

    res = sender.send(notif)
    assert res.success is True
    assert res.status_code == 200
    assert "ok" in (res.response_body or "")


@patch("urllib.request.urlopen")
def test_webhook_sender_http_error(mock_urlopen):
    mock_urlopen.side_effect = urllib.error.HTTPError(
        url="https://api.example.com/webhook",
        code=500,
        msg="Internal Server Error",
        hdrs={},
        fp=io.BytesIO(b"error"),
    )

    sender = WebhookSender(webhook_url="https://api.example.com/webhook")
    notif = Notification(id="notif_4", event_id=104, channel="webhook", status="pending", created_at=_now(), payload={})

    res = sender.send(notif)
    assert res.success is False
    assert res.status_code == 500
    assert "HTTPError" in (res.error_message or "")


@patch("urllib.request.urlopen")
def test_webhook_sender_network_timeout(mock_urlopen):
    mock_urlopen.side_effect = TimeoutError("Connection timed out")

    sender = WebhookSender(webhook_url="https://api.example.com/webhook", timeout=1.0)
    notif = Notification(id="notif_5", event_id=105, channel="webhook", status="pending", created_at=_now(), payload={})

    res = sender.send(notif)
    assert res.success is False
    assert "NetworkError" in (res.error_message or "")


def test_webhook_sender_missing_url():
    sender = WebhookSender(webhook_url="")
    notif = Notification(id="notif_6", event_id=106, channel="webhook", status="pending", created_at=_now(), payload={})
    res = sender.send(notif)
    assert res.success is False
    assert "URL is not configured" in (res.error_message or "")
