"""Phase 11-A K.1 — Investigation contract.

Defines the core contracts that every investigation module depends on:
task categories, tool capabilities, budget policy, and a dedicated AI
planning provider protocol.

This module is stdlib-only, network-free, immutable, and contains no
side effects.  It does not import or modify any Phase 10 module.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Protocol


# ---------------------------------------------------------------------------
# Investigation task categories
# ---------------------------------------------------------------------------


class InvestigationTask(str, Enum):
    """Allowed investigation task categories.

    These categories are the only tasks that an InvestigationPlanner may
    propose.  Any task outside this enum is forbidden and must be
    rejected before execution.
    """

    VERIFY_SOURCE = "verify_source"
    FETCH_RELATED_SOURCE = "fetch_related_source"
    COMPARE_WITH_HISTORY = "compare_with_history"
    EXTRACT_EVIDENCE = "extract_evidence"
    CROSS_CHECK = "cross_check"


# ---------------------------------------------------------------------------
# Tool capabilities
# ---------------------------------------------------------------------------


class ToolCapability(str, Enum):
    """Capabilities a Tool may advertise.

    A Tool declares which capabilities it supports so that the Planner
    can match a proposed InvestigationTask to an appropriate Tool.
    """

    WEB_FETCH = "web_fetch"
    WEB_SEARCH = "web_search"
    PAGE_PARSE = "page_parse"
    HISTORICAL_LOOKUP = "historical_lookup"


# ---------------------------------------------------------------------------
# Investigation policy (budget)
# ---------------------------------------------------------------------------


class PolicyValidationError(ValueError):
    """Raised when an InvestigationPolicy field is outside its allowed range."""


def _validate_max_steps(value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise PolicyValidationError(f"max_steps must be int, got {type(value).__name__}")
    if value < 1:
        raise PolicyValidationError(f"max_steps must be >= 1, got {value}")


def _validate_max_pages(value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise PolicyValidationError(f"max_pages must be int, got {type(value).__name__}")
    if value < 0:
        raise PolicyValidationError(f"max_pages must be >= 0, got {value}")


def _validate_timeout_seconds(value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PolicyValidationError(
            f"timeout_seconds must be numeric, got {type(value).__name__}"
        )
    if value <= 0:
        raise PolicyValidationError(
            f"timeout_seconds must be > 0, got {value}"
        )


@dataclass(frozen=True)
class InvestigationPolicy:
    """Immutable budget and timeout constraints for an investigation run.

    Invariants enforced in __post_init__:
        * max_steps >= 1
        * max_pages >= 0
        * timeout_seconds > 0
    """

    max_steps: int = 5
    max_pages: int = 10
    timeout_seconds: float = 60.0

    def __post_init__(self) -> None:
        _validate_max_steps(self.max_steps)
        _validate_max_pages(self.max_pages)
        _validate_timeout_seconds(self.timeout_seconds)


# ---------------------------------------------------------------------------
# AI planning provider (Investigation-specific)
# ---------------------------------------------------------------------------


class ToolProvider(Protocol):
    """Dedicated AI planning provider for Investigation Planning.

    This Protocol is Investigation-specific — it does NOT reuse
    ``AIProvider`` from ``ai_contract.py``.  A ``ToolProvider`` may
    suggest the next ``InvestigationTask`` given context about the
    current investigation state.

    The caller (InvestigationPlanner) is responsible for validating
    any suggested task: capability existence, budget availability,
    authorization, and state validity.  The provider never executes
    or directly constructs a Tool.

    ``context`` is a read-only mapping of non-sensitive investigation
    metadata (event type, remaining budget, already-collected evidence
    hints, etc.).  It must never contain secrets, API keys, or raw
    response bodies.
    """

    def suggest_task(
        self,
        context: Mapping[str, str],
    ) -> InvestigationTask:
        ...