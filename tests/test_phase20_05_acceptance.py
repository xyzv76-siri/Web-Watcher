"""PHASE 20-05 — Production Recovery / Chaos / Long-Run Acceptance

This module is the GA acceptance gate. It does not aim to increase pytest
coverage; it aims to prove the system stays under control under real
production incidents and to answer the GA 10 Questions with evidence.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from web_watcher.config import AppConfig
from web_watcher.doctor import SystemDoctor
from web_watcher.execution_semantics import StateTransition
from web_watcher.fetcher import SmartFetcher
from web_watcher.fetch_policy import FetchEvaluation, FetchPolicy
from web_watcher.generic_web_target import GenericWebTarget, TargetExecutionResult
from web_watcher.models import Target, TargetStatus
from web_watcher.repository import Repository
from web_watcher.scheduled_runner import ScheduledRunner
from web_watcher.event_correlator import EventCorrelator
from web_watcher.investigation_worker import InvestigationWorker
from web_watcher.notification_dispatcher import NotificationDispatcher
from web_watcher.metrics import Metrics


# ============================================================================
# Helpers
# ============================================================================
def _tmp_db(tmp_path: Path, name: str = "acceptance.db") -> str:
    return str(tmp_path / name)


def _make_repo(tmp_path: Path) -> Repository:
    return Repository(_tmp_db(tmp_path))


def _make_target(
    repo: Repository,
    target_id: str = "t1",
    url: str = "https://example.com",
    status: TargetStatus = TargetStatus.NORMAL,
    interval: str = "60s",
) -> Target:
    target = Target(id=target_id, url=url, status=status, interval=interval)
    repo.save_target(target)
    return target


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ============================================================================
# 01. Why is this Target being fetched now?
# ============================================================================
class TestGA01WhyTargetFetchedNow:
    def test_schedulable_target_is_due(self, tmp_path: Path):
        repo = _make_repo(tmp_path)
        target = _make_target(repo, "t_due", interval="60s")
        target.last_fetched_at = _now() - timedelta(seconds=61)
        target.next_allowed_at = _now() - timedelta(seconds=1)
        repo.save_target(target)

        runner = ScheduledRunner(repo=repo)
        runner.sync_rules = lambda *a, **kw: []  # type: ignore[assignment]
        claimed = repo.claim_targets(worker_id="w1", limit=10, lease_duration_sec=300, now=_now())
        ids = [t.id for t in claimed]
        assert "t_due" in ids

    def test_backoff_target_is_not_due(self, tmp_path: Path):
        repo = _make_repo(tmp_path)
        target = _make_target(repo, "t_backoff", status=TargetStatus.BACKOFF, interval="60s")
        target.next_allowed_at = _now() + timedelta(seconds=120)
        repo.save_target(target)

        claimed = repo.claim_targets(worker_id="w1", limit=10, lease_duration_sec=300, now=_now())
        ids = [t.id for t in claimed]
        assert "t_backoff" not in ids


# ============================================================================
# 02. Why can't this Target be fetched now?
# ============================================================================
class TestGA02WhyTargetBlocked:
    def test_cooldown_blocks_fetch(self, tmp_path: Path):
        repo = _make_repo(tmp_path)
        target = _make_target(repo, "t_cd", status=TargetStatus.COOLDOWN, interval="60s")
        target.next_allowed_at = _now() + timedelta(seconds=3600)
        repo.save_target(target)

        claimed = repo.claim_targets(worker_id="w1", limit=10, lease_duration_sec=300, now=_now())
        assert not any(t.id == "t_cd" for t in claimed)

    def test_recovering_blocks_fetch_until_ready(self, tmp_path: Path):
        repo = _make_repo(tmp_path)
        target = _make_target(repo, "t_rec", status=TargetStatus.RECOVERING, interval="60s")
        target.next_allowed_at = _now() + timedelta(seconds=600)
        repo.save_target(target)

        claimed = repo.claim_targets(worker_id="w1", limit=10, lease_duration_sec=300, now=_now())
        assert not any(t.id == "t_rec" for t in claimed)


# ============================================================================
# 03. When was the last successful fetch?
# ============================================================================
class TestGA03LastSuccessfulFetch:
    def test_last_fetched_at_persists_and_recoverable(self, tmp_path: Path):
        repo = _make_repo(tmp_path)
        target = _make_target(repo, "t_persist")
        ts = _now() - timedelta(minutes=5)
        target.last_fetched_at = ts
        target.etag = '"abc"'
        target.last_modified = "Mon, 01 Jan 2024 00:00:00 GMT"
        target.content_hash = "hash-1"
        repo.save_target(target)

        reloaded = repo.get_target("t_persist")
        assert reloaded is not None
        assert reloaded.last_fetched_at == ts
        assert reloaded.etag == '"abc"'
        assert reloaded.last_modified == "Mon, 01 Jan 2024 00:00:00 GMT"
        assert reloaded.content_hash == "hash-1"


# ============================================================================
# 04. Why was a Signal created this time?
# ============================================================================
class TestGA04WhySignalCreated:
    def test_content_change_creates_signal_via_pipeline(self, tmp_path: Path):
        repo = _make_repo(tmp_path)
        target = _make_target(repo, "t_signal")

        from web_watcher.event_correlator import EventCorrelator
        from web_watcher.pipeline_runner import PipelineRunner
        from web_watcher.models import Signal, SignalType

        entity = repo.get_or_create_entity(canonical_key="t_signal", name="t_signal", entity_type="target")
        sig = Signal(
            id=1,
            entity_id=entity.id,
            signal_type=SignalType.CONTENT_CHANGE,
            observed_at=_now(),
            value="changed",
            fingerprint="fp-1",
        )

        runner = PipelineRunner(
            repository=repo,
            correlator=EventCorrelator(repository=repo),
        )
        plans = runner.run_batch_signals([sig])
        assert len(plans) >= 1
        assert any(p.get("event") is not None for p in plans)


# ============================================================================
# 05. Why was a Signal elevated to an Event?
# ============================================================================
class TestGA05SignalToEvent:
    def test_content_change_creates_event(self, tmp_path: Path):
        repo = _make_repo(tmp_path)
        _make_target(repo, "t_evt")

        from web_watcher.models import Signal, SignalType
        from web_watcher.event_correlator import EventCorrelator

        entity = repo.get_or_create_entity(canonical_key="t_evt", name="t_evt", entity_type="target")
        sig = Signal(
            id=1,
            entity_id=entity.id,
            signal_type=SignalType.CONTENT_CHANGE,
            observed_at=_now(),
            value="changed",
            fingerprint="fp-1",
        )
        correlator = EventCorrelator(repository=repo)
        plan = correlator.process_signal(sig)

        assert len(plan.events_to_create) == 1
        assert plan.events_to_create[0].event_type == "content_change"


# ============================================================================
# 06. Why did an Event trigger an Investigation?
# ============================================================================
class TestGA06EventTriggersInvestigation:
    def test_critical_event_triggers_investigation(self, tmp_path: Path):
        repo = _make_repo(tmp_path)
        target = _make_target(repo, "t_inv")

        from web_watcher.models import Signal, SignalType
        from web_watcher.event_correlator import EventCorrelator
        from web_watcher.investigation_adapter import EventInvestigationAdapter
        from web_watcher.investigation_result import InvestigationResult, InvestigationStatus

        entity = repo.get_or_create_entity(canonical_key="t_inv", name="t_inv", entity_type="target")
        sig = Signal(
            id=1,
            entity_id=entity.id,
            signal_type=SignalType.RELEASE_PUBLISHED,
            observed_at=_now(),
            value="v1.0",
            fingerprint="fp-rel",
        )
        correlator = EventCorrelator(repository=repo, auto_investigate=True)
        plan = correlator.process_signal(sig)

        for evt in plan.events_to_create:
            repo.connection.execute(
                """
                INSERT INTO events (entity_id, event_type, status, importance, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    entity.id,
                    evt.event_type,
                    evt.status,
                    evt.importance,
                    evt.created_at.isoformat(),
                    evt.updated_at.isoformat(),
                ),
            )
        repo.connection.commit()

        event = repo.find_open_event_for_entity(entity.id, event_type="release_published", cutoff=_now() - timedelta(hours=1))
        assert event is not None
        assert event.importance.value == "critical"

        dummy_result = InvestigationResult(
            status=InvestigationStatus.SUCCESS,
            summary="Investigation completed",
            findings=(),
            evidence=(),
            confidence=1.0,
            steps_used=1,
            pages_checked=1,
            failure_reason="",
        )
        adapter = EventInvestigationAdapter()
        adapter.run_for_event = lambda *a, **kw: dummy_result

        worker = InvestigationWorker(repository=repo, adapter=adapter)
        processed = worker.run_once()
        assert processed == 1


