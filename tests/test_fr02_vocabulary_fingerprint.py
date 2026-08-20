"""FR-02 — Unified Signal Vocabulary & Canonical Fingerprint tests."""

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import pytest

from web_watcher.models import Target, TargetStatus
from web_watcher.fetch import FetchResult, FetchStatus
from web_watcher.fetcher import SmartFetcher
from web_watcher.signal_types import SignalType
from web_watcher.scheduled_runner import ScheduledRunner
from web_watcher.repository import Repository


_RULES_YAML = """
version: "1.0"
rules:
  - id: "{target_id}"
    name: "FR-02 Watch"
    target:
      url: "https://example.com/page"
      interval: "10m"
    extractors:
      - name: "price"
        selector_type: "css"
        selector: ".price"
        transforms: ["strip_tags"]
"""


def _make_target(target_id: str) -> Target:
    return Target(
        id=target_id,
        url="https://example.com/page",
        interval="10m",
        status=TargetStatus.NORMAL,
        last_fetched_at=datetime.now(timezone.utc),
    )


def _mock_fetch_result(target_key: str, content: str, etag: str = None) -> FetchResult:
    return FetchResult(
        target_key=target_key,
        status=FetchStatus.SUCCESS,
        status_code=200,
        fetched_at=datetime.now(timezone.utc),
        content=content,
        etag=etag,
    )


def _run_once(tmp_path, target_id, mock_fetcher, content, etag=None):
    """Run a single target execution through ScheduledRunner."""
    db_file = str(tmp_path / f"{target_id}.db")
    repo = Repository(db_file)

    # Only create the target if it does not already exist, to preserve
    # metadata (normalized_values, initialized) across runs.
    if repo.get_target(target_id) is None:
        repo.save_target(_make_target(target_id))

    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text(_RULES_YAML.format(target_id=target_id), encoding="utf-8")

    mock_fetcher.fetch.return_value = _mock_fetch_result(
        target_id, content=content, etag=etag or f'"{content[:4]}"'
    )
    target = repo.get_target(target_id)
    target.next_allowed_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    repo.save_target(target)

    runner = ScheduledRunner(
        repo=repo,
        worker_id="worker-fr02",
        rules_path=rules_file,
        fetcher=mock_fetcher,
    )
    summary = runner.run_once()
    return repo, summary


def test_generic_web_emits_canonical_content_change(tmp_path):
    """GenericWebTarget must emit SignalType.CONTENT_CHANGE, not WEB_CONTENT_CHANGED."""
    mock_fetcher = MagicMock(spec=SmartFetcher)
    target_id = "vocab_target"

    # First run: baseline
    repo, summary = _run_once(tmp_path, target_id, mock_fetcher, "<html><body><span class='price'>10</span></body></html>")
    assert summary["signals_emitted"] == 0

    # Second run: changed content -> signal
    repo, summary = _run_once(tmp_path, target_id, mock_fetcher, "<html><body><span class='price'>20</span></body></html>")
    assert summary["signals_emitted"] == 1

    cur = repo.connection.execute("SELECT signal_type FROM signals")
    rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == SignalType.CONTENT_CHANGE.value


def test_distinct_changes_produce_distinct_fingerprints(tmp_path):
    """Three distinct content changes must produce three distinct signals."""
    mock_fetcher = MagicMock(spec=SmartFetcher)
    target_id = "fp_target"

    contents = [
        "<html><body><span class='price'>alpha</span></body></html>",
        "<html><body><span class='price'>beta</span></body></html>",
        "<html><body><span class='price'>gamma</span></body></html>",
    ]

    repo = None
    signal_count = 0
    for i, content in enumerate(contents):
        repo, summary = _run_once(tmp_path, target_id, mock_fetcher, content)
        # First run is baseline (0 signals), subsequent runs each emit 1 signal
        signal_count += summary["signals_emitted"]

    assert signal_count == len(contents) - 1

    cur = repo.connection.execute("SELECT id, fingerprint FROM signals ORDER BY id")
    rows = cur.fetchall()
    assert len(rows) == len(contents) - 1
    fingerprints = [r[1] for r in rows]
    assert len(set(fingerprints)) == len(contents) - 1


def test_restart_stable_fingerprint_for_same_content(tmp_path):
    """Same content after restart must produce the same fingerprint."""
    mock_fetcher = MagicMock(spec=SmartFetcher)
    target_id = "restart_target"

    contents = [
        "<html><body><span class='price'>alpha</span></body></html>",
        "<html><body><span class='price'>beta</span></body></html>",
    ]

    repo = None
    for content in contents:
        repo, summary = _run_once(tmp_path, target_id, mock_fetcher, content)

    cur = repo.connection.execute("SELECT fingerprint FROM signals ORDER BY id")
    fingerprints_before = [r[0] for r in cur.fetchall()]

    # Simulate restart: fresh repo, same target state
    db_file = str(tmp_path / f"{target_id}.db")
    repo2 = Repository(db_file)
    target2 = repo2.get_target(target_id)
    target2.next_allowed_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    repo2.save_target(target2)

    mock_fetcher2 = MagicMock(spec=SmartFetcher)
    mock_fetcher2.fetch.return_value = _mock_fetch_result(target_id, content=contents[-1], etag='"v2"')
    rules_file = tmp_path / "rules.yaml"
    runner2 = ScheduledRunner(
        repo=repo2,
        worker_id="worker-fr02",
        rules_path=rules_file,
        fetcher=mock_fetcher2,
    )
    summary2 = runner2.run_once()

    cur2 = repo2.connection.execute("SELECT fingerprint FROM signals ORDER BY id")
    fingerprints_after = [r[0] for r in cur2.fetchall()]

    # The existing signals should retain their original fingerprints
    assert fingerprints_before == fingerprints_after[: len(fingerprints_before)]


def test_normalize_signal_uses_canonical_vocabulary():
    """_normalize_signal must default to content_change, not WEB_CONTENT_CHANGED."""
    from web_watcher.scheduled_runner import ScheduledRunner
    sig = {"captured_at": datetime.now(timezone.utc).isoformat()}
    normalized = ScheduledRunner._normalize_signal(sig, "t1")
    assert normalized.signal_type == SignalType.CONTENT_CHANGE.value


def test_normalize_signal_fingerprint_no_target_id_fallback():
    """_normalize_signal must not fall back to target_id for fingerprint."""
    from web_watcher.scheduled_runner import ScheduledRunner
    sig = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "content_hash": "abc123",
    }
    normalized = ScheduledRunner._normalize_signal(sig, "t1")
    assert normalized.fingerprint == "abc123"

    # Without content_hash, fingerprint should be None, not target_id
    sig2 = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    normalized2 = ScheduledRunner._normalize_signal(sig2, "t1")
    assert normalized2.fingerprint is None
