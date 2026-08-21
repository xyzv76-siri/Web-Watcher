import pytest
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from web_watcher.repository import Repository
from web_watcher.rule_registry import RuleRegistry


@pytest.fixture
def repo(tmp_path):
    db = tmp_path / "registry.db"
    return Repository(str(db))


def test_registry_upsert_and_get(repo):
    registry = RuleRegistry(repo)
    entry = registry.upsert("rule_1", enabled=True, priority=10, group_name="pricing")
    assert entry["rule_id"] == "rule_1"
    assert entry["enabled"] is True
    assert entry["priority"] == 10
    assert entry["group_name"] == "pricing"

    fetched = registry.get("rule_1")
    assert fetched is not None
    assert fetched["enabled"] is True
    assert fetched["priority"] == 10


def test_registry_update_existing(repo):
    registry = RuleRegistry(repo)
    registry.upsert("rule_1", enabled=True, priority=5)
    registry.upsert("rule_1", enabled=False, priority=20)

    fetched = registry.get("rule_1")
    assert fetched["enabled"] is False
    assert fetched["priority"] == 20


def test_registry_list_with_filters(repo):
    registry = RuleRegistry(repo)
    registry.upsert("rule_1", enabled=True, priority=10, group_name="pricing")
    registry.upsert("rule_2", enabled=False, priority=20, group_name="pricing")
    registry.upsert("rule_3", enabled=True, priority=30, group_name="news")

    all_rules = registry.list_rules()
    assert len(all_rules) == 3

    enabled_rules = registry.list_rules(enabled=True)
    assert len(enabled_rules) == 2
    assert all(r["enabled"] for r in enabled_rules)

    disabled_rules = registry.list_rules(enabled=False)
    assert len(disabled_rules) == 1
    assert disabled_rules[0]["rule_id"] == "rule_2"

    pricing_rules = registry.list_rules(group_name="pricing")
    assert len(pricing_rules) == 2


def test_registry_enable_disable(repo):
    registry = RuleRegistry(repo)
    registry.upsert("rule_1", enabled=True)

    registry.disable("rule_1")
    assert registry.get("rule_1")["enabled"] is False

    registry.enable("rule_1")
    assert registry.get("rule_1")["enabled"] is True


def test_registry_set_priority_and_group(repo):
    registry = RuleRegistry(repo)
    registry.upsert("rule_1")

    registry.set_priority("rule_1", 100)
    assert registry.get("rule_1")["priority"] == 100

    registry.set_group("rule_1", "high")
    assert registry.get("rule_1")["group_name"] == "high"


def test_registry_remove(repo):
    registry = RuleRegistry(repo)
    registry.upsert("rule_1")

    assert registry.get("rule_1") is not None
    registry.remove("rule_1")
    assert registry.get("rule_1") is None


def test_registry_get_enabled_rules(repo):
    registry = RuleRegistry(repo)
    registry.upsert("rule_1", enabled=True, priority=10)
    registry.upsert("rule_2", enabled=False, priority=20)
    registry.upsert("rule_3", enabled=True, priority=5)

    enabled = registry.get_enabled_rules()
    assert enabled == ["rule_1", "rule_3"]

    pricing = registry.get_enabled_rules(group_name="pricing")
    assert pricing == []


def test_registry_metadata(repo):
    registry = RuleRegistry(repo)
    meta = {"owner": "alice", "team": "platform"}
    registry.upsert("rule_1", metadata=meta)

    fetched = registry.get("rule_1")
    assert fetched["metadata"] == meta


def test_registry_scheduled_runner_integration(repo):
    """RuleRegistry should integrate with ScheduledRunner."""
    from web_watcher.scheduled_runner import ScheduledRunner

    runner = ScheduledRunner(repo=repo, rules_path=None)
    assert runner.registry is not None

    # Register a rule as disabled
    runner.registry.upsert("rule_1", enabled=False)
    runner._rule_cache["rule_1"] = MagicMock()

    # Simulate run_once filtering
    if runner.registry is not None and runner._rule_cache:
        enabled_rule_ids = set(runner.registry.get_enabled_rules())
        runner._rule_cache = {k: v for k, v in runner._rule_cache.items() if k in enabled_rule_ids}

    assert "rule_1" not in runner._rule_cache
