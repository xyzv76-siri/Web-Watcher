"""Tests for web_watcher.investigation_result — K.3."""

from __future__ import annotations

import copy
from datetime import datetime

import pytest

from web_watcher.investigation_evidence import Evidence, EvidenceType
from web_watcher.investigation_result import (
    InvestigationFinding,
    InvestigationResult,
    InvestigationStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 8, 17, 12, 0, 0)


def _make_evidence(
    source: str = "test_source",
    claim: str = "test_claim",
    text: str = "test_text",
    etype: EvidenceType = EvidenceType.PRIMARY,
) -> Evidence:
    return Evidence(
        source=source,
        url="https://example.com/test",
        retrieved_at=_NOW,
        claim=claim,
        supporting_text=text,
        evidence_type=etype,
    )


def _make_finding(
    claim: str = "a claim",
    status: str = "supported",
    refs: tuple[str, ...] = ("0",),
) -> InvestigationFinding:
    return InvestigationFinding(
        claim=claim,
        finding_status=status,
        evidence_refs=refs,
    )


def _make_result(
    status: InvestigationStatus = InvestigationStatus.SUCCESS,
    summary: str = "ok",
    findings: tuple[InvestigationFinding, ...] | None = None,
    evidence: tuple[Evidence, ...] | None = None,
    confidence: float = 0.95,
    steps_used: int = 3,
    pages_checked: int = 5,
    failure_reason: str = "",
) -> InvestigationResult:
    if findings is None:
        findings = tuple()
    if evidence is None:
        evidence = tuple()
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


# ===================================================================
# InvestigationStatus tests
# ===================================================================


class TestInvestigationStatus:
    def test_success_member(self) -> None:
        assert InvestigationStatus.SUCCESS.value == "success"

    def test_inconclusive_member(self) -> None:
        assert InvestigationStatus.INCONCLUSIVE.value == "inconclusive"

    def test_failed_member(self) -> None:
        assert InvestigationStatus.FAILED.value == "failed"

    def test_timeout_member(self) -> None:
        assert InvestigationStatus.TIMEOUT.value == "timeout"

    def test_budget_exceeded_member(self) -> None:
        assert InvestigationStatus.BUDGET_EXCEEDED.value == "budget_exceeded"

    def test_str_enum_has_exactly_5_members(self) -> None:
        members = list(InvestigationStatus)
        assert len(members) == 5
        values = {m.value for m in members}
        assert values == {"success", "inconclusive", "failed",
                          "timeout", "budget_exceeded"}

    def test_str_comparison_with_value(self) -> None:
        assert InvestigationStatus.SUCCESS == "success"
        assert InvestigationStatus.FAILED == "failed"


# ===================================================================
# InvestigationFinding tests
# ===================================================================


class TestInvestigationFinding:
    def test_valid_finding(self) -> None:
        f = _make_finding()
        assert f.claim == "a claim"
        assert f.finding_status == "supported"
        assert f.evidence_refs == ("0",)

    def test_finding_is_frozen(self) -> None:
        f = _make_finding()
        with pytest.raises(Exception):
            f.claim = "changed"  # type: ignore

    def test_finding_copy_is_deep(self) -> None:
        f = _make_finding()
        f2 = copy.deepcopy(f)
        assert f2 is not f
        assert f2.claim == f.claim
        assert f2.evidence_refs == f.evidence_refs

    def test_finding_status_supported(self) -> None:
        f = _make_finding(status="supported")
        assert f.finding_status == "supported"

    def test_finding_status_contradicted(self) -> None:
        f = _make_finding(status="contradicted")
        assert f.finding_status == "contradicted"

    def test_finding_status_unverified(self) -> None:
        f = _make_finding(status="unverified")
        assert f.finding_status == "unverified"

    def test_finding_status_invalid_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            _make_finding(status="unknown")

    def test_finding_status_invalid_type_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            _make_finding(status=123)  # type: ignore

    def test_finding_empty_claim_raises(self) -> None:
        with pytest.raises(ValueError):
            _make_finding(claim="")

    def test_finding_whitespace_claim_raises(self) -> None:
        with pytest.raises(ValueError):
            _make_finding(claim="   ")

    def test_finding_claim_type_error(self) -> None:
        with pytest.raises(TypeError):
            _make_finding(claim=42)  # type: ignore

    def test_finding_empty_refs_is_ok(self) -> None:
        f = _make_finding(refs=())
        assert f.evidence_refs == ()

    def test_finding_non_string_ref_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            _make_finding(refs=(123,))  # type: ignore

    def test_finding_empty_string_ref_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            _make_finding(refs=("",))

    def test_finding_whitespace_string_ref_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            _make_finding(refs=("  ",))

    def test_finding_multiple_refs(self) -> None:
        f = _make_finding(refs=("0", "2", "5"))
        assert f.evidence_refs == ("0", "2", "5")


# ===================================================================
# InvestigationResult — construction
# ===================================================================


