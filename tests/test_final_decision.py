"""Phase 10C-A — FinalDecision resolver tests.

Tests the pure `resolve()` function in final_decision.py.
All tests are deterministic, require no mocks, no network, no IO.
"""

from __future__ import annotations

import copy
import pickle
from dataclasses import asdict

import pytest

from web_watcher.ai_contract import AIJudgment
from web_watcher.final_decision import FinalDecision, resolve
from web_watcher.policy import Action, Importance, PolicyDecision


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _policy(importance: Importance, action: Action, reason: str) -> PolicyDecision:
    return PolicyDecision(importance=importance, action=action, reason=reason)


def _judgment(
    relevance: float = 0.85,
    importance: Importance = Importance.IMPORTANT,
    worth_notifying: bool = True,
    investigate: bool = False,
    reason: str = "test judgment",
    summary: str = "test summary",
) -> AIJudgment:
    return AIJudgment(
        relevance=relevance,
        importance=importance,
        worth_notifying=worth_notifying,
        investigate=investigate,
        reason=reason,
        summary=summary,
    )


# ===========================================================================
# CRITICAL POLICY IMMUTABILITY
# ===========================================================================


class TestCriticalPolicyImmutability:
    """Rule 1: AI cannot suppress a CRITICAL policy decision."""

    def test_policy_critical_ai_ignores_remains_critical(self):
        policy = _policy(Importance.CRITICAL, Action.INVESTIGATE_AND_NOTIFY, "critical")
        judgment = _judgment(importance=Importance.IGNORE)

        result = resolve(policy, judgment)

        assert result.final_importance is Importance.CRITICAL
        assert result.final_action is Action.INVESTIGATE_AND_NOTIFY
        assert result.ai_overrode is False

    def test_policy_critical_ai_says_critical_stays_critical(self):
        policy = _policy(Importance.CRITICAL, Action.INVESTIGATE_AND_NOTIFY, "critical")
        judgment = _judgment(importance=Importance.CRITICAL)

        result = resolve(policy, judgment)

        assert result.final_importance is Importance.CRITICAL
        assert result.ai_overrode is False

    def test_policy_critical_no_ai_stays_critical(self):
        policy = _policy(Importance.CRITICAL, Action.INVESTIGATE_AND_NOTIFY, "critical")

        result = resolve(policy, None)

        assert result.final_importance is Importance.CRITICAL
        assert result.final_action is Action.INVESTIGATE_AND_NOTIFY
        assert result.ai_overrode is False

    def test_policy_critical_notify_and_investigate_always_true(self):
        policy = _policy(Importance.CRITICAL, Action.INVESTIGATE_AND_NOTIFY, "critical")
        judgment = _judgment(importance=Importance.IGNORE, worth_notifying=False, investigate=False)

        result = resolve(policy, judgment)

        assert result.notify_allowed is True
        assert result.investigate_requested is True


# ===========================================================================
# ELEVATION RULES
# ===========================================================================


