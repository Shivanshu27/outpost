"""Shared HTTP client for every source.

Retries, backoff, timeouts and rate limiting live here, once. A source author
writes none of it (ADR-0008) — they declare a :class:`RateLimit` and call
``get_json`` or ``get_text``.

Centralising this is not only about duplication. Politeness to the boards we
scrape is a property of the whole program, and it cannot be one if each source
implements its own throttling and gets it slightly wrong.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from types import TracebackType
from typing import Any, Self

import httpx
import structlog

from outpost.domain.ports import RateLimit, SourceError

__all__ = ["USER_AGENT", "HttpClient"]

logger = structlog.get_logger(__name__)

USER_AGENT = (
    "Outpost/0.1 (+https://github.com/Shivanshu27/outpost) "
    "local-first job search; contact via GitHub issues"
)
"""Identifying and contactable. A scraper that hides what it is gives the
operator no way to ask us to stop, which is the least we owe a free endpoint."""

_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 3
_BACKOFF_BASE = 1.5
_MAX_BACKOFF = 30.0


class _Throttle:
    """Per-host minimum interval between requests.

    Keyed by host rather than by source, because two sources hitting the same
    ATS host are one load from that host's point of view.
    """

    def __init__(self) -> None:
        self._last: dict[str, float] = defaultdict(float)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def wait(self, host: str, min_interval: float) -> None:
        async with self._locks[host]:
            elapsed = time.monotonic() - self._last[host]
            if elapsed < min_interval:
                await asyncio.sleep(min_interval - elapsed)
            self._last[host] = time.monotonic()


class HttpClient:
    """An ``httpx.AsyncClient`` with retry, backoff and throttling applied."""

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10.0),
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"},
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
        )
        self._throttle = _Throttle()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    @property
    def raw(self) -> httpx.AsyncClient:
        """The underlying client, for adapters that need to talk to something
        other than a job board (LLM providers) and so cannot use ``get``.

        Sharing it means one connection pool and one place that gets closed.
        """
        return self._client

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get(
        self,
        url: str,
        *,
        source: str,
        rate_limit: RateLimit | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """GET with throttling and bounded retries.

        Raises:
            SourceError: once retries are exhausted, or on a non-retryable
                status. Callers isolate it per source so one bad board cannot
                fail a run.
        """
        limit = rate_limit or RateLimit()
        host = httpx.URL(url).host

        last_error = ""
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            await self._throttle.wait(host, limit.min_interval_seconds)
            try:
                response = await self._client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt == _MAX_ATTEMPTS:
                    break
                await self._backoff(attempt, source=source, reason=last_error)
                continue

            if response.status_code in _RETRYABLE_STATUS:
                last_error = f"HTTP {response.status_code}"
                if attempt == _MAX_ATTEMPTS:
                    break
                # Honour Retry-After when the server sets it — guessing a
                # shorter interval than we were told is how a 429 becomes a ban.
                await self._backoff(
                    attempt,
                    source=source,
                    reason=last_error,
                    retry_after=_parse_retry_after(response),
                )
                continue

            if response.status_code >= 400:
                msg = f"HTTP {response.status_code} for {url}"
                raise SourceError(source, msg)

            return response

        msg = f"{url} failed after {_MAX_ATTEMPTS} attempts ({last_error})"
        raise SourceError(source, msg)

    async def get_json(
        self,
        url: str,
        *,
        source: str,
        rate_limit: RateLimit | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        response = await self.get(
            url,
            source=source,
            rate_limit=rate_limit,
            headers={"Accept": "application/json", **(headers or {})},
        )
        try:
            return response.json()
        except ValueError as exc:
            msg = f"{url} returned non-JSON: {exc}"
            raise SourceError(source, msg) from exc

    async def get_text(
        self,
        url: str,
        *,
        source: str,
        rate_limit: RateLimit | None = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        response = await self.get(
            url, source=source, rate_limit=rate_limit, headers=headers
        )
        return response.text

    @staticmethod
    async def _backoff(
        attempt: int,
        *,
        source: str,
        reason: str,
        retry_after: float | None = None,
    ) -> None:
        delay = retry_after if retry_after is not None else _BACKOFF_BASE**attempt
        delay = min(delay, _MAX_BACKOFF)
        logger.debug(
            "http.retry", source=source, attempt=attempt, reason=reason, delay=delay
        )
        await asyncio.sleep(delay)


def _parse_retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        # The HTTP-date form is rare here and not worth parsing; fall back to
        # exponential backoff rather than guessing.
        return None
