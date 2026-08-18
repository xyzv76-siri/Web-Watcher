from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from web_watcher.models import Target, TargetStatus
from web_watcher.repository import Repository


def test_target_lease_fields_default_none(tmp_path):
    target = Target(id="t1", url="https://example.com")
    assert target.lease_owner is None
    assert target.lease_until is None
    assert target.claim_token is None


def test_target_lease_fields_can_be_set(tmp_path):
    now = datetime.now(timezone.utc)
    target = Target(
        id="t1",
        url="https://example.com",
        lease_owner="worker-1",
        lease_until=now + timedelta(seconds=300),
        claim_token="token-123",
    )
    assert target.lease_owner == "worker-1"
    assert target.claim_token == "token-123"
    assert target.lease_until > now


def test_repository_claim_targets_assigns_lease(tmp_path):
    db_file = tmp_path / "lease.db"
    repo = Repository(str(db_file))
    now = datetime.now(timezone.utc)
    target = Target(id="t_claim", url="https://example.com", status=TargetStatus.NORMAL)
    repo.save_target(target)

    claimed = repo.claim_targets(worker_id="worker-1", limit=10, lease_duration_sec=300, now=now)
    assert len(claimed) == 1
    assert claimed[0].lease_owner == "worker-1"
    assert claimed[0].claim_token is not None
    assert claimed[0].lease_until > now

    loaded = repo.get_target("t_claim")
    assert loaded.lease_owner == "worker-1"
    assert loaded.claim_token == claimed[0].claim_token


def test_repository_claim_targets_skips_cooldown_until_due(tmp_path):
    db_file = tmp_path / "lease_cooldown.db"
    repo = Repository(str(db_file))
    now = datetime.now(timezone.utc)
    target = Target(
        id="t_cd",
        url="https://example.com",
        status=TargetStatus.COOLDOWN,
        next_allowed_at=now + timedelta(seconds=600),
    )
    repo.save_target(target)

    claimed = repo.claim_targets(worker_id="worker-1", limit=10, now=now)
    assert len(claimed) == 0


def test_repository_claim_targets_claims_cooldown_when_due(tmp_path):
    db_file = tmp_path / "lease_cooldown_due.db"
    repo = Repository(str(db_file))
    now = datetime.now(timezone.utc)
    target = Target(
        id="t_cd_due",
        url="https://example.com",
        status=TargetStatus.COOLDOWN,
        next_allowed_at=now - timedelta(seconds=1),
    )
    repo.save_target(target)

    claimed = repo.claim_targets(worker_id="worker-1", limit=10, now=now)
    assert len(claimed) == 1
    assert claimed[0].status == TargetStatus.RECOVERING


def test_repository_commit_target_execution_fenced(tmp_path):
    db_file = tmp_path / "lease_commit.db"
    repo = Repository(str(db_file))
    now = datetime.now(timezone.utc)
    target = Target(
        id="t_commit",
        url="https://example.com",
        status=TargetStatus.NORMAL,
        etag="v1",
        content_hash="abc",
    )
    repo.save_target(target)

    claimed = repo.claim_targets(worker_id="worker-1", limit=10, lease_duration_sec=300, now=now)
    assert len(claimed) == 1
    claim_token = claimed[0].claim_token

    committed = repo.commit_target_execution(
        target_id="t_commit",
        claim_token=claim_token,
        new_status=TargetStatus.NORMAL,
        etag="v2",
        content_hash="def",
        next_allowed_at=now + timedelta(seconds=600),
        now=now,
    )
    assert committed is True

    loaded = repo.get_target("t_commit")
    assert loaded.etag == "v2"
    assert loaded.content_hash == "def"
    assert loaded.lease_owner is None
    assert loaded.claim_token is None


def test_repository_commit_target_execution_rejects_stale_token(tmp_path):
    db_file = tmp_path / "lease_stale.db"
    repo = Repository(str(db_file))
    now = datetime.now(timezone.utc)
    target = Target(id="t_stale", url="https://example.com", status=TargetStatus.NORMAL)
    repo.save_target(target)

    claimed = repo.claim_targets(worker_id="worker-1", limit=10, lease_duration_sec=300, now=now)
    claim_token = claimed[0].claim_token

    repo.release_target_lease("t_stale", claim_token, now=now)

    committed = repo.commit_target_execution(
        target_id="t_stale",
        claim_token=claim_token,
        new_status=TargetStatus.NORMAL,
        now=now,
    )
    assert committed is False


def test_repository_release_target_lease(tmp_path):
    db_file = tmp_path / "lease_release.db"
    repo = Repository(str(db_file))
    now = datetime.now(timezone.utc)
    target = Target(id="t_release", url="https://example.com", status=TargetStatus.NORMAL)
    repo.save_target(target)

    claimed = repo.claim_targets(worker_id="worker-1", limit=10, lease_duration_sec=300, now=now)
    claim_token = claimed[0].claim_token

    released = repo.release_target_lease("t_release", claim_token, now=now)
    assert released is True

    loaded = repo.get_target("t_release")
    assert loaded.lease_owner is None
    assert loaded.claim_token is None


def test_repository_release_target_lease_idempotent(tmp_path):
    db_file = tmp_path / "lease_release_twice.db"
    repo = Repository(str(db_file))
    now = datetime.now(timezone.utc)
    target = Target(id="t_twice", url="https://example.com", status=TargetStatus.NORMAL)
    repo.save_target(target)

    claimed = repo.claim_targets(worker_id="worker-1", limit=10, lease_duration_sec=300, now=now)
    claim_token = claimed[0].claim_token

    released1 = repo.release_target_lease("t_twice", claim_token, now=now)
    released2 = repo.release_target_lease("t_twice", claim_token, now=now)

    assert released1 is True
    assert released2 is False
