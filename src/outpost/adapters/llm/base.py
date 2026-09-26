"""Everything the LLM adapters share: prompts, batching, parsing, throttling.

An adapter in this package is meant to be about forty lines of wire format
(ADR-0006). That is only true if the parts that are *identical* across providers
live here: the prompt, the batch loop, the JSON extraction, the validation into
:class:`MatchResult`, the sliding-window rate limit and the retry policy.

The reason to centralise is not brevity. Each of these has a failure mode that is
silent when it drifts — a provider whose fence-stripping is slightly wrong drops
whole batches and simply reports fewer scores; a provider whose retry policy is
slightly too eager gets the user's key rate-limited. Bugs that present as "fewer
results than expected" are exactly the ones nobody reports, so there is one
implementation and one place to fix it.

Prompts are **data**, resolved against the package via ``importlib.resources``
and never against the current working directory (ADR-0006).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from importlib import resources
from typing import Any, Final

import structlog
from pydantic import ValidationError

from outpost.domain.models import MatchResult, ScoringRequest
from outpost.domain.ports import QuotaExhausted

__all__ = [
    "DEFAULT_BATCH_SIZE",
    "BatchingLLMProvider",
    "MalformedResponse",
    "ProviderRateLimited",
    "RateLimiter",
    "extract_json",
    "load_prompt",
    "parse_batch_response",
    "render_scoring_prompt",
]

logger = structlog.get_logger(__name__)

PROMPT_PACKAGE: Final = "outpost.resources.prompts"
SCORING_PROMPT: Final = "scoring.txt"

DEFAULT_BATCH_SIZE: Final = 5
"""Jobs per request.

