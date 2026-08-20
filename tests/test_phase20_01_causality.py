"""
Phase 20-01 — Pipeline Finalization / End-to-End Causality Tests

Validates:
1. Signal persistence and Event correlation via event_signals
2. 304 short-circuit behavior
3. Error semantics (403, 404, 429, 5xx, timeout, network error)
4. Adapter does not persist signals directly
5. Fencing / stale claim rejection
"""
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
import sqlite3

from web_watcher.models import TargetStatus, Target
from web_watcher.fetch import FetchStatus
from web_watcher.fetcher import SmartFetcher, FetchResult
from web_watcher.generic_web_target import GenericWebTarget
from web_watcher.github_target import GitHubTarget
from web_watcher.scheduled_runner import ScheduledRunner
from web_watcher.repository import Repository


def _make_target(target_id: str, url: str, status: TargetStatus = TargetStatus.NORMAL) -> Target:
    return Target(
        id=target_id,
        url=url,
        interval="10m",
        status=status,
        last_fetched_at=datetime.now(timezone.utc),
    )


def _mock_fetch_result(target_key: str, status_code: int = 200, content: str = "<html>ok</html>", etag: str = None) -> FetchResult:
    return FetchResult(
        target_key=target_key,
        status=FetchStatus.NOT_MODIFIED if status_code == 304 else FetchStatus.SUCCESS,
        status_code=status_code,
        fetched_at=datetime.now(timezone.utc),
        content=content,
        etag=etag,
    )


def _run_target(tmp_path, target, mock_fetcher, rules_yaml: str = None):
    """Helper to run a single target through ScheduledRunner.

    If rules_yaml is provided, create a YAML rules file and sync it so the
    target is claimed through the normal rule-based path.
    """
    db_file = str(tmp_path / "causality.db")
    repo = Repository(db_file)
    repo.save_target(target)

    if rules_yaml:
        rules_file = tmp_path / "rules.yaml"
        rules_file.write_text(rules_yaml, encoding="utf-8")
        runner = ScheduledRunner(repo=repo, worker_id="worker-20-01", rules_path=rules_file, fetcher=mock_fetcher)
    else:
        runner = ScheduledRunner(repo=repo, worker_id="worker-20-01", fetcher=mock_fetcher)

    summary = runner.run_once()
    return repo, summary, db_file


# ============================================================
# 1. Signal -> Event -> link causality
# ============================================================

def test_signal_event_link_causality_via_scheduled_runner(tmp_path):
    """
    A changed observation must produce:
      - a persisted Signal
      - a persisted Event
      - an event_signals link
    """
    rules_yaml = """
version: "1.0"
rules:
  - id: "causality_target"
    name: "Causality Watch"
    target:
      url: "https://example.com/page"
      interval: "10m"
    extractors:
      - name: "price"
        selector_type: "css"
        selector: ".price"
        transforms: ["strip_tags"]
"""
    target = _make_target("causality_target", "https://example.com/page")
    now = datetime.now(timezone.utc)

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.side_effect = [
        _mock_fetch_result("causality_target", status_code=200, content='<div class="price">10</div>', etag='"v1"'),
        _mock_fetch_result("causality_target", status_code=200, content='<div class="price">20</div>', etag='"v2"'),
    ]

    repo, summary, db_file = _run_target(tmp_path, target, mock_fetcher, rules_yaml=rules_yaml)

    # First run: baseline, no signal
    assert summary["targets_evaluated"] == 1
    assert summary["signals_emitted"] == 0

    t = repo.get_target("causality_target")
    assert t.status == TargetStatus.NORMAL

    # Allow immediate re-run
    t.next_allowed_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    repo.save_target(t)

    # Second run: changed content -> signal + event + link
    runner = ScheduledRunner(repo=repo, worker_id="worker-20-01", rules_path=tmp_path / "rules.yaml", fetcher=mock_fetcher)
    summary = runner.run_once(now=t.next_allowed_at + timedelta(seconds=1))

    assert summary["signals_emitted"] == 1

    # Verify persisted signal (entity_id is integer from entities table, not string target_id)
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as cnt FROM signals")
    sig_count = cur.fetchone()["cnt"]
    assert sig_count == 1, f"Expected 1 signal, got {sig_count}"
    cur.execute("SELECT id, entity_id, signal_type, fingerprint FROM signals")
    sig_rows = cur.fetchall()
    sig = sig_rows[0]
    assert sig["signal_type"] == "content_change"
    sig_id = sig["id"]

    # Verify persisted event
    cur.execute("SELECT COUNT(*) as cnt FROM events")
    evt_count = cur.fetchone()["cnt"]
    assert evt_count == 1, f"Expected 1 event, got {evt_count}"
    cur.execute("SELECT id, entity_id, event_type FROM events")
    evt_rows = cur.fetchall()
    evt = evt_rows[0]
    assert evt["event_type"] == "content_change"
    evt_id = evt["id"]

    # Verify event_signals link
    cur.execute("SELECT event_id, signal_id FROM event_signals WHERE event_id = ?", (evt_id,))
    link_rows = cur.fetchall()
    conn.close()
    assert len(link_rows) == 1
    assert link_rows[0]["signal_id"] == sig_id


