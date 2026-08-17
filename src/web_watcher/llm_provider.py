"""Phase 10B — SenseNova-compatible LLM provider.

Concrete implementation of the Phase 10A AIProvider Protocol using
stdlib urllib only.  Performs HTTP POST to a compatible
``/chat/completions`` endpoint and returns the assistant message
``content`` as a ProviderResponse for Phase 10A validation.

HTTP CONTRACT (established from provider configuration metadata):
    * Endpoint: https://token.sensenova.cn/v1/chat/completions
    * Auth: Authorization: Bearer <key>
    * Request: {"model": str, "messages": [{"role": str, "content": str}]}
    * Response: {"choices": [{"message": {"content": str}}]}
    * Timeout: explicit bounded timeout (default 30s)
    * No streaming, no tool calling, no arbitrary provider discovery.

Invariants
----------
* Never reads framework-internal configuration.
* Never logs or prints credentials.
* Never owns deterministic decision business logic.
* Never mutates domain model or context objects.
* Returns data through the Phase 10A ProviderResponse contract.
* Preserves the Phase 10A validation boundary.
"""

from __future__ import annotations

import json
import os
import ssl
import time
from typing import Mapping

import urllib.error
import urllib.request

from .ai_contract import ProviderResponse
from .ai_config import SenseNovaProviderConfig
from .ai_errors import (
    ProviderError,
    ProviderTimeoutError,
)


# HTTP statuses that are retryable (transient failures only)
_RETRIABLE_STATUSES = (429, 500, 502, 503, 504)
# HTTP statuses that are permanent (never retry)
_AUTH_FAILURE_STATUSES = (401, 403)
_BAD_REQUEST_STATUSES = (400,)
_TIMEOUT_STATUSES = (408,)
_MAX_BACKOFF_SECONDS = 2.0
_BACKOFF_STEP_SECONDS = 0.5


