"""Unit tests for AuditExporter and CLI export subcommand (Phase 13-C)."""

import os
from io import StringIO
from unittest.mock import MagicMock
from datetime import datetime, timedelta
import pytest

from web_watcher.exporter import AuditExporter, parse_since
from web_watcher.cli import main
from web_watcher.models import Notification, Event, EventStatus, EventType, Importance
from web_watcher.repository import Repository


def _make_event(event_id=1, entity_id=1, event_type=EventType.CONTENT_CHANGE, status=EventStatus.OPEN, importance=Importance.CRITICAL, created_at=None):
    return Event(
        id=event_id,
        entity_id=entity_id,
        event_type=event_type,
        status=status,
        importance=importance,
        created_at=created_at or datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )


def _make_notification(notif_id=1, event_id=1, channel="webhook", status="pending", payload=None):
    return Notification(
        id=notif_id,
        event_id=event_id,
        channel=channel,
        status=status,
        created_at=datetime.utcnow(),
        payload=payload or {},
    )


class TestParseSince:
    def test_none_returns_none(self):
        assert parse_since(None) is None

    def test_empty_string_returns_none(self):
        assert parse_since("") is None

    def test_seconds(self):
        now = datetime.utcnow()
        result = parse_since("30s")
        assert now - timedelta(seconds=30) <= result <= now

    def test_minutes(self):
        now = datetime.utcnow()
        result = parse_since("15m")
        assert now - timedelta(minutes=15) <= result <= now

    def test_hours(self):
        now = datetime.utcnow()
        result = parse_since("2h")
        assert now - timedelta(hours=2) <= result <= now

    def test_days(self):
        now = datetime.utcnow()
        result = parse_since("7d")
        assert now - timedelta(days=7) <= result <= now

    def test_iso_format(self):
        dt = datetime(2024, 1, 1, 12, 0, 0)
        result = parse_since("2024-01-01T12:00:00")
        assert result == dt

    def test_invalid_falls_back_to_24h(self):
        now = datetime.utcnow()
        result = parse_since("invalid")
        assert now - timedelta(hours=24) <= result <= now


class TestAuditExporter:
    def test_collect_data_empty(self):
        repo = MagicMock()
        repo.list_events.return_value = []
        repo.list_all_notifications.return_value = []

        exporter = AuditExporter(repo)
        data = exporter.collect_data()

        assert data["events"] == []
        assert data["notifications"] == []
        assert "generated_at" in data

    def test_collect_data_filters_by_since(self):
        repo = MagicMock()
        old_event = _make_event(created_at=datetime.utcnow() - timedelta(days=2))
        new_event = _make_event(created_at=datetime.utcnow() - timedelta(hours=1))
        repo.list_events.return_value = [old_event, new_event]
        repo.list_all_notifications.return_value = []

        exporter = AuditExporter(repo)
        since = datetime.utcnow() - timedelta(hours=2)
        data = exporter.collect_data(since=since)

        assert len(data["events"]) == 1
        assert data["events"][0].id == new_event.id

    def test_export_markdown_contains_events(self):
        repo = MagicMock()
        ev = _make_event(event_id=1, created_at=datetime.utcnow())
        notif = _make_notification(event_id=1, channel="slack", status="delivered")
        repo.list_events.return_value = [ev]
        repo.list_all_notifications.return_value = [notif]

        exporter = AuditExporter(repo)
        md = exporter.export_markdown("24h")

        assert "# Web Watcher 审计报告" in md
        assert "- **事件总数**: 1" in md
        assert "- **通知记录数**: 1" in md
        assert "content_change" in md or "`1`" in md

    def test_export_markdown_no_events(self):
        repo = MagicMock()
        repo.list_events.return_value = []
        repo.list_all_notifications.return_value = []

        exporter = AuditExporter(repo)
        md = exporter.export_markdown("24h")

        assert "未发现事件记录" in md

    def test_export_html_structure(self):
        repo = MagicMock()
        ev = _make_event(event_id=1, created_at=datetime.utcnow())
        repo.list_events.return_value = [ev]
        repo.list_all_notifications.return_value = []

        exporter = AuditExporter(repo)
        html_content = exporter.export_html("24h")

        assert "<!DOCTYPE html>" in html_content
        assert "<title>Web Watcher Audit Report</title>" in html_content
        assert "事件列表" in html_content
        assert f"<code>{ev.id}</code>" in html_content

    def test_export_html_no_events(self):
        repo = MagicMock()
        repo.list_events.return_value = []
        repo.list_all_notifications.return_value = []

        exporter = AuditExporter(repo)
        html_content = exporter.export_html("24h")

        assert "暂无事件记录" in html_content


class TestCliExport:
    def test_export_markdown_to_stdout(self, monkeypatch, capsys):
        repo = MagicMock()
        ev = _make_event(event_id=1, created_at=datetime.utcnow())
        repo.list_events.return_value = [ev]
        repo.list_all_notifications.return_value = []

        monkeypatch.setattr("web_watcher.cli.Repository", lambda db: repo)
        monkeypatch.setattr("web_watcher.cli.AuditExporter", lambda r: AuditExporter(r))

        ret = main(["export", "--format", "markdown", "--since", "24h"])
        assert ret == 0
        captured = capsys.readouterr()
        assert "# Web Watcher 审计报告" in captured.out

    def test_export_html_to_file(self, tmp_path, monkeypatch):
        repo = MagicMock()
        repo.list_events.return_value = []
        repo.list_all_notifications.return_value = []

        monkeypatch.setattr("web_watcher.cli.Repository", lambda db: repo)
        monkeypatch.setattr("web_watcher.cli.AuditExporter", lambda r: AuditExporter(r))

        output_file = tmp_path / "audit.html"
        ret = main(["export", "--format", "html", "--output", str(output_file)])
        assert ret == 0
        assert output_file.exists()
        content = output_file.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content