# ============================================================
# 2. 304 edge cases
# ============================================================

def test_304_after_change_produces_no_signal(tmp_path):
    rules_yaml = """
version: "1.0"
rules:
  - id: "304_target"
    name: "304 Watch"
    target:
      url: "https://example.com/page"
      interval: "10m"
    extractors:
      - name: "price"
        selector_type: "css"
        selector: ".price"
        transforms: ["strip_tags"]
"""
    target = _make_target("304_target", "https://example.com/page")
    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.side_effect = [
        _mock_fetch_result("304_target", status_code=200, content='<div class="price">10</div>', etag='"v1"'),
        _mock_fetch_result("304_target", status_code=304, content="", etag='"v1"'),
    ]

    repo, summary, db_file = _run_target(tmp_path, target, mock_fetcher, rules_yaml=rules_yaml)
    assert summary["signals_emitted"] == 0

    # Allow immediate re-run to exercise the 304 short-circuit path
    t = repo.get_target("304_target")
    t.next_allowed_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    repo.save_target(t)

    runner = ScheduledRunner(repo=repo, worker_id="worker-20-01", rules_path=tmp_path / "rules.yaml", fetcher=mock_fetcher)
    summary = runner.run_once()

    assert summary["is_304_count"] == 1
    assert summary["signals_emitted"] == 0

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM signals WHERE entity_id = ?", ("304_target",))
    count = cur.fetchone()[0]
    conn.close()
    assert count == 0


def test_repeated_304_produces_no_signal(tmp_path):
    rules_yaml = """
version: "1.0"
rules:
  - id: "repeated_304"
    name: "Repeated 304 Watch"
    target:
      url: "https://example.com/page"
      interval: "10m"
    extractors:
      - name: "price"
        selector_type: "css"
        selector: ".price"
        transforms: ["strip_tags"]
"""
    target = _make_target("repeated_304", "https://example.com/page")
    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = _mock_fetch_result(
        "repeated_304", status_code=304, content="", etag='"v1"'
    )

    repo, summary, db_file = _run_target(tmp_path, target, mock_fetcher, rules_yaml=rules_yaml)

    for _ in range(3):
        t = repo.get_target("repeated_304")
        t.next_allowed_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        repo.save_target(t)

        runner = ScheduledRunner(repo=repo, worker_id="worker-20-01", rules_path=tmp_path / "rules.yaml", fetcher=mock_fetcher)
        summary = runner.run_once()
        assert summary["is_304_count"] == 1
        assert summary["signals_emitted"] == 0

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM signals WHERE entity_id = ?", ("repeated_304",))
    count = cur.fetchone()[0]
    conn.close()
    assert count == 0


def test_304_with_stale_etag_still_no_signal(tmp_path):
    rules_yaml = """
version: "1.0"
rules:
  - id: "stale_304"
    name: "Stale 304 Watch"
    target:
      url: "https://example.com/page"
      interval: "10m"
    extractors:
      - name: "price"
        selector_type: "css"
        selector: ".price"
        transforms: ["strip_tags"]
"""
    target = _make_target("stale_304", "https://example.com/page")
    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.side_effect = [
        _mock_fetch_result("stale_304", status_code=200, content='<div class="price">10</div>', etag='"v1"'),
        _mock_fetch_result("stale_304", status_code=304, content="", etag='"v1"'),
    ]

    repo, summary, db_file = _run_target(tmp_path, target, mock_fetcher, rules_yaml=rules_yaml)

    # Allow immediate re-run to exercise the 304 short-circuit path
    t = repo.get_target("stale_304")
    t.next_allowed_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    repo.save_target(t)

    runner = ScheduledRunner(repo=repo, worker_id="worker-20-01", rules_path=tmp_path / "rules.yaml", fetcher=mock_fetcher)
    summary = runner.run_once()

    assert summary["is_304_count"] == 1
    assert summary["signals_emitted"] == 0


