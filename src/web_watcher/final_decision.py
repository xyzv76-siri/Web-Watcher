"""Phase 10C-A — Final Decision contract.

Combines the deterministic PolicyDecision (Phase 9) and the semantic
AIJudgment (Phase 10A) into one resolved, immutable decision object.

This module is pure: no IO, no network, no mutable state, no subprocess,
no external packages.

Contract rules enforced by the resolver:
    1. PolicyDecision is the deterministic baseline.
    2. AIJudgment refines ONLY importance (up, down, or same).
    3. For non-CRITICAL policy importance, AI may upgrade, downgrade, or keep.
    4. For CRITICAL policy importance, final importance MUST remain CRITICAL.
    5. PolicyDecision.action remains authoritative; AI does not choose Action.
    6. final_action = policy_decision.action always.
    7. notify_allowed derives from final_action only (AI cannot override).
    8. investigate_requested derives from final_action only (AI cannot override).
    9. AI_UNAVAILABLE: preserve policy importance/action, no notify/investigate.
    10. FinalDecision is immutable and side-effect-free.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .ai_contract import AIJudgment
from .policy import Action, Importance, PolicyDecision


# ---------------------------------------------------------------------------
# Decision status
# ---------------------------------------------------------------------------


class DecisionStatus(str, Enum):
    """Explicit status distinguishing a resolved decision from an
    AI-unavailable decision.

    RESOLVED:
        An AI judgment was produced and incorporated. Normal resolution
        rules apply.

    AI_UNAVAILABLE:
        An AI judge was configured but the provider raised an AIError.
        No notification or investigation is allowed, even for CRITICAL
        events.
    """

    RESOLVED = "resolved"
    AI_UNAVAILABLE = "ai_unavailable"


# ---------------------------------------------------------------------------
# Importance ordering
# ---------------------------------------------------------------------------

_IMPORTANCE_RANK: dict[Importance, int] = {
    Importance.IGNORE: 0,
    Importance.INTERESTING: 1,
    Importance.IMPORTANT: 2,
    Importance.CRITICAL: 3,
}


def _importance_rank(importance: Importance) -> int:
    return _IMPORTANCE_RANK[importance]


# ---------------------------------------------------------------------------
# FinalDecision
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FinalDecision:
    """Minimal, deterministic, immutable decision.

    ``ai_judgment`` is None when the AI provider was unavailable.
    When status is AI_UNAVAILABLE, ``ai_judgment`` is always None.
    """

    policy_decision: PolicyDecision
    ai_judgment: Optional[AIJudgment]
    final_importance: Importance
    final_action: Action
    notify_allowed: bool
    investigate_requested: bool
    ai_overrode: bool
    reason: str
    summary: str
    status: DecisionStatus


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------


def _resolve_notify(final_action: Action) -> bool:
    """notify_allowed = True iff final_action in (NOTIFY, INVESTIGATE_AND_NOTIFY).

    AI cannot override the authoritative policy action.
    """
    return final_action in (Action.NOTIFY, Action.INVESTIGATE_AND_NOTIFY)


def _resolve_investigate(final_action: Action) -> bool:
    """investigate_requested = True iff final_action == INVESTIGATE_AND_NOTIFY.

    AI cannot override the authoritative policy action.
    """
    return final_action == Action.INVESTIGATE_AND_NOTIFY


def _build_reason(
    policy_decision: PolicyDecision,
    ai_judgment: Optional[AIJudgment],
    ai_overrode: bool,
    ai_rank: int,
    policy_rank: int,
) -> str:
    """Build a human-readable reason string for a RESOLVED decision."""
    ai_label = (
        ai_judgment.importance.value
        if ai_judgment is not None
        else "unavailable"
    )

    if ai_overrode:
        if ai_rank > policy_rank:
            return (
                f"policy={policy_decision.importance.value}, "
                f"ai={ai_label}: AI elevated the decision"
            )
        else:
            return (
                f"policy={policy_decision.importance.value}, "
                f"ai={ai_label}: AI downgraded the decision"
            )

    return (
        f"policy={policy_decision.importance.value}, "
        f"ai={ai_label}: policy was authoritative"
    )


def _build_failure_reason(policy_decision: PolicyDecision) -> str:
    return (
        f"AI judgment was unavailable; "
        f"policy_importance={policy_decision.importance.value}, "
        f"policy_action={policy_decision.action.value}"
    )


def _build_summary(ai_judgment: Optional[AIJudgment]) -> str:
    if ai_judgment is not None:
        return ai_judgment.summary
    return ""


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


def resolve(
    policy_decision: PolicyDecision,
    ai_judgment: Optional[AIJudgment] = None,
) -> FinalDecision:
    """Resolve PolicyDecision and AIJudgment into a FinalDecision.

    Pure function. No IO, no network, no mutation of inputs.

    Status is determined from ai_judgment:
        - ai_judgment is not None -> RESOLVED
        - ai_judgment is None    -> AI_UNAVAILABLE
    """

    # ---- AI_UNAVAILABLE: ai_judgment is None ----------------------------
    if ai_judgment is None:
        return FinalDecision(
            policy_decision=policy_decision,
            ai_judgment=None,
            final_importance=policy_decision.importance,
            final_action=policy_decision.action,
            notify_allowed=False,
            investigate_requested=False,
            ai_overrode=False,
            reason=_build_failure_reason(policy_decision),
            summary="",
            status=DecisionStatus.AI_UNAVAILABLE,
        )

    ai_rank = _importance_rank(ai_judgment.importance)
    policy_rank = _importance_rank(policy_decision.importance)

    # ---- CRITICAL policy is immutable -------------------------------------
    if policy_decision.importance is Importance.CRITICAL:
        return FinalDecision(
            policy_decision=policy_decision,
            ai_judgment=ai_judgment,
            final_importance=Importance.CRITICAL,
            final_action=policy_decision.action,
            notify_allowed=_resolve_notify(policy_decision.action),
            investigate_requested=_resolve_investigate(policy_decision.action),
            ai_overrode=False,
            reason=_build_reason(policy_decision, ai_judgment, False, ai_rank, policy_rank),
            summary=_build_summary(ai_judgment),
            status=DecisionStatus.RESOLVED,
        )

    # ---- Non-CRITICAL: AI may elevate, downgrade, or keep ------------------
    if ai_rank != policy_rank:
        final_importance = ai_judgment.importance
        ai_overrode = True
    else:
        final_importance = policy_decision.importance
        ai_overrode = False

    final_action = policy_decision.action

    return FinalDecision(
        policy_decision=policy_decision,
        ai_judgment=ai_judgment,
        final_importance=final_importance,
        final_action=final_action,
        notify_allowed=_resolve_notify(final_action),
        investigate_requested=_resolve_investigate(final_action),
        ai_overrode=ai_overrode,
        reason=_build_reason(policy_decision, ai_judgment, ai_overrode, ai_rank, policy_rank),
        summary=_build_summary(ai_judgment),
        status=DecisionStatus.RESOLVED,
    )
