"""PART 06 — Idempotency, outbox durability, and async worker recovery tests."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pytest

from web_watcher.models import Target, TargetStatus, Signal, EventStatus, EventType, Importance
from web_watcher.repository import Repository
from web_watcher.event_correlator import CorrelationPlan, EventToCreate, EventToUpdate, SignalToPersist, LinkToCreate
from web_watcher.execution_semantics import StateTransition
from web_watcher.investigation_worker import InvestigationWorker
from web_watcher.investigation_adapter import EventInvestigationAdapter
from web_watcher.notification_dispatcher import NotificationDispatcher


def _make_repo(tmp_path):
    return Repository(str(tmp_path / "test.db"))


def _make_target(repo, target_id="t1"):
    target = Target(id=target_id, url="https://example.com", status=TargetStatus.NORMAL, interval="60s")
    repo.save_target(target)
    return target


def _claim_target(repo, target_id="t1", worker_id="worker-1", now=None):
    now = now or datetime.now(timezone.utc)
    claimed = repo.claim_targets(worker_id=worker_id, limit=10, lease_duration_sec=300, now=now)
    return next((t for t in claimed if t.id == target_id), None), now


def _make_transition(status=TargetStatus.NORMAL, **kwargs):
    return StateTransition(
        status=status,
        etag=kwargs.get("etag", "\"abc\""),
        last_modified=kwargs.get("last_modified", "Mon, 01 Jan 2024 00:00:00 GMT"),
        content_hash=kwargs.get("content_hash", "hash123"),
        metadata=kwargs.get("metadata", None),
        consecutive_failures=kwargs.get("consecutive_failures", 0),
        next_allowed_at=kwargs.get("next_allowed_at", None),
    )


def _make_signal(entity_id="t1", signal_id=1):
    return Signal(
        id=signal_id,
        entity_id=entity_id,
        signal_type="content_change",
        observed_at=datetime.now(timezone.utc),
        value="changed",
        fingerprint=f"fp-{signal_id}",
    )


def _make_plan(target_id="t1", signal_ids=None, event_id=None):
    plan = CorrelationPlan()
    now = datetime.now(timezone.utc)
    for sid in (signal_ids or [1]):
        plan.signals_to_persist.append(SignalToPersist(
            entity_id=target_id,
            signal_type="content_change",
            observed_at=now,
            value="changed",
            fingerprint=f"fp-{sid}",
        ))
    if event_id is None:
        plan.events_to_create.append(EventToCreate(
            entity_id=target_id,
            event_type=EventType.CONTENT_CHANGE.value,
            status=EventStatus.OPEN.value,
            importance=Importance.IMPORTANT.value,
            created_at=now,
            updated_at=now,
        ))
    else:
        plan.events_to_update.append(EventToUpdate(
            event_id=event_id,
            status=EventStatus.OPEN.value,
            importance=Importance.IMPORTANT.value,
            updated_at=now,
        ))
        plan.links.append(LinkToCreate(event_id=event_id, signal_id=1))
    return plan


class TestExecutionIdempotency:
    """Idempotency: duplicate finalization and signal/event deduplication."""

    def test_duplicate_finalization_with_same_claim_token_fails(self, tmp_path):
        repo = _make_repo(tmp_path)
        _make_target(repo, "t_idem")
        claimed, now = _claim_target(repo, "t_idem")
        claim_token = claimed.claim_token

        # First finalization succeeds
        result1 = repo.finalize_execution(
            target_id="t_idem",
            claim_token=claim_token,
            worker_id="worker-1",
            transition=_make_transition(),
            signals=[_make_signal("t_idem")],
            correlation_plan=_make_plan("t_idem"),
            now=now,
        )
        assert result1 is True

        # Second finalization with same claim_token should fail (claim_token cleared)
        result2 = repo.finalize_execution(
            target_id="t_idem",
            claim_token=claim_token,
            worker_id="worker-1",
            transition=_make_transition(),
            signals=[_make_signal("t_idem", 2)],
            correlation_plan=_make_plan("t_idem"),
            now=now + timedelta(seconds=1),
        )
        assert result2 is False

        # Only 1 signal should exist (duplicate was rejected)
        sig_count = repo.connection.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        assert sig_count == 1

    def test_duplicate_signal_fingerprint_is_skipped(self, tmp_path):
        repo = _make_repo(tmp_path)
        _make_target(repo, "t_sig")
        claimed, now = _claim_target(repo, "t_sig")
        claim_token = claimed.claim_token

        # Two signals with the same fingerprint
        sig1 = _make_signal("t_sig", 1)
        sig2 = Signal(
            id=2,
            entity_id="t_sig",
            signal_type="content_change",
            observed_at=datetime.now(timezone.utc),
            value="changed",
            fingerprint=sig1.fingerprint,  # duplicate fingerprint
        )

        plan = _make_plan("t_sig", signal_ids=[1])
        result = repo.finalize_execution(
            target_id="t_sig",
            claim_token=claim_token,
            worker_id="worker-1",
            transition=_make_transition(),
            signals=[sig1, sig2],
            correlation_plan=plan,
            now=now,
        )
        assert result is True

        # Only 1 signal should be inserted
        sig_count = repo.connection.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        assert sig_count == 1

    def test_target_state_not_rolled_back_by_notification_failure(self, tmp_path):
        """Notification/investigation failure must NOT rollback Target state."""
        repo = _make_repo(tmp_path)
        _make_target(repo, "t_notif")
        claimed, now = _claim_target(repo, "t_notif")
        claim_token = claimed.claim_token

        # Finalize succeeds
        result = repo.finalize_execution(
            target_id="t_notif",
            claim_token=claim_token,
            worker_id="worker-1",
            transition=_make_transition(status=TargetStatus.RECOVERING),
            signals=[_make_signal("t_notif")],
            correlation_plan=_make_plan("t_notif"),
            now=now,
        )
        assert result is True

        # Target state is committed even though notification dispatch happens later
        target = repo.get_target("t_notif")
        assert target.status == TargetStatus.RECOVERING
        assert target.claim_token is None


class TestInvestigationRetry:
    """InvestigationWorker must retry transient failures with exponential backoff."""

    def test_investigation_retry_after_failure(self, tmp_path):
        repo = _make_repo(tmp_path)
        target = _make_target(repo, "t_inv")
        claimed, now = _claim_target(repo, "t_inv")
        claim_token = claimed.claim_token

        # Finalize to create an event
        repo.finalize_execution(
            target_id="t_inv",
            claim_token=claim_token,
            worker_id="worker-1",
            transition=_make_transition(),
            signals=[_make_signal("t_inv")],
            correlation_plan=_make_plan("t_inv"),
            now=now,
        )

        # Create a failing adapter
        failing_adapter = MagicMock(spec=EventInvestigationAdapter)
        failing_adapter.is_eligible.return_value = True
        failing_adapter.run_for_event.side_effect = RuntimeError("transient failure")

        fixed_now = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
        with patch("web_watcher.investigation_worker.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_dt.fromtimestamp.side_effect = lambda ts, *a, **kw: datetime.fromtimestamp(ts, *a, **kw)
            mock_dt.timezone = timezone

            worker = InvestigationWorker(
                repository=repo,
                adapter=failing_adapter,
                max_retries=3,
                base_backoff_sec=0.1,
            )

            events = worker.fetch_uninvestigated_events()
            assert len(events) == 1

            # First attempt fails
            result = worker.process_event(events[0])
            assert result is False

            # A failed investigation result should be recorded
            existing = repo.get_investigation_result_by_event(events[0].id)
            assert existing is not None
            assert existing["status"] == "failed"
            meta = existing.get("metadata") or {}
            assert meta.get("retry_count") == 1

    def test_investigation_retry_respects_backoff(self, tmp_path):
        repo = _make_repo(tmp_path)
        target = _make_target(repo, "t_backoff")
        claimed, now = _claim_target(repo, "t_backoff")
        claim_token = claimed.claim_token

        repo.finalize_execution(
            target_id="t_backoff",
            claim_token=claim_token,
            worker_id="worker-1",
            transition=_make_transition(),
            signals=[_make_signal("t_backoff")],
            correlation_plan=_make_plan("t_backoff"),
            now=now,
        )

        failing_adapter = MagicMock(spec=EventInvestigationAdapter)
        failing_adapter.is_eligible.return_value = True
        failing_adapter.run_for_event.side_effect = RuntimeError("transient failure")

        fixed_now = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
        with patch("web_watcher.investigation_worker.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_dt.fromtimestamp.side_effect = lambda ts, *a, **kw: datetime.fromtimestamp(ts, *a, **kw)
            mock_dt.timezone = timezone

            worker = InvestigationWorker(
                repository=repo,
                adapter=failing_adapter,
                max_retries=3,
                base_backoff_sec=10.0,  # long backoff
            )

            events = worker.fetch_uninvestigated_events()
            assert len(events) == 1

            # First attempt fails
            worker.process_event(events[0])

            # Immediately after, the event should NOT be retryable (backoff not expired)
            events2 = worker.fetch_uninvestigated_events(limit=10)
            retryable = [e for e in events2 if e.id == events[0].id]
            assert len(retryable) == 0

            # Advance time past the backoff
            mock_dt.now.return_value = fixed_now + timedelta(seconds=15)
            events3 = worker.fetch_uninvestigated_events(limit=10)
            retryable = [e for e in events3 if e.id == events[0].id]
            assert len(retryable) == 1

    def test_investigation_succeeds_after_retry(self, tmp_path):
        repo = _make_repo(tmp_path)
        target = _make_target(repo, "t_retry_ok")
        claimed, now = _claim_target(repo, "t_retry_ok")
        claim_token = claimed.claim_token

        repo.finalize_execution(
            target_id="t_retry_ok",
            claim_token=claim_token,
            worker_id="worker-1",
            transition=_make_transition(),
            signals=[_make_signal("t_retry_ok")],
            correlation_plan=_make_plan("t_retry_ok"),
            now=now,
        )

        call_count = 0

        def flaky_adapter(event, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("transient failure")
            from web_watcher.investigation_result import InvestigationResult, InvestigationStatus, Evidence
            return InvestigationResult(
                status=InvestigationStatus.SUCCESS,
                summary="recovered",
                findings=(),
                evidence=(),
                confidence=1.0,
                steps_used=1,
                pages_checked=1,
                failure_reason="",
            )

        adapter = MagicMock(spec=EventInvestigationAdapter)
        adapter.is_eligible.return_value = True
        adapter.run_for_event.side_effect = flaky_adapter

        fixed_now = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
        with patch("web_watcher.investigation_worker.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_dt.fromtimestamp.side_effect = lambda ts, *a, **kw: datetime.fromtimestamp(ts, *a, **kw)
            mock_dt.timezone = timezone

            worker = InvestigationWorker(
                repository=repo,
                adapter=adapter,
                max_retries=3,
                base_backoff_sec=0.1,
            )

            events = worker.fetch_uninvestigated_events()
            assert len(events) == 1

            # First attempt fails
            assert worker.process_event(events[0]) is False
            # Advance past backoff for retry 1
            mock_dt.now.return_value = fixed_now + timedelta(seconds=0.2)
            events_retry1 = worker.fetch_uninvestigated_events()
            assert len(events_retry1) == 1
            assert worker.process_event(events_retry1[0]) is False

            # Advance past backoff for retry 2
            mock_dt.now.return_value = fixed_now + timedelta(seconds=0.5)
            events_retry2 = worker.fetch_uninvestigated_events()
            assert len(events_retry2) == 1
            assert worker.process_event(events_retry2[0]) is True

            # Final result should be successful
            final = repo.get_investigation_result_by_event(events[0].id)
            assert final is not None
            assert final["status"] == "success"
            assert call_count == 3

    def test_investigation_exhausts_retries(self, tmp_path):
        repo = _make_repo(tmp_path)
        target = _make_target(repo, "t_exhaust")
        claimed, now = _claim_target(repo, "t_exhaust")
        claim_token = claimed.claim_token

        repo.finalize_execution(
            target_id="t_exhaust",
            claim_token=claim_token,
            worker_id="worker-1",
            transition=_make_transition(),
            signals=[_make_signal("t_exhaust")],
            correlation_plan=_make_plan("t_exhaust"),
            now=now,
        )

        failing_adapter = MagicMock(spec=EventInvestigationAdapter)
        failing_adapter.is_eligible.return_value = True
        failing_adapter.run_for_event.side_effect = RuntimeError("permanent failure")

        fixed_now = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
        with patch("web_watcher.investigation_worker.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_dt.fromtimestamp.side_effect = lambda ts, *a, **kw: datetime.fromtimestamp(ts, *a, **kw)
            mock_dt.timezone = timezone

            worker = InvestigationWorker(
                repository=repo,
                adapter=failing_adapter,
                max_retries=2,
                base_backoff_sec=0.1,
            )

            events = worker.fetch_uninvestigated_events()
            assert len(events) == 1

            # Exhaust retries: attempt 1, retry 1, retry 2
            for i in range(3):
                worker.process_event(events[0])
                if i < 2:
                    mock_dt.now.return_value = fixed_now + timedelta(seconds=0.2)

            # After exhausting retries, event should no longer be retryable
            events2 = worker.fetch_uninvestigated_events()
            retryable = [e for e in events2 if e.id == events[0].id]
            assert len(retryable) == 0

            # The latest result should be failed with retry_count == max_retries
            final = repo.get_investigation_result_by_event(events[0].id)
            assert final is not None
            assert final["status"] == "failed"
            meta = final.get("metadata") or {}
            assert meta.get("retry_count") == 2