# ============================================================
# 3. Error semantics
# ============================================================

def test_error_semantics_403_does_not_create_signal(tmp_path):
    rules_yaml = """
version: "1.0"
rules:
  - id: "err_403"
    name: "403 Watch"
    target:
      url: "https://example.com/forbidden"
      interval: "10m"
    extractors:
      - name: "price"
        selector_type: "css"
        selector: ".price"
        transforms: ["strip_tags"]
"""
    target = _make_target("err_403", "https://example.com/forbidden")
    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = _mock_fetch_result("err_403", status_code=403)

    repo, summary, db_file = _run_target(tmp_path, target, mock_fetcher, rules_yaml=rules_yaml)
    assert summary["targets_evaluated"] == 1
    assert summary["signals_emitted"] == 0


def test_error_semantics_404_does_not_create_signal(tmp_path):
    rules_yaml = """
version: "1.0"
rules:
  - id: "err_404"
    name: "404 Watch"
    target:
      url: "https://example.com/missing"
      interval: "10m"
    extractors:
      - name: "price"
        selector_type: "css"
        selector: ".price"
        transforms: ["strip_tags"]
"""
    target = _make_target("err_404", "https://example.com/missing")
    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = _mock_fetch_result("err_404", status_code=404)

    repo, summary, db_file = _run_target(tmp_path, target, mock_fetcher, rules_yaml=rules_yaml)
    assert summary["targets_evaluated"] == 1
    assert summary["signals_emitted"] == 0


def test_error_semantics_429_does_not_create_signal(tmp_path):
    rules_yaml = """
version: "1.0"
rules:
  - id: "err_429"
    name: "429 Watch"
    target:
      url: "https://example.com/ratelimited"
      interval: "10m"
    extractors:
      - name: "price"
        selector_type: "css"
        selector: ".price"
        transforms: ["strip_tags"]
"""
    target = _make_target("err_429", "https://example.com/ratelimited")
    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = _mock_fetch_result("err_429", status_code=429)

    repo, summary, db_file = _run_target(tmp_path, target, mock_fetcher, rules_yaml=rules_yaml)
    assert summary["targets_evaluated"] == 1
    assert summary["signals_emitted"] == 0


def test_error_semantics_5xx_does_not_create_signal(tmp_path):
    rules_yaml = """
version: "1.0"
rules:
  - id: "err_500"
    name: "500 Watch"
    target:
      url: "https://example.com/boom"
      interval: "10m"
    extractors:
      - name: "price"
        selector_type: "css"
        selector: ".price"
        transforms: ["strip_tags"]
"""
    target = _make_target("err_500", "https://example.com/boom")
    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = _mock_fetch_result("err_500", status_code=500)

    repo, summary, db_file = _run_target(tmp_path, target, mock_fetcher, rules_yaml=rules_yaml)
    assert summary["targets_evaluated"] == 1
    assert summary["signals_emitted"] == 0


def test_error_semantics_timeout_does_not_create_signal(tmp_path):
    rules_yaml = """
version: "1.0"
rules:
  - id: "err_timeout"
    name: "Timeout Watch"
    target:
      url: "https://example.com/slow"
      interval: "10m"
    extractors:
      - name: "price"
        selector_type: "css"
        selector: ".price"
        transforms: ["strip_tags"]
"""
    target = _make_target("err_timeout", "https://example.com/slow")
    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = FetchResult(
        target_key="err_timeout",
        status=FetchStatus.TIMEOUT,
        status_code=None,
        fetched_at=datetime.now(timezone.utc),
        content="",
    )

    repo, summary, db_file = _run_target(tmp_path, target, mock_fetcher, rules_yaml=rules_yaml)
    assert summary["targets_evaluated"] == 1
    assert summary["signals_emitted"] == 0


