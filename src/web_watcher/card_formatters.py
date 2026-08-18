from abc import ABC, abstractmethod
from typing import Dict, Any
from web_watcher.models import Notification


def get_importance_badge(importance: str) -> str:
    level = (importance or "").lower()
    if level in ("critical", "high", "p0", "p1"):
        return "🔴"
    if level in ("medium", "p2"):
        return "🟡"
    return "🔵"


class BaseCardFormatter(ABC):
    @abstractmethod
    def format(self, notification: Notification) -> Dict[str, Any]:
        raise NotImplementedError


class MarkdownFormatter(BaseCardFormatter):
    def format(self, notification: Notification) -> Dict[str, Any]:
        payload = notification.payload or {}
        title = payload.get("title", f"Event #{notification.event_id}")
        body = payload.get("body", "No description provided.")
        importance = str(payload.get("importance", "info"))
        badge = get_importance_badge(importance)

        lines = [
            f"## {badge} {title}",
            "",
            body,
            "",
            f"- **Event ID**: `{notification.event_id}`",
            f"- **Channel**: `{notification.channel}`",
        ]

        if "score" in payload:
            lines.append(f"- **Score**: `{payload['score']}`")

        inv = payload.get("investigation")
        if inv and isinstance(inv, dict):
            lines.extend(["", "### Investigation Summary", f"- **Status**: `{inv.get('status', 'N/A')}`"])
            if "summary" in inv:
                lines.append(f"- **Details**: {inv['summary']}")

        return {"text": "\n".join(lines)}


class SlackBlockKitFormatter(BaseCardFormatter):
    def format(self, notification: Notification) -> Dict[str, Any]:
        payload = notification.payload or {}
        title = payload.get("title", f"Event #{notification.event_id}")
        body = payload.get("body", "No description provided.")
        importance = str(payload.get("importance", "info"))
        badge = get_importance_badge(importance)

        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": f"{badge} {title}"[:150], "emoji": True}},
            {"type": "section", "text": {"type": "mrkdwn", "text": body}},
        ]

        fields = [
            {"type": "mrkdwn", "text": f"*Event ID:*\n`{notification.event_id}`"},
            {"type": "mrkdwn", "text": f"*Channel:*\n`{notification.channel}`"},
        ]
        if "score" in payload:
            fields.append({"type": "mrkdwn", "text": f"*Score:*\n{payload['score']}"})

        blocks.append({"type": "section", "fields": fields})
        return {"blocks": blocks}


class LarkCardFormatter(BaseCardFormatter):
    def format(self, notification: Notification) -> Dict[str, Any]:
        payload = notification.payload or {}
        title = payload.get("title", f"Event #{notification.event_id}")
        body = payload.get("body", "No description provided.")
        importance = str(payload.get("importance", "info")).lower()
        template_color = "red" if importance in ("critical", "high") else "blue"

        elements = [
            {"tag": "div", "text": {"tag": "lark_md", "content": body}},
            {"tag": "note", "elements": [{"tag": "plain_text", "content": f"Event ID: {notification.event_id} | Channel: {notification.channel}"}]},
        ]
        return {
            "msg_type": "interactive",
            "card": {
                "header": {"template": template_color, "title": {"tag": "plain_text", "content": title}},
                "elements": elements,
            },
        }


class DingTalkCardFormatter(BaseCardFormatter):
    def format(self, notification: Notification) -> Dict[str, Any]:
        payload = notification.payload or {}
        title = payload.get("title", f"Event #{notification.event_id}")
        body = payload.get("body", "No description provided.")
        badge = get_importance_badge(str(payload.get("importance", "info")))

        lines = [f"### {badge} {title}", "", body, "", f"**Event ID**: `{notification.event_id}`"]
        return {"msgtype": "markdown", "markdown": {"title": title, "text": "\n".join(lines)}}


def get_card_formatter(channel: str) -> BaseCardFormatter:
    ch = (channel or "").strip().lower()
    if ch == "slack":
        return SlackBlockKitFormatter()
    if ch in ("lark", "feishu"):
        return LarkCardFormatter()
    if ch == "dingtalk":
        return DingTalkCardFormatter()
    return MarkdownFormatter()
