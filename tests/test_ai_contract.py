"""Phase 10A — AI Contract tests.

Covers AIContext, AIJudgment, ProviderResponse, AIProvider protocol,
AIJudge orchestration, MockProvider determinism, validation strictness,
immutability, event/policy non-mutation, and security regressions.

No network, no LLM, no external side effects.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from web_watcher.ai_contract import (
    AIContext,
    AIJudge,
    AIJudgment,
    ProviderResponse,
    _parse_provider_json,
    _parse_provider_json_to_judgment,
)

from tests.mock_ai_provider import MockProvider
from web_watcher.ai_errors import (
    AIError,
    InvalidJSONError,
    InvalidResponseError,
    ProviderError,
    ProviderTimeoutError,
    SchemaValidationError,
    UnsupportedValueError,
)
from web_watcher.models import Entity, Event, Signal
from web_watcher.policy import Action, Importance, PolicyDecision


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

UTC = timezone.utc


def _ts(y=2026, m=8, d=17, h=12, mi=0, s=0):
    return datetime(y, m, d, h, mi, s, tzinfo=UTC)


def _event(event_type="new_release", importance="medium") -> Event:
    dt = _ts()
    return Event(
        id=1,
        entity_id=1,
        event_type=event_type,
        status="open",
        importance=importance,
        created_at=dt,
        updated_at=dt,
    )


def _entity() -> Entity:
    return Entity(
        id=1,
        canonical_key="repo:owner/name",
        name="Test Repo",
        entity_type="github_repo",
    )


def _signal() -> Signal:
    return Signal(
        id=1,
        entity_id=1,
        signal_type="content_change",
        observed_at=_ts(),
        value="hash-abc",
        fingerprint="fp-abc",
    )


def _policy_decision() -> PolicyDecision:
    return PolicyDecision(
        importance=Importance.IMPORTANT,
        action=Action.NOTIFY,
        reason="policy reason",
    )


def _context() -> AIContext:
    return AIContext(
        event=_event(),
        policy_decision=_policy_decision(),
        entity=_entity(),
        signals=(_signal(),),
        evidence=("evidence line",),
    )


# ===========================================================================
# A. AIContext — construction and immutability
# ===========================================================================


class TestAIContextConstruction:
    def test_valid_context_creation(self):
        ctx = _context()
        assert ctx.event.id == 1
        assert ctx.policy_decision.importance == Importance.IMPORTANT
        assert ctx.entity.canonical_key == "repo:owner/name"
        assert len(ctx.signals) == 1
        assert ctx.signals[0].signal_type == "content_change"
        assert len(ctx.evidence) == 1
        assert ctx.evidence[0] == "evidence line"

    def test_context_with_no_optional_fields(self):
        ctx = AIContext(event=_event(), policy_decision=_policy_decision())
        assert ctx.entity is None
        assert ctx.signals == ()
        assert ctx.evidence == ()

    def test_context_signals_default_to_empty_tuple(self):
        ctx = AIContext(event=_event(), policy_decision=_policy_decision())
        assert ctx.signals == ()
        assert isinstance(ctx.signals, tuple)

    def test_context_evidence_default_to_empty_tuple(self):
        ctx = AIContext(event=_event(), policy_decision=_policy_decision())
        assert ctx.evidence == ()
        assert isinstance(ctx.evidence, tuple)

    def test_context_immutability(self):
        ctx = _context()
        with pytest.raises(Exception):
            ctx.event = _event("other")
        with pytest.raises(Exception):
            ctx.entity = _entity()
        with pytest.raises(Exception):
            ctx.evidence += ("more",)


# ===========================================================================
# B. AIJudgment — construction and immutability
# ===========================================================================


class TestAIJudgmentConstruction:
    def test_valid_judgment(self):
        j = AIJudgment(
            relevance=0.75,
            importance=Importance.IMPORTANT,
            worth_notifying=True,
            investigate=False,
            reason="significant change",
            summary="summary text",
        )
        assert j.relevance == 0.75
        assert j.importance == Importance.IMPORTANT
        assert j.worth_notifying is True
        assert j.investigate is False
        assert j.reason == "significant change"
        assert j.summary == "summary text"

    def test_judgment_immutability(self):
        j = AIJudgment(
            relevance=0.5,
            importance=Importance.IGNORE,
            worth_notifying=False,
            investigate=False,
            reason="r",
            summary="s",
        )
        with pytest.raises(Exception):
            j.relevance = 0.99
        with pytest.raises(Exception):
            j.reason = "changed"

    def test_relevance_lower_boundary_inclusive(self):
        j = AIJudgment(
            relevance=0.0,
            importance=Importance.IGNORE,
            worth_notifying=False,
            investigate=False,
            reason="r",
            summary="s",
        )
        assert j.relevance == 0.0

    def test_relevance_upper_boundary_inclusive(self):
        j = AIJudgment(
            relevance=1.0,
            importance=Importance.CRITICAL,
            worth_notifying=True,
            investigate=True,
            reason="r",
            summary="s",
        )
        assert j.relevance == 1.0

    def test_relevance_below_zero_rejected(self):
        with pytest.raises(UnsupportedValueError):
            AIJudgment(
                relevance=-0.01,
                importance=Importance.IGNORE,
                worth_notifying=False,
                investigate=False,
                reason="r",
                summary="s",
            )

    def test_relevance_above_one_rejected(self):
        with pytest.raises(UnsupportedValueError):
            AIJudgment(
                relevance=1.01,
                importance=Importance.IGNORE,
                worth_notifying=False,
                investigate=False,
                reason="r",
                summary="s",
            )

    def test_relevance_int_rejected_not_coerced(self):
        with pytest.raises(SchemaValidationError):
            AIJudgment(
                relevance=1,  # int, not float
                importance=Importance.IGNORE,
                worth_notifying=False,
                investigate=False,
                reason="r",
                summary="s",
            )

    def test_invalid_importance_rejected(self):
        with pytest.raises(SchemaValidationError):
            AIJudgment(
                relevance=0.5,
                importance="not_an_enum",  # type: ignore[arg-type]
                worth_notifying=False,
                investigate=False,
                reason="r",
                summary="s",
            )

    def test_empty_reason_rejected(self):
        with pytest.raises(SchemaValidationError):
            AIJudgment(
                relevance=0.5,
                importance=Importance.IGNORE,
                worth_notifying=False,
                investigate=False,
                reason="",
                summary="s",
            )

    def test_whitespace_reason_rejected(self):
        with pytest.raises(SchemaValidationError):
            AIJudgment(
                relevance=0.5,
                importance=Importance.IGNORE,
                worth_notifying=False,
                investigate=False,
                reason="   ",
                summary="s",
            )

    def test_empty_summary_rejected(self):
        with pytest.raises(SchemaValidationError):
            AIJudgment(
                relevance=0.5,
                importance=Importance.IGNORE,
                worth_notifying=False,
                investigate=False,
                reason="r",
                summary="",
            )

    def test_bool_int_not_accepted_for_worth_notifying(self):
        with pytest.raises(SchemaValidationError):
            AIJudgment(
                relevance=0.5,
                importance=Importance.IGNORE,
                worth_notifying=1,  # type: ignore[arg-type]
                investigate=False,
                reason="r",
                summary="s",
            )

    def test_bool_int_not_accepted_for_investigate(self):
        with pytest.raises(SchemaValidationError):
            AIJudgment(
                relevance=0.5,
                importance=Importance.IGNORE,
                worth_notifying=False,
                investigate=0,  # type: ignore[arg-type]
                reason="r",
                summary="s",
            )


# ===========================================================================
# C. Structured response parsing
# ===========================================================================


class TestStructuredParsing:
    def _make_valid_json(self, **overrides) -> dict[str, Any]:
        base = {
            "relevance": 0.75,
            "importance": "important",
            "worth_notifying": True,
            "investigate": False,
            "reason": "parsed reason",
            "summary": "parsed summary",
        }
        base.update(overrides)
        return base

    def test_valid_json_parsed_to_judgment(self):
        data = self._make_valid_json()
        j = _parse_provider_json_to_judgment(data)
        assert j.relevance == 0.75
        assert j.importance == Importance.IMPORTANT
        assert j.worth_notifying is True
        assert j.investigate is False
        assert j.reason == "parsed reason"
        assert j.summary == "parsed summary"

    def test_missing_field_rejection(self):
        data = self._make_valid_json()
        del data["relevance"]
        with pytest.raises(SchemaValidationError):
            _parse_provider_json_to_judgment(data)

    def test_missing_importance_rejection(self):
        data = self._make_valid_json()
        del data["importance"]
        with pytest.raises(SchemaValidationError):
            _parse_provider_json_to_judgment(data)

    def test_missing_worth_notifying_rejection(self):
        data = self._make_valid_json()
        del data["worth_notifying"]
        with pytest.raises(SchemaValidationError):
            _parse_provider_json_to_judgment(data)

    def test_missing_investigate_rejection(self):
        data = self._make_valid_json()
        del data["investigate"]
        with pytest.raises(SchemaValidationError):
            _parse_provider_json_to_judgment(data)

    def test_missing_reason_rejection(self):
        data = self._make_valid_json()
        del data["reason"]
        with pytest.raises(SchemaValidationError):
            _parse_provider_json_to_judgment(data)

    def test_missing_summary_rejection(self):
        data = self._make_valid_json()
        del data["summary"]
        with pytest.raises(SchemaValidationError):
            _parse_provider_json_to_judgment(data)

    def test_wrong_type_relevance_rejection(self):
        data = self._make_valid_json(relevance="high")
        with pytest.raises(SchemaValidationError):
            _parse_provider_json_to_judgment(data)

    def test_wrong_type_importance_rejection(self):
        data = self._make_valid_json(importance=42)
        with pytest.raises(SchemaValidationError):
            _parse_provider_json_to_judgment(data)

    def test_wrong_type_reason_rejection(self):
        data = self._make_valid_json(reason=123)
        with pytest.raises(SchemaValidationError):
            _parse_provider_json_to_judgment(data)

    def test_wrong_type_summary_rejection(self):
        data = self._make_valid_json(summary=None)
        with pytest.raises(SchemaValidationError):
            _parse_provider_json_to_judgment(data)

    def test_invalid_importance_value_rejected(self):
        data = self._make_valid_json(importance="mega_important")
        with pytest.raises(UnsupportedValueError):
            _parse_provider_json_to_judgment(data)

    def test_relevance_out_of_range_in_json_rejected(self):
        data = self._make_valid_json(relevance=1.5)
        with pytest.raises(UnsupportedValueError):
            _parse_provider_json_to_judgment(data)

    def test_empty_reason_in_json_rejected(self):
        data = self._make_valid_json(reason="")
        with pytest.raises(SchemaValidationError):
            _parse_provider_json_to_judgment(data)

    def test_empty_summary_in_json_rejected(self):
        data = self._make_valid_json(summary="")
        with pytest.raises(SchemaValidationError):
            _parse_provider_json_to_judgment(data)

    def test_non_string_json_root_rejected(self):
        with pytest.raises(InvalidResponseError):
            _parse_provider_json('[1, 2, 3]')

    def test_number_json_root_rejected(self):
        with pytest.raises(InvalidResponseError):
            _parse_provider_json('42')

    def test_string_json_root_rejected(self):
        with pytest.raises(InvalidResponseError):
            _parse_provider_json('"hello"')

    def test_invalid_json_rejection(self):
        with pytest.raises(InvalidJSONError):
            _parse_provider_json("{not valid}")

    def test_empty_string_rejected(self):
        with pytest.raises(InvalidJSONError):
            _parse_provider_json("")


# ===========================================================================
# D. AIJudge orchestration
# ===========================================================================


class TestAIJudge:
    def test_valid_mock_provider_returns_judgment(self):
        provider = MockProvider(scenario="valid")
        judge = AIJudge(provider)
        j = judge.judge(_context())
        assert isinstance(j, AIJudgment)
        assert j.relevance == 0.85
        assert j.importance == Importance.IMPORTANT
        assert j.worth_notifying is True

    def test_provider_error_propagates(self):
        provider = MockProvider(scenario="provider_error")
        judge = AIJudge(provider)
        with pytest.raises(ProviderError):
            judge.judge(_context())

    def test_timeout_propagates(self):
        provider = MockProvider(scenario="timeout")
        judge = AIJudge(provider)
        with pytest.raises(ProviderTimeoutError):
            judge.judge(_context())

    def test_invalid_json_from_provider_raises_invalid_json_error(self):
        provider = MockProvider(scenario="invalid_json")
        judge = AIJudge(provider)
        with pytest.raises(InvalidJSONError):
            judge.judge(_context())

    def test_invalid_schema_from_provider_raises_schema_error(self):
        provider = MockProvider(scenario="invalid_schema")
        judge = AIJudge(provider)
        with pytest.raises(SchemaValidationError):
            judge.judge(_context())

    def test_unknown_provider_exception_wrapped_as_provider_error(self):
        class RaisingProvider:
            def invoke(self, prompt, context):
                raise RuntimeError("unexpected")

        judge = AIJudge(RaisingProvider())  # type: ignore[arg-type]
        with pytest.raises(ProviderError):
            judge.judge(_context())

    def test_event_not_mutated_by_judge(self):
        event = _event()
        before = (
            event.id,
            event.entity_id,
            event.event_type,
            event.status,
            event.importance,
            event.created_at,
            event.updated_at,
        )
        ctx = AIContext(event=event, policy_decision=_policy_decision())
        judge = AIJudge(MockProvider(scenario="valid"))
        judge.judge(ctx)
        after = (
            event.id,
            event.entity_id,
            event.event_type,
            event.status,
            event.importance,
            event.created_at,
            event.updated_at,
        )
        assert after == before

    def test_policy_decision_not_mutated_by_judge(self):
        pd = _policy_decision()
        before = (pd.importance, pd.action, pd.reason)
        ctx = AIContext(event=_event(), policy_decision=pd)
        judge = AIJudge(MockProvider(scenario="valid"))
        judge.judge(ctx)
        after = (pd.importance, pd.action, pd.reason)
        assert after == before


# ===========================================================================
# E. MockProvider determinism and scenarios
# ===========================================================================


class TestMockProvider:
    def test_valid_scenario_returns_fixed_content(self):
        provider = MockProvider(scenario="valid")
        resp = provider.invoke("prompt", {})
        assert resp.content == MockProvider.VALID_RESPONSE
        assert isinstance(resp.content, str)
        assert len(resp.content) > 0

    def test_deterministic_same_scenario_same_output(self):
        provider = MockProvider(scenario="valid")
        r1 = provider.invoke("p1", {})
        r2 = provider.invoke("p2", {})  # different prompt
        assert r1.content == r2.content == MockProvider.VALID_RESPONSE

    def test_invalid_json_scenario(self):
        provider = MockProvider(scenario="invalid_json")
        resp = provider.invoke("p", {})
        assert not resp.content.startswith("{") or "}" not in resp.content

    def test_invalid_schema_scenario_is_valid_json_but_bad_types(self):
        provider = MockProvider(scenario="invalid_schema")
        resp = provider.invoke("p", {})
        import json

        data = json.loads(resp.content)
        assert isinstance(data, dict)
        assert not isinstance(data.get("relevance"), float)

    def test_provider_error_scenario_raises(self):
        provider = MockProvider(scenario="provider_error")
        with pytest.raises(ProviderError):
            provider.invoke("p", {})

    def test_timeout_scenario_raises(self):
        provider = MockProvider(scenario="timeout")
        with pytest.raises(ProviderTimeoutError):
            provider.invoke("p", {})

    def test_unknown_scenario_raises_provider_error(self):
        provider = MockProvider(scenario="unknown_scenario")
        with pytest.raises(ProviderError):
            provider.invoke("p", {})

    def test_same_valid_input_produces_same_judgment_via_judge(self):
        ctx1 = AIContext(event=_event(), policy_decision=_policy_decision())
        ctx2 = AIContext(event=_event(), policy_decision=_policy_decision())
        judge = AIJudge(MockProvider(scenario="valid"))
        j1 = judge.judge(ctx1)
        j2 = judge.judge(ctx2)
        assert j1 == j2


# ===========================================================================
# F. ProviderResponse
# ===========================================================================


class TestProviderResponse:
    def test_response_creation_with_content_only(self):
        resp = ProviderResponse(content='{"a": 1}')
        assert resp.content == '{"a": 1}'
        assert resp.metadata == {}

    def test_response_creation_with_metadata(self):
        resp = ProviderResponse(content="x", metadata={"key": "val"})
        assert resp.metadata["key"] == "val"

    def test_response_immutability(self):
        resp = ProviderResponse(content="x")
        with pytest.raises(Exception):
            resp.content = "y"


# ===========================================================================
# G. Error model hierarchy
# ===========================================================================


class TestErrorModel:
    def test_invalid_json_is_invalid_response_is_ai_error(self):
        err = InvalidJSONError("bad")
        assert isinstance(err, InvalidResponseError)
        assert isinstance(err, AIError)

    def test_schema_validation_is_invalid_response_is_ai_error(self):
        err = SchemaValidationError("bad")
        assert isinstance(err, InvalidResponseError)
        assert isinstance(err, AIError)

    def test_unsupported_value_is_schema_is_invalid_response_is_ai(self):
        err = UnsupportedValueError("bad")
        assert isinstance(err, SchemaValidationError)
        assert isinstance(err, InvalidResponseError)
        assert isinstance(err, AIError)

    def test_provider_error_is_ai_error(self):
        err = ProviderError("bad")
        assert isinstance(err, AIError)

    def test_provider_timeout_is_ai_error(self):
        err = ProviderTimeoutError("bad")
        assert isinstance(err, AIError)


# ===========================================================================
# H. Security / side-effect regression checks
# ===========================================================================


class TestSecurityRegression:
    def test_no_forbidden_imports_in_ai_contract(self):
        import ast
        from pathlib import Path

        src = Path(__file__).resolve().parents[1] / "src" / "web_watcher" / "ai_contract.py"
        tree = ast.parse(src.read_text())
        imported_modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_modules.append(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module.split(".")[0])

        forbidden = {
            "requests", "httpx", "urllib", "aiohttp", "http.client",
            "openai", "anthropic", "google", "google_generativeai",
            "telegram", "selenium", "playwright", "subprocess",
            "os", "sys", "socket", "http",
        }
        assert not forbidden.intersection(imported_modules), (
            f"forbidden imports found: {forbidden.intersection(imported_modules)}"
        )

    def test_no_forbidden_imports_in_ai_errors(self):
        import ast
        from pathlib import Path

        src = Path(__file__).resolve().parents[1] / "src" / "web_watcher" / "ai_errors.py"
        tree = ast.parse(src.read_text())
        imported_modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_modules.append(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module.split(".")[0])

        forbidden = {
            "requests", "httpx", "urllib", "aiohttp", "http.client",
            "openai", "anthropic", "telegram", "subprocess",
            "os", "sys", "socket", "http", "os.path",
        }
        assert not forbidden.intersection(imported_modules), (
            f"forbidden imports found: {forbidden.intersection(imported_modules)}"
        )

    def test_no_secret_or_key_references_in_ai_contract_source(self):
        """Check for dangerous secret-access *code patterns*, not docstring mentions."""
        from pathlib import Path

        src = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "web_watcher"
            / "ai_contract.py"
        )
        text = src.read_text()

        # Dangerous code patterns — these would indicate actual secret access
        forbidden_patterns = [
            "os.environ", "os.getenv", "getenv(",
            "API_KEY", "api_key", "APIKEY",
            "SECRET_KEY", "secret_key",
            "Authorization:", "Authorization = ",
            'Authorization: Bearer', "Bearer token",
            "PRIVATE_KEY", "private_key",
            "password", "PASSWORD",
        ]
        for pattern in forbidden_patterns:
            assert pattern not in text, (
                f"dangerous secret-access code pattern found in ai_contract.py: {pattern!r}"
            )

    def test_no_secret_or_key_references_in_ai_errors_source(self):
        from pathlib import Path

        src = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "web_watcher"
            / "ai_errors.py"
        )
        text = src.read_text()
        forbidden_patterns = [
            "os.environ", "os.getenv",
            "API_KEY", "api_key", "SECRET", "secret", "TOKEN", "token",
            "Authorization", "password", "PASSWORD",
        ]
        for pattern in forbidden_patterns:
            assert pattern not in text, (
                f"dangerous secret-access code pattern found in ai_errors.py: {pattern!r}"
            )

    def test_provider_headers_contain_no_secrets(self):
        ctx = _context()
        from web_watcher.ai_contract import _build_context_headers

        headers = _build_context_headers(ctx)
        assert headers == {}
        assert "Authorization" not in headers
        assert "api_key" not in headers
        assert "token" not in headers

    def test_no_shell_or_subprocess_in_ai_contract(self):
        from pathlib import Path

        src = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "web_watcher"
            / "ai_contract.py"
        )
        text = src.read_text()
        forbidden_calls = ["subprocess.", "os.system", "os.popen", "eval(", "exec("]
        for call in forbidden_calls:
            assert call not in text, f"forbidden call found: {call}"
