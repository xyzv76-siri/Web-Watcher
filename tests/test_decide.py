"""Phase 10C-A — Application-level decision path tests.

Tests the `decide_event()` function in decide.py.
Verifies the full chain:
    Event -> PolicyDecision -> AIContext -> AIJudgment -> FinalDecision
"""

from __future__ import annotations

from dataclasses import asdict

import pytest

from web_watcher.ai_contract import AIJudge, MockProvider
from web_watcher.decide import decide_event
from web_watcher.final_decision import DecisionStatus
from web_watcher.models import Event
from web_watcher.policy import Action, Importance, PolicyDecision, PolicyEngine


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_event(event_type: str = "interesting", importance: str = "medium") -> Event:
    """Create a minimal Event for testing."""
    from datetime import datetime

    return Event(
        id=1,
        entity_id=1,
        event_type=event_type,
        status="open",
        importance=importance,
        created_at=datetime(2026, 8, 17, 10, 0, 0),
        updated_at=datetime(2026, 8, 17, 10, 0, 0),
    )


# ===========================================================================
# HAPPY PATH
# ===========================================================================


class TestHappyPath:
    """Event flows through the full decision chain."""

    def test_interesting_event_with_valid_ai(self):
        event = _make_event(event_type="interesting")
        judge = AIJudge(MockProvider("valid"))

        result = decide_event(event, judge=judge)

        assert isinstance(result, type(result))
        assert result.policy_decision.importance is Importance.INTERESTING
        assert result.policy_decision.action is Action.SUMMARIZE
        # MockProvider returns importance="important" -> elevation
        assert result.final_importance is Importance.IMPORTANT
        assert result.final_action is Action.SUMMARIZE
        assert result.ai_overrode is True
        assert result.notify_allowed is False

    def test_important_event_with_valid_ai(self):
        event = _make_event(event_type="important")
        judge = AIJudge(MockProvider("valid"))

        result = decide_event(event, judge=judge)

        assert result.policy_decision.importance is Importance.IMPORTANT
        # MockProvider returns importance="important" -> no elevation (equal)
        assert result.final_importance is Importance.IMPORTANT
        assert result.final_action is Action.NOTIFY
        assert result.ai_overrode is False

    def test_critical_event_with_valid_ai(self):
        event = _make_event(event_type="critical")
        judge = AIJudge(MockProvider("valid"))

        result = decide_event(event, judge=judge)

        assert result.policy_decision.importance is Importance.CRITICAL
        assert result.final_importance is Importance.CRITICAL
        assert result.final_action is Action.INVESTIGATE_AND_NOTIFY
        assert result.ai_overrode is False
        assert result.notify_allowed is True
        assert result.investigate_requested is True

    def test_unknown_event_with_valid_ai(self):
        event = _make_event(event_type="unknown_event_type")
        judge = AIJudge(MockProvider("valid"))

        result = decide_event(event, judge=judge)

        assert result.policy_decision.importance is Importance.IGNORE
        # MockProvider returns importance="important" -> elevation
        assert result.final_importance is Importance.IMPORTANT
        assert result.final_action is Action.DISCARD
        assert result.ai_overrode is True


# ===========================================================================
# AI ERROR FALLBACK
# ===========================================================================


class TestAiErrorFallback:
    """When the AI provider fails, fall back to policy-only decision."""

    def test_provider_error_falls_back_to_policy_only(self):
        event = _make_event(event_type="interesting")
        judge = AIJudge(MockProvider("provider_error"))

        result = decide_event(event, judge=judge)

        # Should not raise — falls back gracefully
        assert result.ai_judgment is None
        assert result.final_importance is Importance.INTERESTING
        assert result.final_action is Action.SUMMARIZE
        assert result.ai_overrode is False

    def test_timeout_falls_back_to_policy_only(self):
        event = _make_event(event_type="interesting")
        judge = AIJudge(MockProvider("timeout"))

        result = decide_event(event, judge=judge)

        assert result.ai_judgment is None
        assert result.final_importance is Importance.INTERESTING
        assert result.final_action is Action.SUMMARIZE
        assert result.ai_overrode is False

    def test_invalid_json_falls_back_to_policy_only(self):
        event = _make_event(event_type="important")
        judge = AIJudge(MockProvider("invalid_json"))

        result = decide_event(event, judge=judge)

        assert result.ai_judgment is None
        assert result.final_importance is Importance.IMPORTANT
        assert result.final_action is Action.NOTIFY
        assert result.ai_overrode is False

    def test_invalid_schema_falls_back_to_policy_only(self):
        event = _make_event(event_type="important")
        judge = AIJudge(MockProvider("invalid_schema"))

        result = decide_event(event, judge=judge)

        assert result.ai_judgment is None
        assert result.final_importance is Importance.IMPORTANT
        assert result.final_action is Action.NOTIFY
        assert result.ai_overrode is False


