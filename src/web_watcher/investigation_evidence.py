"""Phase 11-A K.2 — Investigation evidence contract.

Defines the immutable data record produced during an investigation run.

This module is a pure data object.  It does not fetch, parse, call AI,
write files, or perform any side effects.  It depends only on the
standard library and on ``investigation_contract.py`` for the
``EvidenceType`` enum.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Evidence type categories
# ---------------------------------------------------------------------------


class EvidenceType(str, Enum):
    """Classification of a piece of investigation evidence."""

    PRIMARY = "primary"
    SECONDARY = "secondary"
    HISTORICAL = "historical"
    DERIVED = "derived"


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_non_empty_string(name: str, value: Any) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be str, got {type(value).__name__}")
    if not value.strip():
        raise ValueError(f"{name} must be non-empty")


def _validate_datetime(name: str, value: Any) -> None:
    if not isinstance(value, datetime):
        raise TypeError(
            f"{name} must be datetime, got {type(value).__name__}"
        )


def _validate_evidence_type(name: str, value: Any) -> None:
    if not isinstance(value, EvidenceType):
        raise TypeError(
            f"{name} must be EvidenceType, got {type(value).__name__}"
        )


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Evidence:
    """Immutable record of a single piece of investigation evidence.

    Invariants enforced in __post_init__:
        * source is a non-empty str
        * url is a non-empty str
        * claim is a non-empty str
        * supporting_text is a non-empty str
        * retrieved_at is a datetime instance
        * evidence_type is an EvidenceType member

    No timezone conversion or auto-defaulting is applied — the value
    provided by the caller is preserved verbatim.
    """

    source: str
    url: str
    retrieved_at: datetime
    claim: str
    supporting_text: str
    evidence_type: EvidenceType

    def __post_init__(self) -> None:
        _validate_non_empty_string("source", self.source)
        _validate_non_empty_string("url", self.url)
        _validate_non_empty_string("claim", self.claim)
        _validate_non_empty_string("supporting_text", self.supporting_text)
        _validate_datetime("retrieved_at", self.retrieved_at)
        _validate_evidence_type("evidence_type", self.evidence_type)