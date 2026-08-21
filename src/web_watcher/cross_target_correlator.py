"""Cross-target correlation: group related signals/events across different entities (Phase 14-C)."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple


@dataclass(frozen=True)
class CrossTargetRule:
    """Defines a cross-target correlation rule.

    A rule matches when signals from distinct entities in ``entity_ids``
    are observed within ``window_seconds`` of each other.
    """

    name: str
    entity_ids: Sequence[str]
    window_seconds: int = 3600
    min_signals: int = 2
    importance_boost: str = "important"

    def __post_init__(self):
        if self.window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        if self.min_signals < 2:
            raise ValueError("min_signals must be >= 2")
        if len(self.entity_ids) < 2:
            raise ValueError("entity_ids must contain at least 2 entities")
        unique = set(self.entity_ids)
        if len(unique) != len(self.entity_ids):
            raise ValueError("entity_ids must be unique")


@dataclass
class _MatchedSignal:
    entity_id: str
    signal_type: str
    observed_at: datetime
    value: Any
    fingerprint: str
    event_id: Optional[int]
    signal_id: Optional[int] = None
    source_type: str = "signal"


@dataclass
class CrossTargetGroup:
    """A correlated group of signals/events across multiple entities."""

    rule_name: str
    entity_ids: Tuple[str, ...]
    signals: Tuple[_MatchedSignal, ...]
    window_start: datetime
    window_end: datetime
    importance: str
    correlation_type: str = "signal_based"

    @property
    def distinct_entity_count(self) -> int:
        return len(set(s.entity_id for s in self.signals))


class CrossTargetCorrelator:
    """Correlates signals/events across different entities using simple rule matching.

    This correlator is intentionally rule-based and does not require AI judgment.
    It is useful for scenarios such as:
    - Monitoring a company's website + GitHub releases together
    - Watching multiple news sources for the same story
    - Correlating status page + support forum changes
    """

    def __init__(self, rules: Optional[Sequence[CrossTargetRule]] = None):
        self.rules = list(rules or [])

    def add_rule(self, rule: CrossTargetRule) -> None:
        self.rules.append(rule)

    def evaluate_signals(
        self,
        signals: Sequence[Tuple[str, str, datetime, Any, str, Optional[int], Optional[int]]],
        now: Optional[datetime] = None,
    ) -> List[CrossTargetGroup]:
        """Evaluate a batch of signals against all registered rules.

        Each signal tuple is: (entity_id, signal_type, observed_at, value, fingerprint, event_id, signal_id)
        Returns list of CrossTargetGroup matches.
        """
        now = now or datetime.now(timezone.utc)
        matched_groups: List[CrossTargetGroup] = []

        for rule in self.rules:
            rule_entities = set(rule.entity_ids)
            window = timedelta(seconds=rule.window_seconds)
            cutoff = now - window

            # Filter signals that belong to rule entities and are within window
            candidates = []
            for sig in signals:
                entity_id, signal_type, observed_at, value, fingerprint, event_id, signal_id = sig
                if entity_id not in rule_entities:
                    continue
                if observed_at < cutoff:
                    continue
                candidates.append(_MatchedSignal(
                    entity_id=entity_id,
                    signal_type=signal_type,
                    observed_at=observed_at,
                    value=value,
                    fingerprint=fingerprint,
                    event_id=event_id,
                    signal_id=signal_id,
                    source_type="signal",
                ))

            # Check if we have enough distinct entities
            distinct_entities = {c.entity_id for c in candidates}
            if len(distinct_entities) < rule.min_signals:
                continue

            # Group by entity and take the most recent signal per entity
            latest_per_entity: Dict[str, _MatchedSignal] = {}
            for c in candidates:
                if c.entity_id not in latest_per_entity or c.observed_at > latest_per_entity[c.entity_id].observed_at:
                    latest_per_entity[c.entity_id] = c

            group_signals = tuple(latest_per_entity.values())
            earliest = min(s.observed_at for s in group_signals)
            latest = max(s.observed_at for s in group_signals)

            matched_groups.append(CrossTargetGroup(
                rule_name=rule.name,
                entity_ids=tuple(sorted(distinct_entities)),
                signals=group_signals,
                window_start=earliest,
                window_end=latest,
                importance=rule.importance_boost,
                correlation_type="signal_based",
            ))

        return matched_groups

    def evaluate_events(
        self,
        events: Sequence[Tuple[int, str, str, str, datetime, datetime]],
        now: Optional[datetime] = None,
    ) -> List[CrossTargetGroup]:
        """Evaluate a batch of events against all registered rules.

        Each event tuple is: (event_id, entity_id, event_type, status, created_at, updated_at)
        Returns list of CrossTargetGroup matches based on event creation time.
        """
        now = now or datetime.now(timezone.utc)
        matched_groups: List[CrossTargetGroup] = []

        for rule in self.rules:
            rule_entities = set(rule.entity_ids)
            window = timedelta(seconds=rule.window_seconds)
            cutoff = now - window

            candidates = []
            for ev in events:
                event_id, entity_id, event_type, status, created_at, updated_at = ev
                if entity_id not in rule_entities:
                    continue
                if created_at < cutoff:
                    continue
                candidates.append(_MatchedSignal(
                    entity_id=entity_id,
                    signal_type=event_type,
                    observed_at=created_at,
                    value=None,
                    fingerprint=f"event:{event_id}",
                    event_id=event_id,
                    signal_id=None,
                    source_type="event",
                ))

            distinct_entities = {c.entity_id for c in candidates}
            if len(distinct_entities) < rule.min_signals:
                continue

            latest_per_entity: Dict[str, _MatchedSignal] = {}
            for c in candidates:
                if c.entity_id not in latest_per_entity or c.observed_at > latest_per_entity[c.entity_id].observed_at:
                    latest_per_entity[c.entity_id] = c

            group_signals = tuple(latest_per_entity.values())
            earliest = min(s.observed_at for s in group_signals)
            latest = max(s.observed_at for s in group_signals)

            matched_groups.append(CrossTargetGroup(
                rule_name=rule.name,
                entity_ids=tuple(sorted(distinct_entities)),
                signals=group_signals,
                window_start=earliest,
                window_end=latest,
                importance=rule.importance_boost,
                correlation_type="event_based",
            ))

        return matched_groups
