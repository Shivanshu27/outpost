"""LLM provider registry.

One port, several adapters, and a path that needs no provider at all
(ADR-0006). Adding a provider means adding a module and one registry entry;
nothing in ``app/`` or ``domain/`` changes.

The factory takes an explicit :class:`LLMConfig` rather than reading settings.
``adapters`` may not import ``config`` (ADR-0009), so the composition root
translates its settings into this shape — which keeps the boundary typed
instead of duck-typed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from outpost.adapters.llm.gemini import DEFAULT_MODEL as GEMINI_DEFAULT_MODEL
from outpost.adapters.llm.gemini import GeminiProvider
from outpost.adapters.llm.manual import ManualProvider
from outpost.adapters.llm.null import NullProvider
from outpost.adapters.llm.ollama import OllamaProvider
from outpost.adapters.llm.openai_compatible import OpenAICompatibleProvider
from outpost.domain.ports import LLMProvider, ProviderUnavailable

__all__ = [
    "PROVIDER_REGISTRY",
    "GeminiProvider",
    "LLMConfig",
    "ManualProvider",
    "NullProvider",
    "OllamaProvider",
    "OpenAICompatibleProvider",
    "available_providers",
    "build_provider",
]


@dataclass(frozen=True, slots=True)
class LLMConfig:
    """What a provider needs, independent of how the user expressed it."""

    provider: str = "none"
    api_key: str | None = None
    model: str | None = None
    base_url: str | None = None
    batch_size: int = 10
    requests_per_minute: int = 15
    timeout_seconds: float = 120.0
    export_path: Path | None = None


ProviderFactory = Callable[[LLMConfig, httpx.AsyncClient | None], LLMProvider]


def _require_key(config: LLMConfig, provider: str) -> str:
    if not config.api_key:
        msg = (
            f"{provider} needs an API key. Set OUTPOST_LLM__API_KEY, or set "
            f"the provider to `none` to run without scoring."
        )
        raise ProviderUnavailable(msg)
    return config.api_key


def _build_gemini(config: LLMConfig, client: httpx.AsyncClient | None) -> LLMProvider:
    return GeminiProvider(
        api_key=_require_key(config, "gemini"),
        model=config.model or GEMINI_DEFAULT_MODEL,
        client=client,
        batch_size=config.batch_size,
        requests_per_minute=config.requests_per_minute,
    )


def _build_openai_compatible(
    config: LLMConfig, client: httpx.AsyncClient | None
) -> LLMProvider:
    if not config.base_url:
        msg = (
            "openai_compatible needs a base_url (e.g. "
            "https://openrouter.ai/api/v1 or http://localhost:1234/v1)."
        )
        raise ProviderUnavailable(msg)
    if not config.model:
        msg = "openai_compatible needs a model name."
        raise ProviderUnavailable(msg)
    return OpenAICompatibleProvider(
        base_url=config.base_url,
        model=config.model,
        api_key=config.api_key,
        client=client,
        batch_size=config.batch_size,
        requests_per_minute=config.requests_per_minute,
    )


def _build_ollama(config: LLMConfig, client: httpx.AsyncClient | None) -> LLMProvider:
    kwargs: dict[str, object] = {
        "client": client,
        "batch_size": config.batch_size,
        "timeout": config.timeout_seconds,
    }
    if config.base_url:
        kwargs["base_url"] = config.base_url
    if config.model:
        kwargs["model"] = config.model
    return OllamaProvider(**kwargs)  # type: ignore[arg-type]


def _build_manual(config: LLMConfig, _client: httpx.AsyncClient | None) -> LLMProvider:
    return ManualProvider(export_path=config.export_path)


def _build_null(_config: LLMConfig, _client: httpx.AsyncClient | None) -> LLMProvider:
    return NullProvider()


PROVIDER_REGISTRY: dict[str, ProviderFactory] = {
    "none": _build_null,
    "gemini": _build_gemini,
    "openai_compatible": _build_openai_compatible,
    "ollama": _build_ollama,
    "manual": _build_manual,
}


def available_providers() -> tuple[str, ...]:
    return tuple(sorted(PROVIDER_REGISTRY))


def build_provider(
    config: LLMConfig, client: httpx.AsyncClient | None = None
) -> LLMProvider:
    """Construct the configured provider.

    Always returns a provider. ``none`` yields :class:`NullProvider`, which
    scores nothing and is a real shipped mode rather than a placeholder — the
    tool is designed to be useful with no LLM at all (ADR-0006), so callers
    need no special case for its absence.

    Raises:
        ProviderUnavailable: the provider is known but cannot be built with
            this configuration — a missing key or base URL. Raised here, at
            startup, rather than at stage 8 after a long scrape.
    """
    factory = PROVIDER_REGISTRY.get(config.provider)
    if factory is None:
        msg = (
            f"Unknown LLM provider {config.provider!r}. "
            f"Available: {', '.join(available_providers())}"
        )
        raise ProviderUnavailable(msg)
    return factory(config, client)
