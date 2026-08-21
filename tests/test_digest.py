"""Phase X — Digest v1 tests.

Deterministic only. No AI, LLM, network, or wall-clock dependencies.
Tests verify:
    1. Empty digest returns empty report
    2. Daily / weekly presets compute correct time windows
    3. Custom since / until filters
    4. Minimum importance filtering
    5. Target grouping and importance distribution
    6. Markdown report contains expected sections
    7. Signal summary extraction
"""

from datetime import datetime, timedelta, timezone
import json

import pytest

from web_watcher.digest import DigestBuilder, DigestReport, TargetDigest
from web_watcher.repository import Repository
from web_watcher.models import Entity, Event, Signal
from web_watcher.event_types import EventType
from web_watcher.event_status import EventStatus
from web_watcher.importance import Importance


def _ts(year=2026, month=8, day=17, hour=10, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


def _entity(repo, key="target:alpha"):
    return repo.get_or_create_entity(key, "Alpha", "generic_web")


def _entity_id(repo, key="target:alpha"):
    return _entity(repo, key).id


def _create_event(repo, entity_id, created_at, importance=Importance.INTERESTING):
    return repo.create_event(
        entity_id=entity_id,
        event_type=EventType.CONTENT_CHANGE,
        status=EventStatus.OPEN,
        importance=importance,
        created_at=created_at,
    )


def _attach_signal(repo, event_id, entity_id, value=None, fingerprint=None):
    if fingerprint is None:
        fingerprint = f"fp-{event_id}"
    observed_at = _ts()
    cursor = repo.connection.execute(
        "INSERT INTO signals (entity_id, signal_type, observed_at, value, fingerprint, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            entity_id,
            "content_change",
            observed_at.isoformat(),
            value or json.dumps({"target_id": "target:alpha", "url": "https://example.com"}),
            fingerprint,
            observed_at.isoformat(),
        ),
    )
    repo.connection.commit()
    sig_id = cursor.lastrowid
    repo.attach_signal_to_event(event_id, sig_id)
    return Signal(
        id=sig_id,
        entity_id=entity_id,
        signal_type="content_change",
        observed_at=observed_at,
        value=value or json.dumps({"target_id": "target:alpha", "url": "https://example.com"}),
        fingerprint=fingerprint,
    )


class TestDigestBuilder:
    def test_empty_digest(self, tmp_path):
        repo = Repository(tmp_path / "digest.db")
        builder = DigestBuilder(repo)
        report = builder.build(since=_ts(2026, 8, 17), until=_ts(2026, 8, 18))
        assert report.total_events == 0
        assert report.summary == "无事件发生。"
        assert "无事件发生" in report.to_markdown()
        repo.close()

    def test_daily_with_events(self, tmp_path):
        repo = Repository(tmp_path / "digest.db")
        eid = _entity_id(repo)
        now = _ts(2026, 8, 17, 12)
        e1 = _create_event(repo, eid, now - timedelta(hours=2), Importance.INTERESTING)
        e2 = _create_event(repo, eid, now - timedelta(hours=1), Importance.CRITICAL)
        _attach_signal(repo, e1.id, eid, value=json.dumps({"target_id": "target:alpha", "url": "https://a.com"}))
        _attach_signal(repo, e2.id, eid, value=json.dumps({"target_id": "target:alpha", "url": "https://a.com"}))

        builder = DigestBuilder(repo)
        report = builder.build(since=now - timedelta(days=1), until=now)
        assert report.total_events == 2
        assert "target:alpha" in report.targets
        assert report.targets["target:alpha"].event_count == 2
        assert report.importance_distribution.get("critical") == 1
        assert report.importance_distribution.get("interesting") == 1
        md = report.to_markdown()
        assert "target:alpha" in md
        assert "Events: 2" in md
        repo.close()

    def test_weekly_window(self, tmp_path):
        repo = Repository(tmp_path / "digest.db")
        eid = _entity_id(repo)
        now = _ts(2026, 8, 17, 12)
        e1 = _create_event(repo, eid, now - timedelta(days=3))
        e2 = _create_event(repo, eid, now - timedelta(days=6))
        _attach_signal(repo, e1.id, eid)
        _attach_signal(repo, e2.id, eid)

        builder = DigestBuilder(repo)
        report = builder.build(since=now - timedelta(days=7), until=now)
        assert report.total_events == 2
        repo.close()

    def test_custom_since_until(self, tmp_path):
        repo = Repository(tmp_path / "digest.db")
        eid = _entity_id(repo)
        since = _ts(2026, 8, 15)
        until = _ts(2026, 8, 16)
        e1 = _create_event(repo, eid, since + timedelta(hours=1))
        e2 = _create_event(repo, eid, until + timedelta(hours=1))
        _attach_signal(repo, e1.id, eid)
        _attach_signal(repo, e2.id, eid)

        builder = DigestBuilder(repo)
        report = builder.build(since=since, until=until)
        assert report.total_events == 1
        assert report.targets["target:alpha"].event_count == 1
        repo.close()

    def test_min_importance_filter(self, tmp_path):
        repo = Repository(tmp_path / "digest.db")
        eid = _entity_id(repo)
        now = _ts(2026, 8, 17, 12)
        e1 = _create_event(repo, eid, now - timedelta(hours=2), Importance.INTERESTING)
        e2 = _create_event(repo, eid, now - timedelta(hours=1), Importance.CRITICAL)
        _attach_signal(repo, e1.id, eid)
        _attach_signal(repo, e2.id, eid)

        builder = DigestBuilder(repo)
        report = builder.build(since=now - timedelta(days=1), until=now, min_importance=Importance.IMPORTANT)
        assert report.total_events == 1
        assert "critical" in report.importance_distribution
        assert "interesting" not in report.importance_distribution
        repo.close()

    def test_markdown_contains_expected_sections(self, tmp_path):
        repo = Repository(tmp_path / "digest.db")
        eid = _entity_id(repo)
        now = _ts(2026, 8, 17, 12)
        _create_event(repo, eid, now - timedelta(hours=1))
        _attach_signal(repo, eid, eid, value=json.dumps({"target_id": "target:alpha", "url": "https://a.com"}))

        builder = DigestBuilder(repo)
        report = builder.build(since=now - timedelta(days=1), until=now)
        md = report.to_markdown()
        assert "# Digest Report" in md
        assert "## Overview" in md
        assert "## By Target" in md
        assert "target:alpha" in md
        repo.close()

    def test_signal_summary_extraction(self, tmp_path):
        repo = Repository(tmp_path / "digest.db")
        eid = _entity_id(repo)
        now = _ts(2026, 8, 17, 12)
        e = _create_event(repo, eid, now - timedelta(hours=1))
        _attach_signal(
            repo,
            e.id,
            eid,
            value=json.dumps({
                "target_id": "target:alpha",
                "url": "https://example.com",
                "extracted_values": {"price": "19.99"},
            }),
        )

        builder = DigestBuilder(repo)
        report = builder.build(since=now - timedelta(days=1), until=now)
        target = report.targets["target:alpha"]
        assert "url=https://example.com" in target.latest_summary
        assert "price=19.99" in target.latest_summary
        repo.close()