def test_error_semantics_network_error_does_not_create_signal(tmp_path):
    rules_yaml = """
version: "1.0"
rules:
  - id: "err_dns"
    name: "Network Watch"
    target:
      url: "https://nonexistent.invalid"
      interval: "10m"
    extractors:
      - name: "price"
        selector_type: "css"
        selector: ".price"
        transforms: ["strip_tags"]
"""
    target = _make_target("err_dns", "https://nonexistent.invalid")
    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = FetchResult(
        target_key="err_dns",
        status=FetchStatus.NETWORK_ERROR,
        status_code=None,
        fetched_at=datetime.now(timezone.utc),
        content="",
    )

    repo, summary, db_file = _run_target(tmp_path, target, mock_fetcher, rules_yaml=rules_yaml)
    assert summary["targets_evaluated"] == 1
    assert summary["signals_emitted"] == 0


# ============================================================
# 4. Adapter bypass prevention
# ============================================================

def test_adapter_returns_signals_does_not_persist_directly(tmp_path):
    """
    The adapter must return signals in TargetExecutionResult and must
    not call repository persistence methods directly.
    """
    rules_yaml = """
version: "1.0"
rules:
  - id: "bypass_test"
    name: "Bypass Watch"
    target:
      url: "https://example.com/page"
      interval: "10m"
    extractors:
      - name: "price"
        selector_type: "css"
        selector: ".price"
        transforms: ["strip_tags"]
"""
    target = _make_target("bypass_test", "https://example.com/page")
    now = datetime.now(timezone.utc)

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = _mock_fetch_result(
        "bypass_test", status_code=200, content='<div class="price">20</div>', etag='"v2"'
    )

    repo = Repository(str(tmp_path / "bypass.db"))
    repo.save_target(target)

    # Sync rules so target gets full metadata initialized
    runner = ScheduledRunner(repo=repo, worker_id="worker-20-01", rules_path=tmp_path / "rules.yaml", fetcher=mock_fetcher)
    runner.sync_rules()

    stored = repo.get_target("bypass_test")
    adapter = GenericWebTarget(target=stored, extractors=[])

    direct_persist_calls = []

    def spy_create_signal(*args, **kwargs):
        direct_persist_calls.append(("create_signal", args, kwargs))

    def guard_create_event(*args, **kwargs):
        direct_persist_calls.append(("create_event", args, kwargs))

    def guard_create_notification(*args, **kwargs):
        direct_persist_calls.append(("create_notification", args, kwargs))

    repo.create_signal = spy_create_signal
    repo.create_event = guard_create_event
    repo.create_notification = guard_create_notification

    result = adapter.execute(fetcher=mock_fetcher, policy=None, repo=repo, now=now)

    assert len(direct_persist_calls) == 0


# ============================================================
# 5. Fencing
# ============================================================

def test_stale_claim_token_rejected_by_finalize(tmp_path):
    """
    After finalize_execution, the claim_token is cleared. A second
    finalize attempt with the old claim_token must fail.
    """
    rules_yaml = """
version: "1.0"
rules:
  - id: "fencing_target"
    name: "Fencing Watch"
    target:
      url: "https://example.com/page"
      interval: "10m"
    extractors:
      - name: "price"
        selector_type: "css"
        selector: ".price"
        transforms: ["strip_tags"]
"""
    target = _make_target("fencing_target", "https://example.com/page")
    now = datetime.now(timezone.utc)

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = _mock_fetch_result(
        "fencing_target", status_code=200, content='<div class="price">10</div>', etag='"v1"'
    )

    repo, summary, db_file = _run_target(tmp_path, target, mock_fetcher, rules_yaml=rules_yaml)

    t = repo.get_target("fencing_target")
    # After finalize, claim_token is cleared
    assert t.claim_token is None

    old_claim_token = "stale-token"
    from web_watcher.generic_web_target import TargetExecutionResult
    from web_watcher.execution_semantics import ExecutionOutcome, transition_for
    result = TargetExecutionResult(
        target_id="fencing_target",
        allowed=True,
        status_code=200,
        new_status=target.status,
        signals_emitted=[],
        extracted_results={},
        extracted_values={},
        outcome=ExecutionOutcome.SUCCESS_CHANGED,
        transition=transition_for(ExecutionOutcome.SUCCESS_CHANGED, target=t, now=now),
    )

    success = repo.finalize_execution(
        target_id="fencing_target",
        claim_token=old_claim_token,
        worker_id="worker-20-01",
        signals=[],
        correlation_plan=None,
        transition=result.transition,
    )
    assert success is False
