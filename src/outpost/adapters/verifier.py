"""Stage 6 (part two) — liveness checking.

The legitimacy half is pure and lives in ``domain/verification.py``. This half
needs the network: fetch the listing page and see whether it still exists and
still says the role is open.

The governing rule is FR-4.5: **a network failure must never penalise a
listing.** We could not check is not the same as we found a problem, and
conflating them would let a flaky connection silently bury good jobs — the same
failure class ADR-0002 exists to prevent, arriving by a different route.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime

import httpx
import structlog

from outpost.adapters.http import HttpClient
from outpost.domain.models import Job, VerificationResult, VerificationTier
from outpost.domain.ports import Clock, RateLimit
from outpost.domain.verification import assess_legitimacy, looks_closed

__all__ = ["HttpVerifier"]

logger = structlog.get_logger(__name__)

_GONE_STATUSES = frozenset({404, 410})

# Page text is only needed for the "position filled" phrases, which appear
# early. Reading the whole of a heavy careers page is wasted bandwidth.
_MAX_BODY_CHARS = 60_000


class HttpVerifier:
    """Checks liveness over HTTP and combines it with the text heuristics.

    Satisfies :class:`~outpost.domain.ports.Verifier` structurally.
    """

    def __init__(
        self,
        client: HttpClient,
        clock: Clock,
        *,
        concurrency: int = 8,
        rate_limit: RateLimit | None = None,
    ) -> None:
        self._client = client
        self._clock = clock
        self._semaphore = asyncio.Semaphore(concurrency)
        self._rate_limit = rate_limit or RateLimit(requests_per_minute=120)

    async def verify(self, job: Job) -> VerificationResult:
        """Verify one listing.

        Legitimacy is assessed first because it is free: a listing the text
        heuristics already call a scam does not deserve a network round trip.
        """
        legitimacy = assess_legitimacy(job)
        if legitimacy.tier is VerificationTier.SCAM:
            return legitimacy.model_copy(update={"checked_at": self._clock.now()})

        liveness = await self._check_liveness(job)
        return self._combine(legitimacy, liveness, now=self._clock.now())

    async def verify_many(
        self, jobs: Sequence[Job]
    ) -> list[tuple[str, VerificationResult]]:
        """Verify a batch concurrently, bounded by the semaphore."""

        async def one(job: Job) -> tuple[str, VerificationResult]:
            async with self._semaphore:
                return (job.id, await self.verify(job))

        return list(await asyncio.gather(*(one(job) for job in jobs)))

    async def _check_liveness(self, job: Job) -> VerificationResult:
        url = str(job.url)
        try:
            response = await self._client.get(
                url, source="verifier", rate_limit=self._rate_limit
            )
        except Exception as exc:
            # Deliberately broad, and deliberately benign. Any failure to reach
            # the page yields UNKNOWN; see the module docstring.
            logger.debug("verify.unreachable", job_id=job.id, error=str(exc))
            return VerificationResult.unknown(f"Could not reach the listing: {exc}")

        if response.status_code in _GONE_STATUSES:
            return VerificationResult(
                tier=VerificationTier.EXPIRED,
                reasons=(f"Listing page returns HTTP {response.status_code}",),
                http_status=response.status_code,
            )

        body = _body_text(response)
        if body and looks_closed(body):
            return VerificationResult(
                tier=VerificationTier.EXPIRED,
                reasons=("Listing page says the position is no longer open",),
                http_status=response.status_code,
            )

        return VerificationResult(
            tier=VerificationTier.OK, http_status=response.status_code
        )

    @staticmethod
    def _combine(
        legitimacy: VerificationResult,
        liveness: VerificationResult,
        *,
        now: datetime,
    ) -> VerificationResult:
        """Merge the two checks, worst tier wins, reasons accumulate.

        Ordering matters: EXPIRED outranks SUSPICIOUS, because a closed listing
        is not worth flagging as merely dubious. UNKNOWN never overrides a
        determined tier — an unreachable page does not erase a scam signal we
        already found in the text.
        """
        reasons = (*legitimacy.reasons, *liveness.reasons)

        if liveness.tier is VerificationTier.EXPIRED:
            tier = VerificationTier.EXPIRED
        elif legitimacy.tier is VerificationTier.SUSPICIOUS:
            tier = VerificationTier.SUSPICIOUS
        elif liveness.tier is VerificationTier.UNKNOWN:
            tier = VerificationTier.UNKNOWN
        else:
            tier = VerificationTier.OK

        return VerificationResult(
            tier=tier,
            reasons=reasons,
            checked_at=now,
            http_status=liveness.http_status,
        )


def _body_text(response: httpx.Response) -> str:
    """Response body as text, truncated, and empty for non-HTML."""
    content_type = response.headers.get("content-type", "")
    if "html" not in content_type and "text" not in content_type:
        return ""
    try:
        return response.text[:_MAX_BODY_CHARS]
    except (UnicodeDecodeError, httpx.ResponseNotRead):
        return ""
