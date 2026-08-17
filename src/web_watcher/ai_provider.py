"""Phase 10B — provider factory.

Convenience entry point that combines config loading and provider
construction.  Accepts direct ``api_key`` injection (preferred for
unit tests) or falls back to the named environment variable.
"""

from __future__ import annotations

from typing import Optional

from .ai_config import (
    SenseNovaProviderConfig,
    load_sensenova_provider_config,
)
from .llm_provider import SenseNovaLLMProvider


def build_sensenova_provider(
    config: Optional[SenseNovaProviderConfig] = None,
    api_key: Optional[str] = None,
    **config_kwargs,
) -> SenseNovaLLMProvider:
    """Build a SenseNova LLM provider from explicit config or defaults.

    Args:
        config: A pre-built ``SenseNovaProviderConfig``.  If provided,
            ``api_key`` and ``config_kwargs`` are ignored.
        api_key: Direct API key injection (takes precedence over env var).
            Never stored or logged.
        **config_kwargs: Passed through to ``load_sensenova_provider_config``.

    Returns:
        A configured ``SenseNovaLLMProvider`` instance.

    Raises:
        Never raises at construction time.  The provider validates
        the API key only at request time.
    """
    if config is not None:
        return SenseNovaLLMProvider(config=config)

    effective_api_key = api_key if api_key is not None else config_kwargs.pop("api_key", None)
    config = load_sensenova_provider_config(
        api_key=effective_api_key,
        **config_kwargs,
    )
    return SenseNovaLLMProvider(config=config)
