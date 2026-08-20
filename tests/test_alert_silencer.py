from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from web_watcher.models import Notification
from web_watcher.alert_silencer import AlertSilencer, SilencingRule
from web_watcher.channel_senders import BaseChannelSender, DeliveryResult
from web_watcher.notification_dispatcher import NotificationDispatcher


def _make_notification(entity_id="page_pricing", event_type="dom_diff", channel="slack") -> Notification:
    return Notification(
        id=101,
        event_id=202,
        channel=channel,
        status="pending",
        created_at=datetime.now(timezone.utc),
        payload={
            "entity_id": entity_id,
            "event_type": event_type,
            "title": "Pricing Modified",
            "body": "DOM changed in pricing table",
        },
    )


def test_first_notification_not_silenced():
    silencer = AlertSilencer(default_cooldown_seconds=300)
    ntf = _make_notification()
    silenced, reason = silencer.should_silence(ntf)
    assert silenced is False
    assert reason is None


def test_subsequent_notification_within_window_silenced():
    silencer = AlertSilencer(default_cooldown_seconds=300)
    ntf = _make_notification()
    now = datetime.now(timezone.utc)

    silencer.record_dispatch(ntf, now=now)
    silenced, reason = silencer.should_silence(ntf, now=now + timedelta(seconds=60))

    assert silenced is True
    assert "remaining: 240s" in reason


def test_notification_after_cooldown_allowed():
    silencer = AlertSilencer(default_cooldown_seconds=300)
    ntf = _make_notification()
    now = datetime.now(timezone.utc)

    silencer.record_dispatch(ntf, now=now)
    silenced, _ = silencer.should_silence(ntf, now=now + timedelta(seconds=301))

    assert silenced is False


def test_different_entities_isolated():
    silencer = AlertSilencer(default_cooldown_seconds=300)
    ntf1 = _make_notification(entity_id="page_pricing")
    ntf2 = _make_notification(entity_id="page_terms")
    now = datetime.now(timezone.utc)

    silencer.record_dispatch(ntf1, now=now)
    silenced, _ = silencer.should_silence(ntf2, now=now + timedelta(seconds=30))

    assert silenced is False


def test_different_channels_isolated():
    silencer = AlertSilencer(default_cooldown_seconds=300)
    ntf_slack = _make_notification(channel="slack")
    ntf_lark = _make_notification(channel="lark")
    now = datetime.now(timezone.utc)

    silencer.record_dispatch(ntf_slack, now=now)
    silenced, _ = silencer.should_silence(ntf_lark, now=now + timedelta(seconds=30))

    assert silenced is False


def test_custom_silencing_rule_matches():
    silencer = AlertSilencer(default_cooldown_seconds=300)
    rule = SilencingRule(rule_id="quick_rule", entity_id="page_quick", cooldown_seconds=60)
    silencer.add_rule(rule)

    ntf = _make_notification(entity_id="page_quick")
    now = datetime.now(timezone.utc)

    silencer.record_dispatch(ntf, now=now)
    # 30 秒内被静音
    silenced, _ = silencer.should_silence(ntf, now=now + timedelta(seconds=30))
    assert silenced is True

    # 65 秒后已过 60s 冷却，允许发送
    silenced, _ = silencer.should_silence(ntf, now=now + timedelta(seconds=65))
    assert silenced is False


def test_dispatcher_suppresses_silenced_notification():
    mock_repo = MagicMock()
    mock_sender = MagicMock(spec=BaseChannelSender)
    mock_sender.send.return_value = DeliveryResult(success=True, status_code=200, response_body="ok")

    silencer = AlertSilencer(default_cooldown_seconds=300)
    dispatcher = NotificationDispatcher(
        repository=mock_repo,
        senders={"slack": mock_sender},
        silencer=silencer,
    )

    ntf = _make_notification()
    now = datetime.now(timezone.utc)
    silencer.record_dispatch(ntf, now=now)

    # 60 秒内应被静默
    result = dispatcher.dispatch_one(ntf)
    assert isinstance(result, DeliveryResult)
    assert result.success is True
    assert result.response_body == "suppressed"
    mock_sender.send.assert_not_called()


def test_dispatcher_records_dispatch_after_success():
    mock_repo = MagicMock()
    mock_sender = MagicMock(spec=BaseChannelSender)
    mock_sender.send.return_value = DeliveryResult(success=True, status_code=200, response_body="ok")

    silencer = AlertSilencer(default_cooldown_seconds=300)
    dispatcher = NotificationDispatcher(
        repository=mock_repo,
        senders={"slack": mock_sender},
        silencer=silencer,
    )

    ntf = _make_notification()
    result = dispatcher.dispatch_one(ntf)
    assert isinstance(result, DeliveryResult)
    assert result.success is True
    mock_sender.send.assert_called_once()

    # 立即再次发送应在冷却期内被静默
    result2 = dispatcher.dispatch_one(ntf)
    assert isinstance(result2, DeliveryResult)
    assert result2.success is True
    assert result2.response_body == "suppressed"
    assert mock_sender.send.call_count == 1
