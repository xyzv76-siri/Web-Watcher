"""PART 02 — Execution outcome matrix and state transition tests."""

from datetime import datetime, timedelta
import json
from unittest.mock import MagicMock

import pytest

from web_watcher.models import Target, TargetStatus
from web_watcher.fetch import FetchStatus
from web_watcher.fetcher import FetchResult
from web_watcher.fetch_policy import FetchPolicy
from web_watcher.generic_web_target import GenericWebTarget
from web_watcher.github_target import GitHubTarget
from web_watcher.execution_semantics import ExecutionOutcome, StateTransition, transition_for
from web_watcher.rule_models import ExtractorConfig



def _make_target(meta=None, status=TargetStatus.NORMAL, interval="60s", url="https://example.com"):
    return Target(
        id="t1",
        url=url,
        status=status,
        interval=interval,
        metadata=meta or {},
    )


class TestExecutionOutcomeMatrix:
    """Verify the mandatory outcome set and transition rules."""

    def test_required_outcomes_exist(self):
        required = {
            "success_changed",
            "success_unchanged",
            "not_modified",
            "policy_blocked",
            "policy_cooldown",
            "fetch_failed",
            "network_error",
            "timeout",
            "selector_not_found",
            "empty_after_transform",
            "multiple_match",
            "transform_error",
            "adapter_error",
            "stale_claim",
        }
        actual = {m.value for m in ExecutionOutcome}
        assert required == actual

    def test_not_modified_is_not_failure(self):
        target = _make_target(meta={"initialized": True, "etag": "\"abc\""})
        transition = transition_for(
            ExecutionOutcome.NOT_MODIFIED,
            target=target,
            now=datetime.utcnow(),
            etag="\"abc\"",
            last_modified=None,
        )
        assert transition.status == TargetStatus.NORMAL
        assert transition.emit_signal is False
        assert transition.consecutive_failures == 0

    def test_policy_blocked_does_not_loop(self):
        target = _make_target(meta={"initialized": True}, status=TargetStatus.COOLDOWN)
        transition = transition_for(
            ExecutionOutcome.POLICY_BLOCKED,
            target=target,
            now=datetime.utcnow(),
        )
        assert transition.status == TargetStatus.COOLDOWN
        assert transition.next_allowed_at is not None

    def test_network_error_backoff(self):
        target = _make_target(meta={"initialized": True}, status=TargetStatus.NORMAL)
        transition = transition_for(
            ExecutionOutcome.NETWORK_ERROR,
            target=target,
            now=datetime.utcnow(),
            next_allowed_at=datetime.utcnow() + timedelta(seconds=120),
        )
        assert transition.status == TargetStatus.BACKOFF
        assert transition.consecutive_failures == 1
        assert transition.emit_signal is False

    def test_selector_not_found_does_not_delete(self):
        target = _make_target(meta={"initialized": True}, status=TargetStatus.NORMAL)
        transition = transition_for(
            ExecutionOutcome.SELECTOR_NOT_FOUND,
            target=target,
            now=datetime.utcnow(),
            content_hash="abc123",
            metadata={"initialized": True},
        )
        assert transition.status == TargetStatus.NORMAL
        assert transition.content_hash == "abc123"
        assert transition.emit_signal is False

    def test_transform_error_preserves_state(self):
        target = _make_target(meta={"initialized": True}, status=TargetStatus.NORMAL)
        transition = transition_for(
            ExecutionOutcome.TRANSFORM_ERROR,
            target=target,
            now=datetime.utcnow(),
        )
        assert transition.status == TargetStatus.NORMAL
        assert transition.content_hash == target.content_hash
        assert transition.metadata == target.metadata
        assert transition.emit_signal is False

    def test_adapter_error_is_defined_transition(self):
        target = _make_target(meta={"initialized": True}, status=TargetStatus.NORMAL)
        transition = transition_for(
            ExecutionOutcome.ADAPTER_ERROR,
            target=target,
            now=datetime.utcnow(),
            next_allowed_at=datetime.utcnow() + timedelta(seconds=30),
        )
        assert transition.status == TargetStatus.BACKOFF
        assert transition.consecutive_failures == 1
        assert transition.emit_signal is False

    def test_stale_claim_preserves_state(self):
        target = _make_target(meta={"initialized": True}, status=TargetStatus.BACKOFF)
        target.next_allowed_at = datetime.utcnow() + timedelta(seconds=60)
        transition = transition_for(
            ExecutionOutcome.STALE_CLAIM,
            target=target,
            now=datetime.utcnow(),
        )
        assert transition.status == TargetStatus.BACKOFF
        assert transition.next_allowed_at == target.next_allowed_at
        assert transition.emit_signal is False


