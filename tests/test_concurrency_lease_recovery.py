"""PART 04 — Concurrency, lease, fencing, and crash recovery tests."""

from datetime import datetime, timedelta, timezone
import pytest

from web_watcher.models import Target, TargetStatus, Signal
from web_watcher.repository import Repository
from web_watcher.execution_semantics import StateTransition


def _make_repo(tmp_path):
    return Repository(str(tmp_path / "concurrency.db"))


def _make_target(repo, target_id="t1", status=TargetStatus.NORMAL):
    target = Target(id=target_id, url="https://example.com", status=status, interval="60s")
    repo.save_target(target)
    return target


def _claim_target(repo, target_id="t1", worker_id="worker-1", now=None):
    now = now or datetime.now(timezone.utc)
    claimed = repo.claim_targets(worker_id=worker_id, limit=10, lease_duration_sec=300, now=now)
    return next((t for t in claimed if t.id == target_id), None), now


def _make_transition(status=TargetStatus.NORMAL):
    return StateTransition(
        status=status,
        etag='"abc"',
        last_modified="Mon, 01 Jan 2024 00:00:00 GMT",
        content_hash="hash123",
        consecutive_failures=0,
        next_allowed_at=None,
    )


def _make_signal(entity_id="t1"):
    return Signal(
        id=1,
        entity_id=entity_id,
        signal_type="content_change",
        observed_at=datetime.now(timezone.utc),
        value="changed",
        fingerprint="fp-1",
    )


class TestTwoWorkersSameTarget:
    """Two workers must not both successfully claim the same target."""

    def test_two_workers_only_one_claims(self, tmp_path):
        repo = _make_repo(tmp_path)
        _make_target(repo, "t_shared")

        now = datetime.now(timezone.utc)

        # Worker A claims
        claimed_a = repo.claim_targets(worker_id="worker-a", limit=10, lease_duration_sec=300, now=now)
        a_claim = next((t for t in claimed_a if t.id == "t_shared"), None)
        assert a_claim is not None
        assert a_claim.claim_token is not None

        # Worker B tries to claim the same target in the same batch
        claimed_b = repo.claim_targets(worker_id="worker-b", limit=10, lease_duration_sec=300, now=now)
        b_claim = next((t for t in claimed_b if t.id == "t_shared"), None)
        assert b_claim is None, "Worker B must not claim a target already claimed by Worker A"

        # Target must still be owned by Worker A
        target = repo.get_target("t_shared")
        assert target.claim_token == a_claim.claim_token
        assert target.lease_owner == "worker-a"

    def test_two_workers_different_targets_both_claim(self, tmp_path):
        repo = _make_repo(tmp_path)
        _make_target(repo, "t_a")
        _make_target(repo, "t_b")

        now = datetime.now(timezone.utc)
        # Worker A claims only 1 target
        claimed_a = repo.claim_targets(worker_id="worker-a", limit=1, lease_duration_sec=300, now=now)
        assert len(claimed_a) == 1
        assert claimed_a[0].id in ("t_a", "t_b")

        # Worker B claims the remaining target
        claimed_b = repo.claim_targets(worker_id="worker-b", limit=10, lease_duration_sec=300, now=now)
        assert len(claimed_b) == 1
        assert claimed_b[0].id != claimed_a[0].id


class TestExpiredLease:
    """Expired leases must allow other workers to claim."""

    def test_expired_lease_allows_reclaim(self, tmp_path):
        repo = _make_repo(tmp_path)
        _make_target(repo, "t_expire")

        now = datetime.now(timezone.utc)
        claimed = repo.claim_targets(worker_id="worker-1", limit=10, lease_duration_sec=300, now=now)
        first_claim = next(t for t in claimed if t.id == "t_expire")
        assert first_claim.claim_token is not None

        # Lease expires after 300s; advance time by 301s
        future = now + timedelta(seconds=301)
        claimed2 = repo.claim_targets(worker_id="worker-2", limit=10, lease_duration_sec=300, now=future)
        second_claim = next((t for t in claimed2 if t.id == "t_expire"), None)
        assert second_claim is not None
        assert second_claim.claim_token != first_claim.claim_token
        assert second_claim.lease_owner == "worker-2"

    def test_active_lease_blocks_other_workers(self, tmp_path):
        repo = _make_repo(tmp_path)
        _make_target(repo, "t_active")

        now = datetime.now(timezone.utc)
        repo.claim_targets(worker_id="worker-1", limit=10, lease_duration_sec=300, now=now)

        # 1 second later, lease is still active
        soon = now + timedelta(seconds=1)
        claimed2 = repo.claim_targets(worker_id="worker-2", limit=10, lease_duration_sec=300, now=soon)
        assert not any(t.id == "t_active" for t in claimed2)


