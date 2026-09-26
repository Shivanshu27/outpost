"""Stage 6 (part one) — legitimacy heuristics.

Same stance as eligibility: this classifies, it does not delete. Only
unambiguous SCAM signals and confirmed EXPIRED responses are excluded by
default; SUSPICIOUS is shown with its reasons attached, because a heuristic
confident enough to hide a listing would have to be far better than phrase
rules can be (FR-4.4).

Scores are additive. One strong signal is enough to condemn; a single soft one
only raises a flag.
"""

from __future__ import annotations

from tests.conftest import MakeJob

from outpost.domain.models import VerificationResult, VerificationTier
from outpost.domain.verification import CLOSED_PHRASES, assess_legitimacy, looks_closed

# --------------------------------------------------------------------------
# Tiering
# --------------------------------------------------------------------------


def test_an_ordinary_listing_is_ok_with_no_reasons(make_job: MakeJob) -> None:
    result = assess_legitimacy(
        make_job(description="We are hiring a backend engineer. Competitive salary.")
    )

    assert result.tier is VerificationTier.OK
    assert result.reasons == ()


def test_a_single_strong_signal_is_enough_for_scam(make_job: MakeJob) -> None:
    """An upfront fee is unambiguous — legitimate employers never charge
    candidates — so it does not need corroboration."""
    result = assess_legitimacy(
        make_job(description="Pay a one-time registration fee to secure your slot.")
    )

    assert result.tier is VerificationTier.SCAM
    assert len(result.reasons) == 1


def test_a_single_soft_signal_is_only_suspicious(make_job: MakeJob) -> None:
    """Weight-2 signals need corroboration.

    "No experience required" is common in genuinely junior postings, so on its
    own it flags the listing for the user's attention rather than condemning
    it — and a SUSPICIOUS listing is still shown.
    """
    result = assess_legitimacy(
        make_job(description="No experience necessary — we will train you.")
    )

    assert result.tier is VerificationTier.SUSPICIOUS
    assert result.tier.excluded_by_default is False


def test_two_soft_signals_corroborate_into_scam(make_job: MakeJob) -> None:
    """Scores add, so two independent soft indicators reach the same threshold
    as one strong one. That is the corroboration the weights encode."""
    result = assess_legitimacy(
        make_job(
            description=(
                "No experience required. Payment in bitcoin, guaranteed income "
                "from week one."
            )
        )
    )

    assert result.tier is VerificationTier.SCAM
    assert len(result.reasons) >= 2


def test_an_off_platform_messaging_handoff_is_a_scam_signal(
    make_job: MakeJob,
) -> None:
    result = assess_legitimacy(
        make_job(description="Interested? Contact us on Telegram to interview today.")
    )

    assert result.tier is VerificationTier.SCAM


def test_a_request_for_banking_details_is_a_scam_signal(make_job: MakeJob) -> None:
    result = assess_legitimacy(
        make_job(description="Bank account and routing number required to start.")
    )

    assert result.tier is VerificationTier.SCAM


def test_signals_are_matched_across_the_whole_listing_not_just_the_body(
    make_job: MakeJob,
) -> None:
    """Scammers put the hook in the title as often as the description."""
    result = assess_legitimacy(
        make_job(
            title="Data Entry — earn $900 per week",
            description="Flexible hours.",
        )
    )

    assert result.tier is not VerificationTier.OK


def test_scam_and_expired_are_the_only_tiers_excluded_by_default() -> None:
    """Recall over precision: everything else is shown, annotated."""
    excluded = {t for t in VerificationTier if t.excluded_by_default}

    assert excluded == {VerificationTier.SCAM, VerificationTier.EXPIRED}


# --------------------------------------------------------------------------
# Reasons
# --------------------------------------------------------------------------


def test_every_triggered_signal_contributes_a_human_readable_reason(
    make_job: MakeJob,
) -> None:
    """A flag the UI cannot explain reads as a bug in Outpost rather than a
    problem with the listing."""
    result = assess_legitimacy(
        make_job(
            description=(
                "Pay a security deposit before onboarding, then contact us on WhatsApp."
            )
        )
    )

    assert len(result.reasons) >= 2
    for reason in result.reasons:
        assert reason[0].isupper()
        assert len(reason.split()) >= 4
        assert not reason.endswith(".")


def test_a_check_that_could_not_complete_is_unknown_and_not_penalised() -> None:
    """A network failure must never look like a finding (FR-4.5)."""
    result = VerificationResult.unknown("connection timed out")

    assert result.tier is VerificationTier.UNKNOWN
    assert result.tier.excluded_by_default is False
    assert result.reasons == ("connection timed out",)


# --------------------------------------------------------------------------
# Closed listings
# --------------------------------------------------------------------------


def test_every_shipped_closed_phrase_is_detected() -> None:
    for phrase in CLOSED_PHRASES:
        assert looks_closed(f"<h1>Notice</h1> {phrase}. Please browse other roles.")


def test_closed_detection_ignores_case(make_job: MakeJob) -> None:
    assert looks_closed("THIS POSITION HAS BEEN FILLED")
    assert looks_closed("This Position Has Been Filled")


def test_an_open_page_is_not_reported_as_closed() -> None:
    assert not looks_closed(
        "Apply now. We are actively interviewing for this position."
    )


def test_an_empty_page_is_not_reported_as_closed() -> None:
    """A fetch that returned nothing is a failed check, not a closed job — and
    the distinction is what keeps a flaky network from expiring live listings.
    """
    assert not looks_closed("")
