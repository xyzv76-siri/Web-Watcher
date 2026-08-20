"""Phase 11-A K.4 — Investigation Tool Layer.

Defines the passive, deterministic, offline-only mock tool contract and
its four concrete mock implementations.

This module is stdlib-only.  It contains no network access, persistence,
LLM calls, planning logic, budget enforcement, or engine state.  All
tool executions return deterministic ToolResult values for identical
inputs.

Dependencies (K.4 architecture boundary):
    - K.1:  InvestigationTask, ToolCapability
    - K.2:  Evidence, EvidenceType

Forbidden imports (per K.4 architecture §3):
    - requests, httpx, urllib, urllib3, socket, subprocess
    - ai_contract, decide, final_decision, llm_provider
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Protocol

from web_watcher.investigation_contract import (
    InvestigationTask,
    ToolCapability,
)
from web_watcher.investigation_evidence import (
    Evidence,
    EvidenceType,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# No mock constants in production code. Test doubles live in tests/mock_investigation_tools.py.


# ---------------------------------------------------------------------------
# ToolResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolResult:
    """Immutable result of one Tool execution.

    Invariants enforced in ``__post_init__``:
        * success is a bool
        * evidence is a tuple of Evidence instances
        * pages_fetched is a non-negative int
        * message is a non-empty str

    No mutable internal state is retained.
    """

    success: bool
    evidence: tuple[Evidence, ...]
    pages_fetched: int
    message: str

    def __post_init__(self) -> None:
        # success
        if not isinstance(self.success, bool):
            raise TypeError(
                f"success must be bool, got {type(self.success).__name__}"
            )
        # evidence
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
        # pages_fetched
        if isinstance(self.pages_fetched, bool) or not isinstance(
            self.pages_fetched, int
        ):
            raise TypeError(
                f"pages_fetched must be int, got "
                f"{type(self.pages_fetched).__name__}"
            )
        if self.pages_fetched < 0:
            raise ValueError(
                f"pages_fetched must be >= 0, got {self.pages_fetched}"
            )
        # message
        if not isinstance(self.message, str):
            raise TypeError(
                f"message must be str, got {type(self.message).__name__}"
            )
        if not self.message.strip():
            raise ValueError("message must be non-empty")


# ---------------------------------------------------------------------------
# Tool Protocol
# ---------------------------------------------------------------------------


class Tool(Protocol):
    """Passive Tool protocol for investigation tool execution.

    ``capabilities()`` must return a deterministic frozenset of
    ``ToolCapability`` values that does not depend on execution state.

    ``execute()`` must return a ``ToolResult`` and must not mutate
    ``context`` or retain mutable references to it.
    """

    def capabilities(self) -> frozenset[ToolCapability]:
        ...

    def execute(
        self,
        task: InvestigationTask,
        context: Mapping[str, str],
    ) -> ToolResult:
        ...


# ---------------------------------------------------------------------------
# Helper: deterministic unsupported-task result
# ---------------------------------------------------------------------------


def _unsupported(task: InvestigationTask) -> ToolResult:
    """Return the canonical failure ToolResult for an unsupported task.

    This is the shape mandated by the K.4 architecture freeze (§6.2):
        success=False, evidence=(), pages_fetched=0,
        message="unsupported task: <task_value>"
    """

    return ToolResult(
        success=False,
        evidence=(),
        pages_fetched=0,
        message=f"unsupported task: {task.value}",
    )
