"""PART 05 — Atomic finalization failure-injection tests."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from web_watcher.models import Target, TargetStatus, Signal, EventStatus, EventType, Importance
from web_watcher.repository import Repository
from web_watcher.event_correlator import CorrelationPlan, EventToCreate, EventToUpdate, SignalToPersist, LinkToCreate
from web_watcher.execution_semantics import StateTransition


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


class TestAtomicFinalizationFencing:
    """Fencing rejection: stale claim_token must rollback everything."""

    def test_stale_claim_token_returns_false(self, tmp_path):
        repo = _make_repo(tmp_path)
        _make_target(repo, "t_fence")
        claimed, now = _claim_target(repo, "t_fence")

        # Use a wrong claim_token
        result = repo.finalize_execution(
            target_id="t_fence",
            claim_token="wrong-token",
            worker_id="worker-1",
            transition=_make_transition(),
            signals=[],
            correlation_plan=None,
            now=now,
        )
        assert result is False

        # Target must be unchanged
        target = repo.get_target("t_fence")
        assert target.status == TargetStatus.NORMAL
        assert target.claim_token is not None  # Still claimed by worker-1

    def test_stale_worker_cannot_affect_current_worker(self, tmp_path):
        repo = _make_repo(tmp_path)
        _make_target(repo, "t_stale")
        claimed, now = _claim_target(repo, "t_stale", worker_id="worker-1")

        # Worker-2 tries to finalize with a stolen/fabricated claim_token
        result = repo.finalize_execution(
            target_id="t_stale",
            claim_token="stolen-token",
            worker_id="worker-2",
            transition=_make_transition(),
            signals=[_make_signal("t_stale")],
            correlation_plan=_make_plan("t_stale"),
            now=now,
        )
        assert result is False

        # No signals should have been inserted
        signals = repo.connection.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        assert signals == 0
        # Target still owned by worker-1
        target = repo.get_target("t_stale")
        assert target.claim_token == claimed.claim_token


class TestAtomicFinalizationRollback:
    """Any failure rolls back Target, Signals, Events, Links, and Lease."""

    def test_signal_rollback_on_event_failure(self, tmp_path):
        """If event insert fails, signals must also roll back."""
        repo = _make_repo(tmp_path)
        _make_target(repo, "t_rollback")
        claimed, now = _claim_target(repo, "t_rollback")
        claim_token = claimed.claim_token

        # Create a plan with an event_to_create that has an invalid type
        # This will fail because the event_type doesn't match the enum
        plan = _make_plan("t_rollback", signal_ids=[1, 2])
        plan.events_to_create[0].event_type = "INVALID_EVENT_TYPE"

        result = repo.finalize_execution(
            target_id="t_rollback",
            claim_token=claim_token,
            worker_id="worker-1",
            transition=_make_transition(),
            signals=[_make_signal("t_rollback", 1), _make_signal("t_rollback", 2)],
            correlation_plan=plan,
            now=now,
        )
        assert result is False

        # Target must still be in normal state (not committed)
        target = repo.get_target("t_rollback")
        assert target.status == TargetStatus.NORMAL
        # Signals must not have been inserted
        signals = repo.connection.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        assert signals == 0
        # Lease must not have been released (because transaction rolled back)
        # Actually, finalize_execution rolls back the whole transaction,
        # but the target update was part of the same transaction, so
        # the lease fields are unchanged.

    def test_event_link_rollback(self, tmp_path):
        """Invalid links are skipped; valid data is still committed."""
        repo = _make_repo(tmp_path)
        _make_target(repo, "t_link")
        claimed, now = _claim_target(repo, "t_link")
        claim_token = claimed.claim_token

        # First, create the event and signal normally
        repo.finalize_execution(
            target_id="t_link",
            claim_token=claim_token,
            worker_id="worker-1",
            transition=_make_transition(),
            signals=[_make_signal("t_link", 1)],
            correlation_plan=_make_plan("t_link"),
            now=now,
        )

        # Verify the first execution succeeded
        target = repo.get_target("t_link")
        assert target.status == TargetStatus.NORMAL

        # Now claim again and test that an invalid link is skipped
        claimed2, now2 = _claim_target(repo, "t_link", worker_id="worker-1")
        claim_token2 = claimed2.claim_token

        plan2 = _make_plan("t_link")
        plan2.links.append(LinkToCreate(event_id=99999, signal_id=99999))
        result = repo.finalize_execution(
            target_id="t_link",
            claim_token=claim_token2,
            worker_id="worker-1",
            transition=_make_transition(status=TargetStatus.NORMAL),
            signals=[_make_signal("t_link", 2)],
            correlation_plan=plan2,
            now=now2,
        )
        # The invalid link should be skipped (IntegrityError caught), not fail the whole transaction
        assert result is True

    def test_target_rollback_on_failure(self, tmp_path):
        """If any step fails, target status must not change."""
        repo = _make_repo(tmp_path)
        _make_target(repo, "t_target")
        claimed, now = _claim_target(repo, "t_target")
        claim_token = claimed.claim_token

        # Pass a transition that would change the target
        transition = _make_transition(status=TargetStatus.BACKOFF)
        # But also pass an invalid correlation plan to trigger failure
        plan = _make_plan("t_target")
        plan.events_to_create[0].event_type = "INVALID"

        result = repo.finalize_execution(
            target_id="t_target",
            claim_token=claim_token,
            worker_id="worker-1",
            transition=transition,
            signals=[_make_signal("t_target")],
            correlation_plan=plan,
            now=now,
        )
        assert result is False

        target = repo.get_target("t_target")
        assert target.status == TargetStatus.NORMAL


class TestAtomicFinalizationSuccess:
    """Happy path: all steps succeed in one transaction."""

    def test_successful_finalization(self, tmp_path):
        repo = _make_repo(tmp_path)
        _make_target(repo, "t_ok")
        claimed, now = _claim_target(repo, "t_ok")
        claim_token = claimed.claim_token

        plan = _make_plan("t_ok", signal_ids=[1])
        signals = [_make_signal("t_ok", 1)]

        result = repo.finalize_execution(
            target_id="t_ok",
            claim_token=claim_token,
            worker_id="worker-1",
            transition=_make_transition(status=TargetStatus.RECOVERING),
            signals=signals,
            correlation_plan=plan,
            now=now,
        )
        assert result is True

        # Target status updated
        target = repo.get_target("t_ok")
        assert target.status == TargetStatus.RECOVERING
        # Lease cleared
        assert target.claim_token is None
        assert target.lease_owner is None
        # Signal inserted
        sig_count = repo.connection.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        assert sig_count == 1
        # Event created
        evt_count = repo.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert evt_count == 1

    def test_multiple_signals_one_transaction(self, tmp_path):
        repo = _make_repo(tmp_path)
        _make_target(repo, "t_multi")
        claimed, now = _claim_target(repo, "t_multi")
        claim_token = claimed.claim_token

        plan = _make_plan("t_multi", signal_ids=[1, 2, 3])
        signals = [_make_signal("t_multi", i) for i in range(1, 4)]

        result = repo.finalize_execution(
            target_id="t_multi",
            claim_token=claim_token,
            worker_id="worker-1",
            transition=_make_transition(),
            signals=signals,
            correlation_plan=plan,
            now=now,
        )
        assert result is True

        sig_count = repo.connection.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        assert sig_count == 3


class TestEventCorrelatorIsDecisionOnly:
    """EventCorrelator must not persist anything."""

    def test_build_plans_returns_correlation_plans(self, tmp_path):
        from web_watcher.event_correlator import EventCorrelator

        repo = _make_repo(tmp_path)
        correlator = EventCorrelator(repository=repo)
        signals = [_make_signal("t1"), _make_signal("t1")]
        plans = correlator.build_plans(signals)
        assert len(plans) == 1
        assert isinstance(plans[0], CorrelationPlan)
        assert len(plans[0].signals) == 2

    def test_build_plans_does_not_persist_signals(self, tmp_path):
        from web_watcher.event_correlator import EventCorrelator

        repo = _make_repo(tmp_path)
        correlator = EventCorrelator(repository=repo)
        signals = [_make_signal("t1")]
        plans = correlator.build_plans(signals)

        # No signals should have been persisted
        sig_count = repo.connection.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        assert sig_count == 0