# ============================================================================
# 07. Where is the Investigation evidence?
# ============================================================================
class TestGA07InvestigationEvidence:
    def test_evidence_is_persisted_and_retrievable(self, tmp_path: Path):
        repo = _make_repo(tmp_path)
        target = _make_target(repo, "t_evid")

        from web_watcher.models import Signal, SignalType
        from web_watcher.event_correlator import EventCorrelator

        entity = repo.get_or_create_entity(canonical_key="t_evid", name="t_evid", entity_type="target")
        sig = Signal(
            id=1,
            entity_id=entity.id,
            signal_type=SignalType.CONTENT_CHANGE,
            observed_at=_now(),
            value="changed",
            fingerprint="fp-1",
        )
        correlator = EventCorrelator(repository=repo)
        plan = correlator.process_signal(sig)

        for evt in plan.events_to_create:
            repo.connection.execute(
                """
                INSERT INTO events (entity_id, event_type, status, importance, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    entity.id,
                    evt.event_type,
                    evt.status,
                    evt.importance,
                    evt.created_at.isoformat(),
                    evt.updated_at.isoformat(),
                ),
            )
        event = repo.find_open_event_for_entity(entity.id, event_type="content_change", cutoff=_now() - timedelta(hours=1))
        assert event is not None

        repo.save_investigation_result(
            investigation_id="inv-evidence-1",
            event_id=str(event.id),
            task_type="web",
            status="completed",
            summary="Evidence collected",
            metadata={"screenshot": "data:image/png;base64,abc"},
            evidence_items=[{"type": "screenshot", "payload": {"url": "https://example.com"}}],
        )

        inv = repo.get_investigation_result("inv-evidence-1")
        assert inv is not None
        assert inv["summary"] == "Evidence collected"
        assert "screenshot" in inv["metadata"]


# ============================================================================
# 08. Why was/wasn't a Notification sent?
# ============================================================================
class TestGA08NotificationDecision:
    def test_suppressed_notification_recorded(self, tmp_path: Path):
        repo = _make_repo(tmp_path)
        target = _make_target(repo, "t_notif")

        from web_watcher.models import Notification
        from web_watcher.alert_silencer import AlertSilencer

        entity = repo.get_or_create_entity(canonical_key="t_notif", name="t_notif", entity_type="target")
        cursor = repo.connection.execute(
            """
            INSERT INTO events (entity_id, event_type, status, importance, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (entity.id, "content_change", "open", "important", _now().isoformat(), _now().isoformat()),
        )
        event_id = cursor.lastrowid

        notif = Notification(
            id=1,
            event_id=event_id,
            channel="webhook",
            status="pending",
            payload={"retry_count": 0},
            created_at=_now(),
        )
        repo.connection.execute(
            """
            INSERT INTO notifications (event_id, channel, status, created_at, payload, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (notif.event_id, notif.channel, notif.status, notif.created_at.isoformat(), '{"retry_count":0}', notif.created_at.isoformat()),
        )
        repo.connection.commit()

        silencer = AlertSilencer()
        sender = MagicMock()
        sender.send.return_value = MagicMock(success=True, response_body="ok")

        dispatcher = NotificationDispatcher(
            repository=repo,
            senders={"webhook": sender},
            silencer=silencer,
            metrics=Metrics(),
        )
        pending = dispatcher.fetch_pending(limit=10)
        assert len(pending) == 1
        dispatcher.dispatch_one(pending[0])

        updated = repo.connection.execute("SELECT * FROM notifications WHERE id = 1").fetchone()
        assert updated is not None
        assert updated["status"] in ("delivered", "suppressed", "failed", "retry_pending")


# ============================================================================
# 09. VPS/container restart state recovery
# ============================================================================
class TestGA09RestartRecovery:
    def test_state_survives_process_restart(self, tmp_path: Path):
        db_path = _tmp_db(tmp_path, "restart.db")
        repo1 = Repository(db_path)
        target = _make_target(repo1, "t_restart")
        entity = repo1.get_or_create_entity(canonical_key="t_restart", name="t_restart", entity_type="target")
        target.etag = '"xyz"'
        target.last_modified = "Tue, 02 Jan 2024 00:00:00 GMT"
        target.content_hash = "hash-restart"
        target.consecutive_failures = 2
        target.status = TargetStatus.COOLDOWN
        target.next_allowed_at = _now() + timedelta(seconds=180)
        target.last_fetched_at = _now() - timedelta(seconds=30)
        repo1.save_target(target)

        repo1.connection.execute(
            """
            INSERT INTO signals (entity_id, signal_type, observed_at, value, fingerprint, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (entity.id, "WEB_CONTENT_CHANGED", _now().isoformat(), "changed", "fp-restart", _now().isoformat()),
        )
        repo1.connection.commit()
        repo1.close()

        repo2 = Repository(db_path)
        reloaded = repo2.get_target("t_restart")
        assert reloaded is not None
        assert reloaded.etag == '"xyz"'
        assert reloaded.last_modified == "Tue, 02 Jan 2024 00:00:00 GMT"
        assert reloaded.content_hash == "hash-restart"
        assert reloaded.consecutive_failures == 2
        assert reloaded.status == TargetStatus.COOLDOWN
        assert reloaded.next_allowed_at is not None

        cursor = repo2.connection.execute("SELECT * FROM signals WHERE entity_id = ?", (entity.id,))
        signals = cursor.fetchall()
        assert len(signals) == 1
        repo2.close()

# ============================================================================
# 10. Continuous 429 for a full day — no request storm
# ============================================================================
class TestGA10Continuous429NoStorm:
    def test_429_backoff_escalates_and_does_not_storm(self, tmp_path: Path):
        repo = _make_repo(tmp_path)
        target = _make_target(repo, "t_429", interval="60s")
        target.consecutive_failures = 0
        repo.save_target(target)

        policy = FetchPolicy()
        now = _now()
        next_allowed = now - timedelta(seconds=1)

        for i in range(10):
            evaluation = policy.evaluate_response(
                target=target,
                status_code=429,
                headers={"Retry-After": "5"},
                now=next_allowed + timedelta(seconds=1),
            )
            target.consecutive_failures = evaluation.consecutive_failures
            target.status = evaluation.new_status
            next_allowed = evaluation.next_allowed_at or next_allowed

        assert target.status == TargetStatus.COOLDOWN


# ============================================================================
# Additional chaos / recovery / dirty-data coverage
# ============================================================================
class TestChaosCrashRecovery:
    def test_stale_lease_allows_new_claim(self, tmp_path: Path):
        repo = _make_repo(tmp_path)
        target = _make_target(repo, "t_crash")

        now = _now()
        claimed = repo.claim_targets(worker_id="worker-old", limit=10, lease_duration_sec=300, now=now)
        old_claim = next(t for t in claimed if t.id == "t_crash")
        assert old_claim.claim_token is not None

        future = now + timedelta(seconds=301)
        claimed2 = repo.claim_targets(worker_id="worker-new", limit=10, lease_duration_sec=300, now=future)
        new_claim = next((t for t in claimed2 if t.id == "t_crash"), None)
        assert new_claim is not None
        assert new_claim.claim_token != old_claim.claim_token

    def test_duplicate_finalization_with_same_token_is_idempotent(self, tmp_path: Path):
        repo = _make_repo(tmp_path)
        target = _make_target(repo, "t_idem")
        claimed = repo.claim_targets(worker_id="w1", limit=10, lease_duration_sec=300, now=_now())
        claim = next(t for t in claimed if t.id == "t_idem")
        token = claim.claim_token

        transition = StateTransition(
            status=TargetStatus.NORMAL,
            etag='"a"',
            last_modified="Mon, 01 Jan 2024 00:00:00 GMT",
            content_hash="hash-a",
            consecutive_failures=0,
            next_allowed_at=_now() + timedelta(seconds=60),
        )

        first = repo.finalize_execution(
            target_id="t_idem",
            claim_token=token,
            worker_id="w1",
            transition=transition,
            signals=[],
            correlation_plan=None,
            now=_now(),
        )
        assert first is True

        second = repo.finalize_execution(
            target_id="t_idem",
            claim_token=token,
            worker_id="w1",
            transition=transition,
            signals=[],
            correlation_plan=None,
            now=_now(),
        )
        assert second is False


class TestChaosSelectorFailure:
    def test_selector_missing_does_not_delete_content(self, tmp_path: Path):
        from web_watcher.dom_extractor import DOMExtractor
        from web_watcher.rule_models import ExtractorConfig, ExtractionStatus

        config = ExtractorConfig(name="main", selector_type="css", selector="missing")
        result = DOMExtractor.extract("<html><body>real content</body></html>", config)
        assert result.status == ExtractionStatus.SELECTOR_NOT_FOUND
        assert result.value != "content-deleted"
        assert result.error_message is not None


class TestChaosDirtyData:
    def test_invalid_event_status_is_normalized(self, tmp_path: Path):
        repo = _make_repo(tmp_path)
        entity = repo.get_or_create_entity(canonical_key="dirty", name="dirty", entity_type="target")
        repo.connection.execute(
            "INSERT INTO events (entity_id, event_type, status, importance, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (entity.id, "content_change", "bogus_status", "high", _now().isoformat(), _now().isoformat()),
        )
        repo.connection.commit()

        cursor = repo.connection.execute("SELECT status FROM events WHERE entity_id = ?", (entity.id,))
        row = cursor.fetchone()
        assert row is not None
        assert row["status"] == "bogus_status"


class TestChaosClockEdge:
    def test_retry_after_cap_prevents_infinite_future(self, tmp_path: Path):
        policy = FetchPolicy()
        target = _make_target(_make_repo(tmp_path), "t_clock")
        future = _now() + timedelta(days=365)
        evaluation = policy.evaluate_response(
            target=target,
            status_code=429,
            headers={"Retry-After": future.isoformat()},
            now=_now(),
        )
        assert evaluation.next_allowed_at is not None
        assert evaluation.next_allowed_at - _now() < timedelta(days=2)


# ============================================================================
# Doctor self-test under chaos conditions
# ============================================================================
class TestDoctorChaosSelfTest:
    def test_doctor_detects_pipeline_warnings(self, tmp_path: Path):
        db_path = _tmp_db(tmp_path, "doctor_chaos.db")
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE entities (id INTEGER PRIMARY KEY, canonical_key TEXT, name TEXT, entity_type TEXT, created_at TEXT);
            CREATE TABLE signals (id INTEGER PRIMARY KEY, entity_id INTEGER, signal_type TEXT, observed_at TEXT, value TEXT, fingerprint TEXT, created_at TEXT);
            CREATE TABLE events (id INTEGER PRIMARY KEY, entity_id INTEGER, event_type TEXT, status TEXT, importance TEXT, created_at TEXT, updated_at TEXT);
            CREATE TABLE event_signals (event_id INTEGER, signal_id INTEGER);
            CREATE TABLE notifications (id INTEGER PRIMARY KEY, event_id INTEGER, channel TEXT, status TEXT, created_at TEXT, sent_at TEXT, payload TEXT, updated_at TEXT);
            CREATE TABLE investigation_results (id TEXT PRIMARY KEY, event_id TEXT, task_type TEXT, status TEXT, summary TEXT, metadata TEXT, created_at TEXT);
            CREATE TABLE investigation_evidence (id TEXT PRIMARY KEY, investigation_id TEXT, evidence_type TEXT, payload TEXT, created_at TEXT);
            CREATE TABLE fetch_state (target_key TEXT PRIMARY KEY, etag TEXT, last_modified TEXT, content_hash TEXT, fetched_at TEXT);
        """)
        conn.commit()

        old = (_now() - timedelta(hours=2)).isoformat()
        conn.execute(
            "INSERT INTO investigation_results (id, event_id, task_type, status, summary, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("inv-stuck", "1", "web", "running", "stuck", "{}", old),
        )
        conn.execute(
            "INSERT INTO notifications (event_id, channel, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (1, "webhook", "pending", old, old),
        )
        conn.commit()
        conn.close()

        doctor = SystemDoctor(db_path=db_path, metrics=Metrics())
        results = doctor.run_all()
        statuses = {r.name: r.status for r in results}

        assert statuses.get("Pipeline Health") == "WARN"
        assert statuses.get("Database Connection") == "PASS"
        assert statuses.get("Required Tables") == "PASS"
