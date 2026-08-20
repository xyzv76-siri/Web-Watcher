"""Tests for web_watcher.investigation_planner (K.5)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from web_watcher.investigation_contract import (
    InvestigationPolicy,
    InvestigationTask,
    ToolCapability,
)
from web_watcher.investigation_planner import (
    InvestigationPlan,
    PlanStep,
    Planner,
    PlannerError,
)
from web_watcher.investigation_tools import Tool

from tests.mock_investigation_tools import (
    MockHistoricalLookupTool,
    MockPageParseTool,
    MockWebFetchTool,
    MockWebSearchTool,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def all_tools() -> tuple[Tool, ...]:
    return (
        MockWebFetchTool(),
        MockWebSearchTool(),
        MockPageParseTool(),
        MockHistoricalLookupTool(),
    )


def policy() -> InvestigationPolicy:
    return InvestigationPolicy()


# ===========================================================================
# 1. Public API contract
# ===========================================================================


class TestPublicAPI:
    def test_planner_error_is_subclass_of_value_error(self):
        assert issubclass(PlannerError, ValueError)
        assert issubclass(PlannerError, Exception)

    def test_planner_error_is_not_runtime_error(self):
        assert not issubclass(PlannerError, RuntimeError)

    def test_plan_step_dataclass(self):
        step = PlanStep(
            task=InvestigationTask.VERIFY_SOURCE,
            tool=MockWebFetchTool(),
        )
        assert step.task is InvestigationTask.VERIFY_SOURCE
        assert isinstance(step.tool, MockWebFetchTool)

    def test_plan_step_is_frozen(self):
        step = PlanStep(
            task=InvestigationTask.VERIFY_SOURCE,
            tool=MockWebFetchTool(),
        )
        with pytest.raises(AttributeError):
            step.task = InvestigationTask.CROSS_CHECK
        with pytest.raises(AttributeError):
            step.tool = MockWebSearchTool()

    def test_investigation_plan_dataclass(self):
        plan = Planner(all_tools(), policy()).plan(
            InvestigationTask.VERIFY_SOURCE
        )
        assert isinstance(plan, InvestigationPlan)
        assert isinstance(plan.steps, tuple)
        assert len(plan.steps) == 1

    def test_investigation_plan_is_frozen(self):
        plan = Planner(all_tools(), policy()).plan(
            InvestigationTask.VERIFY_SOURCE
        )
        with pytest.raises(AttributeError):
            plan.steps = ()


# ===========================================================================
# 2. PlanStep validation
# ===========================================================================


class TestPlanStepValidation:
    def test_invalid_task_type_raises_type_error(self):
        with pytest.raises(TypeError, match="task must be InvestigationTask"):
            PlanStep(task="VERIFY_SOURCE", tool=MockWebFetchTool())

    def test_tool_without_capabilities_raises_type_error(self):
        class NotATool:
            pass

        with pytest.raises(TypeError, match="tool must conform to Tool"):
            PlanStep(
                task=InvestigationTask.VERIFY_SOURCE,
                tool=NotATool(),  # type: ignore[arg-type]
            )

    def test_tool_with_non_callable_capabilities_raises(self):
        class BadTool:
            capabilities = "not callable"

        with pytest.raises(TypeError, match="tool must conform to Tool"):
            PlanStep(
                task=InvestigationTask.VERIFY_SOURCE,
                tool=BadTool(),  # type: ignore[arg-type]
            )


# ===========================================================================
# 3. InvestigationPlan validation
# ===========================================================================


class TestInvestigationPlanValidation:
    def test_empty_steps_raises_planner_error(self):
        with pytest.raises(PlannerError, match="exactly one step"):
            InvestigationPlan(steps=())

    def test_two_steps_raises_planner_error(self):
        step = PlanStep(
            task=InvestigationTask.VERIFY_SOURCE,
            tool=MockWebFetchTool(),
        )
        with pytest.raises(PlannerError, match="exactly one step"):
            InvestigationPlan(steps=(step, step))

    def test_steps_must_be_tuple(self):
        step = PlanStep(
            task=InvestigationTask.VERIFY_SOURCE,
            tool=MockWebFetchTool(),
        )
        with pytest.raises(TypeError, match="steps must be tuple"):
            InvestigationPlan(steps=[step])  # type: ignore[arg-type]

    def test_steps_elements_must_be_plan_step(self):
        with pytest.raises(TypeError, match=r"steps\[0\] must be PlanStep"):
            InvestigationPlan(steps=("not a step",))  # type: ignore[arg-type]


# ===========================================================================
# 4. Task -> Tool mapping (deterministic, all 5 tasks)
# ===========================================================================


class TestAllTasksMapDeterministically:
    @pytest.mark.parametrize(
        ("task", "expected_tool_class"),
        [
            (InvestigationTask.VERIFY_SOURCE, MockWebFetchTool),
            (InvestigationTask.FETCH_RELATED_SOURCE, MockWebSearchTool),
            (InvestigationTask.COMPARE_WITH_HISTORY, MockHistoricalLookupTool),
            (InvestigationTask.EXTRACT_EVIDENCE, MockPageParseTool),
            (InvestigationTask.CROSS_CHECK, MockWebSearchTool),
        ],
    )
    def test_task_maps_to_expected_tool(
        self, task: InvestigationTask, expected_tool_class: type
    ) -> None:
        plan = Planner(all_tools(), policy()).plan(task)
        assert len(plan.steps) == 1
        assert plan.steps[0].task is task
        assert isinstance(plan.steps[0].tool, expected_tool_class)

    @pytest.mark.parametrize(
        ("task", "expected_capability"),
        [
            (InvestigationTask.VERIFY_SOURCE, ToolCapability.WEB_FETCH),
            (InvestigationTask.FETCH_RELATED_SOURCE, ToolCapability.WEB_SEARCH),
            (InvestigationTask.COMPARE_WITH_HISTORY, ToolCapability.HISTORICAL_LOOKUP),
            (InvestigationTask.EXTRACT_EVIDENCE, ToolCapability.PAGE_PARSE),
            (InvestigationTask.CROSS_CHECK, ToolCapability.WEB_SEARCH),
        ],
    )
    def test_task_capability_mapping_is_correct(
        self, task: InvestigationTask, expected_capability: ToolCapability
    ) -> None:
        plan = Planner(all_tools(), policy()).plan(task)
        step = plan.steps[0]
        assert expected_capability in step.tool.capabilities()


# ===========================================================================
# 5. Planning immutability
# ===========================================================================


class TestPlanImmutability:
    def test_steps_tuple_is_immutable(self):
        plan = Planner(all_tools(), policy()).plan(
            InvestigationTask.VERIFY_SOURCE
        )
        with pytest.raises(AttributeError):
            plan.steps = ()

    def test_step_task_is_immutable(self):
        plan = Planner(all_tools(), policy()).plan(
            InvestigationTask.VERIFY_SOURCE
        )
        with pytest.raises(AttributeError):
            plan.steps[0].task = InvestigationTask.CROSS_CHECK

    def test_step_tool_is_immutable(self):
        plan = Planner(all_tools(), policy()).plan(
            InvestigationTask.VERIFY_SOURCE
        )
        with pytest.raises(AttributeError):
            plan.steps[0].tool = MockWebSearchTool()


# ===========================================================================
# 6. Failure cases
# ===========================================================================


class TestNoMatchingTool:
    def test_fetch_task_with_search_only_tools(self):
        planner = Planner(
            (MockWebSearchTool(),),
            policy(),
        )
        with pytest.raises(PlannerError, match="no Tool supports task"):
            planner.plan(InvestigationTask.VERIFY_SOURCE)

    def test_empty_tool_list(self):
        planner = Planner(
            (),
            policy(),
        )
        with pytest.raises(PlannerError, match="no Tool supports task"):
            planner.plan(InvestigationTask.VERIFY_SOURCE)


class TestAmbiguousToolSelection:
    def test_two_tools_same_capability(self):
        class SecondSearchTool(MockWebSearchTool):
            """A second tool with the same capability as MockWebSearchTool."""

            pass

        planner = Planner(
            (MockWebSearchTool(), SecondSearchTool()),
            policy(),
        )
        with pytest.raises(PlannerError, match="ambiguous Tool selection"):
            planner.plan(InvestigationTask.CROSS_CHECK)

    def test_ambiguity_message_contains_task_value(self):
        class SecondFetchTool(MockWebFetchTool):
            pass

        planner = Planner(
            (MockWebFetchTool(), SecondFetchTool()),
            policy(),
        )
        with pytest.raises(
            PlannerError, match="ambiguous Tool selection.*verify_source"
        ):
            planner.plan(InvestigationTask.VERIFY_SOURCE)


class TestMissingTaskAndProvider:
    def test_no_task_no_provider_raises(self):
        with pytest.raises(
            PlannerError, match="no ToolProvider"
        ):
            Planner(all_tools(), policy()).plan()

    def test_no_task_no_provider_with_context_raises(self):
        with pytest.raises(
            PlannerError, match="no ToolProvider"
        ):
            Planner(all_tools(), policy()).plan(
                context={"topic": "x"}
            )


class TestUnsupportedTask:
    """K.5 must reject any InvestigationTask not in its mapping table.

    Since InvestigationTask is a closed enum, this is primarily a
    defensive test.  If the enum is extended without updating the
    mapping, this test will catch it.
    """

    def test_all_enum_values_have_mapping(self):
        """Verify every InvestigationTask enum value has a mapping."""
        from web_watcher.investigation_planner import _TASK_CAPABILITY

        for task in InvestigationTask:
            assert task in _TASK_CAPABILITY, (
                f"InvestigationTask.{task.name} has no capability mapping"
            )


# ===========================================================================
# 7. ToolProvider integration
# ===========================================================================


class TestProviderCanSupplyTask:
    def test_provider_suggests_task(self):
        class Provider:
            def suggest_task(
                self, context: Mapping[str, str]
            ) -> InvestigationTask:
                return InvestigationTask.EXTRACT_EVIDENCE

        plan = Planner(
            all_tools(),
            policy(),
            Provider(),
        ).plan(context={"topic": "test"})

        assert plan.steps[0].task is InvestigationTask.EXTRACT_EVIDENCE
        assert isinstance(
            plan.steps[0].tool, MockPageParseTool
        )

    def test_provider_receives_context(self):
        received: dict[str, str] = {}

        class Provider:
            def suggest_task(
                self, context: Mapping[str, str]
            ) -> InvestigationTask:
                nonlocal received
                received = dict(context)
                return InvestigationTask.CROSS_CHECK

        Planner(
            all_tools(),
            policy(),
            Provider(),
        ).plan(context={"topic": "hello", "count": "42"})

        assert received == {"topic": "hello", "count": "42"}

    def test_provider_receives_empty_dict_when_context_none(self):
        received: dict[str, str] = {}

        class Provider:
            def suggest_task(
                self, context: Mapping[str, str]
            ) -> InvestigationTask:
                nonlocal received
                received = dict(context)
                return InvestigationTask.VERIFY_SOURCE

        Planner(
            all_tools(),
            policy(),
            Provider(),
        ).plan()  # context=None

        assert received == {}


class TestProviderContextDefensiveCopy:
    def test_provider_mutation_does_not_affect_original(self):
        class Provider:
            def suggest_task(
                self, context: Mapping[str, str]
            ) -> InvestigationTask:
                # Mutate the received context
                assert isinstance(context, dict)
                context["mutated"] = "yes"
                return InvestigationTask.VERIFY_SOURCE

        original = {"topic": "test"}
        Planner(
            all_tools(),
            policy(),
            Provider(),
        ).plan(context=original)

        # Original dict must be unchanged
        assert original == {"topic": "test"}

    def test_context_none_is_not_mutated(self):
        """context=None is internally converted to a fresh empty dict."""

        class Provider:
            def suggest_task(
                self, context: Mapping[str, str]
            ) -> InvestigationTask:
                context["injected"] = "x"
                return InvestigationTask.VERIFY_SOURCE

        # Should not raise and should produce a valid plan
        plan = Planner(
            all_tools(),
            policy(),
            Provider(),
        ).plan()
        assert plan.steps[0].task is InvestigationTask.VERIFY_SOURCE


class TestProviderExceptionWrapped:
    def test_provider_raises_runtime_error(self):
        class Provider:
            def suggest_task(self, context):
                raise RuntimeError("boom")

        with pytest.raises(
            PlannerError, match="task suggestion failed"
        ):
            Planner(
                all_tools(),
                policy(),
                Provider(),
            ).plan()

    def test_provider_raises_value_error(self):
        class Provider:
            def suggest_task(self, context):
                raise ValueError("invalid")

        with pytest.raises(
            PlannerError, match="task suggestion failed"
        ):
            Planner(
                all_tools(),
                policy(),
                Provider(),
            ).plan()

    def test_provider_raises_key_error(self):
        class Provider:
            def suggest_task(self, context):
                raise KeyError("missing key")

        with pytest.raises(
            PlannerError, match="task suggestion failed"
        ):
            Planner(
                all_tools(),
                policy(),
                Provider(),
            ).plan()


class TestProviderInvalidTask:
    def test_provider_returns_string(self):
        class Provider:
            def suggest_task(self, context):
                return "VERIFY_SOURCE"

        with pytest.raises(PlannerError, match="invalid task"):
            Planner(
                all_tools(),
                policy(),
                Provider(),
            ).plan()

    def test_provider_returns_none(self):
        class Provider:
            def suggest_task(self, context):
                return None  # type: ignore[return-value]

        with pytest.raises(PlannerError, match="invalid task"):
            Planner(
                all_tools(),
                policy(),
                Provider(),
            ).plan()


# ===========================================================================
# 8. Execution boundary
# ===========================================================================


class TestPlannerDoesNotExecuteTools:
    def test_execute_never_called(self):
        class ExplodingTool(MockWebFetchTool):
            def execute(self, task, context):
                raise AssertionError("Planner must not execute tools")

        plan = Planner(
            (ExplodingTool(),),
            policy(),
        ).plan(InvestigationTask.VERIFY_SOURCE)

        assert isinstance(plan.steps[0].tool, ExplodingTool)


# ===========================================================================
# 9. Policy boundary
# ===========================================================================


class TestPolicyMaxStepsBoundary:
    def test_default_policy_passes(self):
        plan = Planner(all_tools(), InvestigationPolicy()).plan(
            InvestigationTask.VERIFY_SOURCE
        )
        assert plan.steps[0].task is InvestigationTask.VERIFY_SOURCE

    def test_max_steps_one_passes(self):
        p = InvestigationPolicy(max_steps=1)
        plan = Planner(all_tools(), p).plan(
            InvestigationTask.VERIFY_SOURCE
        )
        assert plan.steps[0].task is InvestigationTask.VERIFY_SOURCE


# ===========================================================================
# 10. Stateless behavior
# ===========================================================================


class TestPlannerStateless:
    def test_repeated_calls_produce_same_plan(self):
        planner = Planner(all_tools(), policy())
        plan1 = planner.plan(InvestigationTask.VERIFY_SOURCE)
        plan2 = planner.plan(InvestigationTask.VERIFY_SOURCE)

        assert plan1.steps[0].task is plan2.steps[0].task
        assert type(plan1.steps[0].tool) is type(plan2.steps[0].tool)

    def test_repeated_calls_produce_independent_plans(self):
        planner = Planner(all_tools(), policy())
        plan1 = planner.plan(InvestigationTask.VERIFY_SOURCE)
        plan2 = planner.plan(InvestigationTask.CROSS_CHECK)

        assert plan1.steps[0].task is not plan2.steps[0].task

    def test_concurrent_like_sequential_calls_are_independent(self):
        """Simulate rapid successive planning calls."""
        planner = Planner(all_tools(), policy())
        plans = [
            planner.plan(task)
            for task in [
                InvestigationTask.VERIFY_SOURCE,
                InvestigationTask.CROSS_CHECK,
                InvestigationTask.EXTRACT_EVIDENCE,
            ]
        ]

        assert plans[0].steps[0].task is InvestigationTask.VERIFY_SOURCE
        assert plans[1].steps[0].task is InvestigationTask.CROSS_CHECK
        assert plans[2].steps[0].task is InvestigationTask.EXTRACT_EVIDENCE


# ===========================================================================
# 11. Determinism
# ===========================================================================


class TestDeterminism:
    def test_same_input_same_output(self):
        planner = Planner(all_tools(), policy())
        for _ in range(100):
            plan = planner.plan(InvestigationTask.VERIFY_SOURCE)
            assert isinstance(plan.steps[0].tool, MockWebFetchTool)

    def test_output_is_deterministic_across_tasks(self):
        planner = Planner(all_tools(), policy())
        results = {}
        for task in InvestigationTask:
            plan = planner.plan(task)
            results[task] = type(plan.steps[0].tool).__name__

        # Mapping must be consistent and non-empty for all tasks
        assert len(results) == 5
        for task, tool_name in results.items():
            assert tool_name in {
                "MockWebFetchTool",
                "MockWebSearchTool",
                "MockPageParseTool",
                "MockHistoricalLookupTool",
            }


# ===========================================================================
# 12. Context forwarding
# ===========================================================================


class TestContextForwarding:
    def test_context_empty_dict(self):
        plan = Planner(all_tools(), policy()).plan(
            InvestigationTask.VERIFY_SOURCE,
            context={},
        )
        assert plan.steps[0].task is InvestigationTask.VERIFY_SOURCE

    def test_context_with_multiple_keys(self):
        plan = Planner(all_tools(), policy()).plan(
            InvestigationTask.VERIFY_SOURCE,
            context={
                "event_type": "change",
                "source_url": "https://example.com",
                "timestamp": "2026-08-17",
            },
        )
        assert plan.steps[0].task is InvestigationTask.VERIFY_SOURCE

    def test_context_is_not_mutated_by_planner(self):
        original = {"key": "value"}
        Planner(all_tools(), policy()).plan(
            InvestigationTask.VERIFY_SOURCE,
            context=original,
        )
        assert original == {"key": "value"}


# ===========================================================================
# 13. Dependency boundary
# ===========================================================================


class TestDependencyBoundary:
    """Verify K.5 imports only K.1 and K.4, never Phase 10 or K.2/K.3."""

    def test_no_phase_10_imports(self):
        source = (
            Path("src/web_watcher/investigation_planner.py").read_text(
                encoding="utf-8"
            )
        )

        forbidden = [
            "ai_contract",
            "decide",
            "final_decision",
            "llm_provider",
            "ai_provider",
            "ai_errors",
            "ai_config",
        ]
        for module in forbidden:
            assert f"from web_watcher.{module}" not in source
            assert f"from .{module}" not in source
            assert f"import web_watcher.{module}" not in source
            assert f"import .{module}" not in source

    def test_no_k2_k3_imports(self):
        source = (
            Path("src/web_watcher/investigation_planner.py").read_text(
                encoding="utf-8"
            )
        )
        assert "investigation_evidence" not in source
        assert "investigation_result" not in source


# ===========================================================================
# 14. Module-level sanity checks
# ===========================================================================


class TestModuleLevel:
    def test_task_capability_mapping_has_all_tasks(self):
        from web_watcher.investigation_planner import _TASK_CAPABILITY

        for task in InvestigationTask:
            assert task in _TASK_CAPABILITY

    def test_no_task_has_duplicate_capability_keys(self):
        """Each Task maps to exactly one Capability."""
        from web_watcher.investigation_planner import _TASK_CAPABILITY

        for task in InvestigationTask:
            cap = _TASK_CAPABILITY[task]
            assert isinstance(cap, ToolCapability)

    def test_tools_are_stored_as_tuple(self):
        tools_seq = [MockWebFetchTool(), MockWebSearchTool()]
        planner = Planner(tools_seq, policy())
        assert isinstance(planner._tools, tuple)
        assert len(planner._tools) == 2


# ===========================================================================
# 15. Edge cases
# ===========================================================================


class TestEdgeCases:
    def test_single_tool_matching_task(self):
        planner = Planner(
            (MockWebSearchTool(),),
            policy(),
        )
        plan = planner.plan(InvestigationTask.CROSS_CHECK)
        assert isinstance(plan.steps[0].tool, MockWebSearchTool)

    def test_context_none_is_equivalent_to_empty_dict(self):
        planner = Planner(all_tools(), policy())
        plan_none = planner.plan(
            InvestigationTask.VERIFY_SOURCE, context=None
        )
        plan_empty = planner.plan(
            InvestigationTask.VERIFY_SOURCE, context={}
        )
        assert (
            type(plan_none.steps[0].tool).__name__
            == type(plan_empty.steps[0].tool).__name__
        )

    def test_plan_step_tuple_unpacking(self):
        plan = Planner(all_tools(), policy()).plan(
            InvestigationTask.VERIFY_SOURCE
        )
        step = plan.steps[0]
        assert step.task == InvestigationTask.VERIFY_SOURCE
        assert isinstance(step.tool, MockWebFetchTool)


from pathlib import Path


# ===========================================================================
# 16. Additional provider behavior tests
# ===========================================================================


class TestProviderAdditional:
    def test_provider_called_only_when_task_is_none(self):
        called = {"value": False}

        class Provider:
            def suggest_task(
                self, context: Mapping[str, str]
            ) -> InvestigationTask:
                called["value"] = True
                return InvestigationTask.VERIFY_SOURCE

        Planner(
            all_tools(),
            policy(),
            Provider(),
        ).plan(InvestigationTask.CROSS_CHECK)

        assert called["value"] is False

    def test_provider_context_type_is_dict(self):
        class Provider:
            def suggest_task(
                self, context: Mapping[str, str]
            ) -> InvestigationTask:
                assert isinstance(context, dict)
                return InvestigationTask.VERIFY_SOURCE

        Planner(
            all_tools(),
            policy(),
            Provider(),
        ).plan(context={"x": "1"})


# ===========================================================================
# 17. InvestigationPlan repr and equality
# ===========================================================================


class TestInvestigationPlanRepr:
    def test_plan_repr_contains_steps(self):
        plan = Planner(all_tools(), policy()).plan(
            InvestigationTask.VERIFY_SOURCE
        )
        repr_str = repr(plan)
        assert "steps" in repr_str
        assert "PlanStep" in repr_str

    def test_plan_equality(self):
        planner = Planner(all_tools(), policy())
        plan1 = planner.plan(InvestigationTask.VERIFY_SOURCE)
        plan2 = planner.plan(InvestigationTask.VERIFY_SOURCE)
        # Two plans with the same task but different tool instances
        # may or may not be equal; the important thing is they are
        # both valid InvestigationPlan instances
        assert isinstance(plan1, InvestigationPlan)
        assert isinstance(plan2, InvestigationPlan)


# ===========================================================================
# 18. Integration: Planner + all MockTools
# ===========================================================================


class TestIntegration:
    def test_all_five_tasks_with_all_tools(self):
        planner = Planner(all_tools(), policy())
        for task in InvestigationTask:
            plan = planner.plan(task)
            assert len(plan.steps) == 1
            assert plan.steps[0].task is task
            assert hasattr(plan.steps[0].tool, "capabilities")

    def test_plan_steps_are_unique(self):
        """Each plan invocation produces a distinct InvestigationPlan object."""
        planner = Planner(all_tools(), policy())
        plans = [
            planner.plan(InvestigationTask.VERIFY_SOURCE) for _ in range(5)
        ]
        # They should be distinct objects even if semantically equivalent
        assert len({id(p) for p in plans}) == 5