class TestElevation:
    """Rule 2: AI can elevate, but never lower below policy importance."""

    def test_policy_ignore_ai_important_elevates(self):
        policy = _policy(Importance.IGNORE, Action.DISCARD, "no rule matched")
        judgment = _judgment(importance=Importance.IMPORTANT)

        result = resolve(policy, judgment)

        assert result.final_importance is Importance.IMPORTANT
        assert result.final_action is Action.DISCARD
        assert result.ai_overrode is True

    def test_policy_interesting_ai_critical_elevates(self):
        policy = _policy(Importance.INTERESTING, Action.SUMMARIZE, "interesting")
        judgment = _judgment(importance=Importance.CRITICAL)

        result = resolve(policy, judgment)

        assert result.final_importance is Importance.CRITICAL
        assert result.final_action is Action.SUMMARIZE
        assert result.ai_overrode is True

    def test_policy_important_ai_important_no_elevation(self):
        policy = _policy(Importance.IMPORTANT, Action.NOTIFY, "important")
        judgment = _judgment(importance=Importance.IMPORTANT)

        result = resolve(policy, judgment)

        assert result.final_importance is Importance.IMPORTANT
        assert result.final_action is Action.NOTIFY
        assert result.ai_overrode is False

    def test_policy_ignore_ai_ignores_no_elevation(self):
        policy = _policy(Importance.IGNORE, Action.DISCARD, "no rule")
        judgment = _judgment(importance=Importance.IGNORE)

        result = resolve(policy, judgment)

        assert result.final_importance is Importance.IGNORE
        assert result.final_action is Action.DISCARD
        assert result.ai_overrode is False

    def test_policy_important_ai_ignores_with_downgrade(self):
        policy = _policy(Importance.IMPORTANT, Action.NOTIFY, "important")
        judgment = _judgment(importance=Importance.IGNORE)

        result = resolve(policy, judgment)

        assert result.final_importance is Importance.IGNORE
        assert result.final_action is Action.NOTIFY
        assert result.ai_overrode is True

    def test_policy_interesting_ai_important_elevates_to_important(self):
        policy = _policy(Importance.INTERESTING, Action.SUMMARIZE, "interesting")
        judgment = _judgment(importance=Importance.IMPORTANT)

        result = resolve(policy, judgment)

        assert result.final_importance is Importance.IMPORTANT
        assert result.final_action is Action.SUMMARIZE
        assert result.ai_overrode is True

    def test_policy_ignore_ai_interesting_elevates_to_interesting(self):
        policy = _policy(Importance.IGNORE, Action.DISCARD, "no rule")
        judgment = _judgment(importance=Importance.INTERESTING)

        result = resolve(policy, judgment)

        assert result.final_importance is Importance.INTERESTING
        assert result.final_action is Action.DISCARD
        assert result.ai_overrode is True


# ===========================================================================
# POLICY-ONLY FALLBACK
# ===========================================================================


class TestPolicyOnlyFallback:
    """When AI is unavailable (None), fall back to policy-only."""

    def test_no_ai_ignores_policy(self):
        policy = _policy(Importance.IGNORE, Action.DISCARD, "no rule")
        result = resolve(policy, None)

        assert result.final_importance is Importance.IGNORE
        assert result.final_action is Action.DISCARD
        assert result.ai_overrode is False

    def test_no_ai_important_policy(self):
        policy = _policy(Importance.IMPORTANT, Action.NOTIFY, "important")
        result = resolve(policy, None)

        assert result.final_importance is Importance.IMPORTANT
        assert result.final_action is Action.NOTIFY
        assert result.ai_overrode is False

    def test_no_ai_summary_is_empty(self):
        policy = _policy(Importance.IGNORE, Action.DISCARD, "no rule")
        result = resolve(policy, None)

        assert result.summary == ""

    def test_no_ai_ai_judgment_is_none(self):
        policy = _policy(Importance.IGNORE, Action.DISCARD, "no rule")
        result = resolve(policy, None)

        assert result.ai_judgment is None

    def test_no_ai_reason_says_unavailable(self):
        policy = _policy(Importance.IGNORE, Action.DISCARD, "no rule")
        result = resolve(policy, None)

        assert "unavailable" in result.reason


# ===========================================================================
# NOTIFY ALLOWED
# ===========================================================================


