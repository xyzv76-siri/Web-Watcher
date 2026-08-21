"""Tests for selective retention and export filters."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call
import pytest

from web_watcher.retention import RetentionManager, RetentionPolicy
from web_watcher.exporter import AuditExporter
from web_watcher.cli import main
from web_watcher.event_types import EventType
from web_watcher.event_status import EventStatus
from web_watcher.importance import Importance


class TestSelectiveRetention:
    def test_dry_run_with_filters_counts_matching(self):
        repo = MagicMock()
        repo.connection.execute.side_effect = [
            MagicMock(fetchone=MagicMock(return_value=(5,))),
            MagicMock(fetchone=MagicMock(return_value=(3,))),
        ]

        policy = RetentionPolicy(
            max_age_days=30,
            dry_run=True,
            entity_ids=[1, 2],
            event_types=[EventType.CONTENT_CHANGE],
            importances=[Importance.CRITICAL],
            statuses=[EventStatus.OPEN],
            channels=["console"],
        )
        manager = RetentionManager(repo=repo, policy=policy)
        summary = manager.enforce()

        assert summary["dry_run"] is True
        assert summary["deleted_events"] == 5
        assert summary["deleted_notifications"] == 3
        assert summary["filters"]["entity_ids"] == [1, 2]
        assert summary["filters"]["event_types"] == ["content_change"]
        assert summary["filters"]["importances"] == ["critical"]
        assert summary["filters"]["statuses"] == ["open"]
        assert summary["filters"]["channels"] == ["console"]

        # Verify WHERE clauses contain filters
        event_call = repo.connection.execute.call_args_list[0]
        event_where = event_call[0][0]
        assert "entity_id IN" in event_where
        assert "event_type IN" in event_where
        assert "importance IN" in event_where
        assert "status IN" in event_where

    def test_enforce_with_filters_deletes_matching(self):
        repo = MagicMock()
        repo.connection.execute.side_effect = [
            MagicMock(rowcount=4),
            MagicMock(rowcount=2),
        ]

        policy = RetentionPolicy(
            max_age_days=30,
            dry_run=False,
            entity_ids=[10],
            event_types=[EventType.STARS_CHANGED],
            channels=["webhook"],
        )
        manager = RetentionManager(repo=repo, policy=policy)
        summary = manager.enforce()

        assert summary["dry_run"] is False
        assert summary["deleted_events"] == 4
        assert summary["deleted_notifications"] == 2

        event_call = repo.connection.execute.call_args_list[0]
        event_where = event_call[0][0]
        assert "entity_id IN (?)" in event_where
        assert "event_type IN (?)" in event_where

        notif_call = repo.connection.execute.call_args_list[1]
        notif_where = notif_call[0][0]
        assert "channel IN (?)" in notif_where

    def test_legacy_delete_methods_still_used_when_available(self):
        repo = MagicMock()
        repo.delete_old_events.return_value = 7
        repo.delete_old_notifications.return_value = 3

        policy = RetentionPolicy(max_age_days=30, dry_run=False)
        manager = RetentionManager(repo=repo, policy=policy)
        summary = manager.enforce()

        assert summary["deleted_events"] == 7
        assert summary["deleted_notifications"] == 3
        repo.delete_old_events.assert_called_once()
        repo.delete_old_notifications.assert_called_once()
        repo.connection.execute.assert_not_called()


class TestSelectiveExport:
    def test_export_markdown_with_filters(self, tmp_path, capsys):
        from web_watcher.repository import Repository

        db_path = tmp_path / "web_watcher.db"
        repo = Repository(str(db_path))
        now = datetime.now(timezone.utc)

        # Insert two entities
        repo.connection.execute(
            "INSERT INTO entities (canonical_key, name, entity_type, created_at) VALUES (?, ?, ?, ?)",
            ("target:e1", "E1", "web", now.isoformat()),
        )
        repo.connection.execute(
            "INSERT INTO entities (canonical_key, name, entity_type, created_at) VALUES (?, ?, ?, ?)",
            ("target:e2", "E2", "web", now.isoformat()),
        )
        repo.connection.commit()

        e1_id = repo.connection.execute("SELECT last_insert_rowid()").fetchone()[0] - 1
        e2_id = e1_id + 1

        # Insert events
        repo.connection.execute(
            "INSERT INTO events (entity_id, event_type, status, importance, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (e1_id, "content_change", "open", "important", now.isoformat(), now.isoformat()),
        )
        repo.connection.execute(
            "INSERT INTO events (entity_id, event_type, status, importance, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (e2_id, "content_change", "open", "important", now.isoformat(), now.isoformat()),
        )
        repo.connection.commit()

        exporter = AuditExporter(repo)
        data = exporter.collect_data(entity_ids=[e1_id])
        assert len(data["events"]) == 1
        assert data["events"][0].entity_id == e1_id

    def test_export_cli_with_event_type_filter(self, tmp_path, monkeypatch, capsys):
        from web_watcher.repository import Repository

        db_path = tmp_path / "web_watcher.db"
        repo = Repository(str(db_path))
        now = datetime.now(timezone.utc)

        repo.connection.execute(
            "INSERT INTO entities (canonical_key, name, entity_type, created_at) VALUES (?, ?, ?, ?)",
            ("target:e1", "E1", "web", now.isoformat()),
        )
        repo.connection.commit()
        e1_id = repo.connection.execute("SELECT last_insert_rowid()").fetchone()[0]

        repo.connection.execute(
            "INSERT INTO events (entity_id, event_type, status, importance, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (e1_id, "content_change", "open", "important", now.isoformat(), now.isoformat()),
        )
        repo.connection.execute(
            "INSERT INTO events (entity_id, event_type, status, importance, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (e1_id, "stars_changed", "open", "important", now.isoformat(), now.isoformat()),
        )
        repo.connection.commit()

        monkeypatch.setattr("web_watcher.cli.Repository", lambda db: repo)
        monkeypatch.setattr("web_watcher.cli.AuditExporter", lambda r: AuditExporter(r))

        ret = main(["export", "--format", "markdown", "--event-type", "content_change", "--db-path", str(db_path)])
        assert ret == 0
        captured = capsys.readouterr()
        assert "content_change" in captured.out
        assert "stars_changed" not in captured.out
