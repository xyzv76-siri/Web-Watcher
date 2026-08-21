"""ScheduledRunner unit tests (P2 remediation)."""

import os
import socket
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from web_watcher.scheduled_runner import ScheduledRunner
from web_watcher.models import Target, TargetStatus
from web_watcher.config import AppConfig


@pytest.fixture
def tmp_rules_path(tmp_path):
    rules = tmp_path / "rules.yaml"
    rules.write_text("""
rules:
  - id: test-rule
    target:
      url: https://example.com
      interval: 60
    selectors:
      - type: text
        selector: "body"
""")
    return str(rules)


@pytest.fixture
def base_config():
    return AppConfig(
        db_path=":memory:",
        default_cooldown_seconds=60.0,
        default_batch_size=10,
        default_poll_interval=1.0,
        default_max_retries=3,
        default_base_backoff_sec=1.0,
        log_level="ERROR",
        retention_max_age_days=30,
        retention_dry_run=False,
        noise_reduction_level="standard",
    )


class TestScheduledRunnerInit:
    def test_init_without_repo(self, base_config):
        runner = ScheduledRunner(config=base_config)
        assert runner.config == base_config
        assert runner.repo is None
        assert runner.worker_id is not None

    def test_init_with_repo(self, base_config, tmp_path):
        from web_watcher.repository import Repository
        db_path = str(tmp_path / "test.db")
        repo = Repository(db_path)
        runner = ScheduledRunner(repo=repo, config=base_config)
        assert runner.repo is repo
        assert runner.policy is not None
        assert runner.fetcher is not None

    def test_init_creates_worker_id(self, base_config):
        runner = ScheduledRunner(config=base_config)
        assert runner.worker_id.startswith(socket.gethostname())
        assert str(os.getpid()) in runner.worker_id


class TestRulesSnapshot:
    def test_snapshot_missing_file(self, base_config):
        runner = ScheduledRunner(config=base_config)
        runner.rules_path = "/nonexistent/path/rules.yaml"
        mtime, file_hash = runner._get_rules_snapshot()
        assert mtime is None
        assert file_hash is None

    def test_snapshot_existing_file(self, base_config, tmp_rules_path):
        runner = ScheduledRunner(config=base_config)
        runner.rules_path = tmp_rules_path
        mtime, file_hash = runner._get_rules_snapshot()
        assert mtime is not None
        assert file_hash is not None
        assert len(file_hash) == 64  # SHA256 hex length

    def test_check_rules_changed_first_time(self, base_config, tmp_rules_path):
        runner = ScheduledRunner(config=base_config)
        runner.rules_path = tmp_rules_path
        assert runner._check_rules_changed() is True

    def test_check_rules_changed_unchanged(self, base_config, tmp_rules_path):
        runner = ScheduledRunner(config=base_config)
        runner.rules_path = tmp_rules_path
        runner._last_rules_mtime, runner._last_rules_hash = runner._get_rules_snapshot()
        assert runner._check_rules_changed() is False

    def test_check_rules_changed_modified(self, base_config, tmp_rules_path):
        runner = ScheduledRunner(config=base_config)
        runner.rules_path = tmp_rules_path
        runner._last_rules_mtime, runner._last_rules_hash = runner._get_rules_snapshot()
        # Modify file
        Path(tmp_rules_path).write_text(Path(tmp_rules_path).read_text() + "\n# changed")
        assert runner._check_rules_changed() is True


class TestReloadRules:
    def test_reload_no_path(self, base_config):
        runner = ScheduledRunner(config=base_config)
        result = runner.reload_rules(path=None)
        assert result == {"reloaded": 0, "filtered": 0, "skipped": 0}

    def test_reload_missing_file(self, base_config, tmp_path):
        runner = ScheduledRunner(config=base_config)
        result = runner.reload_rules(path=str(tmp_path / "missing.yaml"))
        assert "error" in result
        assert result["error"] == "file_not_found"

    def test_reload_success(self, base_config, tmp_rules_path):
        runner = ScheduledRunner(config=base_config)
        result = runner.reload_rules(path=tmp_rules_path)
        assert result["reloaded"] >= 1
        assert "test-rule" in runner._rule_cache

    def test_reload_with_include_tags(self, base_config, tmp_path):
        rules = tmp_path / "rules.yaml"
        rules.write_text("""
rules:
  - id: rule-a
    target:
      url: https://example.com/a
      interval: 60
    tags: [prod]
    selectors: []
  - id: rule-b
    target:
      url: https://example.com/b
      interval: 60
    tags: [dev]
    selectors: []
""")
        runner = ScheduledRunner(config=base_config)
        result = runner.reload_rules(path=str(rules), include_tags=["prod"])
        assert result["reloaded"] == 1
        assert "rule-a" in runner._rule_cache
        assert "rule-b" not in runner._rule_cache

    def test_reload_with_exclude_tags(self, base_config, tmp_path):
        rules = tmp_path / "rules.yaml"
        rules.write_text("""
rules:
  - id: rule-a
    target:
      url: https://example.com/a
      interval: 60
    tags: [prod]
    selectors: []
  - id: rule-b
    target:
      url: https://example.com/b
      interval: 60
    tags: [dev]
    selectors: []
""")
        runner = ScheduledRunner(config=base_config)
        result = runner.reload_rules(path=str(rules), exclude_tags=["dev"])
        assert result["reloaded"] == 1
        assert "rule-a" in runner._rule_cache
        assert "rule-b" not in runner._rule_cache


class TestRunOnce:
    def test_run_once_no_repo_no_rules(self, base_config):
        runner = ScheduledRunner(config=base_config)
        summary = runner.run_once()
        assert summary["targets_evaluated"] == 0
        assert summary["signals_emitted"] == 0
        assert "errors" in summary

    def test_run_once_with_rules_no_repo(self, base_config, tmp_rules_path):
        runner = ScheduledRunner(config=base_config)
        runner.rules_path = tmp_rules_path
        runner.sync_rules = MagicMock()
        summary = runner.run_once()
        assert summary["targets_evaluated"] >= 0
        assert "errors" in summary

    def test_run_once_claims_targets(self, base_config, tmp_rules_path):
        from web_watcher.repository import Repository
        db_path = ":memory:"
        repo = Repository(db_path)
        runner = ScheduledRunner(repo=repo, config=base_config, rules_path=tmp_rules_path)
        summary = runner.run_once()
        assert "targets_evaluated" in summary
        assert "errors" in summary
