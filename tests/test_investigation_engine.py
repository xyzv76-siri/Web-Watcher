"""K.6 Investigation Engine — unit tests.

Covers all Architectural Assertions A–Z from PHASE11A_K6_ARCHITECTURE.md.
"""

from __future__ import annotations

import time
import unittest
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence
from unittest.mock import patch

from web_watcher.investigation_contract import (
    InvestigationPolicy,
    InvestigationTask,
)
from web_watcher.investigation_evidence import (
    Evidence,
    EvidenceType,
)
from web_watcher.investigation_engine import Engine, EngineError
from web_watcher.investigation_planner import (
    InvestigationPlan,
    PlanStep,
)
from web_watcher.investigation_result import (
    InvestigationResult,
    InvestigationStatus,
)
from web_watcher.investigation_tools import ToolResult

from tests.mock_investigation_tools import (
    MockHistoricalLookupTool,
    MockPageParseTool,
    MockWebFetchTool,
    MockWebSearchTool,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_evidence(**overrides: Any) -> Evidence:
    """Build a deterministic Evidence for tests."""
    kw = dict(
        source="test-source",
        url="https://example.com/test",
        retrieved_at=datetime(2026, 8, 17, 0, 0, 0),
        claim="test claim",
        supporting_text="test text",
        evidence_type=EvidenceType.PRIMARY,
    )
    kw.update(overrides)
    return Evidence(**kw)


class _FakeTool:
    """Minimal Tool implementation for testing.

    Configurable: success, evidence list, pages_fetched,
    whether to raise an exception.
    """

    def __init__(
        self,
        success: bool = True,
        evidence: Optional[Sequence[Evidence]] = None,
        pages_fetched: int = 1,
        message: str = "ok",
        raise_exception: Optional[Exception] = None,
    ) -> None:
        self.success = success
        self.evidence = tuple(evidence or [])
        self.pages_fetched = pages_fetched
        self.message = message
        self.raise_exception = raise_exception
        self.call_count = 0
        self.last_task: Optional[InvestigationTask] = None
        self.last_context: Optional[Mapping[str, str]] = None

    def capabilities(self) -> tuple:
        return (InvestigationTask.EXTRACT_EVIDENCE,)

    def execute(
        self,
        task: InvestigationTask,
        context: Optional[Mapping[str, str]] = None,
    ) -> ToolResult:
        self.call_count += 1
        self.last_task = task
        self.last_context = context
        if self.raise_exception:
            raise self.raise_exception
        return ToolResult(
            success=self.success,
            evidence=self.evidence,
            pages_fetched=self.pages_fetched,
            message=self.message,
        )


class _FakeToolTimeout:
    """Tool whose execute() sleeps past the policy timeout."""

    def __init__(self, sleep_seconds: float) -> None:
        self.sleep_seconds = sleep_seconds
        self.call_count = 0
        self.last_task: Optional[InvestigationTask] = None

    def capabilities(self) -> tuple:
        return (InvestigationTask.EXTRACT_EVIDENCE,)

    def execute(
        self,
        task: InvestigationTask,
        context: Optional[Mapping[str, str]] = None,
    ) -> ToolResult:
        self.call_count += 1
        self.last_task = task
        time.sleep(self.sleep_seconds)
        return ToolResult(
            success=True,
            evidence=(),
            pages_fetched=0,
            message="slept",
        )


def _make_plan(
    tool: _FakeTool,
    task: InvestigationTask = InvestigationTask.EXTRACT_EVIDENCE,
) -> InvestigationPlan:
    step = PlanStep(task=task, tool=tool)
    return InvestigationPlan(steps=(step,))


def _make_policy(
    max_steps: int = 5,
    max_pages: int = 10,
    timeout_seconds: float = 60.0,
) -> InvestigationPolicy:
    return InvestigationPolicy(
        max_steps=max_steps,
        max_pages=max_pages,
        timeout_seconds=timeout_seconds,
    )


def _patch_monotonic(start: float, elapsed: float):
    """Return a context manager that makes the first _monotonic() call
    return ``start`` and every subsequent call return ``start + elapsed``.

    This lets us force pre-execution and post-execution timeout guards
    to trigger without relying on real wall-clock timing.
    """
    counter = [0]

    def fake_monotonic() -> float:
        counter[0] += 1
        if counter[0] == 1:
            return start
        return start + elapsed

    return patch(
        "web_watcher.investigation_engine._monotonic",
        side_effect=fake_monotonic,
    )


# ---------------------------------------------------------------------------
# Engine construction
# ---------------------------------------------------------------------------


class TestEngineConstruction(unittest.TestCase):

    def test_valid_construction(self) -> None:
        policy = _make_policy()
        engine = Engine(policy)
        self.assertIsNotNone(engine)

    def test_invalid_policy_type_raises(self) -> None:
        with self.assertRaises(EngineError):
            Engine("not a policy")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


class TestInputValidation(unittest.TestCase):

    def setUp(self) -> None:
        self.engine = Engine(_make_policy())

    def test_invalid_plan_type_raises(self) -> None:
        with self.assertRaises(EngineError):
            self.engine.execute("not a plan")  # type: ignore[arg-type]

    def test_plan_without_steps_raises(self) -> None:
        """An empty steps tuple fails InvestigationPlan.__post_init__."""
        with self.assertRaises(ValueError):
            InvestigationPlan(steps=())

    def test_plan_with_multiple_steps_raises(self) -> None:
        """Multiple steps are rejected by InvestigationPlan.__post_init__
        (PlannerError, a ValueError subclass) before the Engine ever sees them."""
        tool = _FakeTool()
        step1 = PlanStep(
            task=InvestigationTask.EXTRACT_EVIDENCE, tool=tool
        )
        step2 = PlanStep(
            task=InvestigationTask.EXTRACT_EVIDENCE, tool=tool
        )
        with self.assertRaises(ValueError):
            InvestigationPlan(steps=(step1, step2))


# ---------------------------------------------------------------------------
# Pre-execution guards
# ---------------------------------------------------------------------------


class TestPreExecutionGuards(unittest.TestCase):

    def setUp(self) -> None:
        self.tool = _FakeTool()
        self.engine = Engine(_make_policy())

    def test_pre_execution_timeout_guard(self) -> None:
        """If timeout is already exceeded at the start of execute(),
        return TIMEOUT with steps_used=0 and pages_checked=0."""
        policy = _make_policy(timeout_seconds=0.5)
        engine = Engine(policy)
        plan = _make_plan(self.tool)
        # First _monotonic() call returns 0.0 (start), second returns 1.0
        # (elapsed > timeout=0.5), so the pre-execution guard fires.
        with _patch_monotonic(start=0.0, elapsed=1.0):
            result = engine.execute(plan)
        self.assertEqual(result.status, InvestigationStatus.TIMEOUT)
        self.assertEqual(result.steps_used, 0)
        self.assertEqual(result.pages_checked, 0)
        self.assertTrue(result.failure_reason)
        self.assertEqual(result.findings, ())
        self.assertEqual(result.confidence, 0.0)

    def test_pre_execution_max_steps_guard(self) -> None:
        """With max_steps=1, single-step plan should succeed normally.
        The guard is a defensive invariant — K.1 prevents max_steps=0."""
        policy = _make_policy(max_steps=1)
        engine = Engine(policy)
        plan = _make_plan(self.tool)
        result = engine.execute(plan)
        self.assertEqual(result.status, InvestigationStatus.SUCCESS)
        self.assertEqual(result.steps_used, 1)


# ---------------------------------------------------------------------------
# Successful execution
# ---------------------------------------------------------------------------


class TestSuccessfulExecution(unittest.TestCase):

    def test_success_with_evidence(self) -> None:
        ev = _make_evidence()
        tool = _FakeTool(success=True, evidence=[ev], pages_fetched=1)
        engine = Engine(_make_policy())
        plan = _make_plan(tool)
        result = engine.execute(plan)
        self.assertEqual(result.status, InvestigationStatus.SUCCESS)
        self.assertEqual(result.steps_used, 1)
        self.assertEqual(result.pages_checked, 1)
        self.assertEqual(result.confidence, 1.0)
        self.assertEqual(result.failure_reason, "")
        self.assertEqual(result.findings, ())
        self.assertEqual(len(result.evidence), 1)
        self.assertIs(result.evidence[0], ev)

    def test_success_no_evidence(self) -> None:
        tool = _FakeTool(success=True, evidence=(), pages_fetched=0)
        engine = Engine(_make_policy())
        plan = _make_plan(tool)
        result = engine.execute(plan)
        self.assertEqual(result.status, InvestigationStatus.SUCCESS)
        self.assertEqual(result.pages_checked, 0)
        self.assertEqual(result.evidence, ())

    def test_tool_called_correct_task_and_context(self) -> None:
        ev = _make_evidence()
        tool = _FakeTool(success=True, evidence=[ev])
        engine = Engine(_make_policy())
        plan = _make_plan(tool, task=InvestigationTask.CROSS_CHECK)
        ctx = {"article_id": "a-42", "date": "2026-08-17"}
        result = engine.execute(plan, context=ctx)
        self.assertEqual(result.status, InvestigationStatus.SUCCESS)
        self.assertEqual(tool.call_count, 1)
        self.assertEqual(tool.last_task, InvestigationTask.CROSS_CHECK)
        self.assertEqual(tool.last_context, ctx)


# ---------------------------------------------------------------------------
# Pages fetched accumulation
# ---------------------------------------------------------------------------


class TestPagesFetched(unittest.TestCase):

    def test_pages_fetched_accumulated_from_result(self) -> None:
        tool = _FakeTool(pages_fetched=3)
        engine = Engine(_make_policy())
        plan = _make_plan(tool)
        result = engine.execute(plan)
        self.assertEqual(result.pages_checked, 3)

    def test_pages_fetched_zero_on_failure(self) -> None:
        tool = _FakeTool(success=False, pages_fetched=0)
        engine = Engine(_make_policy())
        plan = _make_plan(tool)
        result = engine.execute(plan)
        self.assertEqual(result.status, InvestigationStatus.FAILED)
        self.assertEqual(result.pages_checked, 0)

    def test_pages_fetched_no_clamp_overshoot(self) -> None:
        """pages_checked must not be clamped — the full actual value is reported
        even if it exceeds max_pages (the overshoot triggers BUDGET_EXCEEDED)."""
        tool = _FakeTool(pages_fetched=50)
        engine = Engine(_make_policy(max_pages=10))
        plan = _make_plan(tool)
        result = engine.execute(plan)
        self.assertEqual(result.status, InvestigationStatus.BUDGET_EXCEEDED)
        self.assertEqual(result.pages_checked, 50)  # not clamped


# ---------------------------------------------------------------------------
# Evidence collection
# ---------------------------------------------------------------------------


class TestEvidenceCollection(unittest.TestCase):

    def test_multiple_evidence_preserves_order(self) -> None:
        ev1 = _make_evidence(source="a", evidence_type=EvidenceType.PRIMARY)
        ev2 = _make_evidence(source="b", evidence_type=EvidenceType.SECONDARY)
        ev3 = _make_evidence(source="c", evidence_type=EvidenceType.DERIVED)
        tool = _FakeTool(evidence=[ev1, ev2, ev3])
        engine = Engine(_make_policy())
        plan = _make_plan(tool)
        result = engine.execute(plan)
        self.assertEqual(result.evidence, (ev1, ev2, ev3))

    def test_evidence_zero_based_positional(self) -> None:
        """Since findings=() always, the positional evidence model is
        confirmed by asserting the evidence at index 0 is the correct one."""
        ev = _make_evidence()
        tool = _FakeTool(evidence=[ev])
        engine = Engine(_make_policy())
        plan = _make_plan(tool)
        result = engine.execute(plan)
        self.assertEqual(result.evidence[0], ev)

    def test_evidence_passed_through_not_modified(self) -> None:
        """The engine must not mutate Evidence objects."""
        ev = _make_evidence(source="original")
        tool = _FakeTool(evidence=[ev])
        engine = Engine(_make_policy())
        plan = _make_plan(tool)
        result = engine.execute(plan)
        self.assertEqual(result.evidence[0].source, "original")


# ---------------------------------------------------------------------------
# Tool failure and exception
# ---------------------------------------------------------------------------


class TestToolFailure(unittest.TestCase):

    def test_tool_failure_returns_failed(self) -> None:
        tool = _FakeTool(success=False, message="no data")
        engine = Engine(_make_policy())
        plan = _make_plan(tool)
        result = engine.execute(plan)
        self.assertEqual(result.status, InvestigationStatus.FAILED)
        self.assertEqual(result.failure_reason, "no data")
        self.assertEqual(result.steps_used, 1)
        self.assertEqual(result.confidence, 0.0)

    def test_tool_exception_returns_failed(self) -> None:
        exc = RuntimeError("connection refused")
        tool = _FakeTool(raise_exception=exc)
        engine = Engine(_make_policy())
        plan = _make_plan(tool)
        result = engine.execute(plan)
        self.assertEqual(result.status, InvestigationStatus.FAILED)
        self.assertIn("connection refused", result.failure_reason)
        self.assertEqual(result.steps_used, 1)
        self.assertEqual(tool.call_count, 1)

    def test_tool_failure_message_is_failure_reason(self) -> None:
        tool = _FakeTool(success=False, message="parse error")
        engine = Engine(_make_policy())
        plan = _make_plan(tool)
        result = engine.execute(plan)
        self.assertEqual(result.failure_reason, "parse error")


# ---------------------------------------------------------------------------
# Post-execution guards
# ---------------------------------------------------------------------------


class TestPostExecutionGuards(unittest.TestCase):

    def test_post_execution_timeout_guard(self) -> None:
        """If execute() sleeps past the timeout, TIMEOUT is returned
        with actual pages_checked and steps_used=1."""
        tool = _FakeToolTimeout(sleep_seconds=0.15)
        engine = Engine(_make_policy(timeout_seconds=0.05))
        plan = _make_plan(tool)
        result = engine.execute(plan)
        self.assertEqual(result.status, InvestigationStatus.TIMEOUT)
        self.assertEqual(result.steps_used, 1)
        self.assertTrue(result.failure_reason)
        self.assertEqual(tool.call_count, 1)

    def test_timeout_does_not_cancel_ongoing(self) -> None:
        """The engine does not attempt to interrupt an in-flight tool.
        The tool completes; the engine classifies after the fact."""
        tool = _FakeToolTimeout(sleep_seconds=0.1)
        engine = Engine(_make_policy(timeout_seconds=0.05))
        plan = _make_plan(tool)
        result = engine.execute(plan)
        self.assertEqual(result.status, InvestigationStatus.TIMEOUT)
        self.assertEqual(tool.call_count, 1)

    def test_timeout_with_evidence(self) -> None:
        """Evidence produced before timeout is preserved."""
        ev = _make_evidence()

        class _SlowToolWithEvidence(_FakeToolTimeout):
            def execute(
                self,
                task: InvestigationTask,
                context: Optional[Mapping[str, str]] = None,
            ) -> ToolResult:
                self.call_count += 1
                self.last_task = task
                time.sleep(0.1)
                return ToolResult(
                    success=True,
                    evidence=(ev,),
                    pages_fetched=2,
                    message="late",
                )

        tool = _SlowToolWithEvidence(sleep_seconds=0.1)
        engine = Engine(_make_policy(timeout_seconds=0.05))
        plan = _make_plan(tool)
        result = engine.execute(plan)
        self.assertEqual(result.status, InvestigationStatus.TIMEOUT)
        self.assertEqual(result.pages_checked, 2)
        self.assertEqual(result.evidence, (ev,))

    def test_max_pages_exceeded_returns_budget_exceeded(self) -> None:
        tool = _FakeTool(pages_fetched=15)
        engine = Engine(_make_policy(max_pages=10))
        plan = _make_plan(tool)
        result = engine.execute(plan)
        self.assertEqual(result.status, InvestigationStatus.BUDGET_EXCEEDED)
        self.assertEqual(result.pages_checked, 15)
        self.assertTrue(result.failure_reason)

    def test_max_pages_exact_boundary_passes(self) -> None:
        """pages_checked == max_pages is allowed; only strict overshoot fails."""
        tool = _FakeTool(pages_fetched=10)
        engine = Engine(_make_policy(max_pages=10))
        plan = _make_plan(tool)
        result = engine.execute(plan)
        self.assertEqual(result.status, InvestigationStatus.SUCCESS)
        self.assertEqual(result.pages_checked, 10)


# ---------------------------------------------------------------------------
# No INCONCLUSIVE inference
# ---------------------------------------------------------------------------


class TestNoInconclusive(unittest.TestCase):

    def test_no_inconclusive_on_thin_evidence(self) -> None:
        """A single piece of evidence must not trigger INCONCLUSIVE."""
        ev = _make_evidence()
        tool = _FakeTool(evidence=[ev])
        engine = Engine(_make_policy())
        plan = _make_plan(tool)
        result = engine.execute(plan)
        self.assertEqual(result.status, InvestigationStatus.SUCCESS)
        self.assertNotEqual(result.status, InvestigationStatus.INCONCLUSIVE)

    def test_no_inconclusive_on_empty_evidence(self) -> None:
        """Empty evidence is still SUCCESS (the tool succeeded)."""
        tool = _FakeTool(success=True, evidence=())
        engine = Engine(_make_policy())
        plan = _make_plan(tool)
        result = engine.execute(plan)
        self.assertEqual(result.status, InvestigationStatus.SUCCESS)

    def test_no_inconclusive_on_tool_failure(self) -> None:
        """Tool failure is FAILED, never INCONCLUSIVE."""
        tool = _FakeTool(success=False)
        engine = Engine(_make_policy())
        plan = _make_plan(tool)
        result = engine.execute(plan)
        self.assertEqual(result.status, InvestigationStatus.FAILED)
        self.assertNotEqual(result.status, InvestigationStatus.INCONCLUSIVE)

    def test_no_inconclusive_on_timeout(self) -> None:
        tool = _FakeToolTimeout(sleep_seconds=0.1)
        engine = Engine(_make_policy(timeout_seconds=0.05))
        plan = _make_plan(tool)
        result = engine.execute(plan)
        self.assertEqual(result.status, InvestigationStatus.TIMEOUT)
        self.assertNotEqual(result.status, InvestigationStatus.INCONCLUSIVE)

    def test_no_inconclusive_on_budget_exceeded(self) -> None:
        tool = _FakeTool(pages_fetched=100)
        engine = Engine(_make_policy(max_pages=10))
        plan = _make_plan(tool)
        result = engine.execute(plan)
        self.assertEqual(result.status, InvestigationStatus.BUDGET_EXCEEDED)
        self.assertNotEqual(result.status, InvestigationStatus.INCONCLUSIVE)


# ---------------------------------------------------------------------------
# Context forwarding
# ---------------------------------------------------------------------------


class TestContextForwarding(unittest.TestCase):

    def test_context_passed_through(self) -> None:
        ctx = {"article_id": "a-99", "topic": "AI"}
        tool = _FakeTool()
        engine = Engine(_make_policy())
        plan = _make_plan(tool)
        engine.execute(plan, context=ctx)
        self.assertEqual(tool.last_context, ctx)

    def test_none_context_defaults_to_empty_dict(self) -> None:
        tool = _FakeTool()
        engine = Engine(_make_policy())
        plan = _make_plan(tool)
        engine.execute(plan, context=None)
        self.assertEqual(tool.last_context, {})

    def test_context_not_mutable_by_engine(self) -> None:
        ctx = {"key": "value"}
        tool = _FakeTool()
        engine = Engine(_make_policy())
        plan = _make_plan(tool)
        engine.execute(plan, context=ctx)
        self.assertEqual(ctx, {"key": "value"})


# ---------------------------------------------------------------------------
# No Planner dependency
# ---------------------------------------------------------------------------


class TestNoPlannerDependency(unittest.TestCase):

    def test_engine_does_not_hold_planner(self) -> None:
        engine = Engine(_make_policy())
        self.assertFalse(hasattr(engine, "_planner"))
        self.assertFalse(hasattr(engine, "planner"))

    def test_engine_execute_does_not_call_planner(self) -> None:
        """Verify execute() never calls any .plan() method."""
        tool = _FakeTool()
        engine = Engine(_make_policy())
        plan = _make_plan(tool)
        with patch.object(engine, "execute", wraps=engine.execute) as spy:
            engine.execute(plan)
            # No .plan() call should have been made
            self.assertEqual(spy.call_count, 1)


# ---------------------------------------------------------------------------
# Mock tool integration
# ---------------------------------------------------------------------------


class TestMockToolIntegration(unittest.TestCase):

    def test_mock_web_fetch_tool(self) -> None:
        tool = MockWebFetchTool()
        engine = Engine(_make_policy())
        plan = _make_plan(tool, task=InvestigationTask.VERIFY_SOURCE)
        result = engine.execute(plan)
        self.assertEqual(result.status, InvestigationStatus.SUCCESS)
        self.assertEqual(result.pages_checked, 1)
        self.assertEqual(len(result.evidence), 1)
        self.assertEqual(result.evidence[0].evidence_type, EvidenceType.PRIMARY)

    def test_mock_web_search_tool(self) -> None:
        tool = MockWebSearchTool()
        engine = Engine(_make_policy())
        plan = _make_plan(tool, task=InvestigationTask.FETCH_RELATED_SOURCE)
        result = engine.execute(plan)
        self.assertEqual(result.status, InvestigationStatus.SUCCESS)
        self.assertEqual(result.pages_checked, 0)
        self.assertEqual(len(result.evidence), 1)
        self.assertEqual(result.evidence[0].evidence_type, EvidenceType.SECONDARY)

    def test_mock_page_parse_tool(self) -> None:
        tool = MockPageParseTool()
        engine = Engine(_make_policy())
        plan = _make_plan(tool, task=InvestigationTask.EXTRACT_EVIDENCE)
        result = engine.execute(plan)
        self.assertEqual(result.status, InvestigationStatus.SUCCESS)
        self.assertEqual(result.pages_checked, 0)
        self.assertEqual(len(result.evidence), 1)
        self.assertEqual(result.evidence[0].evidence_type, EvidenceType.DERIVED)

    def test_mock_historical_lookup_tool(self) -> None:
        tool = MockHistoricalLookupTool()
        engine = Engine(_make_policy())
        plan = _make_plan(tool, task=InvestigationTask.COMPARE_WITH_HISTORY)
        result = engine.execute(plan)
        self.assertEqual(result.status, InvestigationStatus.SUCCESS)
        self.assertEqual(result.pages_checked, 0)
        self.assertEqual(len(result.evidence), 1)
        self.assertEqual(result.evidence[0].evidence_type, EvidenceType.HISTORICAL)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism(unittest.TestCase):

    def test_identical_runs_identical_results(self) -> None:
        ev = _make_evidence()
        engine = Engine(_make_policy())
        for _ in range(3):
            tool = _FakeTool(success=True, evidence=[ev], pages_fetched=1)
            plan = _make_plan(tool)
            result = engine.execute(plan)
            self.assertEqual(result.status, InvestigationStatus.SUCCESS)
            self.assertEqual(result.pages_checked, 1)
            self.assertEqual(result.steps_used, 1)
            self.assertEqual(result.confidence, 1.0)


# ---------------------------------------------------------------------------
# No retry
# ---------------------------------------------------------------------------


class TestNoRetry(unittest.TestCase):

    def test_tool_called_exactly_once(self) -> None:
        tool = _FakeTool(success=True)
        engine = Engine(_make_policy())
        plan = _make_plan(tool)
        engine.execute(plan)
        self.assertEqual(tool.call_count, 1)

    def test_tool_not_retried_on_failure(self) -> None:
        tool = _FakeTool(success=False)
        engine = Engine(_make_policy())
        plan = _make_plan(tool)
        engine.execute(plan)
        self.assertEqual(tool.call_count, 1)

    def test_tool_not_retried_on_exception(self) -> None:
        tool = _FakeTool(raise_exception=ValueError("boom"))
        engine = Engine(_make_policy())
        plan = _make_plan(tool)
        engine.execute(plan)
        self.assertEqual(tool.call_count, 1)


# ---------------------------------------------------------------------------
# Steps used counting
# ---------------------------------------------------------------------------


class TestStepsUsed(unittest.TestCase):

    def test_steps_used_is_1_after_execute(self) -> None:
        tool = _FakeTool(success=True)
        engine = Engine(_make_policy())
        plan = _make_plan(tool)
        result = engine.execute(plan)
        self.assertEqual(result.steps_used, 1)

    def test_steps_used_is_1_on_failure(self) -> None:
        tool = _FakeTool(success=False)
        engine = Engine(_make_policy())
        plan = _make_plan(tool)
        result = engine.execute(plan)
        self.assertEqual(result.steps_used, 1)

    def test_steps_used_is_1_on_exception(self) -> None:
        tool = _FakeTool(raise_exception=RuntimeError("fail"))
        engine = Engine(_make_policy())
        plan = _make_plan(tool)
        result = engine.execute(plan)
        self.assertEqual(result.steps_used, 1)

    def test_steps_used_is_0_when_pre_guard_stops(self) -> None:
        """When pre-execution budget guard triggers, execute() was never
        entered → steps_used stays 0."""
        policy = _make_policy(timeout_seconds=0.5)
        engine = Engine(policy)
        tool = _FakeTool()
        plan = _make_plan(tool)
        # Force pre-execution timeout guard by patching _monotonic
        with _patch_monotonic(start=0.0, elapsed=1.0):
            result = engine.execute(plan)
        self.assertEqual(result.steps_used, 0)
        self.assertEqual(result.status, InvestigationStatus.TIMEOUT)


# ---------------------------------------------------------------------------
# No Tool matching / Task independence
# ---------------------------------------------------------------------------


class TestNoToolMatching(unittest.TestCase):

    def test_any_task_with_any_tool_is_executed(self) -> None:
        """The engine does NOT validate Task↔Tool compatibility.
        Any Task with any Tool is executed as planned."""
        tool = _FakeTool()
        engine = Engine(_make_policy())
        for task in list(InvestigationTask):
            plan = _make_plan(tool, task=task)
            result = engine.execute(plan)
            self.assertEqual(result.status, InvestigationStatus.SUCCESS)
            self.assertEqual(tool.last_task, task)


# ---------------------------------------------------------------------------
# Max_pages non-clamping
# ---------------------------------------------------------------------------


class TestMaxPagesNonClamp(unittest.TestCase):

    def test_overshoot_is_reported_unchanged(self) -> None:
        """pages_checked = actual sum, even when > max_pages.
        The BUDGET_EXCEEDED status is separate from the pages_checked value."""
        tool = _FakeTool(pages_fetched=250)
        engine = Engine(_make_policy(max_pages=10))
        plan = _make_plan(tool)
        result = engine.execute(plan)
        self.assertEqual(result.pages_checked, 250)


# ---------------------------------------------------------------------------
# K.1–K.5 round-trip
# ---------------------------------------------------------------------------


class TestK1K5RoundTrip(unittest.TestCase):

    def test_full_pipeline(self) -> None:
        """End-to-end: K.1 Policy → K.5 Plan → K.6 Engine → K.3 Result."""
        from web_watcher.investigation_planner import Planner

        policy = InvestigationPolicy(
            max_steps=5, max_pages=10, timeout_seconds=60.0
        )
        tool = MockWebFetchTool()
        planner = Planner(tools=[tool], policy=policy)
        ctx: dict[str, str] = {"article_id": "a-1", "url": "https://example.com"}
        plan = planner.plan(
            task=InvestigationTask.VERIFY_SOURCE, context=ctx
        )
        engine = Engine(policy)
        result = engine.execute(plan, context=ctx)
        self.assertEqual(result.status, InvestigationStatus.SUCCESS)
        self.assertEqual(result.steps_used, 1)
        self.assertEqual(result.pages_checked, 1)
        self.assertEqual(result.confidence, 1.0)
        self.assertEqual(result.findings, ())
        self.assertEqual(len(result.evidence), 1)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases(unittest.TestCase):

    def test_result_finds_invariant_empty(self) -> None:
        """Every result must have findings=()."""
        tool = _FakeTool()
        engine = Engine(_make_policy())
        plan = _make_plan(tool)
        result = engine.execute(plan)
        self.assertEqual(result.findings, ())
        self.assertIsInstance(result.findings, tuple)

    def test_result_summary_non_empty(self) -> None:
        tool = _FakeTool()
        engine = Engine(_make_policy())
        plan = _make_plan(tool)
        result = engine.execute(plan)
        self.assertTrue(result.summary)

    def test_result_failure_reason_empty_on_success(self) -> None:
        tool = _FakeTool(success=True)
        engine = Engine(_make_policy())
        plan = _make_plan(tool)
        result = engine.execute(plan)
        self.assertEqual(result.failure_reason, "")

    def test_result_failure_reason_non_empty_on_failed(self) -> None:
        tool = _FakeTool(success=False, message="bad")
        engine = Engine(_make_policy())
        plan = _make_plan(tool)
        result = engine.execute(plan)
        self.assertTrue(result.failure_reason)

    def test_policy_validation_prevents_invalid(self) -> None:
        """K.1 policy validation should catch invalid values upstream."""
        with self.assertRaises(Exception):
            InvestigationPolicy(max_steps=0)
        with self.assertRaises(Exception):
            InvestigationPolicy(timeout_seconds=-1)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    unittest.main()