class TestNotifyAllowed:
    """notify_allowed: whether notification is appropriate for this decision."""

    def test_policy_notify_action_allows_notification(self):
        policy = _policy(Importance.IMPORTANT, Action.NOTIFY, "important")
        judgment = _judgment(worth_notifying=False)

        result = resolve(policy, judgment)

        assert result.notify_allowed is True

    def test_policy_investigate_action_allows_notification(self):
        policy = _policy(Importance.CRITICAL, Action.INVESTIGATE_AND_NOTIFY, "critical")
        judgment = _judgment(worth_notifying=False)

        result = resolve(policy, judgment)

        assert result.notify_allowed is True

    def test_policy_discard_ai_worth_notifying_high_importance(self):
        policy = _policy(Importance.IGNORE, Action.DISCARD, "no rule")
        judgment = _judgment(
            importance=Importance.IMPORTANT,
            worth_notifying=True,
        )

        result = resolve(policy, judgment)

        assert result.final_importance is Importance.IMPORTANT
        assert result.final_action is Action.DISCARD
        assert result.notify_allowed is False

    def test_policy_discard_ai_worth_notifying_low_importance(self):
        policy = _policy(Importance.IGNORE, Action.DISCARD, "no rule")
        judgment = _judgment(
            importance=Importance.IGNORE,
            worth_notifying=True,
        )

        result = resolve(policy, judgment)

        assert result.final_importance is Importance.IGNORE
        assert result.notify_allowed is False

    def test_policy_discard_ai_not_worth_notifying(self):
        policy = _policy(Importance.IGNORE, Action.DISCARD, "no rule")
        judgment = _judgment(worth_notifying=False)

        result = resolve(policy, judgment)

        assert result.notify_allowed is False

    def test_no_ai_policy_discard_no_notification(self):
        policy = _policy(Importance.IGNORE, Action.DISCARD, "no rule")

        result = resolve(policy, None)

        assert result.notify_allowed is False

    def test_policy_summarize_ai_worth_notifying_interesting(self):
        policy = _policy(Importance.INTERESTING, Action.SUMMARIZE, "interesting")
        judgment = _judgment(worth_notifying=True, importance=Importance.INTERESTING)

        result = resolve(policy, judgment)

        assert result.final_importance is Importance.INTERESTING
        assert result.final_action is Action.SUMMARIZE
        assert result.notify_allowed is False

    def test_policy_summarize_ai_not_worth_notifying(self):
        policy = _policy(Importance.INTERESTING, Action.SUMMARIZE, "interesting")
        judgment = _judgment(worth_notifying=False, importance=Importance.INTERESTING)

        result = resolve(policy, judgment)

        assert result.final_importance is Importance.INTERESTING
        assert result.notify_allowed is False


# ===========================================================================
# INVESTIGATE REQUESTED
# ===========================================================================


class TestInvestigateRequested:
    """investigate_requested: whether investigation is appropriate for this decision."""

    def test_policy_critical_always_requests_investigation(self):
        policy = _policy(Importance.CRITICAL, Action.INVESTIGATE_AND_NOTIFY, "critical")
        judgment = _judgment(investigate=False)

        result = resolve(policy, judgment)

        assert result.investigate_requested is True

    def test_policy_ignore_ai_investigate_important_importance(self):
        policy = _policy(Importance.IGNORE, Action.DISCARD, "no rule")
        judgment = _judgment(
            importance=Importance.IMPORTANT,
            investigate=True,
        )

        result = resolve(policy, judgment)

        assert result.final_importance is Importance.IMPORTANT
        assert result.final_action is Action.DISCARD
        assert result.investigate_requested is False

    def test_policy_ignore_ai_investigate_ignores_importance(self):
        policy = _policy(Importance.IGNORE, Action.DISCARD, "no rule")
        judgment = _judgment(
            importance=Importance.IGNORE,
            investigate=True,
        )

        result = resolve(policy, judgment)

        assert result.final_importance is Importance.IGNORE
        assert result.investigate_requested is False

    def test_policy_important_no_ai(self):
        policy = _policy(Importance.IMPORTANT, Action.NOTIFY, "important")

        result = resolve(policy, None)

        assert result.investigate_requested is False

    def test_policy_discard_no_ai(self):
        policy = _policy(Importance.IGNORE, Action.DISCARD, "no rule")

        result = resolve(policy, None)

        assert result.investigate_requested is False

    def test_ai_investigate_false_no_request(self):
        policy = _policy(Importance.IGNORE, Action.DISCARD, "no rule")
        judgment = _judgment(importance=Importance.IMPORTANT, investigate=False)

        result = resolve(policy, judgment)

        assert result.investigate_requested is False


# ===========================================================================
# IMMUTABILITY
# ===========================================================================


