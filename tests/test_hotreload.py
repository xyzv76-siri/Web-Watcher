import pytest
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock
from datetime import datetime, timezone

from web_watcher.scheduled_runner import ScheduledRunner
from web_watcher.models import Target, TargetStatus
from web_watcher.repository import Repository


@pytest.fixture
def tmp_rules(tmp_path):
    rules = tmp_path / "rules.yaml"
    rules.write_text(
        """
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
        importance: important
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags: []
"""
    )
    return rules


def test_hotreload_detects_change(tmp_rules):
    runner = ScheduledRunner(rules_path=str(tmp_rules))
    runner.sync_rules()

    assert len(runner._rule_cache) == 1
    assert "rule_a" in runner._rule_cache

    # Modify file
    tmp_rules.write_text(
        """
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
        importance: important
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
        importance: important
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags: []
"""
    )

    assert runner._check_rules_changed() is True


def test_hotreload_no_change(tmp_rules):
    runner = ScheduledRunner(rules_path=str(tmp_rules))
    runner.sync_rules()

    # Same content should not trigger reload
    assert runner._check_rules_changed() is False


def test_reload_rules_updates_cache(tmp_rules):
    repo = MagicMock()
    runner = ScheduledRunner(rules_path=str(tmp_rules), repo=repo)
    runner.sync_rules()

    assert len(runner._rule_cache) == 1

    # Modify file
    tmp_rules.write_text(
        """
version: "1.0"
rules:
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
        importance: important
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags: []
"""
    )

    stats = runner.reload_rules()
    assert stats["reloaded"] == 1
    assert "rule_a" not in runner._rule_cache
    assert "rule_b" in runner._rule_cache


def test_reload_rules_with_tag_filter(tmp_rules):
    runner = ScheduledRunner(rules_path=str(tmp_rules))

    # Modify file with tags
    tmp_rules.write_text(
        """
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
        importance: important
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags:
      - pricing
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
        importance: important
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags:
      - news
"""
    )

    # Reload only pricing rules
    stats = runner.reload_rules(include_tags=["pricing"])
    assert stats["reloaded"] == 1
    assert stats["filtered"] == 1
    assert "rule_a" in runner._rule_cache
    assert "rule_b" not in runner._rule_cache


def test_run_once_triggers_hotreload(tmp_rules):
    repo = MagicMock()
    repo.list_targets.return_value = []
    repo.claim_targets.return_value = []

    runner = ScheduledRunner(rules_path=str(tmp_rules), repo=repo)
    runner.sync_rules()

    # Modify file
    tmp_rules.write_text(
        """
version: "1.0"
rules:
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
        importance: important
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags: []
"""
    )

    summary = runner.run_once()
    assert summary["reload"] is not None
    assert summary["reload"]["reloaded"] == 1
    assert "rule_a" not in runner._rule_cache
    assert "rule_b" in runner._rule_cache
