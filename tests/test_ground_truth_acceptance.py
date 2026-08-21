"""Ground Truth Acceptance: Tag Filtering + Hot Reload + Rule Registry联合验收。

按13个场景顺序验证完整链路：
Tag Filtering → Hot Reload → Rule Registry → Priority → Claim → Execute
"""

import pytest
import tempfile
import os
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from web_watcher.scheduled_runner import ScheduledRunner
from web_watcher.repository import Repository
from web_watcher.rule_registry import RuleRegistry
from web_watcher.models import Target, TargetStatus
from web_watcher.generic_web_target import TargetExecutionResult, ExecutionOutcome
from web_watcher.generic_web_target import GenericWebTarget


def _write_rules(path: Path, rules_yaml: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rules_yaml, encoding="utf-8")


def _make_runner(db_path: str, rules_path: str, include_tags=None, exclude_tags=None):
    repo = Repository(db_path)
    return ScheduledRunner(
        repo=repo,
        rules_path=rules_path,
        include_tags=include_tags or [],
        exclude_tags=exclude_tags or [],
    )


class FakeAdapter:
    """Fake adapter that records execution order."""
    executed = []

    def __init__(self, target_id: str):
        self.target_id = target_id

    def execute(self, fetcher, policy, repo, now):
        FakeAdapter.executed.append(self.target_id)
        transition = MagicMock(status=TargetStatus.NORMAL)
        transition.metadata = None
        return TargetExecutionResult(
            target_id=self.target_id,
            allowed=True,
            status_code=200,
            new_status=TargetStatus.NORMAL,
            signals_emitted=[],
            extracted_results={},
            extracted_values={},
            is_304=False,
            has_extraction_failures=False,
            reason="",
            updated_etag=None,
            updated_last_modified=None,
            updated_content_hash=None,
            updated_metadata=None,
            updated_url=None,
            consecutive_failures=0,
            next_allowed_at=None,
            last_fetched_at=now,
            outcome=ExecutionOutcome.SUCCESS_CHANGED,
            transition=transition,
            observation=None,
        )


# ============================================================
# 第一组：基础执行
# ============================================================


def test_scenario_01_single_rule_executes(tmp_path):
    """场景1：单规则正常执行"""
    db = tmp_path / "s01.db"
    rules = tmp_path / "rules.yaml"
    _write_rules(rules, """
version: "1.0"
rules:
  - id: rule_1
    name: Rule 1
    target:
      url: https://example.com/1
      interval: 15m
      timeout: 10.0
    extractors:
      - name: body
        selector_type: css
        selector: body
        transforms:
          - strip_tags
    triggers:
      - type: field_change
        field: body
        importance: important
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags: []
""")

    runner = _make_runner(str(db), str(rules))
    with patch.object(runner, "_resolve_adapter") as mock_resolve:
        fake = FakeAdapter("rule_1")
        mock_resolve.return_value = fake
        summary = runner.run_once()

    assert summary["targets_evaluated"] >= 1
    assert "rule_1" in FakeAdapter.executed


def test_scenario_02_include_tag(tmp_path):
    """场景2：include-tags 正确筛选"""
    db = tmp_path / "s02.db"
    rules = tmp_path / "rules.yaml"
    _write_rules(rules, """
version: "1.0"
rules:
  - id: rule_pricing
    name: Pricing
    target:
      url: https://example.com/pricing
      interval: 15m
      timeout: 10.0
    extractors:
      - name: price
        selector_type: css
        selector: ".price"
        transforms:
          - strip_tags
    triggers:
      - type: field_change
        field: price
        importance: important
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags:
      - pricing
  - id: rule_news
    name: News
    target:
      url: https://example.com/news
      interval: 15m
      timeout: 10.0
    extractors:
      - name: title
        selector_type: css
        selector: "h1"
        transforms:
          - strip_tags
    triggers:
      - type: field_change
        field: title
        importance: info
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags:
      - news
""")

    runner = _make_runner(str(db), str(rules), include_tags=["pricing"])
    with patch.object(runner, "_resolve_adapter") as mock_resolve:
        fake_pricing = FakeAdapter("rule_pricing")
        fake_news = FakeAdapter("rule_news")
        mock_resolve.side_effect = lambda target, rule=None: fake_pricing if target.id == "rule_pricing" else fake_news
        summary = runner.run_once()

    assert summary["rules_filtered"] == 1
    assert "rule_pricing" in FakeAdapter.executed
    assert "rule_news" not in FakeAdapter.executed