class TestImmutability:
    """Rules 5, 7: Inputs must not mutate. FinalDecision is frozen."""

    def test_policy_decision_unchanged_after_resolve(self):
        policy = _policy(Importance.IGNORE, Action.DISCARD, "original reason")
        policy_before = dict(asdict(policy))

        judgment = _judgment(importance=Importance.IMPORTANT)
        result = resolve(policy, judgment)

        assert dict(asdict(policy)) == policy_before
        assert result.policy_decision is policy  # same object reference

    def test_ai_judgment_unchanged_after_resolve(self):
        policy = _policy(Importance.IGNORE, Action.DISCARD, "no rule")
        judgment = _judgment(importance=Importance.IMPORTANT, reason="original reason")
        judgment_dict = dict(asdict(judgment))

        result = resolve(policy, judgment)

        assert dict(asdict(judgment)) == judgment_dict
        assert result.ai_judgment is judgment  # same object reference

    def test_final_decision_is_frozen(self):
        policy = _policy(Importance.IGNORE, Action.DISCARD, "no rule")
        judgment = _judgment(importance=Importance.IMPORTANT)
        result = resolve(policy, judgment)

        with pytest.raises(Exception):
            result.final_importance = Importance.CRITICAL  # type: ignore[attr-defined]

    def test_final_decision_is_pickleable(self):
        """Frozen dataclass with enum fields should be picklable."""
        policy = _policy(Importance.IGNORE, Action.DISCARD, "no rule")
        judgment = _judgment(importance=Importance.IMPORTANT)
        result = resolve(policy, judgment)

        pickled = pickle.dumps(result)
        restored = pickle.loads(pickled)

        assert dict(asdict(result)) == dict(asdict(restored))


# ===========================================================================
# REASON AND SUMMARY
# ===========================================================================


class TestReasonAndSummary:
    """Verify reason and summary fields are populated correctly."""

    def test_reason_includes_policy_and_ai_when_overridden(self):
        policy = _policy(Importance.IGNORE, Action.DISCARD, "no rule")
        judgment = _judgment(importance=Importance.IMPORTANT)

        result = resolve(policy, judgment)

        assert "policy=ignore" in result.reason
        assert "ai=important" in result.reason
        assert "AI elevated" in result.reason

    def test_reason_says_policy_authoritative_when_no_override(self):
        policy = _policy(Importance.IMPORTANT, Action.NOTIFY, "important")
        judgment = _judgment(importance=Importance.IMPORTANT)

        result = resolve(policy, judgment)

        assert "policy=important" in result.reason
        assert "ai=important" in result.reason
        assert "policy was authoritative" in result.reason

    def test_reason_says_unavailable_when_no_ai(self):
        policy = _policy(Importance.IGNORE, Action.DISCARD, "no rule")

        result = resolve(policy, None)

        assert "unavailable" in result.reason

    def test_summary_from_ai_judgment(self):
        policy = _policy(Importance.IGNORE, Action.DISCARD, "no rule")
        judgment = _judgment(summary="custom summary text")

        result = resolve(policy, judgment)

        assert result.summary == "custom summary text"

    def test_summary_empty_when_no_ai(self):
        policy = _policy(Importance.IGNORE, Action.DISCARD, "no rule")

        result = resolve(policy, None)

        assert result.summary == ""


# ===========================================================================
# AI_OVERRODE FLAG
# ===========================================================================


class TestAiOverrode:
    """Verify the ai_overrode flag is set correctly."""

    def test_overrode_true_when_ai_elevates(self):
        policy = _policy(Importance.IGNORE, Action.DISCARD, "no rule")
        judgment = _judgment(importance=Importance.IMPORTANT)

        result = resolve(policy, judgment)

        assert result.ai_overrode is True

    def test_overrode_false_when_ai_equal_to_policy(self):
        policy = _policy(Importance.IMPORTANT, Action.NOTIFY, "important")
        judgment = _judgment(importance=Importance.IMPORTANT)

        result = resolve(policy, judgment)

        assert result.ai_overrode is False

    def test_overrode_true_when_ai_below_policy(self):
        policy = _policy(Importance.IMPORTANT, Action.NOTIFY, "important")
        judgment = _judgment(importance=Importance.IGNORE)

        result = resolve(policy, judgment)

        assert result.ai_overrode is True

    def test_overrode_false_when_no_ai(self):
        policy = _policy(Importance.IGNORE, Action.DISCARD, "no rule")

        result = resolve(policy, None)

        assert result.ai_overrode is False

    def test_overrode_false_when_policy_critical(self):
        policy = _policy(Importance.CRITICAL, Action.INVESTIGATE_AND_NOTIFY, "critical")
        judgment = _judgment(importance=Importance.IGNORE)

        result = resolve(policy, judgment)

        assert result.ai_overrode is False


