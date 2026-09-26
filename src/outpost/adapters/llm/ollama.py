"""Ollama, running on the user's own machine.

The privacy-maximal path and the logical endpoint of ADR-0001: no key, no
account, no listing text leaving the laptop. The cost is latency and quality —
a 7B model is slower and blunter than a hosted frontier model, and its scores
are not comparable with theirs (ADR-0006).

Two things are deliberately different from every other adapter here:

* **It never raises QuotaExhausted.** There is no quota on your own hardware.
  Mapping a local stall onto "budget spent" would make the pipeline end a run
  cleanly and report success when in fact nothing was scored (ADR-0005).
* **The timeout is minutes, not seconds.** A cold model is loaded from disk on
  first request; a 20-second timeout would fail every first run and look like a
  broken install.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Final

import httpx
import structlog

from outpost.adapters.llm.base import DEFAULT_BATCH_SIZE, BatchingLLMProvider
from outpost.domain.ports import ProviderUnavailable

__all__ = ["DEFAULT_BASE_URL", "DEFAULT_MODEL", "OllamaProvider"]

logger = structlog.get_logger(__name__)

DEFAULT_BASE_URL: Final = "http://localhost:11434"
DEFAULT_MODEL: Final = "llama3.2"
DEFAULT_TIMEOUT: Final = 600.0

_UNREACHABLE_HINT: Final = (
    "Could not reach Ollama at {url}. Start it with `ollama serve`, then pull a "
    "model with `ollama pull {model}`."
)


class OllamaProvider(BatchingLLMProvider):
    """Local generation via ``/api/generate``.

    ``/api/generate`` rather than ``/api/chat`` because we send one complete
    prompt and want one completion; the chat endpoint's message list would add a
    template layer that differs per model and changes the prompt we carefully
    calibrated.
    """

    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        client: httpx.AsyncClient | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        timeout: float = DEFAULT_TIMEOUT,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(
            batch_size=batch_size,
            # Local hardware self-limits: requests queue on the GPU anyway, so a
            # throttle here would only add idle time. Kept high rather than
            # removed so the shared code path stays identical everywhere.
            requests_per_minute=600,
            max_rate_limit_retries=0,
            sleep=sleep or asyncio.sleep,
        )
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)

    @property
    def url(self) -> str:
        return f"{self._base_url}/api/generate"

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _complete(self, prompt: str) -> str:
        payload: dict[str, Any] = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.2},
        }
        try:
            response = await self._client.post(self.url, json=payload)
        except httpx.HTTPError as exc:
            logger.debug("ollama.unreachable", error=str(exc))
            raise ProviderUnavailable(
                _UNREACHABLE_HINT.format(url=self._base_url, model=self._model)
            ) from exc

        if response.status_code == 404:
            # Ollama answers 404 for an unpulled model, which reads as a broken
            # URL unless we say otherwise.
            msg = (
                f"Ollama has no model named '{self._model}'. "
                f"Run `ollama pull {self._model}`."
            )
            raise ProviderUnavailable(msg)
        if response.status_code >= 400:
            msg = f"Ollama returned HTTP {response.status_code} from {self.url}"
            raise ProviderUnavailable(msg)

        try:
            body = response.json()
        except ValueError:
            return ""
        if not isinstance(body, dict):
            return ""
        text = body.get("response")
        return text if isinstance(text, str) else ""
