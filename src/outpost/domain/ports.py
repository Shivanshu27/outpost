"""Ports — the interfaces the domain defines and adapters implement.

Dependencies point inward (ADR-0003): the domain declares what it needs, and
``adapters/`` supplies it. Nothing here imports from ``adapters``.

These are :class:`typing.Protocol`, so they are structural. An adapter satisfies
a port by having the right shape; it does not inherit from anything and does not
register itself. That keeps adapters ignorant of the domain and makes a test
fake a plain class with three methods.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from outpost.domain.models import (
    Eligibility,
    EligibilityVerdict,
    Job,
    JobStatus,
    MatchResult,
    RawJob,
    ScoringRequest,
    VerificationResult,
)

__all__ = [
    "Clock",
    "JobRepository",
    "JobSource",
    "LLMProvider",
    "ProviderUnavailable",
    "QuotaExhausted",
    "RateLimit",
    "SourceError",
    "UpsertReport",
    "Verifier",
]


# --------------------------------------------------------------------------
# Typed failure signals
#
# These are domain concepts, not incidental exceptions. The pipeline branches on
# them: QuotaExhausted ends a run cleanly and successfully (ADR-0005), while an
# unexpected error does not. A generic `except Exception` cannot make that
# distinction, which is precisely why these exist.
# --------------------------------------------------------------------------


class SourceError(Exception):
    """A source failed to fetch. Isolated per source; never fails a run."""

    def __init__(self, source: str, message: str) -> None:
        super().__init__(f"{source}: {message}")
        self.source = source


class ProviderUnavailable(Exception):
    """The LLM provider cannot serve requests at all (bad key, network down)."""


class QuotaExhausted(Exception):
    """The provider's quota is spent.

    An *expected* end state, not an error. The run commits what it scored,
    reports honestly, and exits zero; the next run resumes on unscored rows
    (ADR-0005).
    """

    def __init__(self, provider: str, retry_after: datetime | None = None) -> None:
        super().__init__(f"{provider} quota exhausted")
        self.provider = provider
        self.retry_after = retry_after


# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RateLimit:
    """A source's declared politeness budget.

    Declared by the source, enforced by the shared HTTP client — a source author
    writes no throttling code (ADR-0008).
    """

    requests_per_minute: int = 30
    concurrency: int = 4

    @property
    def min_interval_seconds(self) -> float:
        return 60.0 / self.requests_per_minute


@runtime_checkable
class JobSource(Protocol):
    """A place jobs come from.

    Implementations **fetch only**. They do not normalise, filter, deduplicate
    or persist — all of that is shared downstream, so that it cannot drift per
    source (ADR-0008).
    """

    name: str
    rate_limit: RateLimit

    async def fetch(self, since: datetime | None = None) -> Sequence[RawJob]:
        """Return everything available, newest first where the source allows.

        ``since`` is a hint for sources that support incremental fetches; those
        that do not may ignore it, since normalisation deduplicates anyway.

        Raises:
            SourceError: on any failure. The caller isolates it.
        """
        ...


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UpsertReport:
    """What a write actually did — the basis of the run report."""

    inserted: int = 0
    updated: int = 0
    unchanged: int = 0

    @property
    def total(self) -> int:
        return self.inserted + self.updated + self.unchanged


class JobRepository(Protocol):
    """Durable storage for jobs.

    The critical contract is on :meth:`upsert_many`: it refreshes source-derived
    fields and **must never overwrite user-owned ones** (``status``, ``notes``,
    ``eligibility_override``). See ADR-0004.
    """

    def upsert_many(self, jobs: Sequence[Job]) -> UpsertReport: ...

    def get(self, job_id: str) -> Job | None: ...

    def list_jobs(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        unscored_only: bool = False,
        unverified_only: bool = False,
        statuses: Sequence[JobStatus] | None = None,
        eligibilities: Sequence[Eligibility] | None = None,
        sources: Sequence[str] | None = None,
        order_by: str = "match_score",
    ) -> list[Job]: ...

    def count(self) -> int: ...

    def counts_by_eligibility(self) -> dict[str, int]: ...

    def save_eligibility(self, job_id: str, verdict: EligibilityVerdict) -> None: ...

    def save_eligibility_many(
        self, verdicts: Sequence[tuple[str, EligibilityVerdict]]
    ) -> None:
        """Batch form. Separate from the single-row method because a run
        updates every job at once, and one transaction beats N."""
        ...

    def save_verification(self, job_id: str, result: VerificationResult) -> None: ...

    def save_match(self, job_id: str, result: MatchResult) -> None: ...

    def save_prescore(self, job_id: str, score: float) -> None: ...

    def save_prescore_many(self, scores: Sequence[tuple[str, float]]) -> None: ...

    def set_status(self, job_id: str, status: JobStatus) -> None: ...

    def set_notes(self, job_id: str, notes: str | None) -> None: ...

    def set_eligibility_override(
        self, job_id: str, value: Eligibility | None
    ) -> None: ...

    def known_content_hashes(self) -> set[str]: ...


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


class LLMProvider(Protocol):
    """Scores jobs against the user's profile.

    Every provider returns the same validated :class:`MatchResult`. Malformed
    provider output is the adapter's problem and must never reach the domain
    (ADR-0006).
    """

    name: str

    async def score(
        self,
        requests: Sequence[ScoringRequest],
        profile_text: str,
    ) -> Mapping[str, MatchResult]:
        """Score a batch.

        Returns a mapping from ``ScoringRequest.job_id`` to its result,
        containing only the jobs actually scored. A provider that cannot score
        an item omits it rather than inventing a score.

        Keyed rather than positional, deliberately. Since omission is expected,
        a positional contract would let one skipped entry shift every later
        score onto the wrong listing — silently, and undetectably from the
        caller's side.

        Raises:
            QuotaExhausted: budget spent; the caller ends the run cleanly.
            ProviderUnavailable: the provider cannot serve at all.
        """
        ...


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------


class Verifier(Protocol):
    """Checks whether a listing is still live and plausibly genuine."""

    async def verify(self, job: Job) -> VerificationResult: ...

    async def verify_many(
        self, jobs: Sequence[Job]
    ) -> list[tuple[str, VerificationResult]]:
        """Batch form, bounded internally by the implementation's concurrency.

        Returns ``(job_id, result)`` pairs rather than results alone, because a
        bounded-concurrency implementation has no obligation to preserve input
        order and callers must not assume it does.
        """
        ...


# --------------------------------------------------------------------------
# Clock
# --------------------------------------------------------------------------


class Clock(Protocol):
    """Time, as a dependency.

    Injected rather than called directly so that staleness, expiry and
    ``first_seen``/``last_seen`` logic is testable without sleeping or freezing
    time process-wide (ADR-0009).
    """

    def now(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class SourceOutcome:
    """Per-source result, for the run report.

    Yield is tracked because silent rot is the top risk (ADR-0008): a source
    that returns zero jobs twice running is broken, and that must be visible
    rather than merely absent.
    """

    source: str
    fetched: int = 0
    error: str | None = None
    duration_seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(frozen=True, slots=True)
class RunReport:
    """What a pipeline run did, stage by stage.

    Stage counts make degradation legible: a source whose jobs all vanish at the
    filter stage has a broken parser, and the shape of the funnel says so.
    """

    sources: tuple[SourceOutcome, ...] = ()
    fetched: int = 0
    normalised: int = 0
    deduplicated: int = 0
    eligible: int = 0
    ineligible: int = 0
    unknown_eligibility: int = 0
    filtered_out: int = 0
    verified: int = 0
    scored: int = 0
    quota_exhausted: bool = False
    errors: tuple[str, ...] = field(default_factory=tuple)
