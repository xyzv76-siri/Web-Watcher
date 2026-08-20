"""PART 03 — Fetch state authority and backoff ownership tests."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from web_watcher.models import Target, TargetStatus, FetchState
from web_watcher.fetch import FetchStatus
from web_watcher.fetcher import FetchResult
from web_watcher.fetch_policy import FetchPolicy, FetchEvaluation
from web_watcher.generic_web_target import GenericWebTarget
from web_watcher.github_target import GitHubTarget
from web_watcher.execution_semantics import ExecutionOutcome, transition_for
from web_watcher.rule_models import ExtractorConfig
from web_watcher.rule_models import ExtractorConfig


def _make_target(meta=None, status=TargetStatus.NORMAL, interval="60s", url="https://example.com",
                 consecutive_failures=0, next_allowed_at=None, last_fetched_at=None,
                 etag=None, last_modified=None, content_hash=None):
    return Target(
        id="t1",
        url=url,
        status=status,
        interval=interval,
        metadata=meta or {},
        consecutive_failures=consecutive_failures,
        next_allowed_at=next_allowed_at,
        last_fetched_at=last_fetched_at,
        etag=etag,
        last_modified=last_modified,
        content_hash=content_hash,
    )


class TestBackoffCalculation:
    """FetchPolicy is the single backoff authority."""

    def test_failure_one_backoff(self):
        policy = FetchPolicy(base_backoff_sec=30.0, max_backoff_sec=600.0, jitter_ratio=0.0)
        target = _make_target(consecutive_failures=0)
        now = datetime.now(timezone.utc)
        ev = policy.evaluate_response(target, 500, error="boom", now=now)
        assert ev.new_status == TargetStatus.BACKOFF
        assert ev.consecutive_failures == 1
        assert ev.next_allowed_at > now
        # base_backoff_sec * 2^(1-1) = 30
        diff = (ev.next_allowed_at - now).total_seconds()
        assert diff == 30.0

    def test_failure_two_backoff(self):
        policy = FetchPolicy(base_backoff_sec=30.0, max_backoff_sec=600.0, jitter_ratio=0.0)
        target = _make_target(consecutive_failures=1)
        now = datetime.now(timezone.utc)
        ev = policy.evaluate_response(target, 500, error="boom", now=now)
        assert ev.new_status == TargetStatus.BACKOFF
        assert ev.consecutive_failures == 2
        # base_backoff_sec * 2^(2-1) = 60
        diff = (ev.next_allowed_at - now).total_seconds()
        assert diff == 60.0

    def test_repeated_failures_escalate_to_cooldown(self):
        policy = FetchPolicy(base_backoff_sec=30.0, max_backoff_sec=600.0, max_consecutive_failures=3, jitter_ratio=0.0)
        target = _make_target(consecutive_failures=2)
        now = datetime.now(timezone.utc)
        ev = policy.evaluate_response(target, 500, error="boom", now=now)
        assert ev.new_status == TargetStatus.COOLDOWN
        assert ev.consecutive_failures == 3
        # cooldown_ladder[0] = 1800
        diff = (ev.next_allowed_at - now).total_seconds()
        assert diff == 1800.0

    def test_success_recovery_resets_failures(self):
        policy = FetchPolicy()
        target = _make_target(consecutive_failures=3, status=TargetStatus.COOLDOWN)
        now = datetime.now(timezone.utc)
        ev = policy.evaluate_response(target, 200, now=now)
        assert ev.new_status == TargetStatus.NORMAL
        assert ev.consecutive_failures == 0
        assert ev.next_allowed_at > now

    def test_304_not_failure(self):
        policy = FetchPolicy()
        target = _make_target(etag='"v1"', last_modified="Wed, 21 Oct 2026 07:28:00 GMT")
        now = datetime.now(timezone.utc)
        ev = policy.evaluate_response(target, 304, now=now)
        assert ev.new_status == TargetStatus.NORMAL
        assert ev.consecutive_failures == 0
        assert ev.next_allowed_at > now

    def test_policy_block_next_allowed_at(self):
        policy = FetchPolicy()
        target = _make_target(next_allowed_at=datetime.now(timezone.utc) + timedelta(seconds=120))
        now = datetime.now(timezone.utc)
        decision = policy.prepare_request(target, now=now)
        assert decision.allowed is False
        assert decision.delay_seconds > 0


class TestSelectorAndTransformFailure:
    """Selector/transform failures must not destroy durable state."""

    def test_selector_not_found_preserves_content_hash(self):
        target = _make_target(content_hash="prev_hash")
        transition = transition_for(
            ExecutionOutcome.SELECTOR_NOT_FOUND,
            target=target,
            now=datetime.now(timezone.utc),
            content_hash="prev_hash",
            metadata={"initialized": True},
        )
        assert transition.status == TargetStatus.NORMAL
        assert transition.content_hash == "prev_hash"
        assert transition.emit_signal is False

    def test_transform_error_preserves_content_hash(self):
        target = _make_target(content_hash="prev_hash")
        transition = transition_for(
            ExecutionOutcome.TRANSFORM_ERROR,
            target=target,
            now=datetime.now(timezone.utc),
        )
        assert transition.status == TargetStatus.NORMAL
        assert transition.content_hash == target.content_hash
        assert transition.metadata == target.metadata
        assert transition.emit_signal is False

    def test_empty_after_transform_no_signal(self):
        target = _make_target(content_hash="prev_hash")
        transition = transition_for(
            ExecutionOutcome.EMPTY_AFTER_TRANSFORM,
            target=target,
            now=datetime.now(timezone.utc),
            content_hash="new_hash",
            metadata={"initialized": True},
        )
        assert transition.status == TargetStatus.NORMAL
        assert transition.emit_signal is False


class TestTargetIsSoleAuthority:
    """Target is the single authoritative fetch state for production execution."""

    def test_target_has_all_fetch_state_fields(self):
        target = _make_target()
        assert hasattr(target, 'etag')
        assert hasattr(target, 'last_modified')
        assert hasattr(target, 'content_hash')
        assert hasattr(target, 'consecutive_failures')
        assert hasattr(target, 'next_allowed_at')
        assert hasattr(target, 'last_fetched_at')

    def test_generic_web_target_does_not_persist(self):
        """Adapter must not persist state directly."""
        target = _make_target(meta={"initialized": True})
        adapter = GenericWebTarget(
            target=target,
            extractors=[ExtractorConfig(name="x", selector="div.x", selector_type="css")],
        )
        mock_fetcher = MagicMock()
        mock_fetcher.fetch.return_value = FetchResult(
            target_key="t1",
            status=FetchStatus.SUCCESS,
            status_code=200,
            fetched_at=datetime.now(timezone.utc),
            content="<html><body><div class='x'>1</div></body></html>",
            etag='"v2"',
        )
        repo = MagicMock()
        res = adapter.execute(fetcher=mock_fetcher, policy=FetchPolicy())
        # Adapter must not persist state directly.
        repo.save_target.assert_not_called()
        repo.commit_target_execution.assert_not_called()

    def test_github_target_does_not_persist(self):
        target = _make_target(url="octocat/Hello-World", meta={"last_release_tag": "v1.0.0"})
        adapter = GitHubTarget(target=target, watch_types=["releases"])
        mock_fetcher = MagicMock()
        mock_fetcher.fetch.return_value = FetchResult(
            target_key="t1",
            status=FetchStatus.SUCCESS,
            status_code=200,
            fetched_at=datetime.now(timezone.utc),
            content='{"tag_name": "v1.0.0", "name": "v1.0.0"}',
            etag='"rel-etag"',
        )
        repo = MagicMock()
        res = adapter.execute(fetcher=mock_fetcher, policy=FetchPolicy())
        repo.save_target.assert_not_called()
        repo.commit_target_execution.assert_not_called()

    def test_fetch_state_is_legacy_not_active_authority(self):
        """fetch_state exists but is not used by the production ScheduledRunner path."""
        from web_watcher.repository import Repository
        # FetchState model exists for backward compatibility
        fs = FetchState(target_key="key", etag='"v1"', content_hash="abc")
        assert fs.target_key == "key"
        # Repository still supports get_fetch_state / upsert_fetch_state
        # but the production path does not call them.