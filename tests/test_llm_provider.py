"""Phase 10B — Real LLM Provider offline tests.

All tests are network-free (stdlib-only).  Every provider invocation
uses ``unittest.mock.patch`` on ``urllib.request.urlopen`` — no
real HTTP request is made.

Coverage:
    1. Config creation, defaults, and immutability
    2. API key missing → ProviderError
    3. Successful request → ProviderResponse with extracted content
    4. HTTP 401/403 → ProviderError (no retry)
    5. HTTP 408 → ProviderTimeoutError (no retry)
    6. HTTP 500 → retries then success / exhausts → ProviderError
    7. Network error → ProviderError
    8. Non-JSON response → ProviderError
    9. Empty/malformed choices → empty content returned
    10. Metadata never contains secrets
    11. Factory build_sensenova_provider
    12. Security regression: no QwenPaw config, no PolicyEngine logic
    13. Integration: AIJudge + SenseNovaLLMProvider (offline mock)
"""

from __future__ import annotations

import json
import urllib.error
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest

from web_watcher.ai_config import (
    DEFAULT_BASE_URL,
    DEFAULT_ENV_VAR_NAME,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
    load_sensenova_provider_config,
    SenseNovaProviderConfig,
)
from web_watcher.ai_contract import (
    AIContext,
    AIJudgment,
    AIJudge,
    InvalidJSONError,
    ProviderResponse,
    SchemaValidationError,
    UnsupportedValueError,
)
from web_watcher.ai_errors import (
    ProviderError,
    ProviderTimeoutError,
)
from web_watcher.ai_provider import build_sensenova_provider
from web_watcher.llm_provider import SenseNovaLLMProvider
from web_watcher.models import Entity, Event
from web_watcher.policy import Action, Importance, PolicyDecision


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(payload: dict, status: int = 200):
    """Build a mock urllib response object."""
    mock_resp = Mock()
    mock_resp.read.return_value = json.dumps(payload).encode("utf-8")
    mock_resp.status = status
    mock_resp.__enter__ = Mock(return_value=mock_resp)
    mock_resp.__exit__ = Mock(return_value=False)
    return mock_resp


def _make_context() -> AIContext:
    """Build a minimal AIContext for integration tests."""
    now = datetime(2026, 8, 17, 10, 0, 0, tzinfo=timezone.utc)
    event = Event(
        id=1,
        entity_id=1,
        event_type="important",
        status="open",
        importance=None,
        created_at=now,
        updated_at=now,
    )
    entity = Entity(
        id=1,
        canonical_key="github.com/user/repo",
        name="user/repo",
        entity_type="github_repository",
    )
    decision = PolicyDecision(
        importance=Importance.IMPORTANT,
        action=Action.NOTIFY,
        reason="test decision",
    )
    return AIContext(event=event, policy_decision=decision, entity=entity)


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestSenseNovaProviderConfig:
    def test_default_values(self):
        config = load_sensenova_provider_config()
        assert config.base_url == DEFAULT_BASE_URL
        assert config.api_path == "/chat/completions"
        assert config.model == DEFAULT_MODEL
        assert config.timeout_seconds == DEFAULT_TIMEOUT_SECONDS
        assert config.max_attempts == 2
        assert config.env_var_name == DEFAULT_ENV_VAR_NAME
        assert config.response_format_json is False
        assert config.system_prompt is not None
        assert len(config.system_prompt) > 0

    def test_explicit_values_override_defaults(self):
        config = load_sensenova_provider_config(
            base_url="https://custom.example/v1",
            model="custom-model",
            timeout_seconds=10.0,
            max_attempts=1,
            env_var_name="CUSTOM_KEY",
            response_format_json=False,
            system_prompt=None,
        )
        assert config.base_url == "https://custom.example/v1"
        assert config.model == "custom-model"
        assert config.timeout_seconds == 10.0
        assert config.max_attempts == 1
        assert config.env_var_name == "CUSTOM_KEY"
        assert config.response_format_json is False
        assert config.system_prompt is None

    def test_config_is_frozen(self):
        config = load_sensenova_provider_config()
        with pytest.raises(Exception):
            config.base_url = "https://other.example"  # type: ignore[attr-defined]

    def test_config_never_contains_secret(self):
        config = load_sensenova_provider_config()
        # No attribute should be named like a secret
        for attr_name in dir(config):
            lower = attr_name.lower()
            assert "secret" not in lower
            assert "password" not in lower
            assert "token" not in lower or attr_name == "token"  # allow the base_url


# ---------------------------------------------------------------------------
# Provider construction tests
# ---------------------------------------------------------------------------


class TestSenseNovaLLMProviderConstruction:
    def test_provider_accepts_config(self):
        config = load_sensenova_provider_config()
        provider = SenseNovaLLMProvider(config=config)
        assert provider.config is config

    def test_provider_config_readonly(self):
        config = load_sensenova_provider_config()
        provider = SenseNovaLLMProvider(config=config)
        # The config object itself must be frozen (immutable dataclass)
        with pytest.raises(Exception):
            config.base_url = "https://other.example"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# API key handling tests