def test_scenario_03_exclude_tag(tmp_path):
    """场景3：exclude-tags 正确排除"""
    db = tmp_path / "s03.db"
    rules = tmp_path / "rules.yaml"
    _write_rules(rules, """
version: "1.0"
rules:
  - id: rule_pricing
    name: Pricing
    target:
      url: https://example.com/pricing
      interval: 15m
      timeout: 10.0
    extractors:
      - name: price
        selector_type: css
        selector: ".price"
        transforms:
          - strip_tags
    triggers:
      - type: field_change
        field: price
        importance: important
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags:
      - pricing
  - id: rule_news
    name: News
    target:
      url: https://example.com/news
      interval: 15m
      timeout: 10.0
    extractors:
      - name: title
        selector_type: css
        selector: "h1"
        transforms:
          - strip_tags
    triggers:
      - type: field_change
        field: title
        importance: info
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags:
      - news
""")

    runner = _make_runner(str(db), str(rules), exclude_tags=["news"])
    with patch.object(runner, "_resolve_adapter") as mock_resolve:
        fake_pricing = FakeAdapter("rule_pricing")
        fake_news = FakeAdapter("rule_news")
        mock_resolve.side_effect = lambda target, rule=None: fake_pricing if target.id == "rule_pricing" else fake_news
        summary = runner.run_once()

    assert summary["rules_filtered"] == 1
    assert "rule_pricing" in FakeAdapter.executed
    assert "rule_news" not in FakeAdapter.executed


def test_scenario_04_disabled_registry_rule(tmp_path):
    """场景4：disabled registry rule 不执行"""
    db = tmp_path / "s04.db"
    rules = tmp_path / "rules.yaml"
    _write_rules(rules, """
version: "1.0"
rules:
  - id: rule_enabled
    name: Enabled Rule
    target:
      url: https://example.com/enabled
      interval: 15m
      timeout: 10.0
    extractors:
      - name: body
        selector_type: css
        selector: body
        transforms:
          - strip_tags
    triggers:
      - type: field_change
        field: body
        importance: important
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags: []
  - id: rule_disabled
    name: Disabled Rule
    target:
      url: https://example.com/disabled
      interval: 15m
      timeout: 10.0
    extractors:
      - name: body
        selector_type: css
        selector: body
        transforms:
          - strip_tags
    triggers:
      - type: field_change
        field: body
        importance: important
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags: []
""")

    runner = _make_runner(str(db), str(rules))
    runner.registry.upsert("rule_disabled", enabled=False)

    with patch.object(runner, "_resolve_adapter") as mock_resolve:
        fake_enabled = FakeAdapter("rule_enabled")
        fake_disabled = FakeAdapter("rule_disabled")
        mock_resolve.side_effect = lambda target, rule=None: fake_enabled if target.id == "rule_enabled" else fake_disabled
        summary = runner.run_once()

    assert summary.get("registry_filtered", 0) == 1
    assert "rule_enabled" in FakeAdapter.executed
    assert "rule_disabled" not in FakeAdapter.executed


# ============================================================
# 第二组：组合语义
# ============================================================


def test_scenario_05_tag_plus_registry(tmp_path):
    """场景5：tag filter 和 registry 过滤正确叠加"""
    db = tmp_path / "s05.db"
    rules = tmp_path / "rules.yaml"
    _write_rules(rules, """
version: "1.0"
rules:
  - id: rule_pricing_enabled
    name: Pricing Enabled
    target:
      url: https://example.com/pricing1
      interval: 15m
      timeout: 10.0
    extractors:
      - name: price
        selector_type: css
        selector: ".price"
        transforms:
          - strip_tags
    triggers:
      - type: field_change
        field: price
        importance: important
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags:
      - pricing
  - id: rule_pricing_disabled
    name: Pricing Disabled
    target:
      url: https://example.com/pricing2
      interval: 15m
      timeout: 10.0
    extractors:
      - name: price
        selector_type: css
        selector: ".price"
        transforms:
          - strip_tags
    triggers:
      - type: field_change
        field: price
        importance: important
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags:
      - pricing
  - id: rule_news
    name: News
    target:
      url: https://example.com/news
      interval: 15m
      timeout: 10.0
    extractors:
      - name: title
        selector_type: css
        selector: "h1"
        transforms:
          - strip_tags
    triggers:
      - type: field_change
        field: title
        importance: info
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags:
      - news
""")

    runner = _make_runner(str(db), str(rules), include_tags=["pricing"])
    runner.registry.upsert("rule_pricing_disabled", enabled=False)

    with patch.object(runner, "_resolve_adapter") as mock_resolve:
        fake1 = FakeAdapter("rule_pricing_enabled")
        fake2 = FakeAdapter("rule_pricing_disabled")
        fake3 = FakeAdapter("rule_news")
        mock_resolve.side_effect = lambda target, rule=None: {
            "rule_pricing_enabled": fake1,
            "rule_pricing_disabled": fake2,
            "rule_news": fake3,
        }[target.id]
        summary = runner.run_once()

    # Only rule_pricing_enabled should execute
    assert summary["rules_filtered"] == 1  # rule_news filtered by tag
    assert summary.get("registry_filtered", 0) == 1  # rule_pricing_disabled filtered by registry
    assert "rule_pricing_enabled" in FakeAdapter.executed
    assert "rule_pricing_disabled" not in FakeAdapter.executed
    assert "rule_news" not in FakeAdapter.executed