# ===========================================================================
# NO AI (POLICY-ONLY)
# ===========================================================================


class TestNoAi:
    """When no AI judge is injected, decision is policy-only."""

    def test_no_judge_policy_only(self):
        event = _make_event(event_type="interesting")

        result = decide_event(event)

        assert result.ai_judgment is None
        assert result.final_importance is Importance.INTERESTING
        assert result.final_action is Action.SUMMARIZE
        assert result.ai_overrode is False

    def test_no_judge_critical_event(self):
        event = _make_event(event_type="critical")

        result = decide_event(event)

        assert result.final_importance is Importance.CRITICAL
        assert result.final_action is Action.INVESTIGATE_AND_NOTIFY
        # Phase 10C-A.1: AI_UNAVAILABLE semantics apply even to CRITICAL.
        # notify_allowed and investigate_requested are False when AI is
        # unavailable — the decision records the policy assessment but
        # does not authorize notification or investigation without AI.
        assert result.notify_allowed is False
        assert result.investigate_requested is False
        assert result.ai_judgment is None


# ===========================================================================
# IMMUTABILITY
# ===========================================================================


class TestImmutability:
    """decide_event must not mutate Event or PolicyDecision."""

    def test_event_importance_not_mutated(self):
        event = _make_event(event_type="interesting", importance="original")
        judge = AIJudge(MockProvider("valid"))

        importance_before = event.importance

        result = decide_event(event, judge=judge)

        assert event.importance == importance_before
        assert result.policy_decision.importance is Importance.INTERESTING

    def test_event_event_type_not_mutated(self):
        event = _make_event(event_type="interesting")
        judge = AIJudge(MockProvider("valid"))

        event_type_before = event.event_type

        result = decide_event(event, judge=judge)

        assert event.event_type == event_type_before

    def test_event_status_not_mutated(self):
        event = _make_event(event_type="interesting", importance="original")
        judge = AIJudge(MockProvider("valid"))

        status_before = event.status

        result = decide_event(event, judge=judge)

        assert event.status == status_before

    def test_policy_decision_not_mutated(self):
        engine = PolicyEngine()
        event = _make_event(event_type="interesting")
        judge = AIJudge(MockProvider("valid"))

        result = decide_event(event, policy_engine=engine, judge=judge)

        # The PolicyDecision stored in FinalDecision should be identical
        # to what PolicyEngine.evaluate produced
        policy_direct = engine.evaluate(event)
        assert result.policy_decision.importance is policy_direct.importance
        assert result.policy_decision.action is policy_direct.action

    def test_event_with_provider_error_not_mutated(self):
        event = _make_event(event_type="interesting", importance="original")
        judge = AIJudge(MockProvider("provider_error"))

        importance_before = event.importance

        result = decide_event(event, judge=judge)

        assert event.importance == importance_before
        assert result.ai_judgment is None


# ===========================================================================
# SUMMARY AND REASON
# ===========================================================================


class TestSummaryAndReason:
    """Verify summary and reason fields in the application path."""

    def test_summary_from_ai_judgment(self):
        event = _make_event(event_type="interesting")
        judge = AIJudge(MockProvider("valid"))

        result = decide_event(event, judge=judge)

        # MockProvider valid response has summary "mock provider: content change detected"
        assert "mock provider" in result.summary

    def test_summary_empty_when_no_ai(self):
        event = _make_event(event_type="interesting")

        result = decide_event(event)

        assert result.summary == ""

    def test_reason_present_when_no_ai(self):
        event = _make_event(event_type="interesting")

        result = decide_event(event)

        assert "unavailable" in result.reason
        assert result.reason != ""

    def test_reason_present_when_ai_valid(self):
        event = _make_event(event_type="interesting")
        judge = AIJudge(MockProvider("valid"))

        result = decide_event(event, judge=judge)

        assert result.reason != ""
        assert "policy=" in result.reason