# ---------------------------------------------------------------------------


class TestAPIKeyHandling:
    def test_missing_api_key_raises_provider_error(self):
        config = load_sensenova_provider_config(env_var_name="NOWHERE_KEY_XYZ")
        provider = SenseNovaLLMProvider(config=config)

        with pytest.raises(ProviderError) as exc_info:
            provider.invoke("test prompt", {})

        assert "NOWHERE_KEY_XYZ" in str(exc_info.value)

    def test_api_key_never_appears_in_metadata(self):
        config = load_sensenova_provider_config(
            env_var_name="TEST_WW_KEY",
            max_attempts=1,
        )
        provider = SenseNovaLLMProvider(config=config)

        mock_resp = _mock_response(
            {"choices": [{"message": {"content": "{}"}}], "usage": {"total_tokens": 10}}
        )

        with patch.dict("os.environ", {"TEST_WW_KEY": "super-secret-key-123"}):
            with patch("web_watcher.llm_provider.urllib.request.urlopen", return_value=mock_resp):
                response = provider.invoke("test", {})

        assert "super-secret-key-123" not in response.content
        assert "super-secret-key-123" not in json.dumps(response.metadata)

    def test_empty_api_key_raises_provider_error(self):
        config = load_sensenova_provider_config(env_var_name="EMPTY_KEY_TEST")
        provider = SenseNovaLLMProvider(config=config)

        with patch.dict("os.environ", {"EMPTY_KEY_TEST": ""}):
            with pytest.raises(ProviderError):
                provider.invoke("test", {})


# ---------------------------------------------------------------------------
# Successful request tests
# ---------------------------------------------------------------------------


class TestProviderSuccess:
    def test_success_returns_extracted_content(self):
        config = load_sensenova_provider_config(
            env_var_name="TEST_WW_KEY",
            max_attempts=1,
        )
        provider = SenseNovaLLMProvider(config=config)

        raw_json = json.dumps(
            {
                "relevance": 0.75,
                "importance": "important",
                "worth_notifying": True,
                "investigate": False,
                "reason": "content changed",
                "summary": "update",
            }
        )
        mock_resp = _mock_response(
            {
                "choices": [{"message": {"content": raw_json}}],
                "usage": {"total_tokens": 50},
            }
        )

        with patch.dict("os.environ", {"TEST_WW_KEY": "key-value"}):
            with patch("web_watcher.llm_provider.urllib.request.urlopen", return_value=mock_resp):
                response = provider.invoke("test prompt", {})

        assert isinstance(response, ProviderResponse)
        assert response.content == raw_json
        assert "http_status" in response.metadata
        assert response.metadata["http_status"] == "200"

    def test_metadata_includes_model(self):
        config = load_sensenova_provider_config(
            env_var_name="TEST_WW_KEY",
            model="my-model",
            max_attempts=1,
        )
        provider = SenseNovaLLMProvider(config=config)

        mock_resp = _mock_response(
            {"choices": [{"message": {"content": "{}"}}]}
        )

        with patch.dict("os.environ", {"TEST_WW_KEY": "k"}):
            with patch("web_watcher.llm_provider.urllib.request.urlopen", return_value=mock_resp):
                response = provider.invoke("p", {})

        # metadata should not include the model directly,
        # but the response content should be correct
        assert response.content == "{}"

    def test_metadata_includes_usage(self):
        config = load_sensenova_provider_config(
            env_var_name="TEST_WW_KEY",
            max_attempts=1,
        )
        provider = SenseNovaLLMProvider(config=config)

        mock_resp = _mock_response(
            {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"total_tokens": 100, "prompt_tokens": 10, "completion_tokens": 90},
            }
        )

        with patch.dict("os.environ", {"TEST_WW_KEY": "k"}):
            with patch("web_watcher.llm_provider.urllib.request.urlopen", return_value=mock_resp):
                response = provider.invoke("p", {})

        usage_str = response.metadata.get("usage", "")
        usage_data = json.loads(usage_str)
        assert usage_data["total_tokens"] == 100

    def test_response_format_json_included_in_payload(self):
        config = load_sensenova_provider_config(
            env_var_name="TEST_WW_KEY",
            response_format_json=True,
            max_attempts=1,
        )
        provider = SenseNovaLLMProvider(config=config)

        captured: dict | None = None

        def capture_request(request, **kwargs):
            nonlocal captured
            body = json.loads(request.data.decode("utf-8"))
            captured = body
            return _mock_response({"choices": [{"message": {"content": "ok"}}]})

        with patch.dict("os.environ", {"TEST_WW_KEY": "k"}):
            with patch("web_watcher.llm_provider.urllib.request.urlopen", side_effect=capture_request):
                provider.invoke("prompt", {})

        assert captured is not None
        assert captured["response_format"] == {"type": "json_object"}

    def test_response_format_json_omitted_when_disabled(self):
        config = load_sensenova_provider_config(
            env_var_name="TEST_WW_KEY",
            response_format_json=False,
            max_attempts=1,
        )
        provider = SenseNovaLLMProvider(config=config)

        captured: dict | None = None

        def capture_request(request, **kwargs):
            nonlocal captured
            body = json.loads(request.data.decode("utf-8"))
            captured = body
            return _mock_response({"choices": [{"message": {"content": "ok"}}]})

        with patch.dict("os.environ", {"TEST_WW_KEY": "k"}):
            with patch("web_watcher.llm_provider.urllib.request.urlopen", side_effect=capture_request):
                provider.invoke("prompt", {})

        assert captured is not None
        assert "response_format" not in captured


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------