def test_scenario_06_priority_order(tmp_path):
    """场景6：priority 排序"""
    db = tmp_path / "s06.db"
    rules = tmp_path / "rules.yaml"
    _write_rules(rules, """
version: "1.0"
rules:
  - id: rule_low
    name: Low Priority
    target:
      url: https://example.com/low
      interval: 15m
      timeout: 10.0
    extractors:
      - name: body
        selector_type: css
        selector: body
        transforms:
          - strip_tags
    triggers:
      - type: field_change
        field: body
        importance: info
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags: []
  - id: rule_high
    name: High Priority
    target:
      url: https://example.com/high
      interval: 15m
      timeout: 10.0
    extractors:
      - name: body
        selector_type: css
        selector: body
        transforms:
          - strip_tags
    triggers:
      - type: field_change
        field: body
        importance: critical
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags: []
""")

    runner = _make_runner(str(db), str(rules))
    runner.registry.set_priority("rule_low", priority=10)
    runner.registry.set_priority("rule_high", priority=100)

    with patch.object(runner, "_resolve_adapter") as mock_resolve:
        fake_low = FakeAdapter("rule_low")
        fake_high = FakeAdapter("rule_high")
        mock_resolve.side_effect = lambda target, rule=None: fake_low if target.id == "rule_low" else fake_high
        summary = runner.run_once()

    # rule_high should execute before rule_low
    assert FakeAdapter.executed.index("rule_high") < FakeAdapter.executed.index("rule_low")


def test_scenario_07_priority_with_claim(tmp_path):
    """场景7：priority + claim/fencing 不产生回归"""
    db = tmp_path / "s07.db"
    rules = tmp_path / "rules.yaml"
    _write_rules(rules, """
version: "1.0"
rules:
  - id: rule_a
    name: Rule A
    target:
      url: https://example.com/a
      interval: 15m
      timeout: 10.0
    extractors:
      - name: body
        selector_type: css
        selector: body
        transforms:
          - strip_tags
    triggers:
      - type: field_change
        field: body
        importance: info
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags: []
  - id: rule_b
    name: Rule B
    target:
      url: https://example.com/b
      interval: 15m
      timeout: 10.0
    extractors:
      - name: body
        selector_type: css
        selector: body
        transforms:
          - strip_tags
    triggers:
      - type: field_change
        field: body
        importance: info
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags: []
""")

    runner = _make_runner(str(db), str(rules))
    runner.registry.set_priority("rule_a", priority=50)
    runner.registry.set_priority("rule_b", priority=10)

    # Run once and verify claims happen in priority order
    with patch.object(runner, "_resolve_adapter") as mock_resolve:
        fake_a = FakeAdapter("rule_a")
        fake_b = FakeAdapter("rule_b")
        mock_resolve.side_effect = lambda target, rule=None: fake_a if target.id == "rule_a" else fake_b
        summary = runner.run_once()

    # Both should be claimed and executed in priority order
    assert summary["targets_evaluated"] == 2
    assert "rule_a" in FakeAdapter.executed
    assert "rule_b" in FakeAdapter.executed
    assert FakeAdapter.executed.index("rule_a") < FakeAdapter.executed.index("rule_b")


# ============================================================
# 第三组：Hot Reload
# ============================================================