# ===========================================================================
# CHAIN INTEGRITY
# ===========================================================================


class TestChainIntegrity:
    """Verify the full chain produces consistent results."""

    def test_full_chain_event_to_final_decision(self):
        """Event -> PolicyDecision -> AIContext -> AIJudgment -> FinalDecision."""
        event = _make_event(event_type="interesting")
        judge = AIJudge(MockProvider("valid"))

        result = decide_event(event, judge=judge)

        # Verify the chain
        # 1. PolicyDecision exists
        assert result.policy_decision is not None
        assert result.policy_decision.importance is Importance.INTERESTING

        # 2. AIJudgment exists (valid provider)
        assert result.ai_judgment is not None
        assert result.ai_judgment.importance is Importance.IMPORTANT

        # 3. FinalDecision fields are consistent
        assert result.final_importance is Importance.IMPORTANT
        assert result.final_action is Action.SUMMARIZE

        # 4. Notification is not allowed (policy action is SUMMARIZE)
        assert result.notify_allowed is False

        # 5. Investigation is not requested (AI said investigate=False)
        assert result.investigate_requested is False

        # 6. AI overrode
        assert result.ai_overrode is True

    def test_full_chain_with_critical_event(self):
        """Critical event should produce CRITICAL regardless of AI."""
        event = _make_event(event_type="critical")
        judge = AIJudge(MockProvider("valid"))

        result = decide_event(event, judge=judge)

        assert result.policy_decision.importance is Importance.CRITICAL
        assert result.final_importance is Importance.CRITICAL
        assert result.final_action is Action.INVESTIGATE_AND_NOTIFY
        assert result.notify_allowed is True
        assert result.investigate_requested is True
        assert result.ai_overrode is False

    def test_full_chain_with_provider_failure(self):
        """Provider failure should not break the chain."""
        event = _make_event(event_type="interesting")
        judge = AIJudge(MockProvider("provider_error"))

        result = decide_event(event, judge=judge)

        assert result.policy_decision is not None
        assert result.policy_decision.importance is Importance.INTERESTING
        assert result.ai_judgment is None
        assert result.final_importance is Importance.INTERESTING
        assert result.final_action is Action.SUMMARIZE


# ===========================================================================
# AI UNAVAILABLE STATUS
# ===========================================================================


class TestAiUnavailableStatus:
    """Verify that AI errors produce AI_UNAVAILABLE status explicitly."""

    def test_provider_error_ai_unavailable_status(self):
        event = _make_event(event_type="interesting")
        judge = AIJudge(MockProvider("provider_error"))

        result = decide_event(event, judge=judge)

        from web_watcher.final_decision import DecisionStatus
        assert result.status is DecisionStatus.AI_UNAVAILABLE

    def test_provider_timeout_ai_unavailable_status(self):
        event = _make_event(event_type="interesting")
        judge = AIJudge(MockProvider("timeout"))

        result = decide_event(event, judge=judge)

        from web_watcher.final_decision import DecisionStatus
        assert result.status is DecisionStatus.AI_UNAVAILABLE

    def test_invalid_json_ai_unavailable_status(self):
        event = _make_event(event_type="important")
        judge = AIJudge(MockProvider("invalid_json"))

        result = decide_event(event, judge=judge)

        from web_watcher.final_decision import DecisionStatus
        assert result.status is DecisionStatus.AI_UNAVAILABLE

    def test_invalid_schema_ai_unavailable_status(self):
        event = _make_event(event_type="important")
        judge = AIJudge(MockProvider("invalid_schema"))

        result = decide_event(event, judge=judge)

        from web_watcher.final_decision import DecisionStatus
        assert result.status is DecisionStatus.AI_UNAVAILABLE

    def test_unsupported_value_ai_unavailable_status(self):
        event = _make_event(event_type="important")
        judge = AIJudge(MockProvider("unsupported_value"))

        result = decide_event(event, judge=judge)

        from web_watcher.final_decision import DecisionStatus
        assert result.status is DecisionStatus.AI_UNAVAILABLE
        assert result.ai_judgment is None
        assert result.notify_allowed is False
        assert result.investigate_requested is False

    def test_ai_unavailable_reason_explicit(self):
        """Reason must explicitly state AI judgment was unavailable."""
        event = _make_event(event_type="important")
        judge = AIJudge(MockProvider("provider_error"))

        result = decide_event(event, judge=judge)

        assert "AI judgment was unavailable" in result.reason

    def test_success_ai_resolved_status(self):
        """Successful AI judgment produces RESOLVED status."""
        event = _make_event(event_type="interesting")
        judge = AIJudge(MockProvider("valid"))

        result = decide_event(event, judge=judge)

        from web_watcher.final_decision import DecisionStatus
        assert result.status is DecisionStatus.RESOLVED


