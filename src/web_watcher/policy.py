"""Deterministic policy engine for Web Watcher.

Phase 9 deliberately contains no network access, AI/LLM calls,
Telegram integration, scheduling, or external side effects.
"""

from dataclasses import dataclass
from enum import Enum

from .event_types import EventType
from .models import Event


class Importance(str, Enum):
    IGNORE = "ignore"
    INTERESTING = "interesting"
    IMPORTANT = "important"
    CRITICAL = "critical"


class Action(str, Enum):
    DISCARD = "discard"
    SUMMARIZE = "summarize"
    NOTIFY = "notify"
    INVESTIGATE_AND_NOTIFY = "investigate_and_notify"


@dataclass(frozen=True)
class PolicyDecision:
    importance: Importance
    action: Action
    reason: str


class PolicyEngine:
    """Deterministic baseline policy.

    Phase 9 intentionally uses only event attributes already present
    in the domain model. Future signal-specific rules can be added
    without introducing network or AI dependencies.
    """

    def evaluate(self, event: Event) -> PolicyDecision:
        importance = self._importance(event)

        actions = {
            Importance.IGNORE: Action.DISCARD,
            Importance.INTERESTING: Action.SUMMARIZE,
            Importance.IMPORTANT: Action.NOTIFY,
            Importance.CRITICAL: Action.INVESTIGATE_AND_NOTIFY,
        }

        return PolicyDecision(
            importance=importance,
            action=actions[importance],
            reason=self._reason(event, importance),
        )

    @staticmethod
    def _importance(event: Event) -> Importance:
        """Return the deterministic baseline importance.

        The Phase 8 domain does not yet carry rich signal-specific
        metadata, so unknown events remain safely IGNORE by default.
        """

        if event.event_type in ("critical", EventType.RELEASE_PUBLISHED.value):
            return Importance.CRITICAL

        if event.event_type in ("important", EventType.STARS_CHANGED.value):
            return Importance.IMPORTANT

        if event.event_type in ("interesting", EventType.CONTENT_CHANGE.value):
            return Importance.INTERESTING

        return Importance.IGNORE

    @staticmethod
    def _reason(
        event: Event,
        importance: Importance,
    ) -> str:
        if importance is Importance.CRITICAL:
            return "event classified as critical by deterministic policy"

        if importance is Importance.IMPORTANT:
            return "event classified as important by deterministic policy"

        if importance is Importance.INTERESTING:
            return "event classified as interesting by deterministic policy"

        return f"no Phase 9 rule matched event_type={event.event_type!r}"