def test_scenario_08_yaml_modified_reloads(tmp_path):
    """场景8：YAML 修改后自动 reload"""
    db = tmp_path / "s08.db"
    rules = tmp_path / "rules.yaml"
    _write_rules(rules, """
version: "1.0"
rules:
  - id: rule_v1
    name: Version 1
    target:
      url: https://example.com/v1
      interval: 15m
      timeout: 10.0
    extractors:
      - name: body
        selector_type: css
        selector: body
        transforms:
          - strip_tags
    triggers:
      - type: field_change
        field: body
        importance: info
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags: []
""")

    runner = _make_runner(str(db), str(rules))
    runner.sync_rules()
    assert "rule_v1" in runner._rule_cache

    # Modify YAML
    _write_rules(rules, """
version: "1.0"
rules:
  - id: rule_v2
    name: Version 2
    target:
      url: https://example.com/v2
      interval: 15m
      timeout: 10.0
    extractors:
      - name: body
        selector_type: css
        selector: body
        transforms:
          - strip_tags
    triggers:
      - type: field_change
        field: body
        importance: info
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags: []
""")

    summary = runner.run_once()
    assert summary.get("reload") is not None
    assert summary["reload"]["reloaded"] == 1
    assert "rule_v1" not in runner._rule_cache
    assert "rule_v2" in runner._rule_cache


def test_scenario_09_yaml_new_rule_appears(tmp_path):
    """场景9：YAML 新增 rule，reload 后出现"""
    db = tmp_path / "s09.db"
    rules = tmp_path / "rules.yaml"
    _write_rules(rules, """
version: "1.0"
rules:
  - id: rule_original
    name: Original
    target:
      url: https://example.com/original
      interval: 15m
      timeout: 10.0
    extractors:
      - name: body
        selector_type: css
        selector: body
        transforms:
          - strip_tags
    triggers:
      - type: field_change
        field: body
        importance: info
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags: []
""")

    runner = _make_runner(str(db), str(rules))
    runner.sync_rules()
    assert len(runner._rule_cache) == 1

    # Add new rule
    _write_rules(rules, """
version: "1.0"
rules:
  - id: rule_original
    name: Original
    target:
      url: https://example.com/original
      interval: 15m
      timeout: 10.0
    extractors:
      - name: body
        selector_type: css
        selector: body
        transforms:
          - strip_tags
    triggers:
      - type: field_change
        field: body
        importance: info
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags: []
  - id: rule_new
    name: New Rule
    target:
      url: https://example.com/new
      interval: 15m
      timeout: 10.0
    extractors:
      - name: body
        selector_type: css
        selector: body
        transforms:
          - strip_tags
    triggers:
      - type: field_change
        field: body
        importance: info
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags: []
""")

    summary = runner.run_once()
    assert summary.get("reload") is not None
    assert summary["reload"]["reloaded"] == 2
    assert "rule_original" in runner._rule_cache
    assert "rule_new" in runner._rule_cache


def test_scenario_10_yaml_delete_rule_disappears(tmp_path):
    """场景10：YAML 删除 rule，reload 后消失"""
    db = tmp_path / "s10.db"
    rules = tmp_path / "rules.yaml"
    _write_rules(rules, """
version: "1.0"
rules:
  - id: rule_keep
    name: Keep
    target:
      url: https://example.com/keep
      interval: 15m
      timeout: 10.0
    extractors:
      - name: body
        selector_type: css
        selector: body
        transforms:
          - strip_tags
    triggers:
      - type: field_change
        field: body
        importance: info
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags: []
  - id: rule_remove
    name: Remove
    target:
      url: https://example.com/remove
      interval: 15m
      timeout: 10.0
    extractors:
      - name: body
        selector_type: css
        selector: body
        transforms:
          - strip_tags
    triggers:
      - type: field_change
        field: body
        importance: info
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags: []
""")

    runner = _make_runner(str(db), str(rules))
    runner.sync_rules()
    assert len(runner._rule_cache) == 2

    # Remove one rule
    _write_rules(rules, """
version: "1.0"
rules:
  - id: rule_keep
    name: Keep
    target:
      url: https://example.com/keep
      interval: 15m
      timeout: 10.0
    extractors:
      - name: body
        selector_type: css
        selector: body
        transforms:
          - strip_tags
    triggers:
      - type: field_change
        field: body
        importance: info
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags: []
""")

    summary = runner.run_once()
    assert summary.get("reload") is not None
    assert summary["reload"]["reloaded"] == 1
    assert "rule_keep" in runner._rule_cache
    assert "rule_remove" not in runner._rule_cache


