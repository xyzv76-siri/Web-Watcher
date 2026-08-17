"""Phase 11-A K.3 — Investigation result contract.

Defines the immutable aggregate result of an investigation run.

This module is a pure data object.  It does not fetch, parse, call AI,
write files, or perform any side effects.  It depends only on the
standard library and on ``investigation_evidence.py`` (K.2) for the
``Evidence`` dataclass.  It does NOT import any Phase 10 module.

Canonical reference model:
    ``InvestigationFinding.evidence_refs`` is a tuple of ``str`` values
    representing **zero-based positional indices** into the
    ``InvestigationResult.evidence`` tuple.  This is the model mandated
    by the Architecture Freeze (§7).  No unique ``evidence_id`` field
    exists on ``Evidence`` (K.2 is frozen).  Cross-result evidence
    referencing is not supported.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from web_watcher.investigation_evidence import Evidence


# ---------------------------------------------------------------------------
# Investigation status
# ---------------------------------------------------------------------------


class InvestigationStatus(str, Enum):
    """Terminal state of an investigation run."""

    SUCCESS = "success"
    INCONCLUSIVE = "inconclusive"
    FAILED = "failed"
    TIMEOUT = "timeout"
    BUDGET_EXCEEDED = "budget_exceeded"


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_FINDING_STATUSES = frozenset(
    {"supported", "contradicted", "unverified"}
)


def _validate_finding_status(value: Any) -> None:
    if not isinstance(value, str):
        raise TypeError(
            f"finding_status must be str, got {type(value).__name__}"
        )
    if value not in _FINDING_STATUSES:
        raise ValueError(
            f"finding_status must be one of {sorted(_FINDING_STATUSES)}, "
            f"got {value!r}"
        )


def _validate_non_empty_string(name: str, value: Any) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be str, got {type(value).__name__}")
    if not value.strip():
        raise ValueError(f"{name} must be non-empty")


def _validate_string(name: str, value: Any) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be str, got {type(value).__name__}")


def _validate_int_ge(value: Any, name: str, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be int, got {type(value).__name__}")
    if value < minimum:
        raise ValueError(
            f"{name} must be >= {minimum}, got {value}"
        )


def _validate_confidence(value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(
            f"confidence must be float, got {type(value).__name__}"
        )
    if value < 0.0 or value > 1.0:
        raise ValueError(f"confidence must be in [0.0, 1.0], got {value}")


def _validate_evidence_refs_format(refs: Any) -> None:
    """Validate that evidence_refs is a tuple of str at finding level."""
    if not isinstance(refs, tuple):
        raise TypeError(
            f"evidence_refs must be tuple, got {type(refs).__name__}"
        )
    for i, ref in enumerate(refs):
        if not isinstance(ref, str):
            raise TypeError(
                f"evidence_refs[{i}] must be str, got "
                f"{type(ref).__name__}"
            )
        if not ref.strip():
            raise ValueError(
                f"evidence_refs[{i}] must be a non-empty str, got empty"
            )


def _validate_evidence_refs(
    refs: Any, evidence_count: int
) -> None:
    """Validate evidence_refs against the positional-index contract.

    Every element must be a ``str`` that is a valid zero-based index
    into the ``evidence`` tuple of length ``evidence_count``.
    """
    if not isinstance(refs, tuple):
        raise TypeError(
            f"evidence_refs must be tuple, got {type(refs).__name__}"
        )
    for i, ref in enumerate(refs):
        if not isinstance(ref, str):
            raise TypeError(
                f"evidence_refs[{i}] must be str, got "
                f"{type(ref).__name__}"
            )
        try:
            index = int(ref)
        except ValueError:
            raise ValueError(
                f"evidence_refs[{i}] must be a numeric string, got {ref!r}"
            ) from None
        if index < 0 or index >= evidence_count:
            raise ValueError(
                f"evidence_refs[{i}] index {index} out of range; "
                f"evidence tuple has {evidence_count} items"
            )


# ---------------------------------------------------------------------------
# Investigation finding
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InvestigationFinding:
    """A single finding produced by an investigation.

    ``evidence_refs`` uses the canonical positional-index model:
    each element is a ``str`` representation of a zero-based index
    into ``InvestigationResult.evidence``.
    """

    claim: str
    finding_status: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_non_empty_string("claim", self.claim)
        _validate_finding_status(self.finding_status)
        _validate_evidence_refs_format(self.evidence_refs)


# ---------------------------------------------------------------------------
# Investigation result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InvestigationResult:
    """Immutable aggregate result of a single investigation run.

    Invariants enforced in __post_init__:
        * status is a valid InvestigationStatus member
        * summary is a non-empty str
        * findings is a tuple of InvestigationFinding instances
        * evidence is a tuple of Evidence instances
        * confidence is a float in [0.0, 1.0]
        * steps_used >= 0
        * pages_checked >= 0
        * failure_reason is "" when status is SUCCESS or INCONCLUSIVE
        * every evidence_refs in findings is a valid positional index
    """

    status: InvestigationStatus
    summary: str
    findings: tuple[InvestigationFinding, ...]
    evidence: tuple[Evidence, ...]
    confidence: float
    steps_used: int
    pages_checked: int
    failure_reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.status, InvestigationStatus):
            raise TypeError(
                f"status must be InvestigationStatus, got "
                f"{type(self.status).__name__}"
            )
        _validate_non_empty_string("summary", self.summary)

        # findings must be a tuple of InvestigationFinding
        if not isinstance(self.findings, tuple):
            raise TypeError(
                f"findings must be tuple, got {type(self.findings).__name__}"
            )
        for i, f in enumerate(self.findings):
            if not isinstance(f, InvestigationFinding):
                raise TypeError(
                    f"findings[{i}] must be InvestigationFinding, got "
                    f"{type(f).__name__}"
                )

        # evidence must be a tuple of Evidence
        if not isinstance(self.evidence, tuple):
            raise TypeError(
                f"evidence must be tuple, got {type(self.evidence).__name__}"
            )
        for i, ev in enumerate(self.evidence):
            if not isinstance(ev, Evidence):
                raise TypeError(
                    f"evidence[{i}] must be Evidence, got "
                    f"{type(ev).__name__}"
                )

        _validate_confidence(self.confidence)
        _validate_int_ge(self.steps_used, "steps_used", 0)
        _validate_int_ge(self.pages_checked, "pages_checked", 0)
        _validate_string("failure_reason", self.failure_reason)

        # failure_reason must be empty for SUCCESS / INCONCLUSIVE
        if self.status in (InvestigationStatus.SUCCESS,
                           InvestigationStatus.INCONCLUSIVE):
            if self.failure_reason.strip():
                raise ValueError(
                    "failure_reason must be empty when status is "
                    f"{self.status.value}, got {self.failure_reason!r}"
                )
        else:
            # FAILED / TIMEOUT / BUDGET_EXCEEDED must have a non-empty reason
            if not self.failure_reason.strip():
                raise ValueError(
                    f"failure_reason must be non-empty when status is "
                    f"{self.status.value}"
                )

        # Validate all evidence_refs against the evidence tuple
        for i, f in enumerate(self.findings):
            _validate_evidence_refs(
                f.evidence_refs, len(self.evidence)
            )