from datetime import datetime, timedelta, timezone

import pytest

from web_watcher.cross_target_correlator import (
    CrossTargetCorrelator,
    CrossTargetGroup,
    CrossTargetRule,
)


def _now() -> datetime:
    return datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _ts(hours: float) -> datetime:
    return _now() - timedelta(hours=hours)


def test_empty_rules_returns_no_groups():
    correlator = CrossTargetCorrelator()
    signals = [
        ("target-a", "content_change", _ts(0.5), "x", "fp1", 1, 101),
        ("target-b", "content_change", _ts(0.5), "y", "fp2", 2, 102),
    ]
    groups = correlator.evaluate_signals(signals, now=_now())
    assert groups == []


def test_signals_match_cross_target_rule():
    rule = CrossTargetRule(
        name="site_and_repo",
        entity_ids=["target-a", "target-b"],
        window_seconds=3600,
        min_signals=2,
    )
    correlator = CrossTargetCorrelator(rules=[rule])
    signals = [
        ("target-a", "content_change", _ts(0.5), "x", "fp1", 1, 101),
        ("target-b", "content_change", _ts(0.5), "y", "fp2", 2, 102),
    ]
    groups = correlator.evaluate_signals(signals, now=_now())
    assert len(groups) == 1
    g = groups[0]
    assert g.rule_name == "site_and_repo"
    assert g.distinct_entity_count == 2
    assert set(g.entity_ids) == {"target-a", "target-b"}
    assert {s.signal_id for s in g.signals} == {101, 102}


def test_signals_outside_window_do_not_match():
    rule = CrossTargetRule(
        name="site_and_repo",
        entity_ids=["target-a", "target-b"],
        window_seconds=3600,
        min_signals=2,
    )
    correlator = CrossTargetCorrelator(rules=[rule])
    signals = [
        ("target-a", "content_change", _ts(0.5), "x", "fp1", 1, 101),
        ("target-b", "content_change", _ts(2.0), "y", "fp2", 2, 102),
    ]
    groups = correlator.evaluate_signals(signals, now=_now())
    assert groups == []


def test_insufficient_distinct_entities_do_not_match():
    rule = CrossTargetRule(
        name="site_and_repo",
        entity_ids=["target-a", "target-b"],
        window_seconds=3600,
        min_signals=2,
    )
    correlator = CrossTargetCorrelator(rules=[rule])
    signals = [
        ("target-a", "content_change", _ts(0.5), "x", "fp1", 1, 101),
        ("target-a", "content_change", _ts(0.5), "y", "fp2", 2, 102),
    ]
    groups = correlator.evaluate_signals(signals, now=_now())
    assert groups == []


def test_min_signals_greater_than_two_requires_more_entities():
    rule = CrossTargetRule(
        name="triple",
        entity_ids=["a", "b", "c"],
        window_seconds=3600,
        min_signals=3,
    )
    correlator = CrossTargetCorrelator(rules=[rule])
    signals = [
        ("a", "content_change", _ts(0.5), "x", "fp1", 1, 101),
        ("b", "content_change", _ts(0.5), "y", "fp2", 2, 102),
    ]
    groups = correlator.evaluate_signals(signals, now=_now())
    assert groups == []

    signals.append(("c", "content_change", _ts(0.5), "z", "fp3", 3, 103))
    groups = correlator.evaluate_signals(signals, now=_now())
    assert len(groups) == 1
    assert groups[0].distinct_entity_count == 3
    assert {s.signal_id for s in groups[0].signals} == {101, 102, 103}


def test_extra_entities_outside_rule_are_ignored():
    rule = CrossTargetRule(
        name="site_and_repo",
        entity_ids=["target-a", "target-b"],
        window_seconds=3600,
        min_signals=2,
    )
    correlator = CrossTargetCorrelator(rules=[rule])
    signals = [
        ("target-a", "content_change", _ts(0.5), "x", "fp1", 1, 101),
        ("target-b", "content_change", _ts(0.5), "y", "fp2", 2, 102),
        ("target-c", "content_change", _ts(0.5), "z", "fp3", 3, 103),
    ]
    groups = correlator.evaluate_signals(signals, now=_now())
    assert len(groups) == 1
    assert set(groups[0].entity_ids) == {"target-a", "target-b"}
    assert {s.signal_id for s in groups[0].signals} == {101, 102}


def test_event_based_correlation():
    rule = CrossTargetRule(
        name="site_and_repo",
        entity_ids=["target-a", "target-b"],
        window_seconds=3600,
        min_signals=2,
    )
    correlator = CrossTargetCorrelator(rules=[rule])
    events = [
        (1, "target-a", "content_change", "open", _ts(0.5), _ts(0.5)),
        (2, "target-b", "content_change", "open", _ts(0.5), _ts(0.5)),
    ]
    groups = correlator.evaluate_events(events, now=_now())
    assert len(groups) == 1
    assert groups[0].distinct_entity_count == 2
    assert groups[0].signals[0].event_id == 1
    assert groups[0].signals[1].event_id == 2
