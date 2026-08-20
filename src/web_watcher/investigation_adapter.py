"""Event-to-Investigation Adapter (Phase 11A Bridge Layer).

Bridges upstream Domain Events with the autonomous Investigation subsystem
without modifying frozen investigation contracts.
"""

from typing import Any, Dict, Optional, Union
from .event_status import EventStatus
from .event_types import EventType
from .importance import Importance
from .investigation_contract import InvestigationPolicy, InvestigationTask
from .investigation_engine import Engine
from .investigation_planner import Planner
from .investigation_result import InvestigationResult
from .models import Event


class EventInvestigationAdapter:
    """Adapts Domain Events to Investigation plans, policies, and executions."""

    def __init__(
        self,
        min_importance: Importance = Importance.IMPORTANT,
        default_timeout_seconds: float = 30.0,
        max_steps: int = 3,
    ):
        self.min_importance = min_importance
        self.default_timeout_seconds = default_timeout_seconds
        self.max_steps = max_steps

    def is_eligible(self, event: Event) -> bool:
        """Determines if an event meets criteria for autonomous investigation.

        Default rule: Event must be OPEN and meet or exceed min_importance threshold.
        """
        if event.status != EventStatus.OPEN:
            return False

        imp = event.importance
        if isinstance(imp, str):
            try:
                imp = Importance.from_value(imp)
            except ValueError:
                return False

        if self.min_importance == Importance.CRITICAL:
            return imp == Importance.CRITICAL
        if self.min_importance == Importance.IMPORTANT:
            return imp in (Importance.IMPORTANT, Importance.CRITICAL)
        if self.min_importance == Importance.INTERESTING:
            return imp in (Importance.INTERESTING, Importance.IMPORTANT, Importance.CRITICAL)
        return True

    def resolve_task_type(self, event: Event) -> InvestigationTask:
        """Maps EventType to the most relevant InvestigationTask enum member."""
        members = {m.name: m for m in InvestigationTask}

        evt_type = event.event_type
        evt_val = evt_type.value if isinstance(evt_type, EventType) else str(evt_type)

        if evt_val == EventType.CONTENT_CHANGE.value:
            for candidate in ("DIFF_ANALYSIS", "CONTENT_INSPECTION", "INSPECTION", "DEFAULT"):
                if candidate in members:
                    return members[candidate]
        elif evt_val == EventType.STARS_CHANGED.value:
            for candidate in ("TREND_ANALYSIS", "GROWTH_ANALYSIS", "ANOMALY_DETECTION", "INSPECTION"):
                if candidate in members:
                    return members[candidate]
        elif evt_val == EventType.RELEASE_PUBLISHED.value:
            for candidate in ("RELEASE_ANALYSIS", "SUMMARY", "FACT_CHECK", "INSPECTION"):
                if candidate in members:
                    return members[candidate]

        # Universal fallback to first available task enum member
        return list(InvestigationTask)[0]

    def build_policy(
        self,
        event: Event,
        timeout_seconds: Optional[float] = None,
        max_steps: Optional[int] = None,
    ) -> InvestigationPolicy:
        """Formulates an InvestigationPolicy scaled by event importance."""
        timeout = timeout_seconds or self.default_timeout_seconds
        steps = max_steps or self.max_steps

        if event.importance == Importance.CRITICAL:
            timeout *= 1.5

        try:
            return InvestigationPolicy(timeout_seconds=timeout, max_steps=steps)
        except TypeError:
            try:
                return InvestigationPolicy(max_steps=steps)
            except TypeError:
                return InvestigationPolicy()

    def build_context(self, event: Event) -> Dict[str, Any]:
        """Extracts standardized payload parameters from Event to seed investigation."""
        return {
            "event_id": event.id,
            "entity_id": event.entity_id,
            "event_type": event.event_type.value if isinstance(event.event_type, EventType) else str(event.event_type),
            "importance": event.importance.value if isinstance(event.importance, Importance) else str(event.importance),
            "created_at": event.created_at,
            "updated_at": event.updated_at,
        }

    def run_for_event(
        self,
        event: Event,
        planner: Any,
        engine: Any,
    ) -> Optional[Any]:
        """Executes full investigation flow if the event is eligible."""
        if not self.is_eligible(event):
            return None

        if planner is None or engine is None:
            return None

        task = self.resolve_task_type(event)
        policy = self.build_policy(event)
        context = self.build_context(event)

        try:
            plan = planner.plan(task=task, policy=policy, context=context)
        except TypeError:
            plan = planner.plan(task=task, context=context)

        try:
            return engine.execute(plan=plan, policy=policy, context=context)
        except TypeError:
            return engine.execute(plan=plan)
