"""Test-only mock investigation tools.

These tools are deterministic, offline-only doubles used by unit tests.
They must NOT be imported from production code.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping

from web_watcher.investigation_tools import (
    Tool,
    ToolCapability,
    ToolResult,
    InvestigationTask,
    Evidence,
    EvidenceType,
    _unsupported,
)

# Fixed timestamp used for all mock evidence records.
MOCK_EVIDENCE_TIME = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)


class MockWebFetchTool:
    """Deterministic fake web-fetch Tool."""

    CAPABILITIES = frozenset({ToolCapability.WEB_FETCH})
    SUPPORTED_TASKS = frozenset({InvestigationTask.VERIFY_SOURCE})

    def capabilities(self) -> frozenset[ToolCapability]:
        return self.CAPABILITIES

    def execute(self, task: InvestigationTask, context: Mapping[str, str]) -> ToolResult:
        if task not in self.SUPPORTED_TASKS:
            return _unsupported(task)
        evidence = Evidence(
            source="mock_web_fetch",
            url="https://mock.example.com/fetch",
            retrieved_at=MOCK_EVIDENCE_TIME,
            claim="mock fetched claim",
            supporting_text="mock fetched supporting text",
            evidence_type=EvidenceType.PRIMARY,
        )
        return ToolResult(
            success=True,
            evidence=(evidence,),
            pages_fetched=1,
            message="mock web fetch complete",
        )


class MockWebSearchTool:
    """Deterministic fake web-search Tool."""

    CAPABILITIES = frozenset({ToolCapability.WEB_SEARCH})
    SUPPORTED_TASKS = frozenset({
        InvestigationTask.FETCH_RELATED_SOURCE,
        InvestigationTask.CROSS_CHECK,
    })

    def capabilities(self) -> frozenset[ToolCapability]:
        return self.CAPABILITIES

    def execute(self, task: InvestigationTask, context: Mapping[str, str]) -> ToolResult:
        if task not in self.SUPPORTED_TASKS:
            return _unsupported(task)
        evidence = Evidence(
            source="mock_web_search",
            url="https://mock.example.com/search",
            retrieved_at=MOCK_EVIDENCE_TIME,
            claim="mock search claim",
            supporting_text="mock search supporting text",
            evidence_type=EvidenceType.SECONDARY,
        )
        return ToolResult(
            success=True,
            evidence=(evidence,),
            pages_fetched=0,
            message="mock web search complete",
        )


class MockPageParseTool:
    """Deterministic fake page-parse Tool."""

    CAPABILITIES = frozenset({ToolCapability.PAGE_PARSE})
    SUPPORTED_TASKS = frozenset({InvestigationTask.EXTRACT_EVIDENCE})

    def capabilities(self) -> frozenset[ToolCapability]:
        return self.CAPABILITIES

    def execute(self, task: InvestigationTask, context: Mapping[str, str]) -> ToolResult:
        if task not in self.SUPPORTED_TASKS:
            return _unsupported(task)
        evidence = Evidence(
            source="mock_page_parse",
            url="https://mock.example.com/parse",
            retrieved_at=MOCK_EVIDENCE_TIME,
            claim="mock parsed claim",
            supporting_text="mock parsed supporting text",
            evidence_type=EvidenceType.DERIVED,
        )
        return ToolResult(
            success=True,
            evidence=(evidence,),
            pages_fetched=0,
            message="mock page parse complete",
        )


class MockHistoricalLookupTool:
    """Deterministic fake historical-lookup Tool."""

    CAPABILITIES = frozenset({ToolCapability.HISTORICAL_LOOKUP})
    SUPPORTED_TASKS = frozenset({InvestigationTask.COMPARE_WITH_HISTORY})

    def capabilities(self) -> frozenset[ToolCapability]:
        return self.CAPABILITIES

    def execute(self, task: InvestigationTask, context: Mapping[str, str]) -> ToolResult:
        if task not in self.SUPPORTED_TASKS:
            return _unsupported(task)
        evidence = Evidence(
            source="mock_historical_lookup",
            url="https://mock.example.com/history",
            retrieved_at=MOCK_EVIDENCE_TIME,
            claim="mock historical claim",
            supporting_text="mock historical supporting text",
            evidence_type=EvidenceType.HISTORICAL,
        )
        return ToolResult(
            success=True,
            evidence=(evidence,),
            pages_fetched=0,
            message="mock historical lookup complete",
        )