# ===========================================================================
# FIELD DISTINCTNESS
# ===========================================================================


class TestFieldDistinctness:
    """Verify FinalDecision preserves the distinction between
    PolicyDecision, AIJudgment, and FinalDecision."""

    def test_policy_decision_field_matches_input(self):
        policy = _policy(Importance.IGNORE, Action.DISCARD, "original reason")
        judgment = _judgment(importance=Importance.IMPORTANT)

        result = resolve(policy, judgment)

        assert result.policy_decision.importance is policy.importance
        assert result.policy_decision.action is policy.action
        assert result.policy_decision.reason == policy.reason

    def test_ai_judgment_field_matches_input(self):
        policy = _policy(Importance.IGNORE, Action.DISCARD, "no rule")
        judgment = _judgment(
            relevance=0.72,
            importance=Importance.IMPORTANT,
            worth_notifying=True,
            investigate=True,
            reason="custom reason",
            summary="custom summary",
        )

        result = resolve(policy, judgment)

        assert result.ai_judgment is not None
        assert result.ai_judgment.relevance == 0.72
        assert result.ai_judgment.importance is Importance.IMPORTANT
        assert result.ai_judgment.worth_notifying is True
        assert result.ai_judgment.investigate is True
        assert result.ai_judgment.reason == "custom reason"
        assert result.ai_judgment.summary == "custom summary"

    def test_final_action_always_equals_policy_action(self):
        """Contract point 7: final_action must always equal policy_decision.action
        regardless of AI judgment."""
        combos = [
            (Importance.IGNORE, Action.DISCARD, Importance.IGNORE),
            (Importance.IGNORE, Action.DISCARD, Importance.IMPORTANT),
            (Importance.IGNORE, Action.DISCARD, Importance.CRITICAL),
            (Importance.IGNORE, Action.DISCARD, None),
            (Importance.INTERESTING, Action.SUMMARIZE, Importance.INTERESTING),
            (Importance.INTERESTING, Action.SUMMARIZE, Importance.IMPORTANT),
            (Importance.IMPORTANT, Action.NOTIFY, Importance.IGNORE),
            (Importance.IMPORTANT, Action.NOTIFY, Importance.IMPORTANT),
            (Importance.CRITICAL, Action.INVESTIGATE_AND_NOTIFY, Importance.CRITICAL),
            (Importance.CRITICAL, Action.INVESTIGATE_AND_NOTIFY, Importance.IGNORE),
        ]

        for pol_imp, pol_act, ai_imp in combos:
            policy = _policy(pol_imp, pol_act, "test")
            judgment = _judgment(importance=ai_imp) if ai_imp else None

            result = resolve(policy, judgment)

            assert result.final_action is pol_act, (
                f"final_action={result.final_action} != policy.action={pol_act}"
                f" (policy={pol_imp}, ai={ai_imp})"
            )


# ===========================================================================
# DECISION STATUS
# ===========================================================================


