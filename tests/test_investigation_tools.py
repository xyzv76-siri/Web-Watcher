"""Tests for web_watcher.investigation_tools — K.4.

Coverage map to architecture freeze §18:
  [1]  ToolResult construction
  [2]  ToolResult field validation
  [3]  pages_fetched non-negative invariant
  [4]  ToolCapability values (imported from K.1)
  [5]  deterministic capabilities()
  [6]  Tool protocol compatibility
  [7]  each Mock Tool capability
  [8]  supported task execution
  [9]  unsupported task failure
  [10] deterministic execution
  [11] context immutability
  [12] no Tool chaining
  [13] no network access (structural check)
  [14] no persistence (structural check)
  [15] no LLM dependency (structural check)
  [16] forbidden dependency absence (structural check)
  [17] evidence compatibility with K.2
  [18] pages_fetched semantics
  [19] ToolResult determinism
  [20] baseline regression protection
"""

from __future__ import annotations

import ast
import copy
import importlib
from datetime import datetime

import pytest

from web_watcher.investigation_contract import (
    InvestigationTask,
    ToolCapability,
)
from web_watcher.investigation_evidence import (
    Evidence,
    EvidenceType,
)
from web_watcher.investigation_tools import ToolResult

from tests.mock_investigation_tools import (
    MOCK_EVIDENCE_TIME,
    MockHistoricalLookupTool,
    MockPageParseTool,
    MockWebFetchTool,
    MockWebSearchTool,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MOCK_SRC = MockWebFetchTool()
MOCK_SEARCH = MockWebSearchTool()
MOCK_PARSE = MockPageParseTool()
MOCK_HISTORY = MockHistoricalLookupTool()

ALL_TOOLS = (MOCK_SRC, MOCK_SEARCH, MOCK_PARSE, MOCK_HISTORY)
ALL_TASKS = list(InvestigationTask)
ALL_CAPS = list(ToolCapability)

# ---------------------------------------------------------------------------
# [1] [2] ToolResult construction & field validation
# ---------------------------------------------------------------------------


class TestToolResultConstruction:
    def test_valid_success_true(self) -> None:
        r = ToolResult(
            success=True,
            evidence=(),
            pages_fetched=1,
            message="ok",
        )
        assert r.success is True
        assert r.evidence == ()
        assert r.pages_fetched == 1
        assert r.message == "ok"

    def test_valid_success_false(self) -> None:
        r = ToolResult(
            success=False,
            evidence=(),
            pages_fetched=0,
            message="failed",
        )
        assert r.success is False
        assert r.message == "failed"

    def test_valid_with_evidence(self) -> None:
        ev = Evidence(
            source="test",
            url="https://example.com",
            retrieved_at=datetime(2026, 8, 17),
            claim="c",
            supporting_text="s",
            evidence_type=EvidenceType.PRIMARY,
        )
        r = ToolResult(
            success=True,
            evidence=(ev,),
            pages_fetched=1,
            message="found",
        )
        assert len(r.evidence) == 1
        assert r.evidence[0].source == "test"

    def test_pages_fetched_zero_ok(self) -> None:
        r = ToolResult(
            success=True, evidence=(), pages_fetched=0, message="ok"
        )
        assert r.pages_fetched == 0

    def test_pages_fetched_positive_ok(self) -> None:
        r = ToolResult(
            success=True, evidence=(), pages_fetched=42, message="ok"
        )
        assert r.pages_fetched == 42


class TestToolResultFieldValidation:
    def test_success_not_bool_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            ToolResult(success=1, evidence=(), pages_fetched=0, message="x")

    def test_success_not_bool_int_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            ToolResult(success=0, evidence=(), pages_fetched=0, message="x")

    def test_evidence_not_tuple_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            ToolResult(
                success=True,
                evidence=["not a tuple"],  # type: ignore
                pages_fetched=0,
                message="x",
            )

    def test_evidence_element_not_evidence_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            ToolResult(
                success=True,
                evidence=("not evidence",),  # type: ignore
                pages_fetched=0,
                message="x",
            )

    def test_pages_fetched_not_int_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            ToolResult(success=True, evidence=(), pages_fetched=1.5, message="x")

    def test_pages_fetched_bool_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            ToolResult(
                success=True, evidence=(), pages_fetched=True, message="x"  # type: ignore
            )

    def test_pages_fetched_negative_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            ToolResult(success=True, evidence=(), pages_fetched=-1, message="x")

    def test_message_not_str_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            ToolResult(
                success=True, evidence=(), pages_fetched=0, message=123  # type: ignore
            )

    def test_message_empty_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            ToolResult(
                success=True, evidence=(), pages_fetched=0, message=""
            )

    def test_message_whitespace_only_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            ToolResult(
                success=True, evidence=(), pages_fetched=0, message="   "
            )

    def test_frozen_dataclass_is_immutable(self) -> None:
        r = ToolResult(success=True, evidence=(), pages_fetched=0, message="x")
        with pytest.raises(Exception):
            r.success = False  # type: ignore

    def test_deepcopy_is_independent(self) -> None:
        r = ToolResult(
            success=True, evidence=(), pages_fetched=1, message="hello"
        )
        r2 = copy.deepcopy(r)
        assert r2 is not r
        assert r2.success == r.success
        assert r2.pages_fetched == r.pages_fetched
        assert r2.message == r.message


# ---------------------------------------------------------------------------
# [3] pages_fetched non-negative invariant
# ---------------------------------------------------------------------------


class TestPagesFetchedInvariant:
    def test_zero_is_min(self) -> None:
        r = ToolResult(success=True, evidence=(), pages_fetched=0, message="m")
        assert r.pages_fetched == 0

    def test_negative_rejected(self) -> None:
        with pytest.raises(ValueError):
            ToolResult(
                success=True, evidence=(), pages_fetched=-1, message="m"
            )


# ---------------------------------------------------------------------------
# [4] ToolCapability values (imported from K.1)
# ---------------------------------------------------------------------------


class TestToolCapabilityReusedFromK1:
    """K.4 MUST reuse K.1's ToolCapability — no redefinition."""

    def test_web_fetch_is_k1_capability(self) -> None:
        assert ToolCapability.WEB_FETCH.value == "web_fetch"

    def test_web_search_is_k1_capability(self) -> None:
        assert ToolCapability.WEB_SEARCH.value == "web_search"

    def test_page_parse_is_k1_capability(self) -> None:
        assert ToolCapability.PAGE_PARSE.value == "page_parse"

    def test_historical_lookup_is_k1_capability(self) -> None:
        assert ToolCapability.HISTORICAL_LOOKUP.value == "historical_lookup"

    def test_capability_count_is_four(self) -> None:
        assert len(list(ToolCapability)) == 4

    def test_capability_types_are_str(self) -> None:
        for cap in ToolCapability:
            assert isinstance(cap.value, str)


# ---------------------------------------------------------------------------
# [5] Deterministic capabilities()
# ---------------------------------------------------------------------------


class TestDeterministicCapabilities:
    def test_fetch_capabilities_deterministic(self) -> None:
        tool = MockWebFetchTool()
        for _ in range(10):
            assert tool.capabilities() == {ToolCapability.WEB_FETCH}

    def test_search_capabilities_deterministic(self) -> None:
        tool = MockWebSearchTool()
        for _ in range(10):
            assert tool.capabilities() == {ToolCapability.WEB_SEARCH}

    def test_parse_capabilities_deterministic(self) -> None:
        tool = MockPageParseTool()
        for _ in range(10):
            assert tool.capabilities() == {ToolCapability.PAGE_PARSE}

    def test_history_capabilities_deterministic(self) -> None:
        tool = MockHistoricalLookupTool()
        for _ in range(10):
            assert tool.capabilities() == {ToolCapability.HISTORICAL_LOOKUP}

    def test_capabilities_returns_frozenset(self) -> None:
        assert isinstance(MockWebFetchTool().capabilities(), frozenset)

    def test_capabilities_not_mutated_by_repeated_calls(self) -> None:
        tool = MockWebFetchTool()
        first = tool.capabilities()
        for _ in range(5):
            second = tool.capabilities()
            assert first == second


# ---------------------------------------------------------------------------
# [6] Tool protocol compatibility
# ---------------------------------------------------------------------------


class TestToolProtocolCompatibility:
    def test_mock_fetch_has_capabilities_method(self) -> None:
        assert callable(MockWebFetchTool().capabilities)

    def test_mock_fetch_has_execute_method(self) -> None:
        assert callable(MockWebFetchTool().execute)

    def test_execute_accepts_task_and_context(self) -> None:
        tool = MockWebFetchTool()
        result = tool.execute(InvestigationTask.VERIFY_SOURCE, {})
        assert isinstance(result, ToolResult)

    def test_execute_context_is_mapping(self) -> None:
        """Context can be any Mapping — dict, etc."""
        tool = MockWebFetchTool()
        result = tool.execute(InvestigationTask.VERIFY_SOURCE, {"key": "val"})
        assert isinstance(result, ToolResult)


# ---------------------------------------------------------------------------
# [7] Each Mock Tool capability
# ---------------------------------------------------------------------------


class TestMockToolCapabilities:
    def test_web_fetch_has_web_fetch_capability(self) -> None:
        assert MockWebFetchTool().capabilities() == {ToolCapability.WEB_FETCH}

    def test_web_search_has_web_search_capability(self) -> None:
        assert MockWebSearchTool().capabilities() == {ToolCapability.WEB_SEARCH}

    def test_page_parse_has_page_parse_capability(self) -> None:
        assert MockPageParseTool().capabilities() == {ToolCapability.PAGE_PARSE}

    def test_historical_lookup_has_historical_capability(self) -> None:
        assert MockHistoricalLookupTool().capabilities() == {
            ToolCapability.HISTORICAL_LOOKUP
        }


# ---------------------------------------------------------------------------
# [8] [10] Supported task execution — deterministic
# ---------------------------------------------------------------------------


class TestSupportedTaskExecution:
    def test_fetch_supported_verify_source(self) -> None:
        r = MOCK_SRC.execute(InvestigationTask.VERIFY_SOURCE, {})
        assert r.success is True
        assert r.pages_fetched == 1
        assert len(r.evidence) == 1
        assert r.message == "mock web fetch complete"

    def test_fetch_is_deterministic(self) -> None:
        r1 = MOCK_SRC.execute(InvestigationTask.VERIFY_SOURCE, {})
        r2 = MOCK_SRC.execute(InvestigationTask.VERIFY_SOURCE, {})
        assert r1.success == r2.success
        assert r1.pages_fetched == r2.pages_fetched
        assert r1.message == r2.message
        assert len(r1.evidence) == len(r2.evidence)
        assert r1.evidence[0].source == r2.evidence[0].source
        assert r1.evidence[0].url == r2.evidence[0].url
        assert r1.evidence[0].claim == r2.evidence[0].claim
        assert r1.evidence[0].supporting_text == r2.evidence[0].supporting_text
        assert r1.evidence[0].evidence_type == r2.evidence[0].evidence_type

    def test_search_supported_fetch_related(self) -> None:
        r = MOCK_SEARCH.execute(InvestigationTask.FETCH_RELATED_SOURCE, {})
        assert r.success is True
        assert r.pages_fetched == 0

    def test_search_supported_cross_check(self) -> None:
        r = MOCK_SEARCH.execute(InvestigationTask.CROSS_CHECK, {})
        assert r.success is True
        assert r.pages_fetched == 0

    def test_parse_supported_extract_evidence(self) -> None:
        r = MOCK_PARSE.execute(InvestigationTask.EXTRACT_EVIDENCE, {})
        assert r.success is True
        assert r.pages_fetched == 0

    def test_history_supported_compare_history(self) -> None:
        r = MOCK_HISTORY.execute(InvestigationTask.COMPARE_WITH_HISTORY, {})
        assert r.success is True
        assert r.pages_fetched == 0


# ---------------------------------------------------------------------------
# [9] Unsupported task failure
# ---------------------------------------------------------------------------


class TestUnsupportedTaskFailure:
    def test_fetch_rejects_fetch_related(self) -> None:
        r = MOCK_SRC.execute(InvestigationTask.FETCH_RELATED_SOURCE, {})
        assert r.success is False
        assert r.evidence == ()
        assert r.pages_fetched == 0
        assert r.message == "unsupported task: fetch_related_source"

    def test_fetch_rejects_cross_check(self) -> None:
        r = MOCK_SRC.execute(InvestigationTask.CROSS_CHECK, {})
        assert r.success is False
        assert "unsupported task: cross_check" in r.message

    def test_search_rejects_verify_source(self) -> None:
        r = MOCK_SEARCH.execute(InvestigationTask.VERIFY_SOURCE, {})
        assert r.success is False
        assert r.message == "unsupported task: verify_source"

    def test_parse_rejects_fetch_related(self) -> None:
        r = MOCK_PARSE.execute(InvestigationTask.FETCH_RELATED_SOURCE, {})
        assert r.success is False
        assert r.message == "unsupported task: fetch_related_source"

    def test_history_rejects_extract_evidence(self) -> None:
        r = MOCK_HISTORY.execute(InvestigationTask.EXTRACT_EVIDENCE, {})
        assert r.success is False
        assert r.message == "unsupported task: extract_evidence"

    def test_all_tools_reject_all_other_tasks(self) -> None:
        """Every tool must reject every task it doesn't support."""
        tool_task_map = {
            MOCK_SRC: {InvestigationTask.VERIFY_SOURCE},
            MOCK_SEARCH: {
                InvestigationTask.FETCH_RELATED_SOURCE,
                InvestigationTask.CROSS_CHECK,
            },
            MOCK_PARSE: {InvestigationTask.EXTRACT_EVIDENCE},
            MOCK_HISTORY: {InvestigationTask.COMPARE_WITH_HISTORY},
        }
        for tool, supported in tool_task_map.items():
            for task in InvestigationTask:
                if task in supported:
                    r = tool.execute(task, {})
                    assert r.success is True, (
                        f"{tool.__class__.__name__} should accept {task}"
                    )
                else:
                    r = tool.execute(task, {})
                    assert r.success is False, (
                        f"{tool.__class__.__name__} should reject {task}"
                    )
                    assert r.evidence == ()
                    assert r.pages_fetched == 0


# ---------------------------------------------------------------------------
# [11] Context immutability
# ---------------------------------------------------------------------------


class TestContextImmutability:
    def test_context_not_mutated_by_fetch(self) -> None:
        ctx: dict[str, str] = {"key": "original_value"}
        ctx_copy = copy.deepcopy(ctx)
        MOCK_SRC.execute(InvestigationTask.VERIFY_SOURCE, ctx)
        assert ctx == ctx_copy

    def test_context_not_mutated_by_search(self) -> None:
        ctx: dict[str, str] = {"a": "1", "b": "2"}
        ctx_copy = copy.deepcopy(ctx)
        MOCK_SEARCH.execute(InvestigationTask.CROSS_CHECK, ctx)
        assert ctx == ctx_copy

    def test_context_not_mutated_on_unsupported_task(self) -> None:
        ctx: dict[str, str] = {"x": "y"}
        ctx_copy = copy.deepcopy(ctx)
        MOCK_SRC.execute(InvestigationTask.CROSS_CHECK, ctx)
        assert ctx == ctx_copy

    def test_context_dict_type_unchanged(self) -> None:
        ctx: dict[str, str] = {"k": "v"}
        MOCK_SRC.execute(InvestigationTask.VERIFY_SOURCE, ctx)
        assert isinstance(ctx, dict)
        assert ctx == {"k": "v"}


# ---------------------------------------------------------------------------
# [12] No Tool chaining
# ---------------------------------------------------------------------------


class TestNoToolChaining:
    """Tools MUST NOT call other Tools.  Verified behaviourally:
    each tool's execute() produces a deterministic result without
    side effects that would indicate cross-tool invocation."""

    def test_fetch_execute_does_not_create_search_tool(self) -> None:
        """MockWebFetchTool.execute() should not instantiate
        MockWebSearchTool, MockPageParseTool, or MockHistoricalLookupTool.

        Verified by checking the AST of the execute() method."""
        import ast
        import tests.mock_investigation_tools as mod

        source = inspect_source(mod)
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "MockWebFetchTool":
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and item.name == "execute":
                        refs = _collect_names(item)
                        for forbidden in (
                            "MockWebSearchTool",
                            "MockPageParseTool",
                            "MockHistoricalLookupTool",
                        ):
                            assert forbidden not in refs, (
                                f"MockWebFetchTool.execute() references {forbidden}"
                            )
                        return

        pytest.fail("Could not find MockWebFetchTool.execute() in AST")

    def test_search_execute_does_not_call_other_tools(self) -> None:
        import ast
        import tests.mock_investigation_tools as mod

        source = inspect_source(mod)
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "MockWebSearchTool":
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and item.name == "execute":
                        refs = _collect_names(item)
                        for forbidden in (
                            "MockWebFetchTool",
                            "MockPageParseTool",
                            "MockHistoricalLookupTool",
                        ):
                            assert forbidden not in refs, (
                                f"MockWebSearchTool.execute() references {forbidden}"
                            )
                        return

        pytest.fail("Could not find MockWebSearchTool.execute() in AST")

    def test_parse_execute_does_not_call_other_tools(self) -> None:
        import ast
        import tests.mock_investigation_tools as mod

        source = inspect_source(mod)
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "MockPageParseTool":
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and item.name == "execute":
                        refs = _collect_names(item)
                        for forbidden in (
                            "MockWebFetchTool",
                            "MockWebSearchTool",
                            "MockHistoricalLookupTool",
                        ):
                            assert forbidden not in refs, (
                                f"MockPageParseTool.execute() references {forbidden}"
                            )
                        return

        pytest.fail("Could not find MockPageParseTool.execute() in AST")

    def test_history_execute_does_not_call_other_tools(self) -> None:
        import ast
        import tests.mock_investigation_tools as mod

        source = inspect_source(mod)
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "MockHistoricalLookupTool":
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and item.name == "execute":
                        refs = _collect_names(item)
                        for forbidden in (
                            "MockWebFetchTool",
                            "MockWebSearchTool",
                            "MockPageParseTool",
                        ):
                            assert forbidden not in refs, (
                                f"MockHistoricalLookupTool.execute() references {forbidden}"
                            )
                        return

        pytest.fail("Could not find MockHistoricalLookupTool.execute() in AST")


def _collect_names(node: ast.AST) -> set[str]:  # type: ignore[name-defined]
    """Collect all Name / Attribute node names within an AST subtree."""
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
        if isinstance(child, ast.Attribute):
            names.add(child.attr)
    return names


# ---------------------------------------------------------------------------
# [13]–[15] Structural checks: no network / persistence / LLM
# ---------------------------------------------------------------------------


class TestStructuralNoForbiddenImports:
    """K.4 MUST NOT import or reference forbidden modules."""

    @pytest.fixture
    def source(self) -> str:
        import tests.mock_investigation_tools as mod

        return inspect_source(mod)

    def test_no_requests_import(self, source: str) -> None:
        assert _no_import_statement(source, "requests")

    def test_no_httpx_import(self, source: str) -> None:
        assert _no_import_statement(source, "httpx")

    def test_no_urllib_import(self, source: str) -> None:
        assert _no_import_statement(source, "urllib")

    def test_no_socket_import(self, source: str) -> None:
        assert _no_import_statement(source, "socket")

    def test_no_subprocess_import(self, source: str) -> None:
        assert _no_import_statement(source, "subprocess")

    def test_no_ai_contract_import(self, source: str) -> None:
        assert _no_import_statement(source, "ai_contract")

    def test_no_decide_import(self, source: str) -> None:
        assert _no_import_statement(source, "decide")

    def test_no_final_decision_import(self, source: str) -> None:
        assert _no_import_statement(source, "final_decision")

    def test_no_llm_provider_import(self, source: str) -> None:
        assert _no_import_statement(source, "llm_provider")

    def test_no_os_system(self, source: str) -> None:
        assert "os.system" not in _strip_comments_and_docstrings(source)

    def test_no_eval_exec(self, source: str) -> None:
        stripped = _strip_comments_and_docstrings(source)
        assert "eval(" not in stripped
        assert "exec(" not in stripped


# ---------------------------------------------------------------------------
# [17] Evidence compatibility with K.2
# ---------------------------------------------------------------------------


class TestEvidenceCompatibilityWithK2:
    def test_fetch_evidence_is_evidence_instance(self) -> None:
        r = MOCK_SRC.execute(InvestigationTask.VERIFY_SOURCE, {})
        ev = r.evidence[0]
        assert isinstance(ev, Evidence)

    def test_fetch_evidence_has_valid_source(self) -> None:
        ev = MOCK_SRC.execute(InvestigationTask.VERIFY_SOURCE, {}).evidence[0]
        assert ev.source == "mock_web_fetch"
        assert isinstance(ev.source, str)
        assert ev.source.strip()

    def test_fetch_evidence_has_valid_url(self) -> None:
        ev = MOCK_SRC.execute(InvestigationTask.VERIFY_SOURCE, {}).evidence[0]
        assert ev.url == "https://mock.example.com/fetch"
        assert isinstance(ev.url, str)
        assert ev.url.strip()

    def test_fetch_evidence_has_valid_claim(self) -> None:
        ev = MOCK_SRC.execute(InvestigationTask.VERIFY_SOURCE, {}).evidence[0]
        assert ev.claim == "mock fetched claim"
        assert isinstance(ev.claim, str)
        assert ev.claim.strip()

    def test_fetch_evidence_has_valid_supporting_text(self) -> None:
        ev = MOCK_SRC.execute(InvestigationTask.VERIFY_SOURCE, {}).evidence[0]
        assert ev.supporting_text == "mock fetched supporting text"
        assert isinstance(ev.supporting_text, str)
        assert ev.supporting_text.strip()

    def test_fetch_evidence_type_is_primary(self) -> None:
        ev = MOCK_SRC.execute(InvestigationTask.VERIFY_SOURCE, {}).evidence[0]
        assert ev.evidence_type == EvidenceType.PRIMARY

    def test_fetch_evidence_retrieved_at_is_datetime(self) -> None:
        ev = MOCK_SRC.execute(InvestigationTask.VERIFY_SOURCE, {}).evidence[0]
        assert isinstance(ev.retrieved_at, datetime)
        assert ev.retrieved_at == MOCK_EVIDENCE_TIME

    def test_search_evidence_type_is_secondary(self) -> None:
        ev = MOCK_SEARCH.execute(InvestigationTask.CROSS_CHECK, {}).evidence[0]
        assert ev.evidence_type == EvidenceType.SECONDARY

    def test_parse_evidence_type_is_derived(self) -> None:
        ev = MOCK_PARSE.execute(InvestigationTask.EXTRACT_EVIDENCE, {}).evidence[0]
        assert ev.evidence_type == EvidenceType.DERIVED

    def test_history_evidence_type_is_historical(self) -> None:
        ev = MOCK_HISTORY.execute(InvestigationTask.COMPARE_WITH_HISTORY, {}).evidence[0]
        assert ev.evidence_type == EvidenceType.HISTORICAL

    def test_evidence_not_evidence_id(self) -> None:
        """K.4 MUST NOT introduce evidence_id on Evidence."""
        ev = MOCK_SRC.execute(InvestigationTask.VERIFY_SOURCE, {}).evidence[0]
        assert not hasattr(ev, "evidence_id")


# ---------------------------------------------------------------------------
# [18] pages_fetched semantics
# ---------------------------------------------------------------------------


class TestPagesFetchedSemantics:
    def test_fetch_returns_1_page(self) -> None:
        r = MOCK_SRC.execute(InvestigationTask.VERIFY_SOURCE, {})
        assert r.pages_fetched == 1

    def test_search_returns_0_pages(self) -> None:
        r = MOCK_SEARCH.execute(InvestigationTask.CROSS_CHECK, {})
        assert r.pages_fetched == 0

    def test_parse_returns_0_pages(self) -> None:
        r = MOCK_PARSE.execute(InvestigationTask.EXTRACT_EVIDENCE, {})
        assert r.pages_fetched == 0

    def test_history_returns_0_pages(self) -> None:
        r = MOCK_HISTORY.execute(InvestigationTask.COMPARE_WITH_HISTORY, {})
        assert r.pages_fetched == 0

    def test_unsupported_returns_0_pages(self) -> None:
        r = MOCK_SRC.execute(InvestigationTask.CROSS_CHECK, {})
        assert r.pages_fetched == 0


# ---------------------------------------------------------------------------
# [19] ToolResult determinism
# ---------------------------------------------------------------------------


class TestToolResultDeterminism:
    def test_same_tool_same_task_same_context_gives_same_result(self) -> None:
        r1 = MOCK_SRC.execute(InvestigationTask.VERIFY_SOURCE, {})
        r2 = MOCK_SRC.execute(InvestigationTask.VERIFY_SOURCE, {})
        assert r1.success == r2.success
        assert r1.pages_fetched == r2.pages_fetched
        assert r1.message == r2.message
        assert len(r1.evidence) == len(r2.evidence)
        assert r1.evidence[0].source == r2.evidence[0].source
        assert r1.evidence[0].url == r2.evidence[0].url
        assert r1.evidence[0].retrieved_at == r2.evidence[0].retrieved_at
        assert r1.evidence[0].claim == r2.evidence[0].claim
        assert r1.evidence[0].supporting_text == r2.evidence[0].supporting_text
        assert r1.evidence[0].evidence_type == r2.evidence[0].evidence_type

    def test_multiple_contexts_same_task_same_result(self) -> None:
        r1 = MOCK_SRC.execute(InvestigationTask.VERIFY_SOURCE, {"a": "1"})
        r2 = MOCK_SRC.execute(InvestigationTask.VERIFY_SOURCE, {"b": "2"})
        # Context is input-only; output should not depend on context contents
        assert r1.success == r2.success
        assert r1.pages_fetched == r2.pages_fetched
        assert r1.message == r2.message


# ---------------------------------------------------------------------------
# [20] Baseline regression protection
# ---------------------------------------------------------------------------


class TestBaselineRegressionProtection:
    """Ensure K.4 does not break existing K.1/K.2/K.3 imports."""

    def test_k1_imports_still_work(self) -> None:
        from web_watcher.investigation_contract import (
            InvestigationPolicy,
            InvestigationTask,
            ToolCapability,
        )

        assert len(list(InvestigationTask)) == 5
        assert len(list(ToolCapability)) == 4
        policy = InvestigationPolicy()
        assert policy.max_steps == 5

    def test_k2_imports_still_work(self) -> None:
        from web_watcher.investigation_evidence import (
            Evidence,
            EvidenceType,
        )

        assert len(list(EvidenceType)) == 4
        ev = Evidence(
            source="reg",
            url="https://example.com",
            retrieved_at=datetime(2026, 8, 17),
            claim="c",
            supporting_text="s",
            evidence_type=EvidenceType.PRIMARY,
        )
        assert ev.source == "reg"

    def test_k3_imports_still_work(self) -> None:
        from web_watcher.investigation_result import (
            InvestigationResult,
            InvestigationStatus,
        )

        assert len(list(InvestigationStatus)) == 5
        result = InvestigationResult(
            status=InvestigationStatus.SUCCESS,
            summary="regression ok",
            findings=(),
            evidence=(),
            confidence=1.0,
            steps_used=0,
            pages_checked=0,
            failure_reason="",
        )
        assert result.status == InvestigationStatus.SUCCESS


# ---------------------------------------------------------------------------
# Structural inspection helpers
# ---------------------------------------------------------------------------


import inspect  # noqa: E402 — used only in helper functions


def inspect_source(mod: module) -> str:  # type: ignore[name-defined]
    """Return the source text of a module."""
    return inspect.getsource(mod)


def _no_import_statement(source: str, module_name: str) -> bool:
    """Return True if there is NO `import <module_name>` or
    `from <module_name> import ...` statement in the source.

    Comments and docstrings are stripped first to avoid false positives.
    """
    stripped = _strip_comments_and_docstrings(source)
    lines = stripped.splitlines()
    for line in lines:
        s = line.strip()
        if s.startswith(f"import {module_name}") or s.startswith(
            f"from {module_name}"
        ):
            return False
    return True


def _strip_comments_and_docstrings(source: str) -> str:
    """Remove Python comments and module-level docstrings to avoid
    false positives in structural import checks."""
    lines = source.splitlines()
    result: list[str] = []
    in_docstring = False
    for line in lines:
        stripped = line.strip()
        # Toggle on triple-quoted docstrings
        if '"""' in stripped:
            count = stripped.count('"""')
            if count == 1:
                in_docstring = not in_docstring
                continue
            elif count >= 2:
                # Single-line docstring like '''...'''
                continue
        if in_docstring:
            continue
        # Strip comments
        idx = stripped.find("#")
        if idx >= 0:
            stripped = stripped[:idx]
        result.append(stripped)
    return "\n".join(result)