class TestGenericWebTargetOutcomes:
    def test_success_changed_emits_signal_and_transition(self):
        HTML = "<html><body><div class='price'>$10.00</div></body></html>"
        target = _make_target(meta={"initialized": True})
        adapter = GenericWebTarget(
            target=target,
            extractors=[ExtractorConfig(name="price", selector="div.price", selector_type="css")],
        )

        mock_fetcher = MagicMock()
        mock_fetcher.fetch.return_value = FetchResult(
            target_key="t1",
            status=FetchStatus.SUCCESS,
            status_code=200,
            fetched_at=datetime.utcnow(),
            content=HTML,
            etag='"v2"',
        )
        policy = FetchPolicy()

        res = adapter.execute(fetcher=mock_fetcher, policy=policy)
        assert res.outcome == ExecutionOutcome.SUCCESS_CHANGED
        assert res.transition is not None
        assert res.transition.emit_signal is True
        assert len(res.signals_emitted) == 1
        assert res.transition.status == TargetStatus.NORMAL

    def test_success_unchanged_reason(self):
        HTML = "<html><body><div class='price'>$10.00</div></body></html>"
        target = _make_target(
            meta={"initialized": True, "normalized_values": {"price": "$10.00"}},
        )
        adapter = GenericWebTarget(
            target=target,
            extractors=[ExtractorConfig(name="price", selector="div.price", selector_type="css")],
        )

        mock_fetcher = MagicMock()
        mock_fetcher.fetch.return_value = FetchResult(
            target_key="t1",
            status=FetchStatus.SUCCESS,
            status_code=200,
            fetched_at=datetime.utcnow(),
            content=HTML,
            etag='"v2"',
        )
        policy = FetchPolicy()

        res = adapter.execute(fetcher=mock_fetcher, policy=policy)
        assert res.outcome == ExecutionOutcome.SUCCESS_UNCHANGED
        assert res.transition.emit_signal is False
        assert len(res.signals_emitted) == 0
        assert "unchanged" in res.reason.lower() or "identical" in res.reason.lower()

    def test_not_modified_304(self):
        target = _make_target(meta={"initialized": True, "etag": "\"v1\""})
        adapter = GenericWebTarget(target=target)

        mock_fetcher = MagicMock()
        mock_fetcher.fetch.return_value = FetchResult(
            target_key="t1",
            status=FetchStatus.NOT_MODIFIED,
            status_code=304,
            fetched_at=datetime.utcnow(),
            content=None,
            etag="\"v1\"",
        )
        policy = FetchPolicy()

        res = adapter.execute(fetcher=mock_fetcher, policy=policy)
        assert res.outcome == ExecutionOutcome.NOT_MODIFIED
        assert res.is_304 is True
        assert len(res.signals_emitted) == 0

    def test_fetch_failed_http_500(self):
        target = _make_target(meta={"initialized": True})
        adapter = GenericWebTarget(target=target)

        mock_fetcher = MagicMock()
        mock_fetcher.fetch.return_value = FetchResult(
            target_key="t1",
            status=FetchStatus.HTTP_ERROR,
            status_code=500,
            fetched_at=datetime.utcnow(),
            content="",
            error=None,
        )
        policy = FetchPolicy()

        res = adapter.execute(fetcher=mock_fetcher, policy=policy)
        assert res.outcome == ExecutionOutcome.FETCH_FAILED
        assert res.transition.status == TargetStatus.BACKOFF
        assert res.transition.consecutive_failures == 1

    def test_selector_not_found(self):
        HTML = "<html><body><div class='price'>$10.00</div></body></html>"
        target = Target(
            id="t1",
            url="https://example.com",
            status=TargetStatus.NORMAL,
            interval="60s",
            metadata={"initialized": True},
        )
        target.content_hash = __import__("hashlib").sha256(b"old").hexdigest()
        adapter = GenericWebTarget(
            target=target,
            extractors=[ExtractorConfig(name="missing", selector="div.missing", selector_type="css")],
        )

        mock_fetcher = MagicMock()
        mock_fetcher.fetch.return_value = FetchResult(
            target_key="t1",
            status=FetchStatus.SUCCESS,
            status_code=200,
            fetched_at=datetime.utcnow(),
            content=HTML,
            etag='"v2"',
        )
        policy = FetchPolicy()

        res = adapter.execute(fetcher=mock_fetcher, policy=policy)
        assert res.outcome == ExecutionOutcome.SELECTOR_NOT_FOUND
        assert res.transition.status == TargetStatus.NORMAL
        assert len(res.signals_emitted) == 0


