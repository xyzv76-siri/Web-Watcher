"""Unit tests for specialized channel senders (Phase 13-B)."""

from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from web_watcher.models import Notification
from web_watcher.channel_senders import (
    SlackSender,
    LarkSender,
    DingTalkSender,
    DeliveryResult,
)
from web_watcher.notification_dispatcher import NotificationDispatcher


def _now():
    return datetime.now(timezone.utc).isoformat()


def _make_notification(channel: str, recipient: str = "https://hook.example.com") -> Notification:
    return Notification(
        id=1,
        event_id=10,
        channel=channel,
        status="pending",
        created_at=_now(),
        payload={
            "recipient": recipient,
            "title": "Service Degradation",
            "body": "API latency high",
            "importance": "high",
        },
    )


@patch("urllib.request.urlopen")
def test_slack_sender_posts_blocks(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.getcode.return_value = 200
    mock_resp.read.return_value = b'{"ok": true}'
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    sender = SlackSender()
    ntf = _make_notification(channel="slack")
    result = sender.send(ntf)
    assert isinstance(result, DeliveryResult)
    assert result.success is True
    assert result.status_code == 200

    req = mock_urlopen.call_args[0][0]
    body = req.data.decode("utf-8")
    assert "blocks" in body


@patch("urllib.request.urlopen")
def test_lark_sender_posts_card(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.getcode.return_value = 200
    mock_resp.read.return_value = b'{"ok": true}'
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    sender = LarkSender()
    ntf = _make_notification(channel="lark")
    result = sender.send(ntf)
    assert isinstance(result, DeliveryResult)
    assert result.success is True
    assert result.status_code == 200

    req = mock_urlopen.call_args[0][0]
    body = req.data.decode("utf-8")
    assert "interactive" in body


@patch("urllib.request.urlopen")
def test_dingtalk_sender_posts_markdown(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.getcode.return_value = 200
    mock_resp.read.return_value = b'{"ok": true}'
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    sender = DingTalkSender()
    ntf = _make_notification(channel="dingtalk")
    result = sender.send(ntf)
    assert isinstance(result, DeliveryResult)
    assert result.success is True
    assert result.status_code == 200

    req = mock_urlopen.call_args[0][0]
    body = req.data.decode("utf-8")
    assert "markdown" in body


def test_sender_missing_recipient_returns_false():
    sender = SlackSender()
    ntf = Notification(
        id=2,
        event_id=11,
        channel="slack",
        status="pending",
        created_at=_now(),
        payload={},
    )
    result = sender.send(ntf)
    assert isinstance(result, DeliveryResult)
    assert result.success is False
    assert "Webhook URL is not configured" in (result.error_message or "")


def test_dispatcher_registers_all_specialized_senders():
    mock_repo = MagicMock()
    dispatcher = NotificationDispatcher(
        repository=mock_repo,
        senders={
            "slack": SlackSender(),
            "lark": LarkSender(),
            "feishu": LarkSender(),
            "dingtalk": DingTalkSender(),
        },
    )

    assert isinstance(dispatcher.senders["slack"], SlackSender)
    assert isinstance(dispatcher.senders["lark"], LarkSender)
    assert isinstance(dispatcher.senders["feishu"], LarkSender)
    assert isinstance(dispatcher.senders["dingtalk"], DingTalkSender)


@patch.object(SlackSender, "send", return_value=DeliveryResult(success=True, status_code=200, response_body="ok"))
def test_dispatcher_dispatches_and_marks_delivered(mock_send):
    mock_repo = MagicMock()
    ntf = _make_notification(channel="slack")

    dispatcher = NotificationDispatcher(
        repository=mock_repo,
        default_sender=SlackSender(),
    )
    result = dispatcher.dispatch(ntf)

    assert isinstance(result, DeliveryResult)
    assert result.success is True
    assert mock_send.called
    mock_repo.update_notification_status.assert_called_once()
    args, kwargs = mock_repo.update_notification_status.call_args
    assert kwargs["status"] == "delivered"
