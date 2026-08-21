"""Part 2 — Cross-target Data Semantics Ground Truth."""

from datetime import datetime, timezone

from web_watcher.cross_target_correlator import (
    CrossTargetCorrelator,
    CrossTargetGroup,
    CrossTargetRule,
    _MatchedSignal,
)


def test_signal_based_groups_have_correct_metadata():
    rule = CrossTargetRule(
        name="test_signal",
        entity_ids=["a", "b"],
        window_seconds=3600,
        min_signals=2,
        importance_boost="high",
    )
    correlator = CrossTargetCorrelator(rules=[rule])
    now = datetime.now(timezone.utc)
    signals = [
        ("a", "content_change", now, "v1", "fp1", 1, 10),
        ("b", "content_change", now, "v2", "fp2", 2, 20),
    ]
    groups = correlator.evaluate_signals(signals, now=now)
    assert len(groups) == 1
    group = groups[0]
    assert group.correlation_type == "signal_based"
    assert group.rule_name == "test_signal"
    assert group.importance == "high"
    assert all(s.source_type == "signal" for s in group.signals)
    assert all(s.signal_id is not None for s in group.signals)


def test_event_based_groups_do_not_fake_signal_id():
    rule = CrossTargetRule(
        name="test_event",
        entity_ids=["a", "b"],
        window_seconds=3600,
        min_signals=2,
        importance_boost="high",
    )
    correlator = CrossTargetCorrelator(rules=[rule])
    now = datetime.now(timezone.utc)
    events = [
        (101, "a", "content_change", "open", now, now),
        (102, "b", "content_change", "open", now, now),
    ]
    groups = correlator.evaluate_events(events, now=now)
    assert len(groups) == 1
    group = groups[0]
    assert group.correlation_type == "event_based"
    assert group.rule_name == "test_event"
    assert all(s.source_type == "event" for s in group.signals)
    assert all(s.signal_id is None for s in group.signals)
    assert all(s.event_id is not None for s in group.signals)
    assert {s.event_id for s in group.signals} == {101, 102}


def test_event_based_preserves_event_ids_for_traceability():
    rule = CrossTargetRule(
        name="trace",
        entity_ids=["x", "y", "z"],
        window_seconds=7200,
        min_signals=3,
        importance_boost="critical",
    )
    correlator = CrossTargetCorrelator(rules=[rule])
    now = datetime.now(timezone.utc)
    events = [
        (201, "x", "status_change", "open", now, now),
        (202, "y", "status_change", "open", now, now),
        (203, "z", "status_change", "open", now, now),
    ]
    groups = correlator.evaluate_events(events, now=now)
    assert len(groups) == 1
    group = groups[0]
    assert group.correlation_type == "event_based"
    event_ids = [s.event_id for s in group.signals if s.event_id is not None]
    assert set(event_ids) == {201, 202, 203}


def test_window_start_end_are_correct():
    rule = CrossTargetRule(
        name="window",
        entity_ids=["a", "b"],
        window_seconds=3600,
        min_signals=2,
        importance_boost="medium",
    )
    correlator = CrossTargetCorrelator(rules=[rule])
    t1 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2024, 1, 1, 12, 30, 0, tzinfo=timezone.utc)
    signals = [
        ("a", "content_change", t1, "v1", "fp1", 1, 10),
        ("b", "content_change", t2, "v2", "fp2", 2, 20),
    ]
    groups = correlator.evaluate_signals(signals, now=t2)
    assert len(groups) == 1
    assert groups[0].window_start == t1
    assert groups[0].window_end == t2


def test_insufficient_distinct_entities_produces_no_group():
    rule = CrossTargetRule(
        name="dedup",
        entity_ids=["a", "b"],
        window_seconds=3600,
        min_signals=2,
        importance_boost="low",
    )
    correlator = CrossTargetCorrelator(rules=[rule])
    now = datetime.now(timezone.utc)
    signals = [
        ("a", "content_change", now, "v1", "fp1", 1, 10),
        ("a", "content_change", now, "v2", "fp2", 2, 20),
    ]
    groups = correlator.evaluate_signals(signals, now=now)
    assert len(groups) == 0


def test_latest_per_entity_is_selected():
    rule = CrossTargetRule(
        name="latest",
        entity_ids=["a", "b"],
        window_seconds=3600,
        min_signals=2,
        importance_boost="high",
    )
    correlator = CrossTargetCorrelator(rules=[rule])
    t1 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2024, 1, 1, 12, 30, 0, tzinfo=timezone.utc)
    signals = [
        ("a", "content_change", t1, "old", "fp_old", 1, 10),
        ("a", "content_change", t2, "new", "fp_new", 1, 11),
        ("b", "content_change", t2, "v2", "fp2", 2, 20),
    ]
    groups = correlator.evaluate_signals(signals, now=t2)
    assert len(groups) == 1
    values = {s.entity_id: s.value for s in groups[0].signals}
    assert values["a"] == "new"
    assert values["b"] == "v2"
