"""Stage 7 — the pre-score that makes stage 8 interruptible.

This stage orders, it never filters (ADR-0005). Its value is entirely in the
ordering being *stable* and *roughly right*: the LLM stage is capped, so what
falls below the cap is what quota exhaustion costs the user. An unstable sort
would silently change that set from run to run.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from tests.conftest import MakeJob

from outpost.domain.models import (
    DimensionVerdict,
    Eligibility,
    EligibilityDimension,
    EligibilityVerdict,
    UserProfile,
)
from outpost.domain.ranking import prescore, rank


def _verdict(eligibility: Eligibility) -> EligibilityVerdict:
    return EligibilityVerdict.combine(
        [
            DimensionVerdict(
                dimension=EligibilityDimension.LOCATION,
                eligibility=eligibility,
                rule_id="loc.test",
                evidence="fixture",
            )
        ]
    )


# --------------------------------------------------------------------------
# Stability
# --------------------------------------------------------------------------


def test_ranking_is_identical_across_repeated_runs(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    """Determinism is the point: the same corpus must produce the same set of
    jobs above the LLM cap every time."""
    jobs = [
        make_job(title="Senior Backend Engineer", description="Python, AWS, Docker."),
        make_job(title="Frontend Engineer", description="TypeScript."),
        make_job(title="Data Engineer", description="Python and PostgreSQL."),
    ]

    first = [job.id for job, _ in rank(jobs, profile, now=now)]
    second = [job.id for job, _ in rank(jobs, profile, now=now)]
    reordered = [job.id for job, _ in rank(list(reversed(jobs)), profile, now=now)]

    assert first == second == reordered


def test_ties_break_on_job_id_rather_than_input_order(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    """Two identical-looking listings must not swap places because a source
    returned them in a different order this week."""
    a = make_job(title="Engineer", description="A plain listing.", posted_at=None)
    b = make_job(title="Engineer", description="A plain listing.", posted_at=None)

    ranked = rank([b, a], profile, now=now)

    assert ranked[0][1] == ranked[1][1]
    assert [job.id for job, _ in ranked] == sorted([a.id, b.id])


def test_scores_stay_within_the_unit_interval(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    best = make_job(
        title="Senior Software Engineer",
        description="Python TypeScript PostgreSQL AWS Docker",
        posted_at=now,
        eligibility=_verdict(Eligibility.ELIGIBLE),
    )
    worst = make_job(
        title="Cook",
        description="Nothing relevant here.",
        posted_at=now - timedelta(days=365),
        eligibility=_verdict(Eligibility.INELIGIBLE),
    )

    assert 0.0 <= prescore(worst, profile, now=now) <= 1.0
    assert 0.0 <= prescore(best, profile, now=now) <= 1.0
    assert prescore(best, profile, now=now) > prescore(worst, profile, now=now)


# --------------------------------------------------------------------------
# What the ordering encodes
# --------------------------------------------------------------------------


def test_eligible_outranks_unknown_which_outranks_ineligible_all_else_equal(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    """Ordering, not filtering: an ineligible job still scores and still
    appears. It simply is not what the LLM budget is spent on first."""
    common = {"title": "Engineer", "description": "A plain listing.", "posted_at": None}
    eligible = make_job(**common, eligibility=_verdict(Eligibility.ELIGIBLE))
    unknown = make_job(**common, eligibility=EligibilityVerdict.unknown())
    ineligible = make_job(**common, eligibility=_verdict(Eligibility.INELIGIBLE))

    ranked = rank([ineligible, unknown, eligible], profile, now=now)

    assert [job.id for job, _ in ranked] == [eligible.id, unknown.id, ineligible.id]


def test_a_user_override_drives_the_ranking_too(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    """The user's own correction must not be undone by the ranker."""
    job = make_job(
        eligibility=_verdict(Eligibility.INELIGIBLE),
        eligibility_override=Eligibility.ELIGIBLE,
    )
    plain = job.model_copy(update={"eligibility_override": None})

    assert prescore(job, profile, now=now) > prescore(plain, profile, now=now)


def test_skill_overlap_dominates_the_score(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    """It is the signal most predictive of the LLM's eventual judgement, so it
    must outweigh a merely flattering title."""
    right_stack = make_job(
        title="Engineer",
        description="Python, TypeScript, PostgreSQL, AWS and Docker throughout.",
        posted_at=None,
    )
    right_title_only = make_job(
        title="Senior Software Engineer",
        description="We work in Elixir and Erlang.",
        posted_at=None,
    )

    assert prescore(right_stack, profile, now=now) > prescore(
        right_title_only, profile, now=now
    )


def test_skill_matching_respects_word_boundaries(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    """ "Go" must not match "going", or every listing scores as a Go job."""
    go_profile = profile.model_copy(update={"skills": ("Go",)})
    real = make_job(description="We write Go services.", posted_at=None)
    spurious = make_job(description="We are going to grow.", posted_at=None)

    assert prescore(real, go_profile, now=now) > prescore(spurious, go_profile, now=now)


def test_an_exact_title_match_outranks_a_shared_word(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    exact = make_job(title="Backend Engineer", description="", posted_at=None)
    partial = make_job(title="Backend Analyst", description="", posted_at=None)

    assert prescore(exact, profile, now=now) > prescore(partial, profile, now=now)


def test_recency_decays_with_age(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    fresh = make_job(posted_at=now - timedelta(days=1))
    middling = make_job(posted_at=now - timedelta(days=30))
    stale = make_job(posted_at=now - timedelta(days=120))

    scores = [prescore(j, profile, now=now) for j in (fresh, middling, stale)]

    assert scores[0] > scores[1] > scores[2]


def test_a_listing_within_the_first_week_is_treated_as_fully_fresh(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    """Boards post at different times of day; a few days' difference is noise,
    not signal."""
    today = make_job(posted_at=now)
    last_week = make_job(posted_at=now - timedelta(days=6))

    assert prescore(today, profile, now=now) == prescore(last_week, profile, now=now)


def test_an_undated_listing_scores_mid_not_zero(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    """A listing should not be punished for a field the source did not provide
    — that would quietly bury every job from boards that omit dates.
    """
    undated = make_job(posted_at=None)
    fresh = make_job(posted_at=now)
    ancient = make_job(posted_at=now - timedelta(days=365))

    undated_score = prescore(undated, profile, now=now)

    assert prescore(ancient, profile, now=now) < undated_score
    assert undated_score < prescore(fresh, profile, now=now)


def test_a_profile_with_no_skills_or_titles_still_ranks_without_error(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    """A brand-new user has declared almost nothing; the stage must degrade to
    recency-and-eligibility ordering rather than fail."""
    bare = profile.model_copy(update={"skills": (), "titles": ()})
    jobs = [make_job(posted_at=now - timedelta(days=d)) for d in (1, 40)]

    ranked = rank(jobs, bare, now=now)

    assert [job.id for job, _ in ranked] == [jobs[0].id, jobs[1].id]


def test_ranking_an_empty_corpus_returns_nothing(
    profile: UserProfile, now: datetime
) -> None:
    assert rank([], profile, now=now) == []


def test_rank_returns_every_job_it_was_given(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    """It orders; it must never drop. Stage 7 losing a job would remove it from
    the run with no drop reason anywhere."""
    jobs = [make_job() for _ in range(5)]

    ranked = rank(jobs, profile, now=now)

    assert sorted(job.id for job, _ in ranked) == sorted(job.id for job in jobs)
