from datetime import datetime, timezone
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass
import difflib
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

    def __init__(
        self,
        default_cooldown_seconds: float = 300.0,
        similarity_threshold: float = 0.85,
        max_similar_history: int = 20,
    ):
        self.default_cooldown_seconds = default_cooldown_seconds
        self.similarity_threshold = similarity_threshold
        self.max_similar_history = max_similar_history
        self.rules: List[SilencingRule] = []
        self._dispatch_history: Dict[str, datetime] = {}
        self._similarity_history: Dict[str, List[Tuple[datetime, str, str]]] = {}

    def add_rule(self, rule: SilencingRule) -> None:
        self.rules.append(rule)

    def _get_key(self, notification: Notification) -> str:
        payload = notification.payload or {}
        entity_id = str(payload.get("entity_id") or getattr(notification, "entity_id", "") or "global")
        event_type = str(payload.get("event_type") or getattr(notification, "event_type", "") or "change")
        channel = (notification.channel or "").strip().lower()
        return f"{entity_id}:{event_type}:{channel}"

    def _normalize_now(self, now: Optional[datetime]) -> datetime:
        if now is None:
            return datetime.now(timezone.utc)
        if now.tzinfo is None:
            return now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc)

    def _content_text(self, notification: Notification) -> str:
        payload = notification.payload or {}
        title = str(payload.get("title", "") or getattr(notification, "title", "") or "")
        body = str(payload.get("body", "") or getattr(notification, "body", "") or "")
        extra = " ".join(
            str(payload.get(k, ""))
            for k in ("entity_id", "event_type", "target_id")
            if payload.get(k)
        )
        return "\n".join(part for part in (title, body, extra) if part)

    def _similarity(self, a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        return difflib.SequenceMatcher(None, a, b).ratio()

    def should_silence_by_similarity(self, notification: Notification, now: Optional[datetime] = None) -> Tuple[bool, Optional[str]]:
        now = self._normalize_now(now)
        key = self._get_key(notification)
        current_text = self._content_text(notification)
        history = self._similarity_history.get(key, [])

        for past_time, past_text, _ in history:
            if past_text == current_text:
                # Identical content is handled by cooldown; similarity check
                # should only suppress near-duplicates, not exact repeats.
                continue
            if self._similarity(current_text, past_text) >= self.similarity_threshold:
                age = (now - past_time).total_seconds()
                reason = (
                    f"Alert silenced as similar to a recent notification "
                    f"(similarity >= {self.similarity_threshold:.2f}, age: {age:.0f}s)"
                )
                return True, reason

        history.append((now, current_text, current_text))
        if len(history) > self.max_similar_history:
            del history[: len(history) - self.max_similar_history]
        self._similarity_history[key] = history
        return False, None

    def should_silence(self, notification: Notification, now: Optional[datetime] = None) -> Tuple[bool, Optional[str]]:
        now = self._normalize_now(now)
        key = self._get_key(notification)
        last_time = self._dispatch_history.get(key)

        if not last_time:
            # First dispatch for this key; only similarity-check if there is history.
            silent, reason = self.should_silence_by_similarity(notification, now)
            return silent, reason

        # Match the most specific silencing rule first.
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

        # Cooldown passed; still check similarity against recent notifications.
        silent, reason = self.should_silence_by_similarity(notification, now)
        return silent, reason

    def record_dispatch(self, notification: Notification, now: Optional[datetime] = None) -> None:
        now = self._normalize_now(now)
        key = self._get_key(notification)
        self._dispatch_history[key] = now
        text = self._content_text(notification)
        history = self._similarity_history.setdefault(key, [])
        history.append((now, text, text))
        if len(history) > self.max_similar_history:
            del history[: len(history) - self.max_similar_history]

    def clear(self) -> None:
        self._dispatch_history.clear()
        self._similarity_history.clear()