# ===========================================================================
# UNRELATED EXCEPTIONS NOT SWALLOWED
# ===========================================================================


class TestUnrelatedExceptionsNotSwallowed:
    """Unrelated exceptions must NOT be caught and converted to AI_UNAVAILABLE."""

    def test_non_ai_error_from_judge_propagates(self):
        """If the judge raises a non-AIError, decide_event must propagate it."""

        class NonAIErrorJudge:
            def judge(self, context):
                raise RuntimeError("unrelated programming error")

        event = _make_event(event_type="interesting")

        with pytest.raises(RuntimeError, match="unrelated programming error"):
            decide_event(event, judge=NonAIErrorJudge())  # type: ignore[arg-type]

    def test_non_ai_error_from_policy_engine_propagates(self):
        """If the policy engine raises a non-AIError, decide_event must propagate."""

        class FailingPolicyEngine:
            def evaluate(self, event):
                raise ValueError("policy engine bug")

        event = _make_event(event_type="interesting")

        with pytest.raises(ValueError, match="policy engine bug"):
            decide_event(event, policy_engine=FailingPolicyEngine())


# ===========================================================================
# AI UNAVAILABLE CRITICAL SEMANTICS
# ===========================================================================


class TestAiUnavailableCriticalSemantics:
    """AI_UNAVAILABLE for CRITICAL: no notification or investigation."""

    def test_critical_provider_error_no_notification(self):
        event = _make_event(event_type="critical")
        judge = AIJudge(MockProvider("provider_error"))

        result = decide_event(event, judge=judge)

        assert result.final_importance is Importance.CRITICAL
        assert result.final_action is Action.INVESTIGATE_AND_NOTIFY
        assert result.notify_allowed is False
        assert result.investigate_requested is False

    def test_critical_timeout_no_notification(self):
        event = _make_event(event_type="critical")
        judge = AIJudge(MockProvider("timeout"))

        result = decide_event(event, judge=judge)

        assert result.notify_allowed is False
        assert result.investigate_requested is False

    def test_critical_no_judge_no_notification(self):
        event = _make_event(event_type="critical")

        result = decide_event(event)

        assert result.notify_allowed is False
        assert result.investigate_requested is False


# ===========================================================================
# SUCCESSFUL PATH PRESERVATION
# ===========================================================================


class TestSuccessfulPathPreservation:
    """Verify successful AI paths remain unchanged after AI_UNAVAILABLE changes."""

    def test_successful_ai_elevation_unchanged(self):
        """AI elevation behavior remains identical."""
        event = _make_event(event_type="interesting")
        judge = AIJudge(MockProvider("valid"))

        result = decide_event(event, judge=judge)

        assert result.final_importance is Importance.IMPORTANT
        assert result.final_action is Action.SUMMARIZE
        assert result.ai_overrode is True
        assert result.notify_allowed is False

    def test_critical_with_valid_ai_allows_notification(self):
        """CRITICAL + valid AI still allows notification and investigation."""
        event = _make_event(event_type="critical")
        judge = AIJudge(MockProvider("valid"))

        result = decide_event(event, judge=judge)

        assert result.final_importance is Importance.CRITICAL
        assert result.final_action is Action.INVESTIGATE_AND_NOTIFY
        assert result.notify_allowed is True
        assert result.investigate_requested is True
        assert result.status is DecisionStatus.RESOLVED
