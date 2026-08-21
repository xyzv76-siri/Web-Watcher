"""Unit tests for Notification Channel Senders (Phase 12-A)."""

import io
import json
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import urllib.error
import pytest

from web_watcher.channel_senders import ConsoleSender, DeliveryResult, WebhookSender, TelegramSender, DiscordSender
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


@patch("urllib.request.urlopen")
def test_telegram_sender_success(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.getcode.return_value = 200
    mock_resp.read.return_value = b'{"ok": true, "result": {"message_id": 1}}'
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    sender = TelegramSender(bot_token=os.getenv("TEST_TELEGRAM_BOT_TOKEN", "TOKEN"), chat_id=os.getenv("TEST_TELEGRAM_CHAT_ID", "CHAT"))
    notif = Notification(id="notif_7", event_id=107, channel="telegram", status="pending", created_at=_now(), payload={"event_type": "content_change", "importance": "important"})

    res = sender.send(notif)
    assert res.success is True
    assert res.status_code == 200
    assert "ok" in (res.response_body or "")


@patch("urllib.request.urlopen")
def test_telegram_sender_http_error(mock_urlopen):
    mock_urlopen.side_effect = urllib.error.HTTPError(
        url="https://api.telegram.org/botTOKEN/sendMessage",
        code=401,
        msg="Unauthorized",
        hdrs={},
        fp=io.BytesIO(b"error"),
    )

    sender = TelegramSender(bot_token=os.getenv("TEST_TELEGRAM_BOT_TOKEN", "TOKEN"), chat_id=os.getenv("TEST_TELEGRAM_CHAT_ID", "CHAT"))
    notif = Notification(id="notif_8", event_id=108, channel="telegram", status="pending", created_at=_now(), payload={})

    res = sender.send(notif)
    assert res.success is False
    assert res.status_code == 401
    assert "HTTPError" in (res.error_message or "")


@patch("urllib.request.urlopen")
def test_telegram_sender_network_error(mock_urlopen):
    mock_urlopen.side_effect = TimeoutError("Connection timed out")

    sender = TelegramSender(bot_token=os.getenv("TEST_TELEGRAM_BOT_TOKEN", "TOKEN"), chat_id=os.getenv("TEST_TELEGRAM_CHAT_ID", "CHAT"))
    notif = Notification(id="notif_9", event_id=109, channel="telegram", status="pending", created_at=_now(), payload={})

    res = sender.send(notif)
    assert res.success is False
    assert "NetworkError" in (res.error_message or "")


@patch("urllib.request.urlopen")
def test_discord_sender_success(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.getcode.return_value = 204
    mock_resp.read.return_value = b""
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    sender = DiscordSender(webhook_url="https://discord.com/api/webhooks/123/abc")
    notif = Notification(id="notif_10", event_id=110, channel="discord", status="pending", created_at=_now(), payload={"event_type": "release_published", "importance": "critical"})

    res = sender.send(notif)
    assert res.success is True
    assert res.status_code == 204


@patch("urllib.request.urlopen")
def test_discord_sender_invalid_webhook(mock_urlopen):
    mock_urlopen.side_effect = urllib.error.HTTPError(
        url="https://discord.com/api/webhooks/123/abc",
        code=404,
        msg="Not Found",
        hdrs={},
        fp=io.BytesIO(b"error"),
    )

    sender = DiscordSender(webhook_url="https://discord.com/api/webhooks/123/abc")
    notif = Notification(id="notif_11", event_id=111, channel="discord", status="pending", created_at=_now(), payload={})

    res = sender.send(notif)
    assert res.success is False
    assert res.status_code == 404
    assert "HTTPError" in (res.error_message or "")


@patch("urllib.request.urlopen")
def test_discord_sender_content_truncation(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.getcode.return_value = 204
    mock_resp.read.return_value = b""
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    sender = DiscordSender(webhook_url="https://discord.com/api/webhooks/123/abc")
    long_text = "x" * 5000
    notif = Notification(id="notif_12", event_id=112, channel="discord", status="pending", created_at=_now(), payload={"event_type": "content_change", "importance": "important"})

    res = sender.send(notif)
    assert res.success is True
    called_request = mock_urlopen.call_args[0][0]
    body = json.loads(called_request.data.decode("utf-8"))
    embed_description = body["embeds"][0]["description"]
    assert len(embed_description) <= 4000
