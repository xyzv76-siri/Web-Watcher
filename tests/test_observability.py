import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from web_watcher.cli import main
from web_watcher.rule_models import WatcherRule
from web_watcher.rule_parser import RuleParser
from web_watcher.generic_web_target import GenericWebTarget
from web_watcher.github_target import GitHubTarget


class TestRuleStatusField:
    def test_default_status_is_enabled(self):
        rule = WatcherRule(
            id="r1",
            name="Test",
            target=MagicMock(),
        )
        assert rule.status == "enabled"

    def test_parser_reads_status(self):
        yaml = """
version: "1.0"
rules:
  - id: "r1"
    name: "Test"
    status: "disabled"
    target:
      url: "https://example.com"
"""
        ruleset = RuleParser.parse_yaml_str(yaml)
        assert ruleset.rules[0].status == "disabled"

    def test_parser_defaults_status_to_enabled(self):
        yaml = """
version: "1.0"
rules:
  - id: "r1"
    name: "Test"
    target:
      url: "https://example.com"
"""
        ruleset = RuleParser.parse_yaml_str(yaml)
        assert ruleset.rules[0].status == "enabled"


class TestAdapterRuleStatus:
    def test_generic_web_target_skips_disabled_rule(self):
        target = MagicMock()
        target.id = "t1"
        target.status = "normal"
        target.url = "https://example.com"
        target.interval = "15m"
        target.etag = None
        target.last_modified = None
        target.content_hash = None
        target.metadata = {}
        target.consecutive_failures = 0
        target.next_allowed_at = None
        target.last_fetched_at = None

        adapter = GenericWebTarget(target=target, rule_status="disabled")
        result = adapter.execute()

        assert result.allowed is False
        assert result.reason == "Rule disabled"
        assert result.outcome.value == "policy_blocked"

    def test_generic_web_target_runs_enabled_rule(self):
        target = MagicMock()
        target.id = "t1"
        target.status = "normal"
        target.url = "https://example.com"
        target.interval = "15m"
        target.etag = None
        target.last_modified = None
        target.content_hash = None
        target.metadata = {}
        target.consecutive_failures = 0
        target.next_allowed_at = None
        target.last_fetched_at = None

        adapter = GenericWebTarget(target=target, rule_status="enabled")
        with patch("web_watcher.generic_web_target.SmartFetcher") as mock_fetcher_cls:
            mock_fetcher = MagicMock()
            mock_fetcher_cls.return_value = mock_fetcher
            mock_fetcher.fetch.return_value = MagicMock(
                status="ok",
                status_code=200,
                etag=None,
                last_modified=None,
                error=None,
                metadata={},
            )
            result = adapter.execute()
        # Should not be blocked by rule status
        assert result.outcome.value != "policy_blocked" or result.reason != "Rule disabled"

    def test_github_target_skips_disabled_rule(self):
        target = MagicMock()
        target.id = "t1"
        target.status = "normal"
        target.url = "https://github.com/owner/repo"
        target.interval = "15m"
        target.metadata = {}

        adapter = GitHubTarget(target=target, rule_status="disabled")
        result = adapter.execute()

        assert result.allowed is False
        assert result.reason == "Rule disabled"
        assert result.outcome.value == "policy_blocked"


class TestRulesCli:
    def test_rules_list(self, tmp_path, capsys):
        rule_file = tmp_path / "rules.yaml"
        rule_file.write_text("""
version: "1.0"
rules:
  - id: "r1"
    name: "Rule One"
    status: "enabled"
    target:
      url: "https://example.com"
  - id: "r2"
    name: "Rule Two"
    status: "disabled"
    target:
      url: "https://example.org"
""", encoding="utf-8")

        ret = main(["rules", "--rules", str(rule_file), "list"])
        assert ret == 0
        captured = capsys.readouterr()
        assert "r1" in captured.out
        assert "r2" in captured.out
        assert "enabled" in captured.out
        assert "disabled" in captured.out

    def test_rules_show(self, tmp_path, capsys):
        rule_file = tmp_path / "rules.yaml"
        rule_file.write_text("""
version: "1.0"
rules:
  - id: "r1"
    name: "Rule One"
    status: "enabled"
    target:
      url: "https://example.com"
""", encoding="utf-8")

        ret = main(["rules", "--rules", str(rule_file), "show", "r1"])
        assert ret == 0
        captured = capsys.readouterr()
        assert "Rule One" in captured.out
        assert "https://example.com" in captured.out

    def test_rules_enable(self, tmp_path, capsys):
        rule_file = tmp_path / "rules.yaml"
        rule_file.write_text("""
version: "1.0"
rules:
  - id: "r1"
    name: "Rule One"
    status: "disabled"
    target:
      url: "https://example.com"
""", encoding="utf-8")

        ret = main(["rules", "--rules", str(rule_file), "enable", "r1"])
        assert ret == 0
        captured = capsys.readouterr()
        assert "[OK]" in captured.out

        content = rule_file.read_text(encoding="utf-8")
        assert "status: enabled" in content

    def test_rules_disable(self, tmp_path, capsys):
        rule_file = tmp_path / "rules.yaml"
        rule_file.write_text("""
version: "1.0"
rules:
  - id: "r1"
    name: "Rule One"
    status: "enabled"
    target:
      url: "https://example.com"
""", encoding="utf-8")

        ret = main(["rules", "--rules", str(rule_file), "disable", "r1"])
        assert ret == 0
        captured = capsys.readouterr()
        assert "[OK]" in captured.out

        content = rule_file.read_text(encoding="utf-8")
        assert "status: disabled" in content

    def test_rules_list_missing_file(self, capsys):
        ret = main(["rules", "--rules", "/nonexistent/rules.yaml", "list"])
        assert ret == 1
        captured = capsys.readouterr()
        assert "[ERROR]" in captured.out


class TestNotifyHistory:
    def test_notify_history_no_records(self, tmp_path, capsys):
        db_path = tmp_path / "web_watcher.db"
        ret = main(["notify", "--db-path", str(db_path), "--history"])
        assert ret == 0
        captured = capsys.readouterr()
        assert "No notification history found" in captured.out

    def test_notify_history_with_records(self, tmp_path, capsys):
        db_path = tmp_path / "web_watcher.db"
        from web_watcher.repository import Repository
        from web_watcher.models import Notification, NotificationStatus
        from datetime import datetime, timezone

        repo = Repository(str(db_path))
        now = datetime.now(timezone.utc).isoformat()

        # Insert an entity first (event requires entity_id foreign key)
        repo.connection.execute(
            "INSERT INTO entities (canonical_key, name, entity_type, created_at) VALUES (?, ?, ?, ?)",
            ("target:t1", "Target One", "web", now),
        )
        entity_id = repo.connection.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Insert an event first (notification requires event_id foreign key)
        repo.connection.execute(
            "INSERT INTO events (entity_id, event_type, status, importance, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (entity_id, "content_change", "open", "important", now, now),
        )
        event_id = repo.connection.execute("SELECT last_insert_rowid()").fetchone()[0]

        repo.connection.execute(
            "INSERT INTO notifications (event_id, channel, status, created_at, sent_at, payload, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (event_id, "console", "delivered", now, now, "{}", now),
        )
        repo.connection.commit()

        ret = main(["notify", "--db-path", str(db_path), "--history", "--history-limit", "10"])
        assert ret == 0
        captured = capsys.readouterr()
        assert "console" in captured.out
        assert "delivered" in captured.out