Small batches waste tokens re-sending the profile; large ones make a single
malformed response cost more results, and push weaker models past the point
where they reliably return every index. Five is the compromise.
"""

_MAX_DESCRIPTION_CHARS: Final = 4000
"""Descriptions are truncated so that one verbose listing cannot crowd the rest
of a batch out of the context window — the failure would show up as missing
indices for the *other* jobs, which is impossible to diagnose from the outside.
"""

_WINDOW_SECONDS: Final = 60.0
_MAX_RETRY_DELAY: Final = 60.0
"""Cap on a provider-supplied retry hint. A provider that asks us to wait ten
minutes is telling us the quota is gone; we would rather surface that as
:class:`QuotaExhausted` and let the next run resume (ADR-0005) than block."""

_FENCE_RE: Final = re.compile(
    r"^\s*```(?:json|JSON)?\s*\n(?P<body>.*?)\n?\s*```\s*$",
    re.DOTALL,
)


class MalformedResponse(Exception):
    """A provider returned something we could not read as our wire contract.

    Typed rather than generic because the batch loop treats it as *this batch
    produced nothing* and continues, while an unexpected exception should still
    end the run. Malformed output is an adapter-level concern and never reaches
    the domain (ADR-0006).
    """

    def __init__(self, provider: str, detail: str) -> None:
        super().__init__(f"{provider}: {detail}")
        self.provider = provider


class ProviderRateLimited(Exception):
    """Internal signal: the provider throttled us and may accept a later retry.

    Adapters parse their own dialect of "wait this long" — a ``Retry-After``
    header, a ``retryDelay`` buried in a JSON error body — and raise this. The
    shared policy in :class:`BatchingLLMProvider` decides whether to wait or to
    give up, so that decision cannot drift between providers.
    """

    def __init__(self, retry_after_seconds: float | None = None) -> None:
        super().__init__("rate limited")
        self.retry_after_seconds = retry_after_seconds


# --------------------------------------------------------------------------
# Prompts as data
# --------------------------------------------------------------------------


def load_prompt(name: str = SCORING_PROMPT) -> str:
    """Read a prompt from the package's resources.

    Resolved against ``outpost.resources.prompts``, so it is found identically
    whether Outpost runs from a checkout, a wheel or a zipapp. A prompt that
    silently degrades because of the working directory is the bug ADR-0006
    designs out.
    """
    return resources.files(PROMPT_PACKAGE).joinpath(name).read_text(encoding="utf-8")


def render_scoring_prompt(
    requests: Sequence[ScoringRequest],
    profile_text: str,
    *,
    template: str | None = None,
) -> str:
    """Fill the scoring template with the profile and a numbered job batch.

    Substitution is literal ``str.replace`` rather than ``str.format``: the
    template contains a JSON example, and every brace in it would have to be
    doubled to survive formatting. Escaping a prompt is a maintenance trap for
    whoever edits it next, who will reasonably not expect that.
    """
    body = template if template is not None else load_prompt()
    return body.replace(
        "{{profile}}", profile_text.strip() or "(no profile provided)"
    ).replace("{{jobs}}", _render_jobs(requests))


def _render_jobs(requests: Sequence[ScoringRequest]) -> str:
    blocks = []
    for index, request in enumerate(requests, start=1):
        description = request.description.strip()
        if len(description) > _MAX_DESCRIPTION_CHARS:
            description = description[:_MAX_DESCRIPTION_CHARS] + "\n[truncated]"
        blocks.append(
            f"### {index}. {request.title}\n"
            f"Company: {request.company or 'unknown'}\n"
            f"Location: {request.location_text or 'unspecified'}\n"
            f"Description:\n{description or '(no description provided)'}"
        )
    return "\n\n".join(blocks)


# --------------------------------------------------------------------------
# Reading the response
# --------------------------------------------------------------------------


def extract_json(text: str) -> str:
    """Pull the JSON object out of a model's reply.

    Models wrap JSON in markdown fences however firmly you ask them not to, and
    many prepend a sentence of narration. We strip a fence if there is one, then
    fall back to the outermost brace pair. This is deliberately permissive: the
    alternative is discarding a batch of perfectly good scores over a stray
    "Here you go:".
    """
    candidate = text.strip()
    if match := _FENCE_RE.match(candidate):
        candidate = match.group("body").strip()

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end < start:
        return candidate
    return candidate[start : end + 1]


def parse_batch_response(
    text: str,
    requests: Sequence[ScoringRequest],
    provider: str,
) -> dict[str, MatchResult]:
    """Validate a provider's reply into :class:`MatchResult` objects, keyed by
    job id.

    **Keyed, not positional.** An earlier version returned a list, which is
    unsound the moment anything is omitted: drop index 3 and every later score
    shifts onto the wrong listing, silently, with no way for a caller to
    detect it. Since omission is not merely possible but *expected* — models
    skip entries — the only safe contract is one where each result names the
    job it belongs to.

    Defensive by design, because the input is generated text:

    * An entry whose ``index`` is missing, duplicated or outside the batch is
      dropped — a plausible-looking score attached to the wrong listing is worse
      than no score, since nothing downstream could detect it.
    * A job the provider simply did not return is **omitted**, never invented.
      The port is explicit that a provider which cannot score an item omits it,
      and a default score would be indistinguishable from a real judgement.
    * A score outside 0-100 is clamped rather than dropped. Out-of-range is the
      model misreading the scale, not misreading the job, so the ranking
      information is still worth keeping.

    Returns a mapping from ``ScoringRequest.job_id`` to its result, containing
    only the jobs the provider actually scored.

    Raises:
        MalformedResponse: the reply is not JSON, or has no ``results`` array.
            The caller treats that as an empty batch.
    """
    payload = extract_json(text)
    try:
        data = json.loads(payload)
    except ValueError as exc:
        raise MalformedResponse(provider, f"response was not JSON — {exc}") from exc

    if not isinstance(data, dict):
        raise MalformedResponse(
            provider, f"expected an object, got {type(data).__name__}"
        )

    entries = data.get("results")
    if not isinstance(entries, list):
        raise MalformedResponse(provider, "response has no 'results' array")

    scored_at = datetime.now(UTC)
    by_index: dict[int, MatchResult] = {}
    for entry in entries:
        parsed = _parse_entry(entry, len(requests), provider, scored_at)
        if parsed is None:
            continue
        index, result = parsed
        # First answer wins: a model that repeats an index has contradicted
        # itself, and picking the later one would be an arbitrary preference.
        by_index.setdefault(index, result)

    missing = [i for i in range(1, len(requests) + 1) if i not in by_index]
    if missing:
        logger.debug(
            "llm.batch_incomplete",
            provider=provider,
            missing=missing,
            expected=len(requests),
        )

    # The index is a prompt-local handle; the job id is the identity. Resolving
    # one to the other here is what keeps misattribution impossible upstream.
    #
    # Sorted by index so iteration order is the batch order rather than
    # whatever order the model happened to emit — stable output makes runs
    # reproducible and test assertions meaningful.
    return {
        requests[index - 1].job_id: result for index, result in sorted(by_index.items())
    }


def _parse_entry(
    entry: object,
    batch_size: int,
    provider: str,
    scored_at: datetime,
) -> tuple[int, MatchResult] | None:
    """Turn one result object into an indexed MatchResult, or None to drop it."""
    if not isinstance(entry, dict):
        return None
    raw: dict[str, Any] = entry

    index = _coerce_int(raw.get("index"))
    if index is None or not 1 <= index <= batch_size:
        return None

    score = _coerce_int(raw.get("score"))
    if score is None:
        return None
    score = max(0, min(100, score))

    reason = raw.get("reason")
    gaps = raw.get("gaps")
    try:
        result = MatchResult(
            score=score,
            reason=str(reason).strip() if reason else "No reason given.",
            gaps=tuple(str(g).strip() for g in gaps if str(g).strip())
            if isinstance(gaps, list)
            else (),
            provider=provider,
            scored_at=scored_at,
        )
    except ValidationError:
        logger.debug("llm.entry_rejected", provider=provider, index=index)
        return None
    return index, result


def _coerce_int(value: object) -> int | None:
    """Accept the numeric shapes models actually emit: 87, 87.0, "87"."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError:
            return None
    return None


