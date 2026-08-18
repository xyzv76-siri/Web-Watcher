from datetime import datetime
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass
from web_watcher.models import Notification


@dataclass
class SilencingRule:
    rule_id: str
    entity_id: Optional[str] = None
    event_type: Optional[str] = None
    channel: Optional[str] = None
    cooldown_seconds: float = 300.0  # 默认 5 分钟冷却
    description: str = ""

    def matches(self, notification: Notification) -> bool:
        payload = notification.payload or {}
        notif_entity = str(payload.get("entity_id") or getattr(notification, "entity_id", "") or "")
        notif_event_type = str(payload.get("event_type") or getattr(notification, "event_type", "") or "")
        notif_channel = (notification.channel or "").strip().lower()

        if self.entity_id and self.entity_id != "*" and self.entity_id != notif_entity:
            return False
        if self.event_type and self.event_type != "*" and self.event_type != notif_event_type:
            return False
        if self.channel and self.channel != "*" and self.channel.lower() != notif_channel:
            return False
        return True


class AlertSilencer:
    """网页变更降噪与冷却管理器：防止同一页面高频微调引发通知风暴"""

    def __init__(self, default_cooldown_seconds: float = 300.0):
        self.default_cooldown_seconds = default_cooldown_seconds
        self.rules: List[SilencingRule] = []
        self._dispatch_history: Dict[str, datetime] = {}

    def add_rule(self, rule: SilencingRule) -> None:
        self.rules.append(rule)

    def _get_key(self, notification: Notification) -> str:
        payload = notification.payload or {}
        entity_id = str(payload.get("entity_id") or getattr(notification, "entity_id", "") or "global")
        event_type = str(payload.get("event_type") or getattr(notification, "event_type", "") or "change")
        channel = (notification.channel or "").strip().lower()
        return f"{entity_id}:{event_type}:{channel}"

    def should_silence(self, notification: Notification, now: Optional[datetime] = None) -> Tuple[bool, Optional[str]]:
        now = now or datetime.utcnow()
        key = self._get_key(notification)
        last_time = self._dispatch_history.get(key)

        if not last_time:
            return False, None

        # 匹配优先级最高的特定规则，未匹配则使用默认冷却时长
        cooldown = self.default_cooldown_seconds
        for rule in self.rules:
            if rule.matches(notification):
                cooldown = rule.cooldown_seconds
                break

        elapsed = (now - last_time).total_seconds()
        if elapsed < cooldown:
            remaining = int(cooldown - elapsed)
            reason = f"Alert silenced by cooldown window (remaining: {remaining}s / {int(cooldown)}s)"
            return True, reason

        return False, None

    def record_dispatch(self, notification: Notification, now: Optional[datetime] = None) -> None:
        now = now or datetime.utcnow()
        key = self._get_key(notification)
        self._dispatch_history[key] = now

    def clear(self) -> None:
        self._dispatch_history.clear()
