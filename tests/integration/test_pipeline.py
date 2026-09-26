"""The nine-stage pipeline, wired to fakes.

Real repository on a temporary file, fake sources, fake LLM, no network and no
clock. What is under test is the orchestration contract from ADR-0005:

* a broken source is isolated and *reported*, never fatal;
* a source that silently returns nothing is visible, because silent rot is the
  top risk (ADR-0008);
* quota exhaustion is an expected end state that commits what it has;
* scoring only touches unscored rows, which is what makes a run resumable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta

import pytest

from outpost.adapters.clock import FixedClock
from outpost.adapters.storage.sqlite import SqliteJobRepository
from outpost.app.pipeline import Pipeline, PipelineDeps
from outpost.domain.filters import FilterCriteria
from outpost.domain.models import (
    Eligibility,
    Job,
    MatchResult,
    RawJob,
    ScoringRequest,
    UserProfile,
    VerificationResult,
    VerificationTier,
)
from outpost.domain.ports import QuotaExhausted, RateLimit, SourceError
from outpost.domain.rules import CompiledRuleSet

# --------------------------------------------------------------------------
# Fakes. Ports are Protocols (ADR-0003), so a fake is a plain class with the
# right shape — no base class, no registration.
# --------------------------------------------------------------------------


class FakeSource:
    """A source that returns exactly what it was handed."""

    def __init__(self, name: str, jobs: Sequence[RawJob]) -> None:
        self.name = name
        self.rate_limit = RateLimit()
        self._jobs = tuple(jobs)
        self.fetch_count = 0

    async def fetch(self, since: datetime | None = None) -> Sequence[RawJob]:
        self.fetch_count += 1
        return self._jobs


class BrokenSource:
    """A source whose board is down, or whose parser has rotted."""

    def __init__(self, name: str, error: Exception) -> None:
        self.name = name
        self.rate_limit = RateLimit()
        self._error = error

    async def fetch(self, since: datetime | None = None) -> Sequence[RawJob]:
        raise self._error


class FakeLLM:
    """Scores everything it is given, optionally until a quota runs out."""

    name = "fake-llm"

    def __init__(self, quota: int | None = None) -> None:
        self.quota = quota
        self.seen: list[str] = []

    async def score(
        self, requests: Sequence[ScoringRequest], profile_text: str
    ) -> Mapping[str, MatchResult]:
        if self.quota is not None and len(self.seen) >= self.quota:
            raise QuotaExhausted("fake-llm")
        results: dict[str, MatchResult] = {}
        for request in requests:
            if self.quota is not None and len(self.seen) >= self.quota:
                break
            self.seen.append(request.job_id)
            results[request.job_id] = MatchResult(
                score=70, reason="plausible overlap", provider=self.name
            )
        return results


class FakeVerifier:
    """Marks a named set of listings expired; everything else is fine."""

    def __init__(self, expired_titles: frozenset[str] = frozenset()) -> None:
        self._expired = expired_titles
        self.checked: list[str] = []

    async def verify(self, job: Job) -> VerificationResult:
        self.checked.append(job.id)
        tier = (
            VerificationTier.EXPIRED
            if job.title in self._expired
            else VerificationTier.OK
        )
        return VerificationResult(tier=tier)

    async def verify_many(
        self, jobs: Sequence[Job]
    ) -> list[tuple[str, VerificationResult]]:
        return [(job.id, await self.verify(job)) for job in jobs]


def raw(
    *,
    source: str = "alpha",
    n: int = 1,
    title: str = "Senior Backend Engineer",
    description: str = "We build in Python on AWS with PostgreSQL and Docker.",
    url: str | None = None,
    **extra: object,
) -> RawJob:
    return RawJob(
        source=source,
        url=url or f"https://jobs.example.com/{source}/{n}",
        title=title,
        company="Acme Systems",
        description=description,
        location_text="Remote",
        **extra,  # type: ignore[arg-type]
    )


@pytest.fixture
def deps(
    repo: SqliteJobRepository,
    frozen_clock: FixedClock,
    ruleset: CompiledRuleSet,
    profile: UserProfile,
) -> PipelineDeps:
    return PipelineDeps(
        repository=repo,
        sources=(),
        clock=frozen_clock,
        ruleset=ruleset,
        profile=profile,
        filter_criteria=FilterCriteria(),
    )


# --------------------------------------------------------------------------
# A full run
# --------------------------------------------------------------------------


async def test_a_full_run_persists_a_labelled_ranked_and_scored_corpus(
    deps: PipelineDeps, repo: SqliteJobRepository
) -> None:
    deps.sources = (
        FakeSource("alpha", [raw(n=1), raw(n=2, title="Platform Engineer")]),
        FakeSource("beta", [raw(source="beta", n=1, title="Backend Engineer")]),
    )
    deps.llm = FakeLLM()
    deps.verifier = FakeVerifier()

    report = await Pipeline(deps).run()

    assert report.fetched == 3
    assert [o.source for o in report.sources] == ["alpha", "beta"]
    assert report.errors == ()
    assert report.quota_exhausted is False
    assert repo.count() == 3
    assert report.scored == 3
    stored = repo.list_jobs()
    assert all(job.match is not None for job in stored)
    assert all(job.prescore is not None for job in stored)
    assert all(job.verification is not None for job in stored)


async def test_a_run_with_no_llm_configured_still_produces_a_useful_corpus(
    deps: PipelineDeps, repo: SqliteJobRepository
) -> None:
    """Stages 1-7 are most of the product (ADR-0005): with no key at all the
    user still gets a deduplicated, labelled, verified, ranked list."""
    deps.sources = (FakeSource("alpha", [raw(n=1), raw(n=2)]),)

    report = await Pipeline(deps).run()

    assert report.scored == 0
    assert report.quota_exhausted is False
    assert all(job.prescore is not None for job in repo.list_jobs())


async def test_the_pipeline_reports_progress_without_printing(
    deps: PipelineDeps,
) -> None:
    """The pipeline never writes to a terminal; the presentation layer decides
    how to render, which is what lets the same run drive the CLI and the UI."""
    messages: list[tuple[str, str]] = []
    deps.sources = (FakeSource("alpha", [raw(n=1)]),)

    def record(stage: str, message: str) -> None:
        messages.append((stage, message))

    await Pipeline(deps, progress=record).run()

    assert {stage for stage, _ in messages} >= {
        "collect",
        "eligibility",
        "filter",
        "prescore",
    }


# --------------------------------------------------------------------------
# Source isolation (ADR-0008)
# --------------------------------------------------------------------------


async def test_a_source_that_raises_does_not_fail_the_run_and_is_reported(
    deps: PipelineDeps, repo: SqliteJobRepository
) -> None:
    """One board being down must not cost the user the other five.

    The error is recorded per source rather than swallowed, so the run report
    says which board failed and why.
    """
    deps.sources = (
        BrokenSource("broken", SourceError("broken", "502 from upstream")),
        FakeSource("healthy", [raw(source="healthy", n=1)]),
    )

    report = await Pipeline(deps).run()

    outcomes = {o.source: o for o in report.sources}
    assert outcomes["broken"].ok is False
    assert "502 from upstream" in (outcomes["broken"].error or "")
    assert outcomes["broken"].fetched == 0
    assert outcomes["healthy"].ok is True
    assert report.errors == ("broken: 502 from upstream",)
    assert repo.count() == 1


async def test_an_unexpected_bug_in_a_source_is_isolated_and_named(
    deps: PipelineDeps, repo: SqliteJobRepository
) -> None:
    """Sources are third-party-shaped code touching third-party data. A plain
    ``KeyError`` from a changed payload must be contained the same way."""
    deps.sources = (
        BrokenSource("buggy", KeyError("company")),
        FakeSource("healthy", [raw(source="healthy", n=1)]),
    )

    report = await Pipeline(deps).run()

    outcomes = {o.source: o for o in report.sources}
    assert outcomes["buggy"].error is not None
    assert "KeyError" in outcomes["buggy"].error
    assert repo.count() == 1


async def test_a_source_returning_nothing_is_reported_rather_than_merely_absent(
    deps: PipelineDeps,
) -> None:
    """Silent rot is the top risk (ADR-0008).

    A board that quietly starts returning an empty list looks exactly like a
    quiet week unless the zero is reported. It is not an error — so it must be
    visible as a *yield*, with ``ok`` still true.
    """
    deps.sources = (
        FakeSource("silent", []),
        FakeSource("healthy", [raw(source="healthy", n=1)]),
    )

    report = await Pipeline(deps).run()

    outcomes = {o.source: o for o in report.sources}
    assert outcomes["silent"].fetched == 0
    assert outcomes["silent"].ok is True
    assert outcomes["silent"].error is None
    assert outcomes["healthy"].fetched == 1


async def test_every_source_is_reported_even_when_all_of_them_fail(
    deps: PipelineDeps,
) -> None:
    deps.sources = (
        BrokenSource("a", SourceError("a", "down")),
        BrokenSource("b", SourceError("b", "down")),
    )

    report = await Pipeline(deps).run()

    assert len(report.sources) == 2
    assert len(report.errors) == 2


# --------------------------------------------------------------------------
# Deduplication
# --------------------------------------------------------------------------


async def test_the_same_listing_from_two_sources_collapses_to_one_row(
    deps: PipelineDeps, repo: SqliteJobRepository
) -> None:
    """Aggregators republish each other constantly. Identity is the canonical
    URL, so the same listing reached two ways is one job the user reads once.
    """
    shared = "https://boards.example.com/acme/senior-backend-engineer"
    deps.sources = (
        FakeSource("alpha", [raw(source="alpha", url=f"{shared}?utm_source=alpha")]),
        FakeSource("beta", [raw(source="beta", url=f"{shared}/")]),
    )

    report = await Pipeline(deps).run()

    assert report.fetched == 2
    assert repo.count() == 1


async def test_a_duplicate_within_one_source_is_also_collapsed(
    deps: PipelineDeps, repo: SqliteJobRepository
) -> None:
    deps.sources = (FakeSource("alpha", [raw(n=1), raw(n=1), raw(n=1)]),)

    await Pipeline(deps).run()

    assert repo.count() == 1


async def test_rerunning_the_pipeline_is_idempotent(
    deps: PipelineDeps, repo: SqliteJobRepository
) -> None:
    deps.sources = (FakeSource("alpha", [raw(n=1), raw(n=2)]),)
    pipeline = Pipeline(deps)

    await pipeline.run()
    await pipeline.run()

    assert repo.count() == 2


# --------------------------------------------------------------------------
# Eligibility labels, never drops (ADR-0002)
# --------------------------------------------------------------------------


async def test_ineligible_listings_are_stored_and_labelled_not_discarded(
    deps: PipelineDeps, repo: SqliteJobRepository
) -> None:
    """The filter hides them from the default view; storage keeps them, with
    their evidence, so the user can widen the filter and audit the verdict."""
    deps.sources = (
        FakeSource(
            "alpha",
            [
                raw(n=1, description="Python role. US only, sorry."),
                raw(n=2),
            ],
        ),
    )

    report = await Pipeline(deps).run()

    assert report.ineligible == 1
    assert repo.count() == 2
    ineligible = repo.list_jobs(eligibilities=[Eligibility.INELIGIBLE])
    assert len(ineligible) == 1
    assert ineligible[0].eligibility.blocking[0].rule_id == "loc.us_only"


async def test_unknown_eligibility_is_counted_and_kept(
    deps: PipelineDeps, repo: SqliteJobRepository
) -> None:
    deps.sources = (FakeSource("alpha", [raw(n=1)]),)

    report = await Pipeline(deps).run()

    assert report.unknown_eligibility == 1
    assert len(repo.list_jobs(eligibilities=[Eligibility.UNKNOWN])) == 1


# --------------------------------------------------------------------------
# Quota exhaustion is an end state, not an error (ADR-0005)
# --------------------------------------------------------------------------


async def test_quota_exhaustion_ends_the_run_cleanly_and_keeps_what_was_scored(
    deps: PipelineDeps, repo: SqliteJobRepository
) -> None:
    """Expected condition, not an exception.

    Everything scored before the quota ran out is already committed, the report
    says what happened, and no exception escapes to fail the run. The next run
    picks up the unscored rows.
    """
    deps.sources = (FakeSource("alpha", [raw(n=i) for i in range(6)]),)
    deps.llm = FakeLLM(quota=3)
    deps.llm_batch_size = 2

    report = await Pipeline(deps).run()

    assert report.quota_exhausted is True
    assert 0 < report.scored < 6
    scored = [job for job in repo.list_jobs() if job.match is not None]
    assert len(scored) == report.scored
    assert len(repo.list_jobs(unscored_only=True)) == 6 - report.scored


async def test_a_later_run_resumes_on_the_rows_the_quota_never_reached(
    deps: PipelineDeps, repo: SqliteJobRepository
) -> None:
    """Resumability by construction: stage 8 asks for jobs with no match, so
    picking up where it stopped needs no bookkeeping of its own."""
    deps.sources = (FakeSource("alpha", [raw(n=i) for i in range(6)]),)
    deps.llm_batch_size = 2
    deps.llm = FakeLLM(quota=2)

    first = await Pipeline(deps).run()
    assert first.quota_exhausted is True

    generous = FakeLLM()
    deps.llm = generous
    second = await Pipeline(deps).run()

    assert second.quota_exhausted is False
    assert repo.list_jobs(unscored_only=True) == []
    # The second provider was never asked to re-score what the first already did.
    assert len(generous.seen) == 6 - first.scored


async def test_scoring_only_touches_jobs_that_have_no_match_yet(
    deps: PipelineDeps, repo: SqliteJobRepository
) -> None:
    deps.sources = (FakeSource("alpha", [raw(n=1), raw(n=2)]),)
    llm = FakeLLM()
    deps.llm = llm

    await Pipeline(deps).run()
    first_pass = list(llm.seen)
    await Pipeline(deps).run()

    assert llm.seen == first_pass


async def test_the_llm_cap_bounds_a_run(
    deps: PipelineDeps, repo: SqliteJobRepository
) -> None:
    """The cap is what makes stage 8 affordable; combined with pre-score
    ordering, what falls outside it is the tail of the ranking."""
    deps.sources = (FakeSource("alpha", [raw(n=i) for i in range(8)]),)
    deps.llm = FakeLLM()
    deps.max_llm_jobs = 3

    report = await Pipeline(deps).run()

    assert report.scored == 3
    assert len(repo.list_jobs(unscored_only=True)) == 5


async def test_scores_are_attributed_by_job_id_not_by_position(
    deps: PipelineDeps, repo: SqliteJobRepository
) -> None:
    """A provider omits what it cannot score. Zipping results positionally
    would silently move every later score onto the wrong listing."""

    class PartialLLM:
        name = "partial"

        async def score(
            self, requests: Sequence[ScoringRequest], profile_text: str
        ) -> Mapping[str, MatchResult]:
            # Score only the last request in the batch.
            last = requests[-1]
            return {
                last.job_id: MatchResult(
                    score=99, reason="the only one it could judge", provider=self.name
                )
            }

    deps.sources = (FakeSource("alpha", [raw(n=1), raw(n=2), raw(n=3)]),)
    deps.llm = PartialLLM()

    report = await Pipeline(deps).run()

    scored = [job for job in repo.list_jobs() if job.match is not None]
    assert report.scored == 1
    assert len(scored) == 1
    assert scored[0].match is not None
    assert scored[0].match.score == 99


async def test_scoring_is_skipped_when_there_is_nothing_to_score_against(
    deps: PipelineDeps,
) -> None:
    """Without resume text the provider has no basis for a judgement, and
    asking anyway would spend quota on noise."""
    deps.profile = deps.profile.model_copy(update={"resume_text": ""})
    deps.sources = (FakeSource("alpha", [raw(n=1)]),)
    llm = FakeLLM()
    deps.llm = llm

    report = await Pipeline(deps).run()

    assert report.scored == 0
    assert llm.seen == []


# --------------------------------------------------------------------------
# Verification feeds back into selection
# --------------------------------------------------------------------------


async def test_a_listing_found_expired_is_not_scored(
    deps: PipelineDeps, repo: SqliteJobRepository
) -> None:
    """Stage 5 runs again after verification so tokens are not spent on jobs
    the verifier just discovered were dead."""
    deps.sources = (
        FakeSource(
            "alpha",
            [raw(n=1, title="Closed Engineer"), raw(n=2, title="Open Engineer")],
        ),
    )
    deps.verifier = FakeVerifier(expired_titles=frozenset({"Closed Engineer"}))
    llm = FakeLLM()
    deps.llm = llm

    report = await Pipeline(deps).run()

    assert report.verified == 2
    assert len(llm.seen) == 1
    scored = [job for job in repo.list_jobs() if job.match is not None]
    assert [job.title for job in scored] == ["Open Engineer"]


async def test_verification_is_not_repeated_for_already_checked_listings(
    deps: PipelineDeps,
) -> None:
    deps.sources = (FakeSource("alpha", [raw(n=1)]),)
    verifier = FakeVerifier()
    deps.verifier = verifier

    await Pipeline(deps).run()
    first = list(verifier.checked)
    await Pipeline(deps).run()

    assert verifier.checked == first


# --------------------------------------------------------------------------
# Stage-level behaviour
# --------------------------------------------------------------------------


async def test_a_run_can_skip_collection_and_reprocess_what_is_stored(
    deps: PipelineDeps, repo: SqliteJobRepository
) -> None:
    """Re-running the cheap stages against stored data is how a user applies a
    corrected ruleset without re-scraping every board."""
    source = FakeSource("alpha", [raw(n=1)])
    deps.sources = (source,)
    await Pipeline(deps).run()

    report = await Pipeline(deps).run(skip_collect=True)

    assert source.fetch_count == 1
    assert report.fetched == 0
    assert report.sources == ()
    assert repo.count() == 1


async def test_filter_drop_counts_reach_the_run_report(
    deps: PipelineDeps, frozen_clock: FixedClock
) -> None:
    """The funnel is a health signal: a source whose listings all die at one
    stage is a source whose parser has broken."""
    deps.sources = (
        FakeSource(
            "alpha",
            [
                raw(n=1),
                raw(n=2, title="Technical Recruiter"),
                raw(n=3, description="Python role. US only."),
            ],
        ),
    )

    report = await Pipeline(deps).run()

    assert report.filtered_out == 2
    assert report.ineligible == 1


async def test_a_listing_that_cannot_be_normalised_is_dropped_without_failing(
    deps: PipelineDeps, repo: SqliteJobRepository
) -> None:
    """A titleless record is unusable, not fatal — the rest of the batch must
    still land."""
    deps.sources = (
        FakeSource(
            "alpha",
            [
                RawJob(source="alpha", url="https://jobs.example.com/alpha/1"),
                raw(n=2),
            ],
        ),
    )

    report = await Pipeline(deps).run()

    assert report.fetched == 2
    assert repo.count() == 1


async def test_an_old_listing_is_filtered_but_still_stored(
    deps: PipelineDeps, repo: SqliteJobRepository, now: datetime
) -> None:
    deps.filter_criteria = FilterCriteria(max_age_days=30)
    deps.sources = (
        FakeSource(
            "alpha",
            [raw(n=1, posted_at=now - timedelta(days=200)), raw(n=2, posted_at=now)],
        ),
    )
    llm = FakeLLM()
    deps.llm = llm

    await Pipeline(deps).run()

    assert repo.count() == 2
    assert len(llm.seen) == 1
