"""Unit tests for Card and Template Formatters (Phase 13-A)."""

from datetime import datetime
import pytest
from web_watcher.card_formatters import (
    MarkdownFormatter,
    SlackBlockKitFormatter,
    LarkCardFormatter,
    DingTalkCardFormatter,
    get_card_formatter,
    get_importance_badge,
)
from web_watcher.models import Notification


def _make_notification(**kwargs):
    payload = {
        "event_type": "stars_changed",
        "importance": "critical",
        "has_investigation": True,
        "investigation": {
            "status": "completed",
            "summary": "Stars increased by 500 within 1h",
            "evidence_preview": [
                {"evidence_type": "star_burst", "payload": {"delta": 500}},
            ],
        },
    }
    payload.update(kwargs.pop("payload", {}))
    defaults = {
        "id": "notif_card_1",
        "event_id": 888,
        "channel": "webhook",
        "status": "pending",
        "created_at": datetime.utcnow(),
        "payload": payload,
    }
    defaults.update(kwargs)
    return Notification(**defaults)


def test_markdown_formatter_plain_and_investigation():
    formatter = MarkdownFormatter()

    plain = _make_notification(payload={
        "title": "Stars Changed",
        "importance": "important",
        "has_investigation": False,
    })
    # Remove investigation data for plain test
    plain.payload.pop("investigation", None)
    plain_res = formatter.format(plain)
    assert "Stars Changed" in plain_res["text"]
    assert "Investigation Summary" not in plain_res["text"]

    inv = _make_notification(payload={
        "title": "Stars Changed",
        "importance": "critical",
        "has_investigation": True,
    })
    inv_res = formatter.format(inv)
    assert "Investigation Summary" in inv_res["text"]
    assert "Stars increased by 500" in inv_res["text"]


def test_slack_block_kit_formatter_structure():
    formatter = SlackBlockKitFormatter()
    notif = _make_notification()
    res = formatter.format(notif)

    assert "blocks" in res
    blocks = res["blocks"]
    assert blocks[0]["type"] == "header"
    assert "Event #888" in blocks[0]["text"]["text"]
    assert blocks[1]["type"] == "section"
    assert blocks[1]["text"]["type"] == "mrkdwn"


def test_lark_card_formatter_includes_investigation():
    formatter = LarkCardFormatter()
    notif = _make_notification()
    res = formatter.format(notif)

    assert res["msg_type"] == "interactive"
    assert "header" in res["card"]
    assert "elements" in res["card"]
    body = "".join(
        el["text"]["content"] if "text" in el else ""
        for el in res["card"]["elements"]
        if isinstance(el, dict)
    )
    assert "No description provided." in body


def test_dingtalk_formatter_uses_markdown_fallback():
    formatter = DingTalkCardFormatter()
    notif = _make_notification()
    res = formatter.format(notif)

    assert res["msgtype"] == "markdown"
    assert "markdown" in res
    md = res["markdown"]
    assert "Event #888" in md["text"]


def test_get_card_formatter_factory_mapping():
    assert isinstance(get_card_formatter("slack"), SlackBlockKitFormatter)
    assert isinstance(get_card_formatter("lark"), LarkCardFormatter)
    assert isinstance(get_card_formatter("feishu"), LarkCardFormatter)
    assert isinstance(get_card_formatter("dingtalk"), DingTalkCardFormatter)
    assert isinstance(get_card_formatter("webhook"), MarkdownFormatter)
    assert isinstance(get_card_formatter("unknown"), MarkdownFormatter)


def test_formatter_factory_case_insensitive():
    assert isinstance(get_card_formatter("SLACK"), SlackBlockKitFormatter)
    assert isinstance(get_card_formatter(" Lark "), LarkCardFormatter)
    assert isinstance(get_card_formatter(""), MarkdownFormatter)


def test_get_importance_badge_mapping():
    assert get_importance_badge("critical") == "🔴"
    assert get_importance_badge("high") == "🔴"
    assert get_importance_badge("medium") == "🟡"
    assert get_importance_badge("low") == "🔵"
    assert get_importance_badge("") == "🔵"