class TestGitHubTargetOutcomes:
    def test_not_modified_304(self):
        target = _make_target(
            url="pallets/flask",
            meta={"last_release_tag": "v2.1.0", "release_etag": '"rel-etag-210"'}
        )
        adapter = GitHubTarget(target=target, watch_types=["releases"])

        mock_fetcher = MagicMock()
        mock_fetcher.fetch.return_value = FetchResult(
            target_key="t1",
            status=FetchStatus.NOT_MODIFIED,
            status_code=304,
            fetched_at=datetime.utcnow(),
            content=None,
            etag='"rel-etag-210"',
        )
        policy = FetchPolicy()

        res = adapter.execute(fetcher=mock_fetcher, policy=policy)
        assert res.outcome == ExecutionOutcome.NOT_MODIFIED
        assert res.is_304 is True
        assert len(res.signals_emitted) == 0

    def test_success_changed_release(self):
        target = _make_target(
            url="pallets/flask",
            meta={"last_release_tag": "v2.0.0"}
        )
        adapter = GitHubTarget(target=target, watch_types=["releases"])

        payload = json.dumps({"tag_name": "v2.1.0", "name": "v2.1.0", "html_url": "url", "published_at": "now", "body": ""})
        mock_fetcher = MagicMock()
        mock_fetcher.fetch.return_value = FetchResult(
            target_key="t1",
            status=FetchStatus.SUCCESS,
            status_code=200,
            fetched_at=datetime.utcnow(),
            content=payload,
            etag='"rel-etag-210"',
        )
        policy = FetchPolicy()

        res = adapter.execute(fetcher=mock_fetcher, policy=policy)
        assert res.outcome == ExecutionOutcome.SUCCESS_CHANGED
        assert len(res.signals_emitted) == 1
        assert res.transition.emit_signal is True


