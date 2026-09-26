"""The nine-stage pipeline.

Orchestration only. Every decision belongs to ``domain/``; every effect belongs
to an adapter behind a port. This module's job is sequencing, error isolation
and reporting (ADR-0003).

Stage order is cost-ascending and load-bearing — see ADR-0005. Two properties
fall out of it and are maintained here rather than assumed:

* **Interruptibility.** Each stage commits before the next begins, so a run
  killed at any point leaves consistent state and the next run resumes.
* **Graceful degradation.** ``QuotaExhausted`` is an expected end state, not an
  error: the run commits what it scored, reports it, and succeeds.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import structlog

from outpost.domain.eligibility import resolve_eligibility as resolve_job_eligibility
from outpost.domain.filters import FilterCriteria, apply_filters
from outpost.domain.models import (
    Eligibility,
    Job,
    RawJob,
    ScoringRequest,
    UserProfile,
)
from outpost.domain.normalisation import normalise
from outpost.domain.ports import (
    Clock,
    JobRepository,
    JobSource,
    LLMProvider,
    QuotaExhausted,
    RunReport,
    SourceError,
    SourceOutcome,
    Verifier,
)
from outpost.domain.ranking import rank
from outpost.domain.rules import CompiledRuleSet

__all__ = ["Pipeline", "PipelineDeps", "ProgressCallback"]

logger = structlog.get_logger(__name__)

ProgressCallback = Callable[[str, str], None]
"""``(stage, message)`` — the presentation layer decides how to render it.
The pipeline never prints."""


@dataclass(slots=True)
class PipelineDeps:
    """Everything the pipeline needs, supplied by the composition root.

    Ports, not the container: a use-case receives what it uses, so the
    container cannot quietly become a service locator (ADR-0009).
    """

    repository: JobRepository
    sources: Sequence[JobSource]
    clock: Clock
    ruleset: CompiledRuleSet
    profile: UserProfile
    filter_criteria: FilterCriteria
    verifier: Verifier | None = None
    llm: LLMProvider | None = None
    llm_batch_size: int = 10
    max_llm_jobs: int = 200
    verify_limit: int = 400


class Pipeline:
    """Runs the stages. Each is separately callable so the CLI can re-run one."""

    def __init__(
        self, deps: PipelineDeps, *, progress: ProgressCallback | None = None
    ) -> None:
        self._deps = deps
        self._progress = progress or (lambda _stage, _message: None)

    # ------------------------------------------------------------------
    # Stage 1-3 — collect, normalise, deduplicate, persist
    # ------------------------------------------------------------------

    async def collect(self) -> tuple[int, tuple[SourceOutcome, ...]]:
        """Fetch from every source concurrently, isolating failures.

        A source that raises is recorded and skipped; it can never fail the
        run. Per-source yield is returned because silent rot is the top risk —
        a board quietly returning nothing must be visible (ADR-0008).
        """
        deps = self._deps
        self._progress("collect", f"fetching from {len(deps.sources)} sources")

        results = await asyncio.gather(
            *(self._fetch_one(source) for source in deps.sources)
        )

        raws: list[RawJob] = []
        outcomes: list[SourceOutcome] = []
        for outcome, fetched in results:
            outcomes.append(outcome)
            raws.extend(fetched)
            if outcome.error:
                logger.warning(
                    "source.failed", source=outcome.source, error=outcome.error
                )
            elif outcome.fetched == 0:
                # Not an error, but the signature of a broken parser.
                logger.warning("source.empty", source=outcome.source)

        now = deps.clock.now()
        jobs: dict[str, Job] = {}
        for raw in raws:
            job = normalise(raw, now=now)
            if job is None:
                continue
            # Same listing from two boards collapses to one row; first wins,
            # which keeps the run deterministic given a stable source order.
            jobs.setdefault(job.id, job)

        report = deps.repository.upsert_many(list(jobs.values()))
        self._progress(
            "collect",
            f"{len(raws)} fetched → {len(jobs)} unique "
            f"({report.inserted} new, {report.updated} changed)",
        )
        return len(raws), tuple(outcomes)

    async def _fetch_one(
        self, source: JobSource
    ) -> tuple[SourceOutcome, Sequence[RawJob]]:
        started = time.monotonic()
        try:
            fetched = await source.fetch()
        except SourceError as exc:
            return (
                SourceOutcome(
                    source=source.name,
                    error=str(exc),
                    duration_seconds=time.monotonic() - started,
                ),
                (),
            )
        except Exception as exc:
            # A source is third-party-shaped code touching third-party data.
            # Isolation is the entire point; a bug in one must not end the run.
            logger.exception("source.unexpected", source=source.name)
            return (
                SourceOutcome(
                    source=source.name,
                    error=f"unexpected {type(exc).__name__}: {exc}",
                    duration_seconds=time.monotonic() - started,
                ),
                (),
            )
        return (
            SourceOutcome(
                source=source.name,
                fetched=len(fetched),
                duration_seconds=time.monotonic() - started,
            ),
            fetched,
        )

    # ------------------------------------------------------------------
    # Stage 4 — eligibility
    # ------------------------------------------------------------------

    def label_eligibility(
        self, jobs: Sequence[Job] | None = None
    ) -> dict[Eligibility, int]:
        """Label every job. Labels, never drops (ADR-0002)."""
        deps = self._deps
        targets = list(jobs) if jobs is not None else deps.repository.list_jobs()
        now = deps.clock.now()

        verdicts = [
            (job.id, resolve_job_eligibility(job, deps.profile, deps.ruleset, now=now))
            for job in targets
        ]
        deps.repository.save_eligibility_many(verdicts)

        counts = dict.fromkeys(Eligibility, 0)
        for _, verdict in verdicts:
            counts[verdict.eligibility] += 1

        self._progress(
            "eligibility",
            f"{counts[Eligibility.ELIGIBLE]} eligible, "
            f"{counts[Eligibility.UNKNOWN]} unknown, "
            f"{counts[Eligibility.INELIGIBLE]} ineligible",
        )
        return counts

    # ------------------------------------------------------------------
    # Stage 5 — filter
    # ------------------------------------------------------------------

    def select(
        self, criteria: FilterCriteria | None = None
    ) -> tuple[list[Job], dict[str, int]]:
        """Narrow to what is worth spending network and tokens on.

        Returns the survivors *and* the drop counts. The counts are not
        decoration: they are how a user discovers a filter is silently costing
        them two hundred listings.
        """
        deps = self._deps
        jobs = deps.repository.list_jobs()
        outcome = apply_filters(
            jobs, criteria or deps.filter_criteria, now=deps.clock.now()
        )
        counts = outcome.drop_counts
        self._progress(
            "filter",
            f"{len(outcome.kept)} of {len(jobs)} kept"
            + (f" (dropped: {_render_counts(counts)})" if counts else ""),
        )
        return list(outcome.kept), counts

    # ------------------------------------------------------------------
    # Stage 6 — verify
    # ------------------------------------------------------------------

    async def verify(self, jobs: Sequence[Job]) -> int:
        """Check liveness and legitimacy for jobs not yet verified."""
        deps = self._deps
        if deps.verifier is None:
            return 0

        pending = [job for job in jobs if job.verification is None][: deps.verify_limit]
        if not pending:
            return 0

        self._progress("verify", f"checking {len(pending)} listings")
        results = await deps.verifier.verify_many(pending)
        for job_id, result in results:
            deps.repository.save_verification(job_id, result)

        flagged = sum(1 for _, r in results if r.tier.excluded_by_default)
        self._progress(
            "verify", f"{len(results)} checked, {flagged} expired or flagged"
        )
        return len(results)

    # ------------------------------------------------------------------
    # Stage 7 — pre-score
    # ------------------------------------------------------------------

    def prescore(self, jobs: Sequence[Job]) -> list[Job]:
        """Order best-first so that stage 8 can be interrupted cheaply."""
        deps = self._deps
        ranked = rank(jobs, deps.profile, now=deps.clock.now())
        deps.repository.save_prescore_many([(job.id, score) for job, score in ranked])
        self._progress("prescore", f"{len(ranked)} ranked")
        return [job.model_copy(update={"prescore": score}) for job, score in ranked]

    # ------------------------------------------------------------------
    # Stage 8 — LLM scoring
    # ------------------------------------------------------------------

    async def score(self, jobs: Sequence[Job]) -> tuple[int, bool]:
        """Score the best candidates. Returns ``(scored, quota_exhausted)``.

        Quota exhaustion is expected, not exceptional: everything scored so far
        is already committed, we stop, and we say so. The next run picks up the
        unscored rows (ADR-0005).
        """
        deps = self._deps
        if deps.llm is None:
            return (0, False)

        pending = [job for job in jobs if job.match is None][: deps.max_llm_jobs]
        if not pending:
            return (0, False)

        profile_text = deps.profile.resume_text.strip()
        if not profile_text:
            self._progress(
                "score",
                "skipped — no resume text, so there is nothing to score against",
            )
            return (0, False)

        self._progress(
            "score",
            f"scoring {len(pending)} of {len(jobs)} with {deps.llm.name}",
        )

        scored = 0
        now = deps.clock.now()
        for start in range(0, len(pending), deps.llm_batch_size):
            batch = pending[start : start + deps.llm_batch_size]
            requests = [
                ScoringRequest(
                    job_id=job.id,
                    title=job.title,
                    company=job.company,
                    description=job.description[:6000],
                    location_text=job.location_text,
                )
                for job in batch
            ]
            try:
                results = await deps.llm.score(requests, profile_text)
            except QuotaExhausted as exc:
                when = (
                    f"retry after {exc.retry_after.isoformat()}"
                    if exc.retry_after
                    else "resume on the next run"
                )
                self._progress("score", f"quota exhausted after {scored} jobs — {when}")
                return (scored, True)

            # Results are keyed by job id, not positional: a provider omits
            # what it cannot score, and zipping would attribute scores to the
            # wrong listings.
            for job_id, result in results.items():
                deps.repository.save_match(
                    job_id, result.model_copy(update={"scored_at": now})
                )
                scored += 1

            self._progress("score", f"{scored}/{len(pending)} scored")

        return (scored, False)

    # ------------------------------------------------------------------
    # Full run
    # ------------------------------------------------------------------

    async def run(self, *, skip_collect: bool = False) -> RunReport:
        """Run every stage in order."""
        fetched = 0
        outcomes: tuple[SourceOutcome, ...] = ()
        if not skip_collect:
            fetched, outcomes = await self.collect()

        counts = self.label_eligibility()
        selected, drop_counts = self.select()
        verified = await self.verify(selected)

        # Re-read after verification so newly-EXPIRED listings drop out before
        # we spend tokens on them. Skipping this would score dead jobs.
        selected, drop_counts = self.select()
        ranked = self.prescore(selected)
        scored, exhausted = await self.score(ranked)

        return RunReport(
            sources=outcomes,
            fetched=fetched,
            normalised=len(selected),
            eligible=counts[Eligibility.ELIGIBLE],
            ineligible=counts[Eligibility.INELIGIBLE],
            unknown_eligibility=counts[Eligibility.UNKNOWN],
            filtered_out=sum(drop_counts.values()),
            verified=verified,
            scored=scored,
            quota_exhausted=exhausted,
            errors=tuple(o.error for o in outcomes if o.error),
        )


def _render_counts(counts: dict[str, int]) -> str:
    return ", ".join(
        f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])
    )
