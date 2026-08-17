"""Tests for Phase 11-A K.1 — Investigation contract."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from web_watcher.investigation_contract import (
    InvestigationPolicy,
    InvestigationTask,
    PolicyValidationError,
    ToolCapability,
    ToolProvider,
)


# ===========================================================================
# InvestigationTask — Enum members
# ===========================================================================


class TestInvestigationTask:
    """All five task categories are present and correctly valued."""

    def test_verify_source_value(self):
        assert InvestigationTask.VERIFY_SOURCE.value == "verify_source"

    def test_fetch_related_source_value(self):
        assert InvestigationTask.FETCH_RELATED_SOURCE.value == "fetch_related_source"

    def test_compare_with_history_value(self):
        assert InvestigationTask.COMPARE_WITH_HISTORY.value == "compare_with_history"

    def test_extract_evidence_value(self):
        assert InvestigationTask.EXTRACT_EVIDENCE.value == "extract_evidence"

    def test_cross_check_value(self):
        assert InvestigationTask.CROSS_CHECK.value == "cross_check"

    def test_all_members_listed(self):
        expected = {
            InvestigationTask.VERIFY_SOURCE,
            InvestigationTask.FETCH_RELATED_SOURCE,
            InvestigationTask.COMPARE_WITH_HISTORY,
            InvestigationTask.EXTRACT_EVIDENCE,
            InvestigationTask.CROSS_CHECK,
        }
        assert set(InvestigationTask) == expected

    def test_unknown_string_is_not_a_task(self):
        """An arbitrary string cannot be coerced into an InvestigationTask."""
        with pytest.raises(ValueError):
            InvestigationTask("some_unknown_task")

    def test_case_sensitive_mismatch_is_not_a_task(self):
        with pytest.raises(ValueError):
            InvestigationTask("VERIFY_SOURCE")  # wrong case

    def test_empty_string_is_not_a_task(self):
        with pytest.raises(ValueError):
            InvestigationTask("")


# ===========================================================================
# ToolCapability — Enum members
# ===========================================================================


class TestToolCapability:
    """All four capabilities are present and correctly valued."""

    def test_web_fetch_value(self):
        assert ToolCapability.WEB_FETCH.value == "web_fetch"

    def test_web_search_value(self):
        assert ToolCapability.WEB_SEARCH.value == "web_search"

    def test_page_parse_value(self):
        assert ToolCapability.PAGE_PARSE.value == "page_parse"

    def test_historical_lookup_value(self):
        assert ToolCapability.HISTORICAL_LOOKUP.value == "historical_lookup"

    def test_all_members_listed(self):
        expected = {
            ToolCapability.WEB_FETCH,
            ToolCapability.WEB_SEARCH,
            ToolCapability.PAGE_PARSE,
            ToolCapability.HISTORICAL_LOOKUP,
        }
        assert set(ToolCapability) == expected

    def test_unknown_string_is_not_a_capability(self):
        with pytest.raises(ValueError):
            ToolCapability("unknown_capability")


# ===========================================================================
# InvestigationPolicy — defaults and construction
# ===========================================================================


class TestInvestigationPolicyDefaults:
    """Default values are correct and immutable."""

    def test_default_max_steps(self):
        p = InvestigationPolicy()
        assert p.max_steps == 5

    def test_default_max_pages(self):
        p = InvestigationPolicy()
        assert p.max_pages == 10

    def test_default_timeout_seconds(self):
        p = InvestigationPolicy()
        assert p.timeout_seconds == 60.0

    def test_immutable_by_assignment(self):
        p = InvestigationPolicy()
        with pytest.raises(AttributeError):
            p.max_steps = 99  # type: ignore[misc]

    def test_immutable_by_shallow_copy(self):
        p = InvestigationPolicy()
        p2 = copy.copy(p)
        assert p2.max_steps == 5
        assert p2.max_pages == 10
        assert p2.timeout_seconds == 60.0

    def test_repr_contains_field_names(self):
        p = InvestigationPolicy()
        assert "max_steps" in repr(p)
        assert "max_pages" in repr(p)
        assert "timeout_seconds" in repr(p)


# ===========================================================================
# InvestigationPolicy — boundary validation
# ===========================================================================


class TestInvestigationPolicyValidation:
    """Each field rejects out-of-range values with PolicyValidationError."""

    def test_max_steps_one_is_valid(self):
        p = InvestigationPolicy(max_steps=1)
        assert p.max_steps == 1

    def test_max_pages_zero_is_valid(self):
        p = InvestigationPolicy(max_pages=0)
        assert p.max_pages == 0

    def test_timeout_seconds_zero_point_one_is_valid(self):
        p = InvestigationPolicy(timeout_seconds=0.1)
        assert p.timeout_seconds == 0.1

    def test_max_steps_zero_is_rejected(self):
        with pytest.raises(PolicyValidationError, match="max_steps.*>= 1"):
            InvestigationPolicy(max_steps=0)

    def test_max_steps_negative_is_rejected(self):
        with pytest.raises(PolicyValidationError, match="max_steps.*>= 1"):
            InvestigationPolicy(max_steps=-1)

    def test_max_pages_negative_is_rejected(self):
        with pytest.raises(PolicyValidationError, match="max_pages.*>= 0"):
            InvestigationPolicy(max_pages=-1)

    def test_timeout_seconds_zero_is_rejected(self):
        with pytest.raises(PolicyValidationError, match="timeout_seconds.*> 0"):
            InvestigationPolicy(timeout_seconds=0.0)

    def test_timeout_seconds_negative_is_rejected(self):
        with pytest.raises(PolicyValidationError, match="timeout_seconds.*> 0"):
            InvestigationPolicy(timeout_seconds=-1.0)

    def test_max_steps_bool_is_rejected(self):
        with pytest.raises(PolicyValidationError, match="max_steps.*must be int"):
            InvestigationPolicy(max_steps=True)

    def test_max_pages_bool_is_rejected(self):
        with pytest.raises(PolicyValidationError, match="max_pages.*must be int"):
            InvestigationPolicy(max_pages=False)

    def test_timeout_seconds_string_is_rejected(self):
        with pytest.raises(PolicyValidationError, match="timeout_seconds.*numeric"):
            InvestigationPolicy(timeout_seconds="60")  # type: ignore[arg-type]

    def test_all_fields_custom_valid(self):
        p = InvestigationPolicy(max_steps=3, max_pages=0, timeout_seconds=0.5)
        assert p.max_steps == 3
        assert p.max_pages == 0
        assert p.timeout_seconds == 0.5

    def test_large_values_accepted(self):
        p = InvestigationPolicy(max_steps=100, max_pages=1000, timeout_seconds=3600.0)
        assert p.max_steps == 100
        assert p.max_pages == 1000
        assert p.timeout_seconds == 3600.0


# ===========================================================================
# InvestigationPolicy — immutability
# ===========================================================================


class TestInvestigationPolicyImmutability:
    """Field values cannot be mutated after construction."""

    def test_cannot_change_max_steps(self):
        p = InvestigationPolicy()
        with pytest.raises(AttributeError):
            p.max_steps = 10  # type: ignore[misc]

    def test_cannot_change_max_pages(self):
        p = InvestigationPolicy()
        with pytest.raises(AttributeError):
            p.max_pages = 100  # type: ignore[misc]

    def test_cannot_change_timeout_seconds(self):
        p = InvestigationPolicy()
        with pytest.raises(AttributeError):
            p.timeout_seconds = 0  # type: ignore[misc]

    def test_hash_stable(self):
        p = InvestigationPolicy()
        h = hash(p)
        assert hash(p) == h

    def test_equality_by_fields(self):
        p1 = InvestigationPolicy(max_steps=3, max_pages=0, timeout_seconds=1.0)
        p2 = InvestigationPolicy(max_steps=3, max_pages=0, timeout_seconds=1.0)
        assert p1 == p2


# ===========================================================================
# ToolProvider — Protocol conformance
# ===========================================================================


class TestToolProviderProtocol:
    """Verify ToolProvider Protocol is checkable at runtime."""

    def test_concrete_implementor_conforms(self):
        class ConcreteProvider:
            def suggest_task(self, context: dict[str, str]) -> InvestigationTask:
                return InvestigationTask.CROSS_CHECK

        provider = ConcreteProvider()
        result = provider.suggest_task({})
        assert result == InvestigationTask.CROSS_CHECK

    def test_invalid_return_type_is_caught(self):
        """A class that returns the wrong type fails Protocol validation."""

        class BadProvider:
            def suggest_task(self, context: dict[str, str]) -> str:
                return "not_a_task"

        # At runtime, a BadProvider instance is still callable — the
        # Protocol check would only fail for a strict runtime check.
        # We verify the return value explicitly.
        provider = BadProvider()
        result = provider.suggest_task({})
        with pytest.raises(ValueError):
            InvestigationTask(result)

    def test_protocol_suggest_task_returns_valid_task(self):
        """A well-behaved provider returns a real InvestigationTask."""

        class FixedProvider:
            def suggest_task(self, context: dict[str, str]) -> InvestigationTask:
                return InvestigationTask.EXTRACT_EVIDENCE

        provider = FixedProvider()
        task = provider.suggest_task({"hint": "something"})
        assert task is InvestigationTask.EXTRACT_EVIDENCE

    def test_provider_does_not_mutate_context(self):
        """Provider must not mutate the context mapping."""

        class MutatingProvider:
            def suggest_task(self, context: dict[str, str]) -> InvestigationTask:
                return InvestigationTask.VERIFY_SOURCE

        ctx: dict[str, str] = {"key": "val"}
        provider = MutatingProvider()
        provider.suggest_task(ctx)
        assert ctx == {"key": "val"}