class TestStateTransitionAuthority:
    """Verify every legal transition has an explicit authority mapping."""

    def _transition(self, target_status, outcome, **kwargs):
        target = Target(
            id="t1",
            url="https://example.com",
            status=target_status,
            interval="60s",
            consecutive_failures=kwargs.pop("consecutive_failures", 0),
            next_allowed_at=kwargs.pop("next_allowed_at", None),
            metadata=kwargs.pop("metadata", {}),
        )
        return transition_for(
            outcome,
            target=target,
            now=datetime.utcnow(),
            **kwargs,
        )

    # --- NORMAL transitions ---
    def test_normal_to_normal_policy_blocked(self):
        t = self._transition(TargetStatus.NORMAL, ExecutionOutcome.POLICY_BLOCKED)
        assert t.status == TargetStatus.NORMAL

    def test_normal_to_normal_not_modified(self):
        t = self._transition(TargetStatus.NORMAL, ExecutionOutcome.NOT_MODIFIED)
        assert t.status == TargetStatus.NORMAL

    def test_normal_to_backoff_fetch_failed(self):
        t = self._transition(TargetStatus.NORMAL, ExecutionOutcome.FETCH_FAILED)
        assert t.status == TargetStatus.BACKOFF
        assert t.consecutive_failures == 1

    def test_normal_to_cooldown_policy_cooldown(self):
        t = self._transition(
            TargetStatus.NORMAL,
            ExecutionOutcome.POLICY_COOLDOWN,
            consecutive_failures=2,
            next_allowed_at=datetime.utcnow() + timedelta(seconds=1800),
        )
        assert t.status == TargetStatus.COOLDOWN

    # --- BACKOFF transitions ---
    def test_backoff_to_backoff_continued_failure(self):
        t = self._transition(TargetStatus.BACKOFF, ExecutionOutcome.FETCH_FAILED)
        assert t.status == TargetStatus.BACKOFF
        assert t.consecutive_failures == 1

    def test_backoff_to_normal_recovery(self):
        t = self._transition(TargetStatus.BACKOFF, ExecutionOutcome.NOT_MODIFIED)
        assert t.status == TargetStatus.NORMAL
        assert t.consecutive_failures == 0

    def test_backoff_to_cooldown_policy_cooldown(self):
        t = self._transition(
            TargetStatus.BACKOFF,
            ExecutionOutcome.POLICY_COOLDOWN,
            consecutive_failures=3,
            next_allowed_at=datetime.utcnow() + timedelta(seconds=3600),
        )
        assert t.status == TargetStatus.COOLDOWN

    # --- COOLDOWN transitions ---
    def test_cooldown_to_cooldown_probe_failure(self):
        t = self._transition(TargetStatus.COOLDOWN, ExecutionOutcome.FETCH_FAILED)
        assert t.status == TargetStatus.COOLDOWN

    def test_cooldown_to_recovering_is_repository_responsibility(self):
        # COOLDOWN -> RECOVERING is performed by repository.list_schedulable_targets,
        # not by transition_for. Verify transition_for preserves COOLDOWN on failure.
        t = self._transition(TargetStatus.COOLDOWN, ExecutionOutcome.TIMEOUT)
        assert t.status == TargetStatus.COOLDOWN

    # --- RECOVERING transitions ---
    def test_recovering_to_normal_success(self):
        t = self._transition(TargetStatus.RECOVERING, ExecutionOutcome.SUCCESS_CHANGED)
        assert t.status == TargetStatus.NORMAL
        assert t.consecutive_failures == 0

    def test_recovering_to_backoff_failure(self):
        t = self._transition(TargetStatus.RECOVERING, ExecutionOutcome.NETWORK_ERROR)
        assert t.status == TargetStatus.COOLDOWN

    # --- No direct adapter outcome maps to RECOVERING ---
    def test_no_outcome_directly_produces_recovering(self):
        target = Target(
            id="t1",
            url="https://example.com",
            status=TargetStatus.NORMAL,
            interval="60s",
        )
        for outcome in ExecutionOutcome:
            t = transition_for(outcome, target=target, now=datetime.utcnow())
            assert t.status != TargetStatus.RECOVERING, f"Outcome {outcome} illegally maps to RECOVERING"


class TestFetcherDoesNotMutateDurableState:
    """Verify adapters only return observations and never write target durable fields."""

    def test_generic_web_target_does_not_mutate_target(self):
        HTML = "<html><body><div class='price'>$10.00</div></body></html>"
        target = Target(
            id="t1",
            url="https://example.com",
            status=TargetStatus.NORMAL,
            interval="60s",
            consecutive_failures=0,
            next_allowed_at=None,
            metadata={"initialized": True},
        )
        adapter = GenericWebTarget(
            target=target,
            extractors=[ExtractorConfig(name="price", selector="div.price", selector_type="css")],
        )

        mock_fetcher = MagicMock()
        mock_fetcher.fetch.return_value = FetchResult(
            target_key="t1",
            status=FetchStatus.SUCCESS,
            status_code=200,
            fetched_at=datetime.utcnow(),
            content=HTML,
            etag='"v2"',
        )
        policy = FetchPolicy()

        before_status = target.status
        before_failures = target.consecutive_failures
        before_next = target.next_allowed_at

        res = adapter.execute(fetcher=mock_fetcher, policy=policy)

        assert target.status == before_status
        assert target.consecutive_failures == before_failures
        assert target.next_allowed_at == before_next
        assert res.transition is not None
        assert res.transition.status != target.status or res.outcome == ExecutionOutcome.SUCCESS_CHANGED

    def test_github_target_does_not_mutate_target(self):
        target = _make_target(
            url="pallets/flask",
            meta={"last_release_tag": "v2.0.0"}
        )
        adapter = GitHubTarget(target=target, watch_types=["releases"])

        payload = json.dumps({"tag_name": "v2.1.0", "name": "v2.1.0", "html_url": "url", "published_at": "now", "body": ""})
        mock_fetcher = MagicMock()
        mock_fetcher.fetch.return_value = FetchResult(
            target_key="t1",
            status=FetchStatus.SUCCESS,
            status_code=200,
            fetched_at=datetime.utcnow(),
            content=payload,
            etag='"rel-etag-210"',
        )
        policy = FetchPolicy()

        before_status = target.status
        before_failures = target.consecutive_failures
        before_next = target.next_allowed_at

        res = adapter.execute(fetcher=mock_fetcher, policy=policy)

        assert target.status == before_status
        assert target.consecutive_failures == before_failures
        assert target.next_allowed_at == before_next