class TestInvestigationResultConstruction:
    def test_successful_result_full(self) -> None:
        ev0 = _make_evidence()
        ev1 = _make_evidence(source="B")
        f0 = _make_finding(claim="C1", refs=("0", "1"))
        f1 = _make_finding(claim="C2", refs=("0",))
        result = _make_result(
            findings=(f0, f1),
            evidence=(ev0, ev1),
        )
        assert result.status == InvestigationStatus.SUCCESS
        assert result.summary == "ok"
        assert len(result.findings) == 2
        assert len(result.evidence) == 2
        assert result.confidence == 0.95
        assert result.steps_used == 3
        assert result.pages_checked == 5
        assert result.failure_reason == ""

    def test_successful_result_minimal(self) -> None:
        result = _make_result()
        assert result.status == InvestigationStatus.SUCCESS
        assert result.findings == ()
        assert result.evidence == ()
        assert result.failure_reason == ""

    def test_inconclusive_result(self) -> None:
        result = _make_result(
            status=InvestigationStatus.INCONCLUSIVE,
            summary="no evidence found",
        )
        assert result.status == InvestigationStatus.INCONCLUSIVE
        assert result.failure_reason == ""

    def test_failed_result_requires_reason(self) -> None:
        result = _make_result(
            status=InvestigationStatus.FAILED,
            failure_reason="something broke",
        )
        assert result.status == InvestigationStatus.FAILED
        assert result.failure_reason == "something broke"

    def test_timeout_result_requires_reason(self) -> None:
        result = _make_result(
            status=InvestigationStatus.TIMEOUT,
            failure_reason="elapsed too long",
        )
        assert result.status == InvestigationStatus.TIMEOUT
        assert result.failure_reason == "elapsed too long"

    def test_budget_exceeded_result_requires_reason(self) -> None:
        result = _make_result(
            status=InvestigationStatus.BUDGET_EXCEEDED,
            failure_reason="max_steps reached",
        )
        assert result.status == InvestigationStatus.BUDGET_EXCEEDED
        assert result.failure_reason == "max_steps reached"


# ===================================================================
# InvestigationResult — failure_reason constraints
# ===================================================================


class TestInvestigationResultFailureReason:
    def test_success_with_non_empty_reason_raises(self) -> None:
        with pytest.raises(ValueError):
            _make_result(
                status=InvestigationStatus.SUCCESS,
                failure_reason="this should fail",
            )

    def test_inconclusive_with_non_empty_reason_raises(self) -> None:
        with pytest.raises(ValueError):
            _make_result(
                status=InvestigationStatus.INCONCLUSIVE,
                failure_reason="this should also fail",
            )

    def test_success_with_whitespace_reason_ok(self) -> None:
        """Whitespace-only failure_reason is treated as effectively empty."""
        result = _make_result(
            status=InvestigationStatus.SUCCESS,
            failure_reason="   ",
        )
        # Whitespace is stripped → treated as empty → accepted
        assert result.failure_reason == "   "

    def test_failed_with_empty_reason_raises(self) -> None:
        with pytest.raises(ValueError):
            _make_result(
                status=InvestigationStatus.FAILED,
                failure_reason="",
            )

    def test_failed_with_whitespace_reason_raises(self) -> None:
        with pytest.raises(ValueError):
            _make_result(
                status=InvestigationStatus.FAILED,
                failure_reason="  ",
            )

    def test_timeout_with_empty_reason_raises(self) -> None:
        with pytest.raises(ValueError):
            _make_result(
                status=InvestigationStatus.TIMEOUT,
                failure_reason="",
            )

    def test_budget_exceeded_with_empty_reason_raises(self) -> None:
        with pytest.raises(ValueError):
            _make_result(
                status=InvestigationStatus.BUDGET_EXCEEDED,
                failure_reason="",
            )


# ===================================================================
# InvestigationResult — confidence validation
# ===================================================================


class TestInvestigationResultConfidence:
    def test_confidence_zero(self) -> None:
        result = _make_result(confidence=0.0)
        assert result.confidence == 0.0

    def test_confidence_one(self) -> None:
        result = _make_result(confidence=1.0)
        assert result.confidence == 1.0

    def test_confidence_mid(self) -> None:
        result = _make_result(confidence=0.5)
        assert result.confidence == 0.5

    def test_confidence_int_zero_auto_promoted(self) -> None:
        result = _make_result(confidence=0)
        assert result.confidence == 0  # int is accepted

    def test_confidence_int_one_auto_promoted(self) -> None:
        result = _make_result(confidence=1)
        assert result.confidence == 1

    def test_confidence_below_zero_raises(self) -> None:
        with pytest.raises(ValueError):
            _make_result(confidence=-0.01)

    def test_confidence_above_one_raises(self) -> None:
        with pytest.raises(ValueError):
            _make_result(confidence=1.01)

    def test_confidence_bool_raises(self) -> None:
        with pytest.raises(TypeError):
            _make_result(confidence=True)  # type: ignore

    def test_confidence_str_raises(self) -> None:
        with pytest.raises(TypeError):
            _make_result(confidence="0.5")  # type: ignore


# ===================================================================
# InvestigationResult — steps / pages validation
# ===================================================================


