"""Built-in predicates — the escape hatch where rules become arithmetic.

The property that matters here is the ADR-0002 guarantee expressed one level
down: a predicate that cannot compute returns ``None``, which produces no
verdict at all, which leaves the dimension UNKNOWN. A predicate is never
allowed to guess, because a guess here becomes a confident INELIGIBLE and a
hidden job.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from tests.conftest import MakeJob

from outpost.domain.models import ContractType, Job, UserProfile
from outpost.domain.predicates import (
    PREDICATES,
    PredicateContext,
    contract_type_mismatch,
    currency_unsupported,
    timezone_insufficient_overlap,
    timezone_sufficient_overlap,
    unknown_predicates,
)


def _ctx(job: Job, profile: UserProfile, now: datetime) -> PredicateContext:
    return PredicateContext(job=job, profile=profile, now=now)


# --------------------------------------------------------------------------
# Timezone overlap
# --------------------------------------------------------------------------


def test_six_hours_of_overlap_with_pacific_is_unreachable_from_india(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    """IST and US Pacific share almost no conventional working hours.

    This is the case the predicate exists for: it is real arithmetic over UTC
    offsets, not a phrase anyone could have written a rule for.
    """
    job = make_job(description="You must have 6 hours of overlap with PST.")

    evidence = timezone_insufficient_overlap(_ctx(job, profile, now))

    assert evidence is not None
    assert "6h overlap with PST" in evidence
    assert "Asia/Kolkata" in evidence


def test_two_hours_of_overlap_with_central_european_time_is_satisfiable(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    """India to CET is a few hours, so a modest requirement is positive
    evidence rather than a blocker."""
    job = make_job(description="We require 2 hours of overlap with CET.")
    context = _ctx(job, profile, now)

    assert timezone_insufficient_overlap(context) is None
    assert timezone_sufficient_overlap(context) == (
        "Stated overlap requirement of 2h is satisfiable"
    )


def test_both_phrasings_of_an_overlap_requirement_are_recognised(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    for text in (
        "Must overlap at least 6 hours with PST.",
        "We need 6 hours of overlap with PST.",
    ):
        job = make_job(description=text)
        assert timezone_insufficient_overlap(_ctx(job, profile, now)) is not None, text


# --- the ADR-0002 guarantee, at the predicate level ------------------------


def test_a_listing_with_no_stated_overlap_requirement_yields_no_verdict(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    """Nothing to compute means no answer — and no answer means UNKNOWN.

    Returning "sufficient" here would invent positive evidence; returning
    "insufficient" would hide the job. Both are worse than silence.
    """
    job = make_job(description="Fully remote engineering role, flexible hours.")
    context = _ctx(job, profile, now)

    assert timezone_insufficient_overlap(context) is None
    assert timezone_sufficient_overlap(context) is None


def test_an_unrecognised_timezone_in_the_listing_yields_no_verdict(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    """Abbreviations are ambiguous by nature — CST is both US Central and China
    Standard. Where the mapping is not unambiguous we decline rather than
    guess, because a guess becomes a confident wrong INELIGIBLE."""
    job = make_job(description="Requires 4 hours of overlap with NZDT.")

    assert timezone_insufficient_overlap(_ctx(job, profile, now)) is None


def test_an_unknown_user_timezone_yields_no_verdict(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    """Missing tzdata (common on bare Windows) must degrade to UNKNOWN rather
    than to a verdict computed from a zone we could not load."""
    unresolvable = profile.model_copy(update={"timezone": "Mars/Olympus_Mons"})
    job = make_job(description="Requires 6 hours of overlap with PST.")

    assert timezone_insufficient_overlap(_ctx(job, unresolvable, now)) is None


def test_a_timezone_mentioned_far_from_the_requirement_is_not_treated_as_it(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    """A "PST" in the company's address paragraph is not the constraint, so the
    predicate declines rather than attaching the requirement to it."""
    filler = "We care deeply about documentation and code review. " * 6
    job = make_job(
        description=f"Requires 6 hours of overlap. {filler} Our office is PST."
    )

    assert timezone_insufficient_overlap(_ctx(job, profile, now)) is None


def test_an_unparseable_overlap_number_yields_no_verdict(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    job = make_job(description="Significant overlap with PST hours is expected.")

    assert timezone_insufficient_overlap(_ctx(job, profile, now)) is None


def test_the_two_timezone_predicates_never_both_fire(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    """They are inverses. If both could fire, the first-match-wins ordering in
    the ruleset would silently decide eligibility by declaration order."""
    for text in (
        "Requires 6 hours of overlap with PST.",
        "Requires 2 hours of overlap with CET.",
        "No overlap requirement stated.",
    ):
        context = _ctx(make_job(description=text), profile, now)
        fired = [
            p(context)
            for p in (timezone_insufficient_overlap, timezone_sufficient_overlap)
        ]
        assert sum(1 for f in fired if f is not None) <= 1, text


# --------------------------------------------------------------------------
# Contract
# --------------------------------------------------------------------------


def test_a_contract_type_the_user_does_not_want_is_reported_with_both_sides(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    full_time_only = profile.model_copy(
        update={"contract_types": (ContractType.FULL_TIME,)}
    )
    job = make_job(contract_text="This is a part-time role.")

    evidence = contract_type_mismatch(_ctx(job, full_time_only, now))

    assert evidence == "Listing is part time; you accept full time"


def test_a_matching_contract_type_produces_no_verdict(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    job = make_job(contract_text="Full-time permanent position.")

    assert contract_type_mismatch(_ctx(job, profile, now)) is None


def test_an_unknown_contract_type_produces_no_verdict(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    """A listing is not disqualified by a field its board simply omitted."""
    job = make_job(title="Senior Backend Engineer", description="Python role.")

    assert job.contract_type is ContractType.UNKNOWN
    assert contract_type_mismatch(_ctx(job, profile, now)) is None


def test_a_user_with_no_contract_preference_is_never_mismatched(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    no_preference = profile.model_copy(update={"contract_types": ()})
    job = make_job(contract_text="This is an internship.")

    assert contract_type_mismatch(_ctx(job, no_preference, now)) is None


# --------------------------------------------------------------------------
# Currency
# --------------------------------------------------------------------------


def test_pay_in_an_unaccepted_currency_is_reported(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    job = make_job(compensation_text="₹ 2500 per hour")

    evidence = currency_unsupported(_ctx(job, profile, now))

    assert evidence is not None
    assert evidence.startswith("Pay quoted in INR")


def test_an_accepted_currency_produces_no_verdict(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    job = make_job(compensation_text="$120,000 - $150,000 per year")

    assert currency_unsupported(_ctx(job, profile, now)) is None


def test_currency_comparison_ignores_case_on_both_sides(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    lowercase = profile.model_copy(update={"currencies": ("usd",)})
    job = make_job(compensation_text="$120,000 per year")

    assert currency_unsupported(_ctx(job, lowercase, now)) is None


def test_an_unparseable_salary_produces_no_currency_verdict(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    """ "Competitive salary" names no currency, so there is nothing to object
    to — and inventing one would block a job on a field nobody stated."""
    job = make_job(compensation_text="Competitive salary, DOE")

    assert job.compensation.currency is None
    assert currency_unsupported(_ctx(job, profile, now)) is None


def test_a_user_who_accepts_any_currency_is_never_blocked_on_currency(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    anything = profile.model_copy(update={"currencies": ()})
    job = make_job(compensation_text="₹ 2500 per hour")

    assert currency_unsupported(_ctx(job, anything, now)) is None


# --------------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(PREDICATES))
def test_every_registered_predicate_declines_on_an_empty_listing(
    name: str, make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    """No input, no verdict — for every predicate, not just the ones we thought
    to test individually."""
    job = make_job(title="Engineer", description="", location_text=None)

    assert PREDICATES[name](_ctx(job, profile, now)) is None


def test_a_predicate_name_this_build_does_not_implement_is_reported() -> None:
    """Checked at ruleset load: a typo'd predicate would otherwise never match,
    and a rule that never matches is indistinguishable from a listing with no
    restrictions."""
    assert unknown_predicates({"timezone.insufficient_overlap"}) == set()
    assert unknown_predicates({"timezone.typo_overlap"}) == {"timezone.typo_overlap"}