class TestLeaseRenewalBoundary:
    """Lease boundary: must not reclaim exactly at expiry instant."""

    def test_lease_not_renewed_at_exact_boundary(self, tmp_path):
        repo = _make_repo(tmp_path)
        _make_target(repo, "t_boundary")

        now = datetime.now(timezone.utc)
        claimed = repo.claim_targets(worker_id="worker-1", limit=10, lease_duration_sec=300, now=now)
        first = next(t for t in claimed if t.id == "t_boundary")

        # Exactly at expiry instant
        exact = now + timedelta(seconds=300)
        claimed2 = repo.claim_targets(worker_id="worker-2", limit=10, lease_duration_sec=300, now=exact)
        # At exact expiry, lease_until < now is False (lease_until == now), so not reclaimable
        second = next((t for t in claimed2 if t.id == "t_boundary"), None)
        assert second is None, "Lease must not be reclaimable at exact expiry instant"

        # 1 microsecond later, it is reclaimable
        later = exact + timedelta(microseconds=1)
        claimed3 = repo.claim_targets(worker_id="worker-3", limit=10, lease_duration_sec=300, now=later)
        third = next((t for t in claimed3 if t.id == "t_boundary"), None)
        assert third is not None
        assert third.claim_token != first.claim_token


class TestWorkerCrashRecovery:
    """Worker crash leaves target in a state recoverable by other workers."""

    def test_crash_before_finalize_allows_reclaim(self, tmp_path):
        repo = _make_repo(tmp_path)
        _make_target(repo, "t_crash")

        now = datetime.now(timezone.utc)
        claimed = repo.claim_targets(worker_id="worker-1", limit=10, lease_duration_sec=2, now=now)
        first = next(t for t in claimed if t.id == "t_crash")
        assert first.claim_token is not None

        # Simulate crash: do NOT finalize or release.
        # Advance past lease expiry
        future = now + timedelta(seconds=3)
        claimed2 = repo.claim_targets(worker_id="worker-2", limit=10, lease_duration_sec=300, now=future)
        second = next((t for t in claimed2 if t.id == "t_crash"), None)
        assert second is not None
        assert second.claim_token != first.claim_token
        assert second.lease_owner == "worker-2"

    def test_restart_preserves_target_state(self, tmp_path):
        repo = _make_repo(tmp_path)
        _make_target(repo, "t_restart", status=TargetStatus.COOLDOWN)
        repo.update_target_status(
            "t_restart",
            status=TargetStatus.COOLDOWN,
            consecutive_failures=5,
            next_allowed_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

        # Simulate restart: new repo instance on same DB
        repo2 = Repository(str(tmp_path / "concurrency.db"))
        target = repo2.get_target("t_restart")
        assert target is not None
        assert target.status == TargetStatus.COOLDOWN
        assert target.consecutive_failures == 5
        assert target.next_allowed_at is not None


class TestDuplicateFinalize:
    """Same claim_token must not finalize twice."""

    def test_duplicate_finalize_returns_false(self, tmp_path):
        repo = _make_repo(tmp_path)
        _make_target(repo, "t_dup")
        claimed, now = _claim_target(repo, "t_dup")
        claim_token = claimed.claim_token

        result1 = repo.finalize_execution(
            target_id="t_dup",
            claim_token=claim_token,
            worker_id="worker-1",
            transition=_make_transition(status=TargetStatus.RECOVERING),
            signals=[_make_signal("t_dup")],
            correlation_plan=None,
            now=now,
        )
        assert result1 is True

        target = repo.get_target("t_dup")
        assert target.claim_token is None  # Cleared after success

        # Duplicate finalize with same token must fail
        result2 = repo.finalize_execution(
            target_id="t_dup",
            claim_token=claim_token,
            worker_id="worker-1",
            transition=_make_transition(status=TargetStatus.NORMAL),
            signals=[_make_signal("t_dup")],
            correlation_plan=None,
            now=now + timedelta(seconds=1),
        )
        assert result2 is False


class TestStaleRelease:
    """Stale worker release must not free an active lease."""

    def test_stale_release_does_not_free_active_lease(self, tmp_path):
        repo = _make_repo(tmp_path)
        _make_target(repo, "t_stale_release")
        claimed, now = _claim_target(repo, "t_stale_release", worker_id="worker-1")
        valid_token = claimed.claim_token

        # Stale worker with old token tries to release
        released = repo.release_target_lease(
            target_id="t_stale_release",
            claim_token="old-token",
            now=now,
        )
        assert released is False

        # Lease must still be active
        target = repo.get_target("t_stale_release")
        assert target.claim_token == valid_token
        assert target.lease_owner == "worker-1"


class TestPartialTransactionRollback:
    """Any failure in finalize_execution must roll back all partial state."""

    def test_partial_insert_rollback(self, tmp_path):
        repo = _make_repo(tmp_path)
        _make_target(repo, "t_partial")
        claimed, now = _claim_target(repo, "t_partial")
        claim_token = claimed.claim_token

        # Create a plan with an invalid event_type to force failure after target update
        from web_watcher.event_correlator import CorrelationPlan, EventToCreate
        plan = CorrelationPlan()
        plan.events_to_create.append(EventToCreate(
            entity_id="t_partial",
            event_type="INVALID_TYPE",
            status="open",
            importance="medium",
            created_at=now,
            updated_at=now,
        ))

        result = repo.finalize_execution(
            target_id="t_partial",
            claim_token=claim_token,
            worker_id="worker-1",
            transition=_make_transition(status=TargetStatus.BACKOFF),
            signals=[_make_signal("t_partial")],
            correlation_plan=plan,
            now=now,
        )
        assert result is False

        # Target must be unchanged
        target = repo.get_target("t_partial")
        assert target.status == TargetStatus.NORMAL
        # No signals or events should have been inserted
        signals = repo.connection.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        events = repo.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert signals == 0
        assert events == 0