# --------------------------------------------------------------------------
# Throttling
# --------------------------------------------------------------------------


class RateLimiter:
    """Sliding-window limiter: at most N requests in any 60-second window.

    A sliding window rather than a fixed interval because that is how the free
    tiers we target actually measure (15 requests per minute, not one every four
    seconds). Pacing evenly would leave a burst of allowance unused on every run
    while still tripping the limit at a window boundary.

    Held timestamps are monotonic, so a clock adjustment mid-run cannot make the
    limiter believe a minute has passed.
    """

    def __init__(
        self,
        requests_per_minute: int,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if requests_per_minute < 1:
            msg = "requests_per_minute must be at least 1"
            raise ValueError(msg)
        self._capacity = requests_per_minute
        self._sleep = sleep
        self._monotonic = monotonic
        self._times: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Block until another request fits inside the window."""
        async with self._lock:
            while True:
                now = self._monotonic()
                while self._times and now - self._times[0] >= _WINDOW_SECONDS:
                    self._times.popleft()
                if len(self._times) < self._capacity:
                    self._times.append(now)
                    return
                await self._sleep(_WINDOW_SECONDS - (now - self._times[0]))


# --------------------------------------------------------------------------
# The batch loop
# --------------------------------------------------------------------------


def chunked(
    items: Sequence[ScoringRequest], size: int
) -> Iterator[Sequence[ScoringRequest]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


class BatchingLLMProvider:
    """Base for every provider that talks to a completion endpoint.

    Subclasses implement exactly one method, :meth:`_complete`, which sends a
    prompt and returns the model's text. Batching, throttling, retry, parsing
    and the mapping from "throttled too often" to :class:`QuotaExhausted` are
    all handled here.

    Satisfies :class:`~outpost.domain.ports.LLMProvider` structurally; no
    provider inherits from the port itself (ADR-0003).
    """

    name: str = "base"

    def __init__(
        self,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        requests_per_minute: int = 15,
        max_rate_limit_retries: int = 2,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._batch_size = max(1, batch_size)
        self._max_rate_limit_retries = max(0, max_rate_limit_retries)
        self._sleep = sleep
        self._limiter = RateLimiter(requests_per_minute, sleep=sleep)

    async def score(
        self,
        requests: Sequence[ScoringRequest],
        profile_text: str,
    ) -> dict[str, MatchResult]:
        """Score a batch, splitting it across as many requests as needed.

        Raises:
            QuotaExhausted: propagated immediately, with whatever was already
                scored discarded for this call — the caller commits per stage
                and resumes on unscored rows next run (ADR-0005).
            ProviderUnavailable: the provider cannot serve at all.
        """
        if not requests:
            return {}

        results: dict[str, MatchResult] = {}
        for batch in chunked(requests, self._batch_size):
            prompt = render_scoring_prompt(batch, profile_text)
            text = await self._complete_with_retries(prompt)
            try:
                results.update(parse_batch_response(text, batch, self.name))
            except MalformedResponse as exc:
                # One unreadable batch costs its own results and nothing else.
                # Failing the run here would throw away every batch before it.
                logger.warning(
                    "llm.batch_unreadable", provider=self.name, error=str(exc)
                )
        return results

    async def _complete_with_retries(self, prompt: str) -> str:
        """Send one prompt, honouring the provider's own throttling hints.

        Retries are **bounded**. A provider that keeps saying "slow down" is out
        of budget, and the honest response is to end the run cleanly rather than
        to keep hammering an endpoint that has already told us to stop — which
        is how a free tier becomes a suspended key.
        """
        attempts = self._max_rate_limit_retries + 1
        last_delay: float | None = None
        for attempt in range(1, attempts + 1):
            await self._limiter.acquire()
            try:
                return await self._complete(prompt)
            except ProviderRateLimited as exc:
                last_delay = exc.retry_after_seconds
                if attempt == attempts:
                    break
                delay = min(
                    last_delay if last_delay is not None else 2.0**attempt,
                    _MAX_RETRY_DELAY,
                )
                logger.debug(
                    "llm.rate_limited",
                    provider=self.name,
                    attempt=attempt,
                    delay=delay,
                )
                await self._sleep(delay)

        raise QuotaExhausted(self.name, retry_after=_retry_at(last_delay))

    async def _complete(self, prompt: str) -> str:
        """Send ``prompt``, return the model's raw text.

        Raises:
            ProviderRateLimited: throttled; the shared policy decides what next.
            ProviderUnavailable: unusable credentials, or the host is unreachable.
            QuotaExhausted: the provider said the budget is gone outright, with
                no point in retrying (e.g. a daily cap).
        """
        raise NotImplementedError


def _retry_at(seconds: float | None) -> datetime | None:
    """Convert a relative retry hint into the absolute, aware instant the
    domain records. Naive datetimes are banned project-wide."""
    if seconds is None:
        return None
    return datetime.now(UTC) + timedelta(seconds=seconds)
