"""Test-only mock AI provider.

This module provides a deterministic, stdlib-only mock provider for unit
tests. It must NOT be imported from production code.
"""

from __future__ import annotations

import json
from typing import Mapping

from web_watcher.ai_contract import (
    ProviderError,
    ProviderResponse,
    ProviderTimeoutError,
)

_VALID_RESPONSE = json.dumps(
    {
        "relevance": 0.85,
        "importance": "important",
        "worth_notifying": True,
        "investigate": False,
        "reason": "mock provider: event appears significant",
        "summary": "mock provider: content change detected",
    }
)


class MockProvider:
    """Deterministic test provider."""

    VALID_RESPONSE = _VALID_RESPONSE

    def __init__(self, scenario: str = "valid") -> None:
        self.scenario = scenario

    def invoke(self, prompt: str, context: Mapping[str, str]) -> ProviderResponse:
        scenario = self.scenario

        if scenario == "valid":
            return ProviderResponse(content=self.VALID_RESPONSE)

        if scenario == "invalid_json":
            return ProviderResponse(content="{not valid json")

        if scenario == "invalid_schema":
            return ProviderResponse(content=json.dumps({"relevance": "wrong_type"}))

        if scenario == "provider_error":
            raise ProviderError("mock provider error")

        if scenario == "timeout":
            raise ProviderTimeoutError("mock provider timeout")

        if scenario == "unsupported_value":
            return ProviderResponse(
                content=json.dumps({
                    "relevance": 0.5,
                    "importance": "super_important",
                    "worth_notifying": True,
                    "investigate": False,
                    "reason": "test",
                    "summary": "test",
                })
            )

        raise ProviderError(f"unknown mock scenario: {scenario!r}")
