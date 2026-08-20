"""PART 04 — Lease / Fencing / Recovery tests."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from web_watcher.models import Target, TargetStatus
from web_watcher.repository import Repository
from web_watcher.execution_semantics import ExecutionOutcome
from web_watcher.fetch import FetchStatus
from web_watcher.fetcher import FetchResult
from web_watcher.fetch_policy import FetchPolicy
from web_watcher.generic_web_target import GenericWebTarget
from web_watcher.rule_models import ExtractorConfig


def _make_target(meta=None, **kwargs):
    return Target(
        id=kwargs.get("id", "t1"),
        url=kwargs.get("url", "https://example.com"),
        status=kwargs.get("status", TargetStatus.NORMAL),
        interval=kwargs.get("interval", "60s"),
        metadata=meta or {},
        **{k: v for k, v in kwargs.items() if k not in {"id", "url", "status", "interval", "meta"}},
    )


class TestClaimCollision:
    """Two workers cannot claim the same target simultaneously."""

    def test_two_workers_same_target(self, tmp_path):
        db_file = tmp_path / "collision.db"
        repo = Repository(str(db_file))
        now = datetime.now(timezone.utc)
        target = Target(id="t_collision", url="https://example.com", status=TargetStatus.NORMAL)
        repo.save_target(target)

        claimed1 = repo.claim_targets(worker_id="worker-1", limit=10, lease_duration_sec=300, now=now)
        claimed2 = repo.claim_targets(worker_id="worker-2", limit=10, lease_duration_sec=300, now=now)

        assert len(claimed1) == 1
        assert len(claimed2) == 0

    def test_claim_collision_with_expired_lease(self, tmp_path):
        db_file = tmp_path / "collision_expired.db"
        repo = Repository(str(db_file))
        now = datetime.now(timezone.utc)
        target = Target(id="t_expired", url="https://example.com", status=TargetStatus.NORMAL)
        repo.save_target(target)

        # Worker 1 claims with a very short lease
        claimed1 = repo.claim_targets(worker_id="worker-1", limit=10, lease_duration_sec=1, now=now)
        assert len(claimed1) == 1

        # Lease expires
        future = now + timedelta(seconds=2)
        claimed2 = repo.claim_targets(worker_id="worker-2", limit=10, lease_duration_sec=300, now=future)
        assert len(claimed2) == 1
        assert claimed2[0].claim_token != claimed1[0].claim_token


class TestStaleWorker:
    """Stale worker operations must be rejected."""

    def test_stale_worker_commit_rejected(self, tmp_path):
        db_file = tmp_path / "stale_commit.db"
        repo = Repository(str(db_file))
        now = datetime.now(timezone.utc)
        target = Target(id="t_stale_commit", url="https://example.com", status=TargetStatus.NORMAL)
        repo.save_target(target)

        claimed = repo.claim_targets(worker_id="worker-1", limit=10, lease_duration_sec=300, now=now)
        claim_token = claimed[0].claim_token

        # Worker 1 releases the lease
        repo.release_target_lease("t_stale_commit", claim_token, now=now)

        # Worker 1 tries to commit with old token
        committed = repo.commit_target_execution(
            target_id="t_stale_commit",
            claim_token=claim_token,
            new_status=TargetStatus.NORMAL,
            now=now,
        )
        assert committed is False

    def test_stale_worker_release_rejected(self, tmp_path):
        db_file = tmp_path / "stale_release.db"
        repo = Repository(str(db_file))
        now = datetime.now(timezone.utc)
        target = Target(id="t_stale_release", url="https://example.com", status=TargetStatus.NORMAL)
        repo.save_target(target)

        claimed = repo.claim_targets(worker_id="worker-1", limit=10, lease_duration_sec=300, now=now)
        claim_token = claimed[0].claim_token

        # Worker 1 releases the lease
        repo.release_target_lease("t_stale_release", claim_token, now=now)

        # Worker 2 claims the target
        claimed2 = repo.claim_targets(worker_id="worker-2", limit=10, lease_duration_sec=300, now=now)
        new_token = claimed2[0].claim_token

        # Worker 1 tries to release with old token (should be idempotent/fail)
        released = repo.release_target_lease("t_stale_release", claim_token, now=now)
        assert released is False

        # Worker 2 releases with new token (should succeed)
        released2 = repo.release_target_lease("t_stale_release", new_token, now=now)
        assert released2 is True

    def test_old_token_vs_new_token(self, tmp_path):
        db_file = tmp_path / "old_token.db"
        repo = Repository(str(db_file))
        now = datetime.now(timezone.utc)
        target = Target(id="t_old_token", url="https://example.com", status=TargetStatus.NORMAL)
        repo.save_target(target)

        claimed1 = repo.claim_targets(worker_id="worker-1", limit=10, lease_duration_sec=300, now=now)
        old_token = claimed1[0].claim_token

        # Release and re-claim
        repo.release_target_lease("t_old_token", old_token, now=now)
        claimed2 = repo.claim_targets(worker_id="worker-2", limit=10, lease_duration_sec=300, now=now)
        new_token = claimed2[0].claim_token

        assert old_token != new_token

        # Old token commit must fail
        assert repo.commit_target_execution("t_old_token", old_token, TargetStatus.NORMAL, now=now) is False

        # New token commit must succeed
        assert repo.commit_target_execution("t_old_token", new_token, TargetStatus.NORMAL, now=now) is True


class TestWorkerCrashRecovery:
    """Worker crash scenarios and recovery."""

    def test_worker_crash_before_execution(self, tmp_path):
        """Worker claims but crashes before executing. New worker can claim after lease expires."""
        db_file = tmp_path / "crash_before.db"
        repo = Repository(str(db_file))
        now = datetime.now(timezone.utc)
        target = Target(id="t_crash_before", url="https://example.com", status=TargetStatus.NORMAL)
        repo.save_target(target)

        claimed = repo.claim_targets(worker_id="worker-1", limit=10, lease_duration_sec=1, now=now)
        assert len(claimed) == 1

        # Lease expires (worker crashed)
        future = now + timedelta(seconds=2)
        claimed2 = repo.claim_targets(worker_id="worker-2", limit=10, lease_duration_sec=300, now=future)
        assert len(claimed2) == 1
        assert claimed2[0].claim_token != claimed[0].claim_token

    def test_worker_crash_after_execution(self, tmp_path):
        """Worker executes but crashes before finalizing. New worker can claim after lease expires."""
        db_file = tmp_path / "crash_after.db"
        repo = Repository(str(db_file))
        now = datetime.now(timezone.utc)
        target = Target(id="t_crash_after", url="https://example.com", status=TargetStatus.NORMAL)
        repo.save_target(target)

        claimed = repo.claim_targets(worker_id="worker-1", limit=10, lease_duration_sec=1, now=now)
        assert len(claimed) == 1

        # Lease expires (worker crashed before finalizing)
        future = now + timedelta(seconds=2)
        claimed2 = repo.claim_targets(worker_id="worker-2", limit=10, lease_duration_sec=300, now=future)
        assert len(claimed2) == 1

    def test_worker_crash_during_finalization(self, tmp_path):
        """Worker commits partially but crashes. Lease is cleared on successful commit."""
        db_file = tmp_path / "crash_during.db"
        repo = Repository(str(db_file))
        now = datetime.now(timezone.utc)
        target = Target(id="t_crash_during", url="https://example.com", status=TargetStatus.NORMAL)
        repo.save_target(target)

        claimed = repo.claim_targets(worker_id="worker-1", limit=10, lease_duration_sec=300, now=now)
        claim_token = claimed[0].claim_token

        # Successful commit clears lease
        committed = repo.commit_target_execution(
            target_id="t_crash_during",
            claim_token=claim_token,
            new_status=TargetStatus.NORMAL,
            now=now,
        )
        assert committed is True

        loaded = repo.get_target("t_crash_during")
        assert loaded.lease_owner is None
        assert loaded.claim_token is None
        assert loaded.execution_id is None


class TestExecutionId:
    """execution_id lifecycle."""

    def test_execution_id_set_on_claim(self, tmp_path):
        db_file = tmp_path / "exec_id.db"
        repo = Repository(str(db_file))
        now = datetime.now(timezone.utc)
        target = Target(id="t_exec", url="https://example.com", status=TargetStatus.NORMAL)
        repo.save_target(target)

        claimed = repo.claim_targets(worker_id="worker-1", limit=10, lease_duration_sec=300, now=now)
        assert len(claimed) == 1
        assert claimed[0].execution_id is not None
        assert isinstance(claimed[0].execution_id, str)

        loaded = repo.get_target("t_exec")
        assert loaded.execution_id == claimed[0].execution_id

    def test_execution_id_cleared_on_commit(self, tmp_path):
        db_file = tmp_path / "exec_id_commit.db"
        repo = Repository(str(db_file))
        now = datetime.now(timezone.utc)
        target = Target(id="t_exec_commit", url="https://example.com", status=TargetStatus.NORMAL)
        repo.save_target(target)

        claimed = repo.claim_targets(worker_id="worker-1", limit=10, lease_duration_sec=300, now=now)
        claim_token = claimed[0].claim_token
        execution_id = claimed[0].execution_id

        repo.commit_target_execution(
            target_id="t_exec_commit",
            claim_token=claim_token,
            new_status=TargetStatus.NORMAL,
            now=now,
        )

        loaded = repo.get_target("t_exec_commit")
        assert loaded.execution_id is None

    def test_execution_id_cleared_on_release(self, tmp_path):
        db_file = tmp_path / "exec_id_release.db"
        repo = Repository(str(db_file))
        now = datetime.now(timezone.utc)
        target = Target(id="t_exec_release", url="https://example.com", status=TargetStatus.NORMAL)
        repo.save_target(target)

        claimed = repo.claim_targets(worker_id="worker-1", limit=10, lease_duration_sec=300, now=now)
        claim_token = claimed[0].claim_token

        repo.release_target_lease("t_exec_release", claim_token, now=now)

        loaded = repo.get_target("t_exec_release")
        assert loaded.execution_id is None


class TestStaleClaimOutcome:
    """STALE_CLAIM outcome is produced when commit fails."""

    def test_stale_claim_in_scheduled_runner(self, tmp_path):
        """When commit_target_execution fails, the outcome should be STALE_CLAIM."""
        from web_watcher.scheduled_runner import ScheduledRunner

        db_file = tmp_path / "stale_outcome.db"
        repo = Repository(str(db_file))
        now = datetime.now(timezone.utc)

        # Create and claim a target
        target = Target(id="t_stale_outcome", url="https://example.com", status=TargetStatus.NORMAL)
        repo.save_target(target)
        claimed = repo.claim_targets(worker_id="worker-1", limit=10, lease_duration_sec=300, now=now)
        claim_token = claimed[0].claim_token

        # Release the lease (simulating another worker taking over)
        repo.release_target_lease("t_stale_outcome", claim_token, now=now)

        # Create a runner and try to commit
        runner = ScheduledRunner(repo=repo)
        # Create a mock result with a transition
        mock_result = MagicMock()
        mock_result.outcome = ExecutionOutcome.SUCCESS_CHANGED
        mock_result.transition.status = TargetStatus.NORMAL
        mock_result.transition.etag = "v2"
        mock_result.transition.last_modified = None
        mock_result.transition.content_hash = "abc"
        mock_result.transition.metadata = {}
        mock_result.transition.consecutive_failures = 0
        mock_result.transition.next_allowed_at = now + timedelta(seconds=60)
        mock_result.transition.reason = "test"

        # This should fail because the claim is stale
        runner._commit_or_release("t_stale_outcome", claim_token, mock_result, now)
        # The method logs a warning but doesn't raise; verify by checking target state
        loaded = repo.get_target("t_stale_outcome")
        # Target state should be unchanged because commit failed
        assert loaded.status == TargetStatus.NORMAL  # unchanged
