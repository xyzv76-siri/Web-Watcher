"""K.6 Investigation Engine — executes an InvestigationPlan and produces
an InvestigationResult.

Phase 11-A K.6 — Investigation Engine (Architecture Freeze APPROVED,
2026-08-17).

Responsibilities:
    * Execute a pre-generated InvestigationPlan (single PlanStep).
    * Track steps_used (PlanSteps for which Tool.execute() was entered).
    * Accumulate pages_checked from ToolResult.pages_fetched (no clamping).
    * Enforce max_steps, max_pages, timeout_seconds at runtime.
    * Collect Evidence from ToolResult.evidence into the final tuple.
    * Construct InvestigationResult with findings=().
    * Produce deterministic SUCCESS / FAILED / TIMEOUT / BUDGET_EXCEEDED.

Non-responsibilities:
    * Planning (K.5).
    * Tool selection or Task→Tool matching (K.5).
    * InvestigationFinding construction (no K.1–K.5 Finding producer exists).
    * INCONCLUSIVE inference (no contract signal).
    * Retry or replanning.
    * Phase 10 AI integration.

Dependencies:
    * K.1: InvestigationPolicy
    * K.2: Evidence
    * K.3: InvestigationStatus, InvestigationFinding, InvestigationResult
    * K.4: Tool, ToolResult
    * K.5: PlanStep, InvestigationPlan, PlannerError
    * stdlib: time.monotonic, dataclasses, typing
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional

from .investigation_contract import InvestigationPolicy
from .investigation_result import (
    InvestigationResult,
    InvestigationStatus,
)
from .investigation_planner import (
    InvestigationPlan,
    PlanStep,
)
from .investigation_tools import ToolResult


# ---------------------------------------------------------------------------
# Engine-specific error
# ---------------------------------------------------------------------------


class EngineError(Exception):
    """Raised for engine-level failures (invalid plan, unexpected internal error)."""


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class Engine:
    """Stateless executor for a single InvestigationPlan.

    Receives a pre-generated InvestigationPlan (produced by K.5 Planner).
    Executes the single PlanStep and assembles an InvestigationResult.

    Does NOT:
        * own a Planner
        * call Planner.plan()
        * retry or re-plan
        * construct InvestigationFinding
        * infer INCONCLUSIVE
    """

    def __init__(self, policy: InvestigationPolicy) -> None:
        if not isinstance(policy, InvestigationPolicy):
            raise EngineError(
                f"policy must be InvestigationPolicy, got {type(policy).__name__}"
            )
        self._policy = policy

    def execute(
        self,
        plan: InvestigationPlan,
        context: Optional[Mapping[str, str]] = None,
    ) -> InvestigationResult:
        """Execute the supplied InvestigationPlan and return an InvestigationResult.

        Pre-execution checks:
            * InvestigationPlan must be a valid instance.
            * Pre-execution timeout guard.
            * Pre-execution max_steps budget guard.

        Execution:
            * Tool.execute() is called once for the single PlanStep.
            * steps_used = 1 after execute() is entered (success or exception).
            * pages_checked accumulates actual ToolResult.pages_fetched.

        Post-execution checks (in order):
            * Post-execution timeout guard.
            * Post-execution max_pages guard.
            * Tool failure check (ToolResult.success == False).

        Status precedence:
            TIMEOUT → BUDGET_EXCEEDED → FAILED → SUCCESS

        INCONCLUSIVE is not produced.
        findings=() for all outcomes.
        """
        # ---- Input validation ----
        if not isinstance(plan, InvestigationPlan):
            raise EngineError(
                f"plan must be InvestigationPlan, got {type(plan).__name__}"
            )
        steps = plan.steps
        if len(steps) != 1:
            raise EngineError(
                f"plan must contain exactly one step, got {len(steps)}"
            )
        step: PlanStep = steps[0]
        if not isinstance(step, PlanStep):
            raise EngineError(
                f"plan.steps[0] must be PlanStep, got {type(step).__name__}"
            )

        # Resolve context
        if context is None:
            exec_context: dict[str, str] = {}
        else:
            exec_context = dict(context)

        # ---- Pre-execution state ----
        start = _monotonic()
        steps_used = 0
        pages_checked = 0
        collected_evidence: tuple = ()
        failure_reason = ""
        status = InvestigationStatus.SUCCESS

        # ---- Pre-execution timeout guard ----
        if _monotonic() - start > self._policy.timeout_seconds:
            return _build_result(
                status=InvestigationStatus.TIMEOUT,
                summary="investigation timed out before execution",
                findings=(),
                evidence=(),
                confidence=0.0,
                steps_used=0,
                pages_checked=0,
                failure_reason="investigation exceeded timeout_seconds",
            )

        # ---- Pre-execution max_steps guard ----
        if steps_used + 1 > self._policy.max_steps:
            return _build_result(
                status=InvestigationStatus.BUDGET_EXCEEDED,
                summary="max_steps exceeded before execution",
                findings=(),
                evidence=(),
                confidence=0.0,
                steps_used=0,
                pages_checked=0,
                failure_reason="investigation exceeded max_steps",
            )

        # ---- Execute the single PlanStep ----
        try:
            tool_result: ToolResult = step.tool.execute(step.task, exec_context)
        except Exception as exc:
            # execute() was entered → step counts
            return _build_result(
                status=InvestigationStatus.FAILED,
                summary="tool execution failed",
                findings=(),
                evidence=(),
                confidence=0.0,
                steps_used=1,
                pages_checked=0,
                failure_reason=str(exc),
            )

        # execute() completed → step counts
        steps_used = 1
        pages_checked = tool_result.pages_fetched

        # Aggregate evidence
        collected_evidence = tuple(tool_result.evidence)

        # ---- Post-execution timeout guard ----
        if _monotonic() - start > self._policy.timeout_seconds:
            return _build_result(
                status=InvestigationStatus.TIMEOUT,
                summary="investigation timed out",
                findings=(),
                evidence=collected_evidence,
                confidence=0.0,
                steps_used=steps_used,
                pages_checked=pages_checked,
                failure_reason="investigation exceeded timeout_seconds",
            )

        # ---- Post-execution max_pages guard ----
        if pages_checked > self._policy.max_pages:
            return _build_result(
                status=InvestigationStatus.BUDGET_EXCEEDED,
                summary="max_pages exceeded",
                findings=(),
                evidence=collected_evidence,
                confidence=0.0,
                steps_used=steps_used,
                pages_checked=pages_checked,
                failure_reason="investigation exceeded max_pages",
            )

        # ---- Tool failure check ----
        if not tool_result.success:
            return _build_result(
                status=InvestigationStatus.FAILED,
                summary=tool_result.message or "tool execution failed",
                findings=(),
                evidence=collected_evidence,
                confidence=0.0,
                steps_used=steps_used,
                pages_checked=pages_checked,
                failure_reason=tool_result.message or "tool returned success=False",
            )

        # ---- Success ----
        return _build_result(
            status=InvestigationStatus.SUCCESS,
            summary="investigation completed successfully",
            findings=(),
            evidence=collected_evidence,
            confidence=1.0,
            steps_used=steps_used,
            pages_checked=pages_checked,
            failure_reason="",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_result(
    status: InvestigationStatus,
    summary: str,
    findings: tuple,
    evidence: tuple,
    confidence: float,
    steps_used: int,
    pages_checked: int,
    failure_reason: str,
) -> InvestigationResult:
    """Construct an InvestigationResult.  Delegates validation to the
    dataclass __post_init__ invariant checks.
    """
    return InvestigationResult(
        status=status,
        summary=summary,
        findings=findings,
        evidence=evidence,
        confidence=confidence,
        steps_used=steps_used,
        pages_checked=pages_checked,
        failure_reason=failure_reason,
    )


def _monotonic() -> float:
    """Wrap time.monotonic() for testability in unit tests."""
    import time

    return time.monotonic()