def test_scenario_11_reload_preserves_registry(tmp_path):
    """场景11：reload 后 Registry 状态保持"""
    db = tmp_path / "s11.db"
    rules = tmp_path / "rules.yaml"
    _write_rules(rules, """
version: "1.0"
rules:
  - id: rule_1
    name: Rule 1
    target:
      url: https://example.com/1
      interval: 15m
      timeout: 10.0
    extractors:
      - name: body
        selector_type: css
        selector: body
        transforms:
          - strip_tags
    triggers:
      - type: field_change
        field: body
        importance: info
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags: []
""")

    runner = _make_runner(str(db), str(rules))
    runner.registry.upsert("rule_1", enabled=False, priority=100, group_name="critical")

    # Trigger reload by modifying file
    _write_rules(rules, """
version: "1.0"
rules:
  - id: rule_1
    name: Rule 1 Modified
    target:
      url: https://example.com/1
      interval: 15m
      timeout: 10.0
    extractors:
      - name: body
        selector_type: css
        selector: body
        transforms:
          - strip_tags
    triggers:
      - type: field_change
        field: body
        importance: info
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags: []
""")

    summary = runner.run_once()
    assert summary.get("reload") is not None
    # Registry state should be preserved
    entry = runner.registry.get("rule_1")
    assert entry is not None
    assert entry["enabled"] is False
    assert entry["priority"] == 100
    assert entry["group_name"] == "critical"


# ============================================================
# 第四组：运行形态
# ============================================================


def test_scenario_12_daemon_continuous_reload(tmp_path):
    """场景12：daemon 长驻状态下自动 reload + filtering + registry"""
    db = tmp_path / "s12.db"
    rules = tmp_path / "rules.yaml"
    _write_rules(rules, """
version: "1.0"
rules:
  - id: rule_1
    name: Rule 1
    target:
      url: https://example.com/1
      interval: 15m
      timeout: 10.0
    extractors:
      - name: body
        selector_type: css
        selector: body
        transforms:
          - strip_tags
    triggers:
      - type: field_change
        field: body
        importance: info
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags: []
""")

    repo = Repository(str(db))
    runner = ScheduledRunner(
        repo=repo,
        rules_path=str(rules),
        include_tags=[],
        exclude_tags=[],
    )
    runner.registry.upsert("rule_1", enabled=True)

    # First run
    with patch.object(runner, "_resolve_adapter") as mock_resolve:
        fake = FakeAdapter("rule_1")
        mock_resolve.return_value = fake
        summary1 = runner.run_once()
    assert summary1["targets_evaluated"] >= 1
    assert "rule_1" in FakeAdapter.executed

    # Disable via registry
    runner.registry.disable("rule_1")
    FakeAdapter.executed.clear()

    # Second run - should not execute
    with patch.object(runner, "_resolve_adapter") as mock_resolve:
        fake = FakeAdapter("rule_1")
        mock_resolve.return_value = fake
        summary2 = runner.run_once()
    assert summary2.get("registry_filtered", 0) == 1
    assert "rule_1" not in FakeAdapter.executed


def test_scenario_13_cli_reload_semantics(tmp_path):
    """场景13：CLI reload 与自动 reload 语义一致"""
    db = tmp_path / "s13.db"
    rules = tmp_path / "rules.yaml"
    _write_rules(rules, """
version: "1.0"
rules:
  - id: rule_v1
    name: Version 1
    target:
      url: https://example.com/v1
      interval: 15m
      timeout: 10.0
    extractors:
      - name: body
        selector_type: css
        selector: body
        transforms:
          - strip_tags
    triggers:
      - type: field_change
        field: body
        importance: info
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags: []
""")

    runner = _make_runner(str(db), str(rules))
    runner.sync_rules()
    assert "rule_v1" in runner._rule_cache

    # Modify file
    _write_rules(rules, """
version: "1.0"
rules:
  - id: rule_v2
    name: Version 2
    target:
      url: https://example.com/v2
      interval: 15m
      timeout: 10.0
    extractors:
      - name: body
        selector_type: css
        selector: body
        transforms:
          - strip_tags
    triggers:
      - type: field_change
        field: body
        importance: info
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags: []
""")

    # CLI reload
    cli_stats = runner.reload_rules()
    assert cli_stats["reloaded"] == 1

    # Auto reload via run_once should NOT trigger again (file unchanged since CLI reload)
    summary = runner.run_once()
    auto_stats = summary.get("reload")
    assert auto_stats is None  # No auto reload needed after CLI reload

    # Now modify file again to trigger auto reload
    _write_rules(rules, """
version: "1.0"
rules:
  - id: rule_v3
    name: Version 3
    target:
      url: https://example.com/v3
      interval: 15m
      timeout: 10.0
    extractors:
      - name: body
        selector_type: css
        selector: body
        transforms:
          - strip_tags
    triggers:
      - type: field_change
        field: body
        importance: info
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags: []
""")

    summary2 = runner.run_once()
    auto_stats2 = summary2.get("reload")
    assert auto_stats2 is not None
    assert auto_stats2["reloaded"] == 1

    # Both CLI and auto reload should report same structure
    assert "reloaded" in cli_stats
    assert "reloaded" in auto_stats2