class SenseNovaLLMProvider:
    """Concrete AIProvider for SenseNova-compatible endpoints.

    Uses stdlib ``urllib.request`` only.  Resolves the API key from
    the config's ``api_key`` (direct injection, takes precedence) or
    the environment variable named in ``env_var_name``.  If neither
    is available, ``ProviderError`` is raised at request time.

    Builds a chat-completions request with a system message
    (prompt instructions) and a user message (the Phase 10A data
    prompt).  Returns the assistant message
    ``content`` as the ``ProviderResponse.content`` field.  Phase 10A
    is responsible for parsing and validating that content.
    """

    def __init__(self, config: SenseNovaProviderConfig) -> None:
        self._config = config

    @property
    def config(self) -> SenseNovaProviderConfig:
        """Return the immutable config (read-only)."""
        return self._config

    def invoke(
        self,
        prompt: str,
        context: Mapping[str, str],
    ) -> ProviderResponse:
        """Invoke the LLM provider with the given prompt.

        The ``context`` mapping is accepted for the Phase 10A contract
        but is not used by this provider (no secrets are transmitted).

        The provider prepends a minimal system prompt explaining the
        structured JSON output requirement to the Phase 10A data
        prompt before sending to the LLM.

        Raises:
            ProviderError: on auth failure, bad request, non-retryable
                HTTP error, network error, or non-JSON response.
            ProviderTimeoutError: on connection/read timeout or
                HTTP 408 after all retry attempts.
        """
        api_key = self._get_api_key()
        url = f"{self._config.base_url}{self._config.api_path}"

        messages = self._build_messages(prompt)
        payload = {
            "model": self._config.model,
            "messages": messages,
        }

        if self._config.response_format_json:
            payload["response_format"] = {"type": "json_object"}

        body = json.dumps(payload).encode("utf-8")
        headers = self._build_headers(api_key)

        request = urllib.request.Request(
            url,
            data=body,
            headers=headers,
            method="POST",
        )

        last_error: Exception | None = None
        for attempt in range(1, self._config.max_attempts + 1):
            try:
                with urllib.request.urlopen(
                    request,
                    timeout=self._config.timeout_seconds,
                ) as response:
                    raw = response.read().decode("utf-8")
                    http_status = str(response.status)

                    data = json.loads(raw)
                    content = self._extract_content(data)
                    metadata = self._build_metadata(http_status, data)

                    return ProviderResponse(content=content, metadata=metadata)

            except urllib.error.HTTPError as exc:
                status = exc.code
                if status in _AUTH_FAILURE_STATUSES:
                    raise ProviderError(
                        "provider authentication failed"
                    ) from exc
                if status in _BAD_REQUEST_STATUSES:
                    raise ProviderError(
                        "provider returned bad request"
                    ) from exc
                if status in _TIMEOUT_STATUSES:
                    raise ProviderTimeoutError(
                        "provider request timed out"
                    ) from exc
                if status in _RETRIABLE_STATUSES and attempt < self._config.max_attempts:
                    self._backoff(attempt)
                    continue
                raise ProviderError(
                    f"provider HTTP error: {status}"
                ) from exc

            except (TimeoutError,) as exc:
                if attempt < self._config.max_attempts:
                    self._backoff(attempt)
                    continue
                raise ProviderTimeoutError(
                    "provider request timed out"
                ) from exc

            except (urllib.error.URLError, ssl.SSLError) as exc:
                if attempt < self._config.max_attempts:
                    self._backoff(attempt)
                    continue
                last_error = ProviderError(
                    "provider network error"
                )

            except json.JSONDecodeError as exc:
                raise ProviderError(
                    "provider returned non-JSON response"
                ) from exc

        raise last_error or ProviderError(
            "provider failed after all retry attempts"
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_messages(self, prompt: str) -> list[dict[str, str]]:
        """Build the messages array for the chat-completions request.

        System message: minimal instructions for structured JSON output.
        User message: the Phase 10A data prompt (passed through as-is).
        """
        system_prompt = self._config.system_prompt or ""
        messages: list[dict[str, str]] = []

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        messages.append({"role": "user", "content": prompt})
        return messages

    @staticmethod
    def _build_headers(api_key: str) -> dict[str, str]:
        """Build HTTP headers.  The Authorization value is constructed
        here and never stored, logged, or returned to the caller."""
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "web-watcher/0.1",
        }

    @staticmethod
    def _extract_content(data: dict) -> str:
        """Extract the assistant message content from the response.

        Expects a chat-completions response shape:
            {"choices": [{"message": {"content": "..."}}]}

        Returns an empty string if the expected structure is absent,
        allowing Phase 10A validation to surface the error.
        """
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""

        first = choices[0]
        if not isinstance(first, dict):
            return ""

        message = first.get("message")
        if not isinstance(message, dict):
            return ""

        content = message.get("content")
        return content if isinstance(content, str) else ""

    @staticmethod
    def _build_metadata(http_status: str, data: dict) -> dict[str, str]:
        """Build non-sensitive metadata for the ProviderResponse.

        Never includes the API key, auth headers, or secrets.
        """
        metadata: dict[str, str] = {
            "http_status": http_status,
        }
        usage = data.get("usage")
        if isinstance(usage, dict):
            metadata["usage"] = json.dumps(usage, sort_keys=True)
        return metadata

    @staticmethod
    def _backoff(attempt: int) -> None:
        seconds = min(attempt * _BACKOFF_STEP_SECONDS, _MAX_BACKOFF_SECONDS)
        time.sleep(seconds)

    def _get_api_key(self) -> str:
        """Resolve the API key from config or environment.

        Precedence:
            1. ``config.api_key`` (direct injection — preferred for tests)
            2. ``os.environ[config.env_var_name]``

        Never reads framework-internal configuration.  Never logs or
        prints the key value.

        Raises:
            ProviderError: if no key is available.  The exception
                message contains no secret value.
        """
        if self._config.api_key:
            return self._config.api_key

        value = os.environ.get(self._config.env_var_name)
        if not value:
            raise ProviderError(
                f"API key not found: set {self._config.env_var_name} "
                "or pass api_key directly"
            )
        return value
