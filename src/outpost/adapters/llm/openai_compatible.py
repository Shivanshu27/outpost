"""Any endpoint that speaks OpenAI's ``/chat/completions``.

One adapter covers OpenRouter, Groq, Together, DeepInfra, vLLM, LM Studio and
whatever appears next, because they all copied the same request shape. That is
the whole value: a user who loses a free tier changes two settings rather than
waiting for us to ship a provider (ADR-0006).

``base_url`` is the API root — the adapter appends ``/chat/completions`` — so the
same class serves ``https://openrouter.ai/api/v1`` and ``http://localhost:1234/v1``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Final

import httpx
import structlog

from outpost.adapters.llm.base import (
    DEFAULT_BATCH_SIZE,
    BatchingLLMProvider,
    ProviderRateLimited,
)
from outpost.domain.ports import ProviderUnavailable, QuotaExhausted

__all__ = ["OpenAICompatibleProvider"]

logger = structlog.get_logger(__name__)

_TIMEOUT: Final = 90.0

_HARD_QUOTA_MARKERS: Final = (
    "insufficient_quota",
    "quota exceeded",
    "billing",
    "credits",
    "exceeded your current quota",
)
"""A 429 that means *the account is out of money*, not *you are going too fast*.

Routers overload 429 for both. Retrying the first kind never succeeds, so it is
surfaced as :class:`QuotaExhausted` immediately instead of being paid for in
wall-clock time.
"""


class OpenAICompatibleProvider(BatchingLLMProvider):
    """Chat-completions client with the shared batching and retry policy."""

    name = "openai_compatible"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
        name: str | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        requests_per_minute: int = 20,
        max_rate_limit_retries: int = 2,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(
            batch_size=batch_size,
            requests_per_minute=requests_per_minute,
            max_rate_limit_retries=max_rate_limit_retries,
            sleep=sleep or asyncio.sleep,
        )
        # The instance name overrides the class one so that a MatchResult records
        # *which* router produced it. Scores are only comparable within a
        # provider (ADR-0006), and "openai_compatible" would collapse Groq and a
        # local 7B model into one incomparable pool.
        if name:
            self.name = name
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=_TIMEOUT)

    @property
    def url(self) -> str:
        return f"{self._base_url}/chat/completions"

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _complete(self, prompt: str) -> str:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            # Best-effort: servers that do not support it ignore the field, and
            # the fence-stripping in base.py covers the ones that do neither.
            "response_format": {"type": "json_object"},
        }

        try:
            response = await self._client.post(self.url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            msg = f"{self.name} unreachable at {self.url}: {type(exc).__name__}: {exc}"
            raise ProviderUnavailable(msg) from exc

        if response.status_code == 429:
            self._raise_for_throttle(response)
        if response.status_code in (401, 403):
            msg = (
                f"{self.name} rejected the credentials (HTTP "
                f"{response.status_code}) at {self.url}."
            )
            raise ProviderUnavailable(msg)
        if response.status_code >= 400:
            msg = f"{self.name} returned HTTP {response.status_code} from {self.url}"
            raise ProviderUnavailable(msg)

        return _extract_text(response, self.name)

    def _raise_for_throttle(self, response: httpx.Response) -> None:
        """Always raises: either the retryable signal or a hard stop."""
        if _is_hard_quota(response):
            logger.info("openai_compatible.quota_exhausted", provider=self.name)
            raise QuotaExhausted(self.name)
        raise ProviderRateLimited(_retry_after_seconds(response))


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """``Retry-After``, in seconds.

    Only the delta-seconds form is read. The HTTP-date form is vanishingly rare
    on these APIs, and the shared policy already has a sane fallback — guessing
    at a date format would add a parsing bug to save nothing.
    """
    raw = response.headers.get("Retry-After") or response.headers.get(
        "X-RateLimit-Reset-After"
    )
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _is_hard_quota(response: httpx.Response) -> bool:
    blob = response.text.lower()
    return any(marker in blob for marker in _HARD_QUOTA_MARKERS)


def _extract_text(response: httpx.Response, provider: str) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        logger.warning("openai_compatible.no_choices", provider=provider)
        return ""
    first = choices[0]
    message = first.get("message") if isinstance(first, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    return content if isinstance(content, str) else ""