class TestInvestigationResultStepsPages:
    def test_zero_steps_zero_pages_ok(self) -> None:
        result = _make_result(steps_used=0, pages_checked=0)
        assert result.steps_used == 0
        assert result.pages_checked == 0

    def test_positive_steps_positive_pages_ok(self) -> None:
        result = _make_result(steps_used=10, pages_checked=42)
        assert result.steps_used == 10
        assert result.pages_checked == 42

    def test_negative_steps_raises(self) -> None:
        with pytest.raises(ValueError):
            _make_result(steps_used=-1)

    def test_negative_pages_raises(self) -> None:
        with pytest.raises(ValueError):
            _make_result(pages_checked=-1)

    def test_steps_bool_raises(self) -> None:
        with pytest.raises(TypeError):
            _make_result(steps_used=True)  # type: ignore

    def test_pages_bool_raises(self) -> None:
        with pytest.raises(TypeError):
            _make_result(pages_checked=False)  # type: ignore


# ===================================================================
# InvestigationResult — evidence_refs positional validation
# ===================================================================


class TestInvestigationResultEvidenceRefs:
    def test_valid_indices_in_bounds(self) -> None:
        evs = (_make_evidence(), _make_evidence(), _make_evidence())
        f = _make_finding(refs=("0", "1", "2"))
        result = _make_result(findings=(f,), evidence=evs)
        assert result.findings[0].evidence_refs == ("0", "1", "2")

    def test_index_at_upper_bound_raises(self) -> None:
        evs = (_make_evidence(), _make_evidence())
        f = _make_finding(refs=("0", "2"))
        with pytest.raises(ValueError):
            _make_result(findings=(f,), evidence=evs)

    def test_negative_index_raises(self) -> None:
        evs = (_make_evidence(),)
        f = _make_finding(refs=("-1",))
        with pytest.raises(ValueError):
            _make_result(findings=(f,), evidence=evs)

    def test_non_numeric_ref_raises(self) -> None:
        evs = (_make_evidence(),)
        f = _make_finding(refs=("abc",))
        with pytest.raises(ValueError):
            _make_result(findings=(f,), evidence=evs)

    def test_empty_evidence_with_empty_refs_ok(self) -> None:
        f = _make_finding(refs=())
        result = _make_result(findings=(f,), evidence=())
        assert result.findings[0].evidence_refs == ()

    def test_empty_evidence_with_non_empty_refs_raises(self) -> None:
        f = _make_finding(refs=("0",))
        with pytest.raises(ValueError):
            _make_result(findings=(f,), evidence=())

    def test_multiple_findings_all_valid_refs(self) -> None:
        evs = (_make_evidence("A"), _make_evidence("B"),
               _make_evidence("C"))
        f0 = _make_finding(claim="c1", refs=("0",))
        f1 = _make_finding(claim="c2", refs=("1", "2"))
        f2 = _make_finding(claim="c3", refs=())
        result = _make_result(findings=(f0, f1, f2), evidence=evs)
        assert result.findings[0].evidence_refs == ("0",)
        assert result.findings[1].evidence_refs == ("1", "2")
        assert result.findings[2].evidence_refs == ()

    def test_duplicate_source_different_indices(self) -> None:
        ev0 = _make_evidence(source="same_source", claim="claim_1")
        ev1 = _make_evidence(source="same_source", claim="claim_2")
        f = _make_finding(claim="meta", refs=("0", "1"))
        result = _make_result(findings=(f,), evidence=(ev0, ev1))
        assert result.findings[0].evidence_refs == ("0", "1")


# ===================================================================
# InvestigationResult — type validation
# ===================================================================


class TestInvestigationResultTypeValidation:
    def test_status_wrong_type(self) -> None:
        with pytest.raises(TypeError):
            _make_result(status="success")  # type: ignore

    def test_summary_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            _make_result(summary="")

    def test_summary_whitespace_raises(self) -> None:
        with pytest.raises(ValueError):
            _make_result(summary="  ")

    def test_summary_wrong_type(self) -> None:
        with pytest.raises(TypeError):
            _make_result(summary=123)  # type: ignore

    def test_findings_wrong_type(self) -> None:
        with pytest.raises(TypeError):
            _make_result(findings="not a tuple")  # type: ignore

    def test_findings_element_wrong_type(self) -> None:
        with pytest.raises(TypeError):
            _make_result(findings=("not_a_finding",))  # type: ignore

    def test_evidence_wrong_type(self) -> None:
        with pytest.raises(TypeError):
            _make_result(evidence="not a tuple")  # type: ignore

    def test_evidence_element_wrong_type(self) -> None:
        with pytest.raises(TypeError):
            _make_result(evidence=("not_evidence",))  # type: ignore


# ===================================================================
# InvestigationResult — frozen
# ===================================================================


class TestInvestigationResultFrozen:
    def test_result_is_frozen(self) -> None:
        result = _make_result()
        with pytest.raises(Exception):
            result.summary = "changed"  # type: ignore

    def test_result_deepcopy_is_independent(self) -> None:
        result = _make_result()
        result2 = copy.deepcopy(result)
        assert result2 is not result
        assert result2.summary == result.summary
        assert result2.confidence == result.confidence