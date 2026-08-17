"""Deterministic investigation planning layer for Phase 11-A K.5.

Phase 11-A K.5 — Investigation Planner (Architecture Freeze APPROVED,
2026-08-17).

Responsibilities:
    * Determine the InvestigationTask (direct or via ToolProvider).
    * Match the task to exactly one compatible Tool using capabilities().
    * Validate the static max_steps policy constraint.
    * Produce an immutable InvestigationPlan containing exactly one PlanStep.

Non-responsibilities:
    * Tool execution (K.6).
    * Runtime budget accounting (K.6).
    * InvestigationResult construction (K.6).
    * Dynamic replanning (out of scope).

Dependencies:
    * K.1: InvestigationPolicy, InvestigationTask, ToolCapability, ToolProvider
    * K.4: Tool protocol

Forbidden:
    * Phase 10 modules (ai_contract, decide, final_decision, etc.)
    * Network access, LLM calls, subprocess, persistence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .investigation_contract import (
    InvestigationPolicy,
    InvestigationTask,
    ToolCapability,
    ToolProvider,
)
from .investigation_tools import Tool


# ---------------------------------------------------------------------------
# Planner-specific error
# ---------------------------------------------------------------------------


class PlannerError(ValueError):
    """Raised when a deterministic investigation plan cannot be created."""


# ---------------------------------------------------------------------------
# Plan dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanStep:
    """One planned Tool action.

    Invariants enforced in __post_init__:
        * task is a valid InvestigationTask
        * tool is a Tool instance
    """

    task: InvestigationTask
    tool: Tool

    def __post_init__(self) -> None:
        if not isinstance(self.task, InvestigationTask):
            raise TypeError(
                f"task must be InvestigationTask, got "
                f"{type(self.task).__name__}"
            )
        if not hasattr(self.tool, "capabilities") or not callable(
            getattr(self.tool, "capabilities")
        ):
            raise TypeError("tool must conform to Tool Protocol")


@dataclass(frozen=True)
class InvestigationPlan:
    """Immutable K.5 plan.

    Phase 11-A currently permits exactly one planned step.

    Invariants enforced in __post_init__:
        * steps is a tuple of PlanStep instances
        * len(steps) == 1
    """

    steps: tuple[PlanStep, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.steps, tuple):
            raise TypeError(
                f"steps must be tuple, got {type(self.steps).__name__}"
            )
        for i, step in enumerate(self.steps):
            if not isinstance(step, PlanStep):
                raise TypeError(
                    f"steps[{i}] must be PlanStep, got "
                    f"{type(step).__name__}"
                )
        if len(self.steps) != 1:
            raise PlannerError(
                "investigation plan must contain exactly one step"
            )


# ---------------------------------------------------------------------------
# Task -> Capability mapping (Architecture Freeze §6)
# ---------------------------------------------------------------------------

# Immutable mapping owned by K.5.  Each InvestigationTask maps to exactly
# one ToolCapability, guaranteeing a unique Tool match in the standard
# MockTool set.

_TASK_CAPABILITY: dict[InvestigationTask, ToolCapability] = {
    InvestigationTask.VERIFY_SOURCE: ToolCapability.WEB_FETCH,
    InvestigationTask.FETCH_RELATED_SOURCE: ToolCapability.WEB_SEARCH,
    InvestigationTask.COMPARE_WITH_HISTORY: ToolCapability.HISTORICAL_LOOKUP,
    InvestigationTask.EXTRACT_EVIDENCE: ToolCapability.PAGE_PARSE,
    InvestigationTask.CROSS_CHECK: ToolCapability.WEB_SEARCH,
}


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


class Planner:
    """Deterministic planner for Phase 11-A investigation tasks.

    Stateless.  Owns the Tool collection, InvestigationPolicy, and an
    optional ToolProvider.  Produces an InvestigationPlan with exactly
    one PlanStep per invocation.

    The Planner does NOT:
        * execute Tools
        * maintain mutable state between calls
        * enforce runtime page budgets
        * construct InvestigationResult
    """

    def __init__(
        self,
        tools: Sequence[Tool],
        policy: InvestigationPolicy,
        tool_provider: ToolProvider | None = None,
    ) -> None:
        if not isinstance(policy, InvestigationPolicy):
            raise TypeError(
                f"policy must be InvestigationPolicy, got "
                f"{type(policy).__name__}"
            )
        self._tools = tuple(tools)
        self._policy = policy
        self._tool_provider = tool_provider

    def plan(
        self,
        task: InvestigationTask | None = None,
        context: Mapping[str, str] | None = None,
    ) -> InvestigationPlan:
        """Create exactly one deterministic Tool action.

        If ``task`` is provided, it is used directly.

        If ``task`` is omitted, a configured ToolProvider is required
        and the task is obtained through ``suggest_task(context_copy)``.
        The context is defensively copied before the provider call to
        satisfy the ToolProvider immutability contract (Architecture
        Freeze §4, Architecture Review F4).

        If both ``task`` and the ToolProvider are absent, ``PlannerError``
        is raised.

        The matching Tool is selected by capability: the task maps to
        exactly one ToolCapability, and exactly one Tool in the configured
        collection must declare that capability.  Zero or more than one
        matching Tool raises ``PlannerError``.
        """

        if context is None:
            context_copy: dict[str, str] = {}
        else:
            context_copy = dict(context)

        # Determine task
        if task is None:
            if self._tool_provider is None:
                raise PlannerError(
                    "task is absent and no ToolProvider is configured"
                )
            try:
                task = self._tool_provider.suggest_task(context_copy)
            except Exception as exc:
                raise PlannerError(
                    "ToolProvider task suggestion failed"
                ) from exc

            if not isinstance(task, InvestigationTask):
                raise PlannerError(
                    "ToolProvider returned invalid task"
                )

        # Static max_steps validation (Architecture Freeze §9)
        if self._policy.max_steps < 1:
            raise PlannerError(
                "policy does not permit any investigation steps"
            )

        # Task -> Capability lookup
        capability = _TASK_CAPABILITY.get(task)
        if capability is None:
            raise PlannerError(
                f"unsupported task: {task.value}"
            )

        # Match Tools by capability
        matches = tuple(
            tool
            for tool in self._tools
            if capability in tool.capabilities()
        )

        if not matches:
            raise PlannerError(
                f"no Tool supports task: {task.value}"
            )

        if len(matches) > 1:
            raise PlannerError(
                f"ambiguous Tool selection for task: {task.value}"
            )

        return InvestigationPlan(
            steps=(PlanStep(task=task, tool=matches[0]),)
        )