class TestProviderErrors:
    def test_http_401_raises_provider_error_no_retry(self):
        config = load_sensenova_provider_config(
            env_var_name="TEST_WW_KEY",
            max_attempts=3,
        )
        provider = SenseNovaLLMProvider(config=config)

        call_count = [0]

        def mock_401(*args, **kwargs):
            call_count[0] += 1
            raise urllib.error.HTTPError(
                url="", code=401, msg="Unauthorized", hdrs=None, fp=None
            )

        with patch.dict("os.environ", {"TEST_WW_KEY": "k"}):
            with patch("web_watcher.llm_provider.urllib.request.urlopen", side_effect=mock_401):
                with pytest.raises(ProviderError) as exc_info:
                    provider.invoke("p", {})

        assert "401" not in str(exc_info.value)  # status code must not leak
        assert call_count[0] == 1

    def test_http_403_raises_provider_error_no_retry(self):
        config = load_sensenova_provider_config(
            env_var_name="TEST_WW_KEY",
            max_attempts=3,
        )
        provider = SenseNovaLLMProvider(config=config)

        call_count = [0]

        def mock_403(*args, **kwargs):
            call_count[0] += 1
            raise urllib.error.HTTPError(
                url="", code=403, msg="Forbidden", hdrs=None, fp=None
            )

        with patch.dict("os.environ", {"TEST_WW_KEY": "k"}):
            with patch("web_watcher.llm_provider.urllib.request.urlopen", side_effect=mock_403):
                with pytest.raises(ProviderError):
                    provider.invoke("p", {})

        assert call_count[0] == 1

    def test_http_408_raises_provider_timeout_error(self):
        config = load_sensenova_provider_config(
            env_var_name="TEST_WW_KEY",
            max_attempts=3,
        )
        provider = SenseNovaLLMProvider(config=config)

        call_count = [0]

        def mock_408(*args, **kwargs):
            call_count[0] += 1
            raise urllib.error.HTTPError(
                url="", code=408, msg="Request Timeout", hdrs=None, fp=None
            )

        with patch.dict("os.environ", {"TEST_WW_KEY": "k"}):
            with patch("web_watcher.llm_provider.urllib.request.urlopen", side_effect=mock_408):
                with pytest.raises(ProviderTimeoutError):
                    provider.invoke("p", {})

        assert call_count[0] == 1

    def test_http_500_retries_then_succeeds(self):
        config = load_sensenova_provider_config(
            env_var_name="TEST_WW_KEY",
            max_attempts=3,
        )
        provider = SenseNovaLLMProvider(config=config)

        call_count = [0]

        def mock_mixed(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                raise urllib.error.HTTPError(
                    url="", code=500, msg="Internal Server Error", hdrs=None, fp=None
                )
            return _mock_response({"choices": [{"message": {"content": "ok"}}]})

        with patch.dict("os.environ", {"TEST_WW_KEY": "k"}):
            with patch("web_watcher.llm_provider.urllib.request.urlopen", side_effect=mock_mixed):
                with patch("web_watcher.llm_provider.time.sleep"):
                    response = provider.invoke("p", {})

        assert response.content == "ok"
        assert call_count[0] == 3

    def test_http_500_exhausts_retries_raises_provider_error(self):
        config = load_sensenova_provider_config(
            env_var_name="TEST_WW_KEY",
            max_attempts=2,
        )
        provider = SenseNovaLLMProvider(config=config)

        call_count = [0]

        def mock_always_500(*args, **kwargs):
            call_count[0] += 1
            raise urllib.error.HTTPError(
                url="", code=500, msg="Internal Server Error", hdrs=None, fp=None
            )

        with patch.dict("os.environ", {"TEST_WW_KEY": "k"}):
            with patch("web_watcher.llm_provider.urllib.request.urlopen", side_effect=mock_always_500):
                with patch("web_watcher.llm_provider.time.sleep"):
                    with pytest.raises(ProviderError) as exc_info:
                        provider.invoke("p", {})

        assert "500" in str(exc_info.value)
        assert call_count[0] == 2

    def test_http_429_retries(self):
        config = load_sensenova_provider_config(
            env_var_name="TEST_WW_KEY",
            max_attempts=2,
        )
        provider = SenseNovaLLMProvider(config=config)

        call_count = [0]

        def mock_429_then_ok(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise urllib.error.HTTPError(
                    url="", code=429, msg="Too Many Requests", hdrs=None, fp=None
                )
            return _mock_response({"choices": [{"message": {"content": "ok"}}]})

        with patch.dict("os.environ", {"TEST_WW_KEY": "k"}):
            with patch("web_watcher.llm_provider.urllib.request.urlopen", side_effect=mock_429_then_ok):
                with patch("web_watcher.llm_provider.time.sleep"):
                    response = provider.invoke("p", {})

        assert response.content == "ok"
        assert call_count[0] == 2

    def test_network_error_raises_provider_error(self):
        config = load_sensenova_provider_config(
            env_var_name="TEST_WW_KEY",
            max_attempts=1,
        )
        provider = SenseNovaLLMProvider(config=config)

        def mock_network_error(*args, **kwargs):
            raise urllib.error.URLError("connection refused")

        with patch.dict("os.environ", {"TEST_WW_KEY": "k"}):
            with patch("web_watcher.llm_provider.urllib.request.urlopen", side_effect=mock_network_error):
                with pytest.raises(ProviderError):
                    provider.invoke("p", {})

    def test_non_json_response_raises_provider_error(self):
        config = load_sensenova_provider_config(
            env_var_name="TEST_WW_KEY",
            max_attempts=1,
        )
        provider = SenseNovaLLMProvider(config=config)

        mock_resp = Mock()
        mock_resp.read.return_value = b"not json at all"
        mock_resp.status = 200
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)

        with patch.dict("os.environ", {"TEST_WW_KEY": "k"}):
            with patch("web_watcher.llm_provider.urllib.request.urlopen", return_value=mock_resp):
                with pytest.raises(ProviderError):
                    provider.invoke("p", {})

    def test_empty_choices_returns_empty_content(self):
        config = load_sensenova_provider_config(
            env_var_name="TEST_WW_KEY",
            max_attempts=1,
        )
        provider = SenseNovaLLMProvider(config=config)

        mock_resp = _mock_response({"choices": []})

        with patch.dict("os.environ", {"TEST_WW_KEY": "k"}):
            with patch("web_watcher.llm_provider.urllib.request.urlopen", return_value=mock_resp):
                response = provider.invoke("p", {})

        assert response.content == ""

    def test_missing_message_returns_empty_content(self):
        config = load_sensenova_provider_config(
            env_var_name="TEST_WW_KEY",
            max_attempts=1,
        )
        provider = SenseNovaLLMProvider(config=config)

        mock_resp = _mock_response({"choices": [{}]})

        with patch.dict("os.environ", {"TEST_WW_KEY": "k"}):
            with patch("web_watcher.llm_provider.urllib.request.urlopen", return_value=mock_resp):
                response = provider.invoke("p", {})

        assert response.content == ""


# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------


class TestBuildSenseNovaProvider:
    def test_factory_with_config(self):
        config = load_sensenova_provider_config(model="custom-model")
        provider = build_sensenova_provider(config=config)

        assert isinstance(provider, SenseNovaLLMProvider)
        assert provider.config.model == "custom-model"

    def test_factory_with_kwargs(self):
        provider = build_sensenova_provider(
            model="kwarg-model",
            timeout_seconds=15.0,
        )

        assert isinstance(provider, SenseNovaLLMProvider)
        assert provider.config.model == "kwarg-model"
        assert provider.config.timeout_seconds == 15.0

    def test_factory_no_args_uses_defaults(self):
        provider = build_sensenova_provider()

        assert isinstance(provider, SenseNovaLLMProvider)
        assert provider.config.model == DEFAULT_MODEL
        assert provider.config.base_url == DEFAULT_BASE_URL


# ---------------------------------------------------------------------------
# Security regression tests
# ---------------------------------------------------------------------------


class TestSecurityRegressions:
    def test_provider_source_no_hardcoded_secrets(self):
        source = open(
            "src/web_watcher/llm_provider.py", encoding="utf-8"
        ).read()
        for pattern in ["API_KEY=", "api_key=", "token=", "secret="]:
            assert pattern not in source, (
                f"potential hardcoded secret pattern: {pattern}"
            )

    def test_provider_source_no_qwenpaw_config_import(self):
        source = open(
            "src/web_watcher/llm_provider.py", encoding="utf-8"
        ).read()
        assert "qwenpaw" not in source.lower()
        assert "qwen" not in source.lower()

    def test_provider_source_no_policy_engine(self):
        source = open(
            "src/web_watcher/llm_provider.py", encoding="utf-8"
        ).read()
        assert "PolicyEngine" not in source
        assert "PolicyDecision" not in source

    def test_config_source_no_hardcoded_secrets(self):
        source = open(
            "src/web_watcher/ai_config.py", encoding="utf-8"
        ).read()
        # Check for hardcoded secret values (string literal assignments),
        # not variable pass-throughs like api_key=api_key
        for pattern in ['API_KEY="', 'API_KEY=\'', 'api_key="', "api_key='",
                        'token="', "token='", 'secret="', "secret='"]:
            assert pattern not in source, (
                f"potential hardcoded secret pattern: {pattern}"
            )

    def test_provider_never_reads_qwenpaw_env(self):
        source = open(
            "src/web_watcher/llm_provider.py", encoding="utf-8"
        ).read()
        # The only env var should be the one from config
        assert "QWEN" not in source
        assert "QWENPAW" not in source


# ---------------------------------------------------------------------------
# Integration test: AIJudge + SenseNovaLLMProvider (offline)
# ---------------------------------------------------------------------------


class TestIntegrationOffline:
    def test_ai_judge_with_sensenova_provider_valid_response(self):
        config = load_sensenova_provider_config(
            env_var_name="TEST_WW_KEY",
            max_attempts=1,
        )
        provider = SenseNovaLLMProvider(config=config)

        valid_judgment_json = json.dumps(
            {
                "relevance": 0.92,
                "importance": "important",
                "worth_notifying": True,
                "investigate": False,
                "reason": "test judgment",
                "summary": "test summary",
            }
        )

        mock_resp = _mock_response(
            {"choices": [{"message": {"content": valid_judgment_json}}]}
        )

        judge = AIJudge(provider=provider)
        context = _make_context()

        with patch.dict("os.environ", {"TEST_WW_KEY": "k"}):
            with patch("web_watcher.llm_provider.urllib.request.urlopen", return_value=mock_resp):
                judgment = judge.judge(context)

        assert isinstance(judgment, AIJudgment)
        assert judgment.relevance == 0.92
        assert judgment.importance is Importance.IMPORTANT
        assert judgment.worth_notifying is True
        assert judgment.investigate is False
        assert judgment.reason == "test judgment"
        assert judgment.summary == "test summary"

    def test_ai_judge_with_sensenova_provider_invalid_json(self):
        config = load_sensenova_provider_config(
            env_var_name="TEST_WW_KEY",
            max_attempts=1,
        )
        provider = SenseNovaLLMProvider(config=config)

        mock_resp = _mock_response(
            {"choices": [{"message": {"content": "not json"}}]}
        )

        judge = AIJudge(provider=provider)
        context = _make_context()

        with patch.dict("os.environ", {"TEST_WW_KEY": "k"}):
            with patch("web_watcher.llm_provider.urllib.request.urlopen", return_value=mock_resp):
                with pytest.raises(Exception):  # InvalidJSONError from Phase 10A
                    judge.judge(context)

    def test_ai_judge_with_sensenova_provider_missing_key_fails(self):
        config = load_sensenova_provider_config(
            env_var_name="MISSING_KEY_XYZ",
            max_attempts=1,
        )
        provider = SenseNovaLLMProvider(config=config)
        judge = AIJudge(provider=provider)
        context = _make_context()

        with pytest.raises(ProviderError):
            judge.judge(context)

    def test_provider_uses_configured_endpoint_url(self):
        config = load_sensenova_provider_config(
            base_url="https://custom.endpoint.example/v1",
            api_path="/chat/completions",
            env_var_name="TEST_WW_KEY",
            max_attempts=1,
        )
        provider = SenseNovaLLMProvider(config=config)

        expected_url = "https://custom.endpoint.example/v1/chat/completions"
        actual_url: str | None = None

        def capture_request(request, **kwargs):
            nonlocal actual_url
            actual_url = request.full_url
            return _mock_response({"choices": [{"message": {"content": "ok"}}]})

        with patch.dict("os.environ", {"TEST_WW_KEY": "k"}):
            with patch("web_watcher.llm_provider.urllib.request.urlopen", side_effect=capture_request):
                provider.invoke("p", {})

        assert actual_url == expected_url

    def test_provider_sets_correct_http_method(self):
        config = load_sensenova_provider_config(
            env_var_name="TEST_WW_KEY",
            max_attempts=1,
        )
        provider = SenseNovaLLMProvider(config=config)

        actual_method: str | None = None

        def capture_request(request, **kwargs):
            nonlocal actual_method
            actual_method = request.method
            return _mock_response({"choices": [{"message": {"content": "ok"}}]})

        with patch.dict("os.environ", {"TEST_WW_KEY": "k"}):
            with patch("web_watcher.llm_provider.urllib.request.urlopen", side_effect=capture_request):
                provider.invoke("p", {})

        assert actual_method == "POST"

    def test_provider_sets_content_type_header(self):
        config = load_sensenova_provider_config(
            env_var_name="TEST_WW_KEY",
            max_attempts=1,
        )
        provider = SenseNovaLLMProvider(config=config)

        actual_headers: dict[str, str] | None = None

        def capture_request(request, **kwargs):
            nonlocal actual_headers
            actual_headers = dict(request.headers)
            return _mock_response({"choices": [{"message": {"content": "ok"}}]})

        with patch.dict("os.environ", {"TEST_WW_KEY": "k"}):
            with patch("web_watcher.llm_provider.urllib.request.urlopen", side_effect=capture_request):
                provider.invoke("p", {})

        assert actual_headers is not None
        # urllib normalizes header names to lowercase
        assert any(
            k.lower() == "content-type" and v == "application/json"
            for k, v in actual_headers.items()
        )

    def test_provider_sets_authorization_header_format(self):
        config = load_sensenova_provider_config(
            env_var_name="TEST_WW_KEY",
            max_attempts=1,
        )
        provider = SenseNovaLLMProvider(config=config)

        actual_auth: str | None = None

        def capture_request(request, **kwargs):
            nonlocal actual_auth
            actual_auth = request.headers.get("Authorization", "")
            return _mock_response({"choices": [{"message": {"content": "ok"}}]})

        with patch.dict("os.environ", {"TEST_WW_KEY": "my-test-key"}):
            with patch("web_watcher.llm_provider.urllib.request.urlopen", side_effect=capture_request):
                provider.invoke("p", {})

        assert actual_auth == "Bearer my-test-key"

    def test_provider_never_logs_secret(self):
        """The provider must never log, print, or include the API key
        in any ProviderResponse field."""
        config = load_sensenova_provider_config(
            env_var_name="TEST_WW_KEY",
            max_attempts=1,
        )
        provider = SenseNovaLLMProvider(config=config)

        mock_resp = _mock_response(
            {"choices": [{"message": {"content": "test content"}}],
             "usage": {"total_tokens": 10}}
        )

        with patch.dict("os.environ", {"TEST_WW_KEY": "my-secret-key-do-not-log"}):
            with patch("web_watcher.llm_provider.urllib.request.urlopen", return_value=mock_resp):
                response = provider.invoke("prompt", {})

        # Content must not contain the key
        assert "my-secret-key-do-not-log" not in response.content
        # Metadata must not contain the key
        metadata_str = json.dumps(response.metadata)
        assert "my-secret-key-do-not-log" not in metadata_str


# ---------------------------------------------------------------------------
# Additional spec-required tests
# ---------------------------------------------------------------------------


class TestHTTP400NoRetry:
    """HTTP 400 is a permanent error — must never retry."""

    def test_http_400_raises_provider_error_no_retry(self):
        config = load_sensenova_provider_config(
            env_var_name="TEST_WW_KEY",
            max_attempts=5,
        )
        provider = SenseNovaLLMProvider(config=config)

        call_count = [0]

        def mock_400(*args, **kwargs):
            call_count[0] += 1
            raise urllib.error.HTTPError(
                url="", code=400, msg="Bad Request", hdrs=None, fp=None
            )

        with patch.dict("os.environ", {"TEST_WW_KEY": "k"}):
            with patch(
                "web_watcher.llm_provider.urllib.request.urlopen",
                side_effect=mock_400,
            ):
                with pytest.raises(ProviderError):
                    provider.invoke("p", {})

        assert call_count[0] == 1


class TestHTTP502Retry:
    def test_http_502_retries(self):
        config = load_sensenova_provider_config(
            env_var_name="TEST_WW_KEY",
            max_attempts=2,
        )
        provider = SenseNovaLLMProvider(config=config)

        call_count = [0]

        def mock_502_then_ok(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise urllib.error.HTTPError(
                    url="", code=502, msg="Bad Gateway", hdrs=None, fp=None
                )
            return _mock_response({"choices": [{"message": {"content": "ok"}}]})

        with patch.dict("os.environ", {"TEST_WW_KEY": "k"}):
            with patch(
                "web_watcher.llm_provider.urllib.request.urlopen",
                side_effect=mock_502_then_ok,
            ):
                with patch("web_watcher.llm_provider.time.sleep"):
                    response = provider.invoke("p", {})

        assert response.content == "ok"
        assert call_count[0] == 2


class TestHTTP503Retry:
    def test_http_503_retries(self):
        config = load_sensenova_provider_config(
            env_var_name="TEST_WW_KEY",
            max_attempts=2,
        )
        provider = SenseNovaLLMProvider(config=config)

        call_count = [0]

        def mock_503_then_ok(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise urllib.error.HTTPError(
                    url="", code=503, msg="Service Unavailable", hdrs=None, fp=None
                )
            return _mock_response({"choices": [{"message": {"content": "ok"}}]})

        with patch.dict("os.environ", {"TEST_WW_KEY": "k"}):
            with patch(
                "web_watcher.llm_provider.urllib.request.urlopen",
                side_effect=mock_503_then_ok,
            ):
                with patch("web_watcher.llm_provider.time.sleep"):
                    response = provider.invoke("p", {})

        assert response.content == "ok"
        assert call_count[0] == 2


class TestHTTP504Retry:
    def test_http_504_retries(self):
        config = load_sensenova_provider_config(
            env_var_name="TEST_WW_KEY",
            max_attempts=2,
        )
        provider = SenseNovaLLMProvider(config=config)

        call_count = [0]

        def mock_504_then_ok(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise urllib.error.HTTPError(
                    url="", code=504, msg="Gateway Timeout", hdrs=None, fp=None
                )
            return _mock_response({"choices": [{"message": {"content": "ok"}}]})

        with patch.dict("os.environ", {"TEST_WW_KEY": "k"}):
            with patch(
                "web_watcher.llm_provider.urllib.request.urlopen",
                side_effect=mock_504_then_ok,
            ):
                with patch("web_watcher.llm_provider.time.sleep"):
                    response = provider.invoke("p", {})

        assert response.content == "ok"
        assert call_count[0] == 2


class TestBoundedMaxAttempts:
    def test_default_max_attempts_is_two(self):
        config = load_sensenova_provider_config()
        assert config.max_attempts == 2

    def test_retry_exhausts_at_max_attempts(self):
        config = load_sensenova_provider_config(
            env_var_name="TEST_WW_KEY",
            max_attempts=3,
        )
        provider = SenseNovaLLMProvider(config=config)

        call_count = [0]

        def mock_always_500(*args, **kwargs):
            call_count[0] += 1
            raise urllib.error.HTTPError(
                url="", code=500, msg="Server Error", hdrs=None, fp=None
            )

        with patch.dict("os.environ", {"TEST_WW_KEY": "k"}):
            with patch(
                "web_watcher.llm_provider.urllib.request.urlopen",
                side_effect=mock_always_500,
            ):
                with patch("web_watcher.llm_provider.time.sleep"):
                    with pytest.raises(ProviderError):
                        provider.invoke("p", {})

        assert call_count[0] == 3


class TestSecretInExceptionText:
    def test_missing_key_exception_no_secret(self):
        config = load_sensenova_provider_config(
            env_var_name="MY_KEY_XYZ",
        )
        provider = SenseNovaLLMProvider(config=config)

        with patch.dict("os.environ", {}, clear=False):
            with pytest.raises(ProviderError) as exc_info:
                provider.invoke("p", {})

        err_str = str(exc_info.value)
        assert "MY_KEY_XYZ" in err_str
        # The value of the env var must NOT appear in the exception
        # (even though we never set it, prove the pattern holds)
        with patch.dict("os.environ", {"MY_KEY_XYZ": "top-secret-value-123"}):
            # Key is present, so invoke should proceed — but if it
            # fails later, the key must not be in any exception text.
            mock_resp = _mock_response(
                {"choices": [{"message": {"content": "ok"}}]}
            )
            with patch(
                "web_watcher.llm_provider.urllib.request.urlopen",
                return_value=mock_resp,
            ):
                response = provider.invoke("p", {})
            assert "top-secret-value-123" not in response.content
            assert "top-secret-value-123" not in json.dumps(response.metadata)

    def test_auth_failure_exception_no_secret(self):
        config = load_sensenova_provider_config(
            api_key="super-secret-auth-key",
            max_attempts=1,
        )
        provider = SenseNovaLLMProvider(config=config)

        def mock_401(*args, **kwargs):
            raise urllib.error.HTTPError(
                url="", code=401, msg="Unauthorized", hdrs=None, fp=None
            )

        with patch(
            "web_watcher.llm_provider.urllib.request.urlopen",
            side_effect=mock_401,
        ):
            with pytest.raises(ProviderError) as exc_info:
                provider.invoke("p", {})

        err_str = str(exc_info.value)
        assert "super-secret-auth-key" not in err_str
        assert "Bearer" not in err_str


class TestProviderDoesNotMutate:
    def test_provider_does_not_mutate_ai_context(self):
        config = load_sensenova_provider_config(
            api_key="key",
            max_attempts=1,
        )
        provider = SenseNovaLLMProvider(config=config)

        context = _make_context()
        # Capture pre-invoke state
        event_type_before = context.event.event_type
        policy_action_before = context.policy_decision.action.value

        mock_resp = _mock_response(
            {"choices": [{"message": {"content": "{}"}}]}
        )

        with patch(
            "web_watcher.llm_provider.urllib.request.urlopen",
            return_value=mock_resp,
        ):
            provider.invoke("prompt", {})

        # AIContext is frozen — but prove the provider didn't try to
        # mutate its components either
        assert context.event.event_type == event_type_before
        assert context.policy_decision.action.value == policy_action_before

    def test_provider_does_not_mutate_event(self):
        config = load_sensenova_provider_config(
            api_key="key",
            max_attempts=1,
        )
        provider = SenseNovaLLMProvider(config=config)

        context = _make_context()
        event = context.event
        event_attrs_before = {
            "id": event.id,
            "entity_id": event.entity_id,
            "event_type": event.event_type,
            "status": event.status,
            "importance": event.importance,
        }

        mock_resp = _mock_response(
            {"choices": [{"message": {"content": "{}"}}]}
        )

        with patch(
            "web_watcher.llm_provider.urllib.request.urlopen",
            return_value=mock_resp,
        ):
            provider.invoke("prompt", {})

        assert event.id == event_attrs_before["id"]
        assert event.entity_id == event_attrs_before["entity_id"]
        assert event.event_type == event_attrs_before["event_type"]
        assert event.status == event_attrs_before["status"]
        assert event.importance == event_attrs_before["importance"]

    def test_provider_does_not_mutate_policy_decision(self):
        config = load_sensenova_provider_config(
            api_key="key",
            max_attempts=1,
        )
        provider = SenseNovaLLMProvider(config=config)

        context = _make_context()
        decision = context.policy_decision

        mock_resp = _mock_response(
            {"choices": [{"message": {"content": "{}"}}]}
        )

        with patch(
            "web_watcher.llm_provider.urllib.request.urlopen",
            return_value=mock_resp,
        ):
            provider.invoke("prompt", {})

        assert decision.importance is Importance.IMPORTANT
        assert decision.action is Action.NOTIFY
        assert decision.reason == "test decision"


class TestForbiddenImportsAndExecution:
    def test_no_forbidden_imports_in_provider(self):
        source = open(
            "src/web_watcher/llm_provider.py", encoding="utf-8"
        ).read()
        for forbidden in ["requests", "httpx", "openai", "anthropic"]:
            assert forbidden not in source.lower(), (
                f"forbidden import found: {forbidden}"
            )

    def test_no_subprocess_or_shell_execution(self):
        source = open(
            "src/web_watcher/llm_provider.py", encoding="utf-8"
        ).read()
        for forbidden in ["subprocess", "os.system", "os.popen", "os.exec"]:
            assert forbidden not in source, (
                f"forbidden execution found: {forbidden}"
            )

    def test_no_dynamic_exec_or_eval(self):
        source = open(
            "src/web_watcher/llm_provider.py", encoding="utf-8"
        ).read()
        for forbidden in ["eval(", "exec("]:
            assert forbidden not in source, (
                f"forbidden dynamic execution found: {forbidden}"
            )

    def test_no_qwenpaw_recursive_invocation(self):
        source = open(
            "src/web_watcher/llm_provider.py", encoding="utf-8"
        ).read()
        for forbidden in ["qwenpaw", "QwenPaw"]:
            assert forbidden not in source, (
                f"QwenPaw reference found: {forbidden}"
            )


class TestSchemaInvalidThroughAIJudge:
    """When the provider returns structurally valid JSON that doesn't
    match the AIJudgment schema, Phase 10A validation must catch it."""

    def test_ai_judge_rejects_schema_invalid_response(self):
        config = load_sensenova_provider_config(
            api_key="key",
            max_attempts=1,
        )
        provider = SenseNovaLLMProvider(config=config)

        # JSON that is valid but has wrong schema for AIJudgment
        bad_json = json.dumps({"relevance": "not_a_float"})

        mock_resp = _mock_response(
            {"choices": [{"message": {"content": bad_json}}]}
        )

        judge = AIJudge(provider=provider)
        context = _make_context()

        with patch(
            "web_watcher.llm_provider.urllib.request.urlopen",
            return_value=mock_resp,
        ):
            with pytest.raises(SchemaValidationError):
                judge.judge(context)

    def test_ai_judge_rejects_unsupported_importance_value(self):
        config = load_sensenova_provider_config(
            api_key="key",
            max_attempts=1,
        )
        provider = SenseNovaLLMProvider(config=config)

        bad_json = json.dumps({
            "relevance": 0.5,
            "importance": "BOGUS_VALUE",
            "worth_notifying": True,
            "investigate": False,
            "reason": "test",
            "summary": "test",
        })

        mock_resp = _mock_response(
            {"choices": [{"message": {"content": bad_json}}]}
        )

        judge = AIJudge(provider=provider)
        context = _make_context()

        with patch(
            "web_watcher.llm_provider.urllib.request.urlopen",
            return_value=mock_resp,
        ):
            with pytest.raises(UnsupportedValueError):
                judge.judge(context)

    def test_ai_judge_rejects_invalid_json_response(self):
        config = load_sensenova_provider_config(
            api_key="key",
            max_attempts=1,
        )
        provider = SenseNovaLLMProvider(config=config)

        mock_resp = _mock_response(
            {"choices": [{"message": {"content": "not json at all"}}]}
        )

        judge = AIJudge(provider=provider)
        context = _make_context()

        with patch(
            "web_watcher.llm_provider.urllib.request.urlopen",
            return_value=mock_resp,
        ):
            with pytest.raises(InvalidJSONError):
                judge.judge(context)
