"""Stage 5 — filtering, and everything it is forbidden from doing.

Two properties carry the weight here. Every drop reports a stable reason, so
the run report reads as a funnel and a user can see that their own settings are
costing them listings. And the filter never drops on *absence*: an unparseable
salary, an unstated contract type or an UNKNOWN eligibility are all things the
board failed to say, not things the user failed to qualify for.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from tests.conftest import MakeJob

from outpost.domain.filters import (
    DropReason,
    FilterCriteria,
    FilterOutcome,
    apply_filters,
)
from outpost.domain.models import (
    ContractType,
    DimensionVerdict,
    Eligibility,
    EligibilityDimension,
    EligibilityVerdict,
    JobStatus,
    VerificationResult,
    VerificationTier,
)


def _verdict(eligibility: Eligibility) -> EligibilityVerdict:
    if not eligibility.is_determined:
        return EligibilityVerdict.unknown()
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


def _reason(outcome: FilterOutcome) -> str | None:
    return outcome.dropped[0][1] if outcome.dropped else None


# --------------------------------------------------------------------------
# Defaults are permissive
# --------------------------------------------------------------------------


def test_a_plain_listing_survives_the_default_criteria(
    make_job: MakeJob, now: datetime
) -> None:
    """A first-time user should see their listings, not an empty table caused
    by a filter they never set."""
    outcome = apply_filters([make_job()], FilterCriteria(), now=now)

    assert len(outcome.kept) == 1
    assert outcome.dropped == ()


def test_unknown_eligibility_survives_by_default(
    make_job: MakeJob, now: datetime
) -> None:
    """ADR-0002 rule 2: UNKNOWN is shown by default and the user opts into
    hiding it. We never opt them in — that would re-create the silent loss the
    tri-state exists to prevent."""
    job = make_job(eligibility=_verdict(Eligibility.UNKNOWN))
    criteria = FilterCriteria()

    assert criteria.exclude_unknown_eligibility is False
    assert apply_filters([job], criteria, now=now).kept == (job,)


def test_hiding_unknown_eligibility_is_available_but_opt_in(
    make_job: MakeJob, now: datetime
) -> None:
    """It is the user's own informed choice, which is the difference between a
    tool that hides things and a tool the user told to hide things."""
    job = make_job(eligibility=_verdict(Eligibility.UNKNOWN))

    outcome = apply_filters(
        [job], FilterCriteria(exclude_unknown_eligibility=True), now=now
    )

    assert outcome.kept == ()


def test_a_job_hidden_for_unknown_eligibility_is_reported_as_such(
    make_job: MakeJob, now: datetime
) -> None:
    job = make_job(eligibility=_verdict(Eligibility.UNKNOWN))

    outcome = apply_filters(
        [job], FilterCriteria(exclude_unknown_eligibility=True), now=now
    )

    assert _reason(outcome) != DropReason.INELIGIBLE


# --------------------------------------------------------------------------
# Every reason is reachable and correctly attributed
# --------------------------------------------------------------------------


def test_an_ineligible_listing_is_dropped_as_ineligible(
    make_job: MakeJob, now: datetime
) -> None:
    job = make_job(eligibility=_verdict(Eligibility.INELIGIBLE))

    assert _reason(apply_filters([job], FilterCriteria(), now=now)) == (
        DropReason.INELIGIBLE
    )


def test_a_user_override_rescues_a_listing_the_rules_rejected(
    make_job: MakeJob, now: datetime
) -> None:
    """The filter reads the *effective* eligibility, so the user's override is
    what actually decides what they see."""
    job = make_job(
        eligibility=_verdict(Eligibility.INELIGIBLE),
        eligibility_override=Eligibility.ELIGIBLE,
    )

    assert apply_filters([job], FilterCriteria(), now=now).kept == (job,)


@pytest.mark.parametrize("tier", [VerificationTier.SCAM, VerificationTier.EXPIRED])
def test_scam_and_expired_listings_are_dropped_as_verification(
    tier: VerificationTier, make_job: MakeJob, now: datetime
) -> None:
    job = make_job(verification=VerificationResult(tier=tier))

    assert _reason(apply_filters([job], FilterCriteria(), now=now)) == (
        DropReason.VERIFICATION
    )


@pytest.mark.parametrize(
    "tier",
    [VerificationTier.OK, VerificationTier.SUSPICIOUS, VerificationTier.UNKNOWN],
)
def test_suspicious_and_unverifiable_listings_are_kept(
    tier: VerificationTier, make_job: MakeJob, now: datetime
) -> None:
    """SUSPICIOUS is shown with its reasons attached (FR-4.4), and a check that
    could not complete must never penalise a listing (FR-4.5)."""
    job = make_job(verification=VerificationResult(tier=tier))

    assert apply_filters([job], FilterCriteria(), now=now).kept == (job,)


def test_a_non_engineering_title_is_dropped_as_title_excluded(
    make_job: MakeJob, now: datetime
) -> None:
    job = make_job(title="Technical Recruiter")

    assert _reason(apply_filters([job], FilterCriteria(), now=now)) == (
        DropReason.TITLE_EXCLUDED
    )


def test_a_title_missing_a_required_phrase_is_dropped_as_title_not_matched(
    make_job: MakeJob, now: datetime
) -> None:
    job = make_job(title="Senior Backend Engineer")

    outcome = apply_filters(
        [job], FilterCriteria(required_title_phrases=("data scientist",)), now=now
    )

    assert _reason(outcome) == DropReason.TITLE_NOT_MATCHED


def test_a_listing_older_than_the_age_limit_is_dropped_as_too_old(
    make_job: MakeJob, now: datetime
) -> None:
    job = make_job(posted_at=now - timedelta(days=90))

    outcome = apply_filters([job], FilterCriteria(max_age_days=45), now=now)

    assert _reason(outcome) == DropReason.TOO_OLD


def test_an_undated_listing_is_never_dropped_for_age(
    make_job: MakeJob, now: datetime
) -> None:
    """No date is a missing field, not an old job."""
    job = make_job(posted_at=None)

    assert apply_filters([job], FilterCriteria(max_age_days=45), now=now).kept == (job,)


def test_an_unwanted_contract_type_is_dropped_as_contract_type(
    make_job: MakeJob, now: datetime
) -> None:
    job = make_job(contract_text="Part-time role")

    outcome = apply_filters(
        [job],
        FilterCriteria(contract_types=frozenset({ContractType.FULL_TIME})),
        now=now,
    )

    assert _reason(outcome) == DropReason.CONTRACT_TYPE


def test_an_unknown_contract_type_never_drops(make_job: MakeJob, now: datetime) -> None:
    """A listing is not disqualified by a field its board omitted — and most
    boards omit this one."""
    job = make_job(title="Senior Backend Engineer", description="Python role.")

    assert job.contract_type is ContractType.UNKNOWN
    outcome = apply_filters(
        [job],
        FilterCriteria(contract_types=frozenset({ContractType.FULL_TIME})),
        now=now,
    )

    assert outcome.kept == (job,)


def test_pay_below_the_users_floor_is_dropped_as_below_min_rate(
    make_job: MakeJob, now: datetime
) -> None:
    job = make_job(compensation_text="$12 per hour")

    outcome = apply_filters(
        [job], FilterCriteria(min_hourly_rate_usd=Decimal(60)), now=now
    )

    assert _reason(outcome) == DropReason.BELOW_MIN_RATE


def test_a_yearly_salary_is_converted_before_comparison(
    make_job: MakeJob, now: datetime
) -> None:
    job = make_job(compensation_text="$150,000 per year")

    outcome = apply_filters(
        [job], FilterCriteria(min_hourly_rate_usd=Decimal(60)), now=now
    )

    assert outcome.kept == (job,)


def test_a_listing_with_no_technical_signal_is_dropped_as_no_signal(
    make_job: MakeJob, now: datetime
) -> None:
    job = make_job(
        title="Community Coordinator",
        description="Organise our meetups and write the newsletter.",
    )

    outcome = apply_filters(
        [job],
        FilterCriteria(require_tech_signal=True, tech_keywords=("python", "rust")),
        now=now,
    )

    assert _reason(outcome) == DropReason.NO_SIGNAL


def test_the_tech_signal_gate_matches_whole_words_only(
    make_job: MakeJob, now: datetime
) -> None:
    """ "Go" must not match "going". The gate is calibrated to the user's own
    skills, so a substring match would wave through most of the corpus."""
    going = make_job(title="Coordinator", description="We are going to grow fast.")
    golang = make_job(title="Coordinator", description="We write Go services.")
    criteria = FilterCriteria(tech_keywords=("go",))

    assert apply_filters([going], criteria, now=now).kept == ()
    assert apply_filters([golang], criteria, now=now).kept == (golang,)


def test_a_dismissed_listing_is_dropped_as_status_dismissed(
    make_job: MakeJob, now: datetime
) -> None:
    job = make_job(status=JobStatus.DISMISSED)

    assert _reason(apply_filters([job], FilterCriteria(), now=now)) == (
        DropReason.STATUS_DISMISSED
    )


def test_dismissed_listings_can_be_asked_for_explicitly(
    make_job: MakeJob, now: datetime
) -> None:
    job = make_job(status=JobStatus.DISMISSED)

    outcome = apply_filters([job], FilterCriteria(include_dismissed=True), now=now)

    assert outcome.kept == (job,)


# --------------------------------------------------------------------------
# Absence is never a drop
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "compensation_text",
    ["Competitive salary", "DOE", "3+ years experience", None],
)
def test_an_unparseable_salary_never_causes_a_drop(
    compensation_text: str | None, make_job: MakeJob, now: datetime
) -> None:
    """Even with a floor set.

    "We could not read the pay" is not "the pay is too low". Dropping here
    would hide well-paid jobs from the users most likely to set a floor, and
    they would never find out (FR-2.3).
    """
    job = make_job(compensation_text=compensation_text)

    outcome = apply_filters(
        [job], FilterCriteria(min_hourly_rate_usd=Decimal(200)), now=now
    )

    assert job.compensation.is_empty or job.compensation.currency is None
    assert outcome.kept == (job,)


def test_pay_in_a_currency_we_cannot_convert_never_causes_a_drop(
    make_job: MakeJob, now: datetime
) -> None:
    """The FX table is deliberately approximate and offline; a currency missing
    from it means "cannot compare", not "too low"."""
    job = make_job(compensation_text="¥12,000,000 per year")

    outcome = apply_filters(
        [job], FilterCriteria(min_hourly_rate_usd=Decimal(200)), now=now
    )

    assert outcome.kept == (job,)


def test_an_unverified_listing_is_never_dropped_for_verification(
    make_job: MakeJob, now: datetime
) -> None:
    job = make_job(verification=None)

    assert apply_filters([job], FilterCriteria(), now=now).kept == (job,)


# --------------------------------------------------------------------------
# The funnel
# --------------------------------------------------------------------------


def test_drop_counts_report_a_funnel_by_reason(
    make_job: MakeJob, now: datetime
) -> None:
    """These counts are how a user discovers a filter is silently costing them
    two hundred listings."""
    jobs = [
        make_job(),
        make_job(eligibility=_verdict(Eligibility.INELIGIBLE)),
        make_job(eligibility=_verdict(Eligibility.INELIGIBLE)),
        make_job(title="Sales Engineer"),
        make_job(status=JobStatus.DISMISSED),
        make_job(posted_at=now - timedelta(days=200)),
    ]

    outcome = apply_filters([*jobs], FilterCriteria(max_age_days=45), now=now)

    assert len(outcome.kept) == 1
    assert outcome.drop_counts == {
        DropReason.INELIGIBLE: 2,
        DropReason.TITLE_EXCLUDED: 1,
        DropReason.STATUS_DISMISSED: 1,
        DropReason.TOO_OLD: 1,
    }
    assert sum(outcome.drop_counts.values()) == len(outcome.dropped)


def test_only_the_first_failing_check_is_reported(
    make_job: MakeJob, now: datetime
) -> None:
    """Checks are ordered cheapest-first and the first failure is the reason,
    so the counts read as a funnel rather than as overlapping totals."""
    job = make_job(
        title="Sales Engineer",
        status=JobStatus.DISMISSED,
        eligibility=_verdict(Eligibility.INELIGIBLE),
    )

    outcome = apply_filters([job], FilterCriteria(), now=now)

    assert outcome.drop_counts == {DropReason.STATUS_DISMISSED: 1}


def test_filtering_nothing_produces_an_empty_outcome(now: datetime) -> None:
    outcome = apply_filters([], FilterCriteria(), now=now)

    assert outcome.kept == ()
    assert outcome.drop_counts == {}


def test_kept_order_follows_input_order(make_job: MakeJob, now: datetime) -> None:
    """Filtering partitions; ordering is stage 7's job, and a filter that also
    reordered would make the two stages hard to reason about together."""
    jobs = [make_job(), make_job(title="Recruiter"), make_job()]

    outcome = apply_filters(jobs, FilterCriteria(), now=now)

    assert [j.id for j in outcome.kept] == [jobs[0].id, jobs[2].id]