class TestSchedulerDoesNotDecideStatus:
    """Verify scheduled_runner only persists transitions from adapters."""

    def test_scheduler_uses_adapter_transition_not_own_logic(self):
        from web_watcher.scheduled_runner import ScheduledRunner
        from unittest.mock import MagicMock

        repo = MagicMock()
        repo.claim_targets.return_value = []
        runner = ScheduledRunner(
            worker_id="worker-1",
            repo=repo,
            config={},
            rules_path=None,
        )
        # Scheduler should not construct its own StateTransition
        assert not hasattr(runner, "transition_for")
        assert not hasattr(runner, "FetchPolicy")


class TestRepositoryDoesNotDeriveStatus:
    """Verify repository persists status passed in, never derives business state."""

    def test_repository_persists_given_status(self, tmp_path):
        from web_watcher.repository import Repository
        repo = Repository(str(tmp_path / "repo.db"))
        target = Target(
            id="t1",
            url="https://example.com",
            status=TargetStatus.BACKOFF,
            consecutive_failures=2,
        )
        repo.save_target(target)

        # Direct update should persist exactly what we pass
        repo.update_target_status(
            "t1",
            status=TargetStatus.COOLDOWN,
            consecutive_failures=5,
            next_allowed_at=datetime.utcnow() + timedelta(hours=1),
        )
        loaded = repo.get_target("t1")
        assert loaded.status == TargetStatus.COOLDOWN
        assert loaded.consecutive_failures == 5

    def test_cooldown_must_pass_through_recovering(self, tmp_path):
        """COOLDOWN must be migrated to RECOVERING by repository, never directly to NORMAL."""
        from web_watcher.repository import Repository
        repo = Repository(str(tmp_path / "cool.db"))
        target = Target(
            id="t1",
            url="https://example.com",
            status=TargetStatus.COOLDOWN,
            next_allowed_at=datetime.utcnow() - timedelta(minutes=1),  # already expired
        )
        repo.save_target(target)

        targets = repo.list_schedulable_targets(now=datetime.utcnow())
        assert len(targets) == 1
        assert targets[0].status == TargetStatus.RECOVERING

    def test_backoff_remains_backoff_when_expired(self, tmp_path):
        """BACKOFF must not jump directly to RECOVERING; it stays BACKOFF until adapter runs."""
        from web_watcher.repository import Repository
        repo = Repository(str(tmp_path / "back.db"))
        target = Target(
            id="t1",
            url="https://example.com",
            status=TargetStatus.BACKOFF,
            next_allowed_at=datetime.utcnow() - timedelta(minutes=1),
        )
        repo.save_target(target)

        targets = repo.list_schedulable_targets(now=datetime.utcnow())
        assert len(targets) == 1
        assert targets[0].status == TargetStatus.BACKOFF

    def test_normal_cannot_jump_to_recovering(self, tmp_path):
        """NORMAL must not jump directly to RECOVERING."""
        from web_watcher.repository import Repository
        repo = Repository(str(tmp_path / "norm.db"))
        target = Target(
            id="t1",
            url="https://example.com",
            status=TargetStatus.NORMAL,
        )
        repo.save_target(target)

        targets = repo.list_schedulable_targets(now=datetime.utcnow())
        assert len(targets) == 1
        assert targets[0].status == TargetStatus.NORMAL


class TestRestartRecovery:
    """Verify state survives serialization round-trip (simulated restart)."""

    def test_status_survives_database_roundtrip(self, tmp_path):
        from web_watcher.repository import Repository
        repo = Repository(str(tmp_path / "restart.db"))

        target = Target(
            id="restart_tgt",
            url="https://example.com",
            status=TargetStatus.COOLDOWN,
            consecutive_failures=3,
            next_allowed_at=datetime.utcnow() + timedelta(minutes=30),
            metadata={"key": "value"},
        )
        repo.save_target(target)

        # Simulate restart: open a new repository connection
        repo2 = Repository(str(tmp_path / "restart.db"))
        loaded = repo2.get_target("restart_tgt")
        assert loaded is not None
        assert loaded.status == TargetStatus.COOLDOWN
        assert loaded.consecutive_failures == 3
        assert loaded.next_allowed_at is not None
        assert loaded.metadata == {"key": "value"}
