"""Phase 10B — explicit provider configuration.

Contains only what is required to operate a SenseNova-compatible
LLM provider.  Actual secret values are never stored in source.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Sentinel to distinguish "not provided" from "explicitly None"
_UNSET = object()

DEFAULT_BASE_URL = "https://token.sensenova.cn/v1"
DEFAULT_API_PATH = "/chat/completions"
DEFAULT_MODEL = "sensenova-6.7-flash-lite"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_ENV_VAR_NAME = "SENSENOVA_API_KEY"

_DEFAULT_SYSTEM_PROMPT = (
    "Analyze the event data below. Return ONLY a valid JSON object "
    "with these fields: relevance (float 0.0-1.0), "
    "importance (one of: ignore, interesting, important, critical), "
    "worth_notifying (boolean), investigate (boolean), "
    "reason (string), summary (string). "
    "Do not output markdown fences or additional text."
)


@dataclass(frozen=True)
class SenseNovaProviderConfig:
    """Immutable configuration for the SenseNova LLM provider.

    Secrets may be provided via either:
    * direct key injection — the ``api_key`` field (preferred for tests)
    * environment variable lookup — via ``env_var_name`` (production use)

    If the direct key is set, it takes precedence.  If neither is set,
    ``ProviderError`` is raised at request time.
    """

    base_url: str = DEFAULT_BASE_URL
    api_path: str = DEFAULT_API_PATH
    model: str = DEFAULT_MODEL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    env_var_name: str = DEFAULT_ENV_VAR_NAME
    api_key: Optional[str] = None
    response_format_json: bool = False
    system_prompt: Optional[str] = None


def load_sensenova_provider_config(
    base_url: Optional[str] = None,
    api_path: Optional[str] = None,
    model: Optional[str] = None,
    timeout_seconds: Optional[float] = None,
    max_attempts: Optional[int] = None,
    env_var_name: Optional[str] = None,
    api_key: Optional[str] = None,
    response_format_json: Optional[bool] = None,
    system_prompt: Optional[str] = _UNSET,  # type: ignore[assignment]
) -> SenseNovaProviderConfig:
    """Build a config from explicit arguments, falling back to defaults.

    Secrets are never read here from the environment — the provider
    resolves the key at request time from the config's ``api_key``
    or the named environment variable.
    """
    return SenseNovaProviderConfig(
        base_url=base_url if base_url is not None else DEFAULT_BASE_URL,
        api_path=api_path if api_path is not None else DEFAULT_API_PATH,
        model=model if model is not None else DEFAULT_MODEL,
        timeout_seconds=timeout_seconds
        if timeout_seconds is not None
        else DEFAULT_TIMEOUT_SECONDS,
        max_attempts=max_attempts
        if max_attempts is not None
        else DEFAULT_MAX_ATTEMPTS,
        env_var_name=env_var_name
        if env_var_name is not None
        else DEFAULT_ENV_VAR_NAME,
        api_key=api_key,
        response_format_json=response_format_json
        if response_format_json is not None
        else False,
        system_prompt=system_prompt
        if system_prompt is not _UNSET
        else _DEFAULT_SYSTEM_PROMPT,
    )
