from unittest.mock import MagicMock, patch

import pytest
import smtplib

from web_watcher.channel_senders import EmailSender, DeliveryResult
from web_watcher.models import Notification, NotificationStatus


def _make_notification() -> Notification:
    return Notification(
        id=1,
        event_id=10,
        channel="email",
        status=NotificationStatus.PENDING,
        created_at="2024-01-01T00:00:00Z",
        payload={
            "event_type": "content_change",
            "importance": "important",
            "has_investigation": True,
            "investigation": {
                "summary": "Test investigation",
                "evidence_preview": [
                    {"evidence_type": "content", "payload": {"before": "a", "after": "b"}}
                ],
            },
        },
    )


def test_email_sender_validates_recipients():
    sender = EmailSender(
        smtp_host="localhost",
        smtp_port=25,
        to_addrs=[],
    )
    result = sender.send(_make_notification())
    assert result.success is False
    assert "recipient addresses" in (result.error_message or "")


@patch("smtplib.SMTP")
def test_email_sender_sends_plain_smtp(mock_smtp_cls):
    mock_server = MagicMock()
    mock_smtp_cls.return_value.__enter__.return_value = mock_server

    sender = EmailSender(
        smtp_host="localhost",
        smtp_port=25,
        from_addr="sender@example.com",
        to_addrs=["recipient@example.com"],
    )
    result = sender.send(_make_notification())

    assert result.success is True
    assert result.status_code == 250
    mock_server.sendmail.assert_called_once()
    args, kwargs = mock_server.sendmail.call_args
    assert args[0] == "sender@example.com"
    assert args[1] == ["recipient@example.com"]
    assert "Subject: [IMPORTANT] Web-Watcher: content_change" in args[2]


@patch("smtplib.SMTP_SSL")
def test_email_sender_sends_ssl(mock_smtp_cls):
    mock_server = MagicMock()
    mock_smtp_cls.return_value.__enter__.return_value = mock_server

    sender = EmailSender(
        smtp_host="smtp.example.com",
        smtp_port=465,
        use_ssl=True,
        from_addr="sender@example.com",
        to_addrs=["recipient@example.com"],
    )
    result = sender.send(_make_notification())

    assert result.success is True
    mock_smtp_cls.assert_called_once_with("smtp.example.com", 465, timeout=10)
    mock_server.sendmail.assert_called_once()


@patch("smtplib.SMTP")
def test_email_sender_sends_with_tls(mock_smtp_cls):
    mock_server = MagicMock()
    mock_smtp_cls.return_value.__enter__.return_value = mock_server

    sender = EmailSender(
        smtp_host="localhost",
        smtp_port=587,
        use_tls=True,
        from_addr="sender@example.com",
        to_addrs=["recipient@example.com"],
    )
    result = sender.send(_make_notification())

    assert result.success is True
    mock_server.starttls.assert_called_once()
    mock_server.sendmail.assert_called_once()


@patch("smtplib.SMTP")
def test_email_sender_handles_smtp_exception(mock_smtp_cls):
    mock_server = MagicMock()
    mock_server.sendmail.side_effect = smtplib.SMTPException("SMTP boom")
    mock_smtp_cls.return_value.__enter__.return_value = mock_server

    sender = EmailSender(
        smtp_host="localhost",
        smtp_port=25,
        from_addr="sender@example.com",
        to_addrs=["recipient@example.com"],
    )
    result = sender.send(_make_notification())

    assert result.success is False
    assert "SMTPError" in (result.error_message or "")