class TestDecisionStatus:
    """DecisionStatus: RESOLVED for successful AI, AI_UNAVAILABLE for failure."""

    def test_success_ai_resolved_status(self):
        policy = _policy(Importance.IGNORE, Action.DISCARD, "no rule")
        judgment = _judgment(importance=Importance.IMPORTANT)

        result = resolve(policy, judgment)

        from web_watcher.final_decision import DecisionStatus
        assert result.status is DecisionStatus.RESOLVED

    def test_no_ai_ai_unavailable_status(self):
        policy = _policy(Importance.INTERESTING, Action.SUMMARIZE, "interesting")

        result = resolve(policy, None)

        from web_watcher.final_decision import DecisionStatus
        assert result.status is DecisionStatus.AI_UNAVAILABLE

    def test_no_ai_critical_ai_unavailable_status(self):
        """AI_UNAVAILABLE applies to CRITICAL — not an exception."""
        policy = _policy(Importance.CRITICAL, Action.INVESTIGATE_AND_NOTIFY, "critical")

        result = resolve(policy, None)

        from web_watcher.final_decision import DecisionStatus
        assert result.status is DecisionStatus.AI_UNAVAILABLE
        assert result.notify_allowed is False
        assert result.investigate_requested is False

    def test_no_ai_notify_allowed_false_for_all_importances(self):
        """AI_UNAVAILABLE: notify_allowed is always False."""
        from web_watcher.final_decision import DecisionStatus

        for imp, act in [
            (Importance.IGNORE, Action.DISCARD),
            (Importance.INTERESTING, Action.SUMMARIZE),
            (Importance.IMPORTANT, Action.NOTIFY),
            (Importance.CRITICAL, Action.INVESTIGATE_AND_NOTIFY),
        ]:
            policy = _policy(imp, act, "test")
            result = resolve(policy, None)
            assert result.status is DecisionStatus.AI_UNAVAILABLE
            assert result.notify_allowed is False, (
                f"notify_allowed should be False for {imp} AI_UNAVAILABLE"
            )

    def test_no_ai_investigate_requested_false_for_all_importances(self):
        """AI_UNAVAILABLE: investigate_requested is always False."""
        from web_watcher.final_decision import DecisionStatus

        for imp, act in [
            (Importance.IGNORE, Action.DISCARD),
            (Importance.INTERESTING, Action.SUMMARIZE),
            (Importance.IMPORTANT, Action.NOTIFY),
            (Importance.CRITICAL, Action.INVESTIGATE_AND_NOTIFY),
        ]:
            policy = _policy(imp, act, "test")
            result = resolve(policy, None)
            assert result.status is DecisionStatus.AI_UNAVAILABLE
            assert result.investigate_requested is False, (
                f"investigate_requested should be False for {imp} AI_UNAVAILABLE"
            )

    def test_no_ai_reason_explicit(self):
        """AI_UNAVAILABLE reason must explicitly state AI was unavailable."""
        policy = _policy(Importance.IMPORTANT, Action.NOTIFY, "important")

        result = resolve(policy, None)

        assert "AI judgment was unavailable" in result.reason
        assert "policy_importance" in result.reason
        assert "policy_action" in result.reason

    def test_no_ai_preserves_policy_importance(self):
        """AI_UNAVAILABLE: final importance equals PolicyDecision importance."""
        for imp, act in [
            (Importance.IGNORE, Action.DISCARD),
            (Importance.INTERESTING, Action.SUMMARIZE),
            (Importance.IMPORTANT, Action.NOTIFY),
            (Importance.CRITICAL, Action.INVESTIGATE_AND_NOTIFY),
        ]:
            policy = _policy(imp, act, "test")
            result = resolve(policy, None)
            assert result.final_importance is policy.importance

    def test_no_ai_preserves_policy_action(self):
        """AI_UNAVAILABLE: final action equals PolicyDecision action."""
        for imp, act in [
            (Importance.IGNORE, Action.DISCARD),
            (Importance.INTERESTING, Action.SUMMARIZE),
            (Importance.IMPORTANT, Action.NOTIFY),
            (Importance.CRITICAL, Action.INVESTIGATE_AND_NOTIFY),
        ]:
            policy = _policy(imp, act, "test")
            result = resolve(policy, None)
            assert result.final_action is policy.action

    def test_no_ai_summary_is_empty(self):
        """AI_UNAVAILABLE: summary is empty — no AI-produced summary."""
        policy = _policy(Importance.IMPORTANT, Action.NOTIFY, "important")
        result = resolve(policy, None)
        assert result.summary == ""

    def test_no_ai_ai_judgment_is_none(self):
        """AI_UNAVAILABLE: ai_judgment is always None."""
        policy = _policy(Importance.IMPORTANT, Action.NOTIFY, "important")
        result = resolve(policy, None)
        assert result.ai_judgment is None
