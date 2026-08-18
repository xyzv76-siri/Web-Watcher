import sqlite3
from datetime import datetime, timedelta
from web_watcher.models import Target, TargetStatus
from web_watcher.repository import Repository


def test_target_creation_and_defaults():
    t = Target(id="tgt_1", url="https://example.com/api")
    assert t.status == TargetStatus.NORMAL
    assert t.consecutive_failures == 0
    assert t.etag is None
    assert t.next_allowed_at is None


def test_repository_save_and_get_target(tmp_path):
    db_file = tmp_path / "test_targets.db"
    repo = Repository(str(db_file))

    t = Target(
        id="tgt_aws",
        url="https://aws.amazon.com/pricing",
        interval="10m",
        status=TargetStatus.NORMAL,
        etag='"etag-9821"',
        consecutive_failures=0,
    )
    repo.save_target(t)

    loaded = repo.get_target("tgt_aws")
    assert loaded is not None
    assert loaded.id == "tgt_aws"
    assert loaded.status == TargetStatus.NORMAL
    assert loaded.etag == '"etag-9821"'
    assert isinstance(loaded.status, TargetStatus)


def test_state_machine_full_lifecycle_flow(tmp_path):
    repo = Repository(str(tmp_path / "lifecycle.db"))
    t = Target(id="tgt_flow", url="https://example.com")
    repo.save_target(t)

    # 1. 正常运行 -> 发生临时 429 失败 -> 进入 BACKOFF
    now = datetime.utcnow()
    backoff_until = now + timedelta(minutes=5)
    repo.update_target_status(
        "tgt_flow",
        status=TargetStatus.BACKOFF,
        consecutive_failures=1,
        next_allowed_at=backoff_until,
    )

    loaded = repo.get_target("tgt_flow")
    assert loaded.status == TargetStatus.BACKOFF
    assert loaded.consecutive_failures == 1
    assert loaded.next_allowed_at is not None

    # 2. 退避到期 -> 恢复为 NORMAL
    repo.update_target_status(
        "tgt_flow",
        status=TargetStatus.NORMAL,
        consecutive_failures=0,
        next_allowed_at=None,
    )
    loaded = repo.get_target("tgt_flow")
    assert loaded.status == TargetStatus.NORMAL
    assert loaded.consecutive_failures == 0

    # 3. 连续失败累积 -> 进入 COOLDOWN
    repo.update_target_status(
        "tgt_flow",
        status=TargetStatus.COOLDOWN,
        consecutive_failures=5,
        next_allowed_at=now + timedelta(hours=1),
    )
    loaded = repo.get_target("tgt_flow")
    assert loaded.status == TargetStatus.COOLDOWN

    # 4. 冷却到期 -> list_schedulable_targets 自动迁移至 RECOVERING
    future = now + timedelta(hours=2)
    schedulable = repo.list_schedulable_targets(now=future)
    tgt = next(x for x in schedulable if x.id == "tgt_flow")
    assert tgt.status == TargetStatus.RECOVERING


def test_list_schedulable_targets_skips_backoff(tmp_path):
    repo = Repository(str(tmp_path / "sched.db"))
    now = datetime.utcnow()
    t = Target(
        id="tgt_skip",
        url="https://example.com",
        status=TargetStatus.BACKOFF,
        next_allowed_at=now + timedelta(minutes=10),
    )
    repo.save_target(t)

    targets = repo.list_schedulable_targets(now=now)
    assert all(x.id != "tgt_skip" for x in targets)


def test_target_status_enum_values():
    assert TargetStatus.NORMAL.value == "normal"
    assert TargetStatus.BACKOFF.value == "backoff"
    assert TargetStatus.COOLDOWN.value == "cooldown"
    assert TargetStatus.RECOVERING.value == "recovering"


def test_repository_target_table_initialization(tmp_path):
    db_file = tmp_path / "init.db"
    repo = Repository(str(db_file))
    repo.save_target(Target(id="init_tgt", url="https://example.com"))

    conn = sqlite3.connect(str(db_file))
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='targets'"
    ).fetchall()
    assert len(tables) == 1
    cols = [c[1] for c in conn.execute("PRAGMA table_info(targets);").fetchall()]
    assert "status" in cols
    assert "consecutive_failures" in cols
    conn.close()


def test_target_metadata_preserved_roundtrip(tmp_path):
    repo = Repository(str(tmp_path / "meta.db"))
    t = Target(
        id="meta_tgt",
        url="https://example.com",
        metadata={"region": "us-east-1", "team": "platform"},
    )
    repo.save_target(t)
    loaded = repo.get_target("meta_tgt")
    assert loaded.metadata == {"region": "us-east-1", "team": "platform"}
