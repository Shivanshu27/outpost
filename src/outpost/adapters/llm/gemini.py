"""Google Gemini via the REST ``generateContent`` endpoint.

The default provider when a key is present (ADR-0006), because its free tier is
the one that currently gets a new user to useful output without a credit card.
That is a commercial fact with a short half-life, which is exactly why it is one
file behind a port.

Deliberately plain REST rather than ``google-generativeai``: the surface we need
is one POST, and the SDK would add a dependency, its own auth machinery and its
own release cadence to save about fifteen lines.
"""

from __future__ import annotations

import asyncio
import re
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

__all__ = ["DEFAULT_MODEL", "GeminiProvider"]

logger = structlog.get_logger(__name__)

DEFAULT_MODEL: Final = "gemini-2.0-flash"
_BASE_URL: Final = "https://generativelanguage.googleapis.com/v1beta/models"
_TIMEOUT: Final = 60.0

_DURATION_RE: Final = re.compile(r"^\s*(?P<value>\d+(?:\.\d+)?)s?\s*$")

_DAILY_QUOTA_MARKERS: Final = ("perday", "per day", "daily")
"""Substrings that mark a 429 as *the day's budget is gone*, not *slow down*.

Gemini reports both through the same status. Retrying a per-minute limit is
correct; retrying a per-day one burns the rest of the run against a wall, so the
two must be told apart rather than treated uniformly.
"""


class GeminiProvider(BatchingLLMProvider):
    """Scores via ``models/{model}:generateContent``."""

    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        client: httpx.AsyncClient | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        requests_per_minute: int = 15,
        max_rate_limit_retries: int = 2,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if not api_key or not api_key.strip():
            msg = (
                "Gemini needs an API key; set OUTPOST_GEMINI_API_KEY, "
                "or choose another provider."
            )
            raise ProviderUnavailable(msg)

        super().__init__(
            batch_size=batch_size,
            requests_per_minute=requests_per_minute,
            max_rate_limit_retries=max_rate_limit_retries,
            sleep=sleep or asyncio.sleep,
        )

        self._api_key = api_key
        self._model = model
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=_TIMEOUT)

    @classmethod
    def default_model(cls) -> str:
        """The model used when the config names none.

        A method rather than a bare constant read by the registry, so that the
        choice of default stays owned by this adapter — it changes whenever
        Google retires a model, and the registry should not have to care.
        """
        return DEFAULT_MODEL

    @property
    def url(self) -> str:
        return f"{_BASE_URL}/{self._model}:generateContent"

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _complete(self, prompt: str) -> str:
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                # Low but non-zero: scoring should be near-deterministic, and
                # exactly zero makes some models loop on degenerate output.
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        }
        try:
            response = await self._client.post(
                self.url,
                params={"key": self._api_key},
                json=payload,
                headers={"Content-Type": "application/json"},
            )
        except httpx.HTTPError as exc:
            msg = f"Gemini unreachable: {type(exc).__name__}: {exc}"
            raise ProviderUnavailable(msg) from exc

        if response.status_code == 429:
            self._raise_for_throttle(response)
        if response.status_code in (401, 403):
            msg = (
                "Gemini rejected the API key (HTTP "
                f"{response.status_code}). Check OUTPOST_GEMINI_API_KEY."
            )
            raise ProviderUnavailable(msg)
        if response.status_code == 400 and _mentions_api_key(response):
            msg = "Gemini rejected the API key as malformed (HTTP 400)."
            raise ProviderUnavailable(msg)
        if response.status_code >= 400:
            msg = f"Gemini returned HTTP {response.status_code}"
            raise ProviderUnavailable(msg)

        return _extract_text(response)

    def _raise_for_throttle(self, response: httpx.Response) -> None:
        """Turn a 429 into either a retryable signal or a hard stop.

        Always raises.
        """
        body = _safe_json(response)
        if _is_daily_quota(body):
            logger.info("gemini.daily_quota", model=self._model)
            raise QuotaExhausted(self.name)
        raise ProviderRateLimited(_retry_delay_seconds(body, response))


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _error_details(body: dict[str, Any]) -> list[dict[str, Any]]:
    error = body.get("error")
    if not isinstance(error, dict):
        return []
    details = error.get("details")
    if not isinstance(details, list):
        return []
    return [d for d in details if isinstance(d, dict)]


def _retry_delay_seconds(
    body: dict[str, Any], response: httpx.Response
) -> float | None:
    """Read Gemini's own "wait this long" hint.

    It arrives as a protobuf ``RetryInfo`` inside ``error.details``, formatted as
    a duration string like ``"17s"`` — not as a ``Retry-After`` header, which is
    where every generic HTTP client looks. Ignoring it means guessing shorter
    than we were told, which is how a rate limit becomes a suspended key.
    """
    for detail in _error_details(body):
        raw = detail.get("retryDelay")
        if isinstance(raw, str) and (match := _DURATION_RE.match(raw)):
            return float(match.group("value"))
    header = response.headers.get("Retry-After")
    if header:
        try:
            return float(header)
        except ValueError:
            return None
    return None


def _is_daily_quota(body: dict[str, Any]) -> bool:
    haystack: list[str] = []
    for detail in _error_details(body):
        violations = detail.get("violations")
        if isinstance(violations, list):
            haystack.extend(
                str(v.get("quotaId", "")) for v in violations if isinstance(v, dict)
            )
    error = body.get("error")
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        haystack.append(error["message"])
    blob = " ".join(haystack).lower()
    return any(marker in blob for marker in _DAILY_QUOTA_MARKERS)


def _mentions_api_key(response: httpx.Response) -> bool:
    error = _safe_json(response).get("error")
    message = error.get("message", "") if isinstance(error, dict) else ""
    return "api key" in str(message).lower()


def _extract_text(response: httpx.Response) -> str:
    """Concatenate the candidate's text parts.

    A response with no candidates means the prompt or the answer was filtered.
    That is returned as empty text rather than raised: the batch parser will
    report nothing scored, which is the honest outcome and keeps one blocked
    batch from ending the run.
    """
    body = _safe_json(response)
    candidates = body.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        logger.warning("gemini.no_candidates", feedback=body.get("promptFeedback"))
        return ""
    first = candidates[0]
    content = first.get("content") if isinstance(first, dict) else None
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list):
        return ""
    return "".join(
        part["text"]
        for part in parts
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    )
