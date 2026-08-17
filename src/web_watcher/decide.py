"""Phase 10C-A — Application-level decision path."""

from __future__ import annotations

from typing import Optional

from .ai_contract import AIContext, AIJudge
from .ai_errors import AIError
from .final_decision import FinalDecision, resolve
from .models import Event
from .policy import PolicyEngine


def decide_event(
    event: Event,
    policy_engine: Optional[PolicyEngine] = None,
    judge: Optional[AIJudge] = None,
) -> FinalDecision:
    """Take an Event and produce a FinalDecision.

    Chain:
        Event -> PolicyDecision -> AIContext -> AIJudgment -> FinalDecision

    If AI is unavailable (judge=None or provider raises AIError),
    fall back to policy-only decision with status AI_UNAVAILABLE.
    """
    policy = (policy_engine or PolicyEngine()).evaluate(event)

    if judge is not None:
        ctx = AIContext(event=event, policy_decision=policy)
        try:
            judgment = judge.judge(ctx)
            return resolve(policy_decision=policy, ai_judgment=judgment)
        except AIError:
            return resolve(policy_decision=policy, ai_judgment=None)

    return resolve(policy_decision=policy, ai_judgment=None)
