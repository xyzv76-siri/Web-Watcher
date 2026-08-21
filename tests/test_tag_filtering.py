import pytest
from web_watcher.pipeline_runner import filter_rules_by_tags
from web_watcher.rule_models import WatcherRule, TargetConfig


class FakeRule:
    """Minimal rule stand-in for tag filtering tests."""
    def __init__(self, id: str, tags):
        self.id = id
        self.tags = tags


def test_filter_rules_by_tags_include_or_semantics():
    rules = [
        FakeRule("r1", ["price", "ecommerce"]),
        FakeRule("r2", ["blog"]),
        FakeRule("r3", ["price", "blog"]),
    ]
    filtered, count = filter_rules_by_tags(rules, include_tags=["price"], exclude_tags=[])
    assert [r.id for r in filtered] == ["r1", "r3"]
    assert count == 1


def test_filter_rules_by_tags_exclude_or_semantics():
    rules = [
        FakeRule("r1", ["price", "ecommerce"]),
        FakeRule("r2", ["blog"]),
        FakeRule("r3", ["status", "ops"]),
    ]
    filtered, count = filter_rules_by_tags(rules, include_tags=[], exclude_tags=["status", "ops"])
    assert [r.id for r in filtered] == ["r1", "r2"]
    assert count == 1


def test_filter_rules_by_tags_include_and_exclude_exclude_priority():
    rules = [
        FakeRule("r1", ["price", "status"]),
        FakeRule("r2", ["price", "ecommerce"]),
        FakeRule("r3", ["blog"]),
    ]
    filtered, count = filter_rules_by_tags(rules, include_tags=["price"], exclude_tags=["status"])
    assert [r.id for r in filtered] == ["r2"]
    assert count == 2


def test_filter_rules_by_tags_no_tags_rule_with_include():
    rules = [
        FakeRule("r1", []),
        FakeRule("r2", ["price"]),
    ]
    filtered, count = filter_rules_by_tags(rules, include_tags=["price"], exclude_tags=[])
    assert [r.id for r in filtered] == ["r2"]
    assert count == 1


def test_filter_rules_by_tags_no_tags_rule_with_exclude():
    rules = [
        FakeRule("r1", []),
        FakeRule("r2", ["status"]),
    ]
    filtered, count = filter_rules_by_tags(rules, include_tags=[], exclude_tags=["status"])
    assert [r.id for r in filtered] == ["r1"]
    assert count == 1


def test_filter_rules_by_tags_no_include_no_exclude_returns_all():
    rules = [
        FakeRule("r1", ["price"]),
        FakeRule("r2", ["blog"]),
    ]
    filtered, count = filter_rules_by_tags(rules, include_tags=[], exclude_tags=[])
    assert [r.id for r in filtered] == ["r1", "r2"]
    assert count == 0


def test_filter_rules_by_tags_multiple_include_or():
    rules = [
        FakeRule("r1", ["price"]),
        FakeRule("r2", ["ecommerce"]),
        FakeRule("r3", ["blog"]),
    ]
    filtered, count = filter_rules_by_tags(rules, include_tags=["price", "ecommerce"], exclude_tags=[])
    assert [r.id for r in filtered] == ["r1", "r2"]
    assert count == 1


def test_filter_rules_by_tags_multiple_exclude_or():
    rules = [
        FakeRule("r1", ["price"]),
        FakeRule("r2", ["ecommerce"]),
        FakeRule("r3", ["status"]),
    ]
    filtered, count = filter_rules_by_tags(rules, include_tags=[], exclude_tags=["status", "ops"])
    assert [r.id for r in filtered] == ["r1", "r2"]
    assert count == 1


def test_filter_rules_by_tags_none_tags_attribute():
    """Rules with tags=None should be treated as empty list."""
    rules = [
        FakeRule("r1", None),
        FakeRule("r2", ["price"]),
    ]
    filtered, count = filter_rules_by_tags(rules, include_tags=["price"], exclude_tags=[])
    assert [r.id for r in filtered] == ["r2"]
    assert count == 1


def test_filter_rules_by_tags_empty_rules_list():
    filtered, count = filter_rules_by_tags([], include_tags=["price"], exclude_tags=[])
    assert filtered == []
    assert count == 0
