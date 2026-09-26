"""Eligibility — the invariant the rest of the product is built on.

ADR-0002 says eligibility is tri-state and that ``UNKNOWN`` never collapses to
``INELIGIBLE``. That is not a stylistic preference: the failure it prevents is
the invisible one, where a job the user could have taken is silently dropped
because a rule did not match, and the user cannot tell the difference between
"a quiet week" and "the parser broke".

These tests therefore assert the *reason* as much as the mechanics — that an
unmatched rule yields UNKNOWN, that every determined verdict carries the
evidence that makes it auditable, and that a naive phrase match cannot mark the
corpus US-only.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError
from tests.conftest import MakeJob

from outpost.domain.eligibility import resolve_dimension, resolve_eligibility
from outpost.domain.models import (
    DimensionVerdict,
    Eligibility,
    EligibilityDimension,
    EligibilityVerdict,
    UserProfile,
)
from outpost.domain.rules import CompiledRuleSet, Rule, RuleSet

LOCATION = EligibilityDimension.LOCATION


def _location_verdict(verdict: EligibilityVerdict) -> DimensionVerdict:
    return next(d for d in verdict.dimensions if d.dimension is LOCATION)


# --------------------------------------------------------------------------
# The tri-state invariant (ADR-0002)
# --------------------------------------------------------------------------


def test_a_listing_matching_no_rule_is_unknown_not_ineligible(
    make_job: MakeJob,
    profile: UserProfile,
    ruleset: CompiledRuleSet,
    now: datetime,
) -> None:
    """ADR-0002: absence of evidence is never evidence of ineligibility.

    This is *the* invariant. A listing that says nothing about location,
    authorisation, timezone or currency has given us no reason to think the
    user cannot hold it — so it is UNKNOWN, it stays visible, and the user
    decides. Defaulting to INELIGIBLE here would hide it forever, invisibly.
    """
    job = make_job(
        title="Software Engineer",
        location_text="Remote",
        description="We are a small team building developer tooling in Python.",
    )

    verdict = resolve_eligibility(job, profile, ruleset, now=now)

    assert verdict.eligibility is Eligibility.UNKNOWN
    assert verdict.eligibility is not Eligibility.INELIGIBLE
    assert all(d.eligibility is Eligibility.UNKNOWN for d in verdict.dimensions)


def test_unknown_is_the_default_for_every_dimension_of_a_silent_listing(
    make_job: MakeJob,
    profile: UserProfile,
    ruleset: CompiledRuleSet,
    now: datetime,
) -> None:
    """Every axis defaults independently, so one silent axis cannot be read as
    a blocker on another."""
    job = make_job(description="Build things. Ship things.", location_text=None)

    for dimension in ruleset.dimensions:
        verdict = resolve_dimension(dimension, job, profile, ruleset, now=now)
        assert verdict.eligibility is Eligibility.UNKNOWN, dimension
        assert verdict.rule_id is None


def test_an_empty_ruleset_yields_unknown_rather_than_a_verdict(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    """A ruleset that failed to cover anything must look like ignorance, not
    like permission or prohibition."""
    empty = CompiledRuleSet(version=1, by_dimension={})

    verdict = resolve_eligibility(make_job(), profile, empty, now=now)

    assert verdict.eligibility is Eligibility.UNKNOWN
    assert verdict.dimensions == ()


def test_unknown_eligibility_is_not_determined() -> None:
    assert Eligibility.UNKNOWN.is_determined is False
    assert Eligibility.ELIGIBLE.is_determined is True
    assert Eligibility.INELIGIBLE.is_determined is True


# --------------------------------------------------------------------------
# A verdict you cannot explain is a bug (ADR-0002)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("eligibility", [Eligibility.ELIGIBLE, Eligibility.INELIGIBLE])
def test_a_determined_verdict_without_evidence_is_rejected_by_the_model(
    eligibility: Eligibility,
) -> None:
    """The type system, not discipline, enforces auditability.

    ADR-0002 requires every non-UNKNOWN verdict to carry the rule that produced
    it and a human-readable reason. Making that a model validator means a rule
    engine cannot emit an unexplainable verdict even by accident.
    """
    with pytest.raises(ValidationError):
        DimensionVerdict(dimension=LOCATION, eligibility=eligibility)

    with pytest.raises(ValidationError):
        DimensionVerdict(
            dimension=LOCATION, eligibility=eligibility, rule_id="loc.us_only"
        )

    with pytest.raises(ValidationError):
        DimensionVerdict(dimension=LOCATION, eligibility=eligibility, evidence="why")


def test_an_unknown_verdict_needs_no_evidence() -> None:
    """The mirror image: UNKNOWN is the *absence* of evidence, so demanding
    evidence for it would be incoherent."""
    verdict = DimensionVerdict.unknown(LOCATION)

    assert verdict.eligibility is Eligibility.UNKNOWN
    assert verdict.rule_id is None and verdict.evidence is None


def test_a_rule_verdict_carries_the_rule_id_and_the_matched_phrase(
    make_job: MakeJob,
    profile: UserProfile,
    ruleset: CompiledRuleSet,
    now: datetime,
) -> None:
    """The user must be able to see *which* rule fired on *which* words — that
    is what turns a wrong rule into a bug report instead of a mystery."""
    job = make_job(location_text="Remote (US)")

    location = _location_verdict(resolve_eligibility(job, profile, ruleset, now=now))

    assert location.eligibility is Eligibility.INELIGIBLE
    assert location.rule_id == "loc.us_only"
    assert location.evidence
    assert location.matched_text is not None
    assert location.matched_text.lower() == "remote (us)"
    assert location.source_field is not None


def test_a_rule_that_cannot_assert_unknown_is_rejected_at_definition_time() -> None:
    """A rule asserting UNKNOWN would claim positive evidence of ignorance, and
    would mask a later rule that would have matched."""
    with pytest.raises(ValidationError):
        Rule(
            id="loc.nonsense",
            dimension=LOCATION,
            when={"any_phrase": ["somewhere"]},
            verdict=Eligibility.UNKNOWN,
            evidence="cannot tell",
        )


def test_the_verdict_summary_names_the_blocking_evidence(
    make_job: MakeJob,
    profile: UserProfile,
    ruleset: CompiledRuleSet,
    now: datetime,
) -> None:
    job = make_job(location_text="Remote (US)")

    verdict = resolve_eligibility(job, profile, ruleset, now=now)

    assert verdict.summary == "Listing restricts hiring to the United States"
    assert [d.rule_id for d in verdict.blocking] == ["loc.us_only"]


def test_an_unknown_verdict_says_so_rather_than_implying_rejection(
    make_job: MakeJob,
    profile: UserProfile,
    ruleset: CompiledRuleSet,
    now: datetime,
) -> None:
    """The UI string matters: "could not determine" reads as an invitation to
    look; anything resembling "not eligible" reads as a closed door."""
    verdict = resolve_eligibility(make_job(), profile, ruleset, now=now)

    assert verdict.summary == "Could not determine eligibility from the listing"


# --------------------------------------------------------------------------
# combine() precedence (ADR-0007)
# --------------------------------------------------------------------------


def _determined(
    dimension: EligibilityDimension, eligibility: Eligibility
) -> DimensionVerdict:
    return DimensionVerdict(
        dimension=dimension,
        eligibility=eligibility,
        rule_id=f"{dimension.value}.test",
        evidence="because the fixture says so",
    )


def test_any_ineligible_dimension_dominates_the_combined_verdict() -> None:
    """One hard blocker is enough: the user cannot hold the job even if every
    other axis is fine."""
    combined = EligibilityVerdict.combine(
        [
            _determined(LOCATION, Eligibility.INELIGIBLE),
            _determined(EligibilityDimension.TIMEZONE, Eligibility.ELIGIBLE),
            DimensionVerdict.unknown(EligibilityDimension.CURRENCY),
        ]
    )

    assert combined.eligibility is Eligibility.INELIGIBLE


def test_an_eligible_dimension_wins_when_nothing_blocks() -> None:
    """Positive evidence on one axis plus silence elsewhere is ELIGIBLE.

    ADR-0007 rejected the stricter reading (any UNKNOWN dominates) on contact
    with real data: "work from anywhere" says nothing about currency, and
    treating that silence as doubt made ELIGIBLE nearly unreachable.
    """
    combined = EligibilityVerdict.combine(
        [
            _determined(LOCATION, Eligibility.ELIGIBLE),
            DimensionVerdict.unknown(EligibilityDimension.CURRENCY),
            DimensionVerdict.unknown(EligibilityDimension.TIMEZONE),
        ]
    )

    assert combined.eligibility is Eligibility.ELIGIBLE


def test_unknown_only_when_no_dimension_matched_anything() -> None:
    combined = EligibilityVerdict.combine(
        [
            DimensionVerdict.unknown(LOCATION),
            DimensionVerdict.unknown(EligibilityDimension.CURRENCY),
        ]
    )

    assert combined.eligibility is Eligibility.UNKNOWN


def test_combining_no_dimensions_at_all_is_unknown() -> None:
    assert EligibilityVerdict.combine([]).eligibility is Eligibility.UNKNOWN


def test_combining_preserves_every_dimension_for_audit() -> None:
    """The combined answer is one enum, but the reasons behind it must survive
    — the detail view renders each dimension separately."""
    dimensions = [
        _determined(LOCATION, Eligibility.INELIGIBLE),
        DimensionVerdict.unknown(EligibilityDimension.CURRENCY),
    ]

    combined = EligibilityVerdict.combine(dimensions)

    assert combined.dimensions == tuple(dimensions)


# --------------------------------------------------------------------------
# One ruleset, many users: unless_profile_matches (ADR-0007)
# --------------------------------------------------------------------------


def test_us_only_blocks_an_indian_user(
    make_job: MakeJob,
    profile: UserProfile,
    ruleset: CompiledRuleSet,
    now: datetime,
) -> None:
    job = make_job(location_text="Remote — US only")

    verdict = resolve_eligibility(job, profile, ruleset, now=now)

    assert verdict.eligibility is Eligibility.INELIGIBLE
    assert _location_verdict(verdict).rule_id == "loc.us_only"


def test_us_only_does_not_block_a_us_user_under_the_same_ruleset(
    make_job: MakeJob,
    us_profile: UserProfile,
    ruleset: CompiledRuleSet,
    now: datetime,
) -> None:
    """Same rules, different declared country, opposite outcome.

    This is why rules are data with a profile predicate rather than a
    hardcoded ``INDIA_BLOCKED_PATTERNS`` list (ADR-0007): a user in Austin and
    a user in Bengaluru share one ruleset without forking it.
    """
    job = make_job(location_text="Remote — US only")

    verdict = resolve_eligibility(job, us_profile, ruleset, now=now)

    assert verdict.eligibility is not Eligibility.INELIGIBLE
    assert _location_verdict(verdict).rule_id is None


def test_the_exemption_matches_country_codes_case_insensitively(
    make_job: MakeJob,
    profile: UserProfile,
    ruleset: CompiledRuleSet,
    now: datetime,
) -> None:
    """Country codes are written inconsistently by users and ruleset authors
    alike; a case mismatch here would silently fail to exempt someone who *is*
    exempt, which is the silent-loss failure again."""
    lowercase_us = profile.model_copy(update={"country": "us"})
    job = make_job(location_text="Remote — US only")

    verdict = resolve_eligibility(job, lowercase_us, ruleset, now=now)

    assert verdict.eligibility is not Eligibility.INELIGIBLE


def test_currency_matching_is_case_insensitive(
    make_job: MakeJob,
    profile: UserProfile,
    ruleset: CompiledRuleSet,
    now: datetime,
) -> None:
    lowercase_currencies = profile.model_copy(update={"currencies": ("usd",)})
    job = make_job(compensation_text="$120,000 - $150,000 per year")

    verdict = resolve_dimension(
        EligibilityDimension.CURRENCY, job, lowercase_currencies, ruleset, now=now
    )

    assert verdict.eligibility is Eligibility.UNKNOWN


def test_an_exemption_on_any_single_facet_is_enough(
    make_job: MakeJob,
    profile: UserProfile,
    ruleset: CompiledRuleSet,
    now: datetime,
) -> None:
    """``unless_profile_matches`` is OR across facets: a user with US work
    authorisation is exempt from the sponsorship rule regardless of where they
    live."""
    authorised = profile.model_copy(update={"work_authorisation": ("US",)})
    job = make_job(description="We do not sponsor visas for this role.")

    blocked = resolve_dimension(
        EligibilityDimension.AUTHORISATION, job, profile, ruleset, now=now
    )
    exempt = resolve_dimension(
        EligibilityDimension.AUTHORISATION, job, authorised, ruleset, now=now
    )

    assert blocked.eligibility is Eligibility.INELIGIBLE
    assert exempt.eligibility is Eligibility.UNKNOWN


# --------------------------------------------------------------------------
# Ordering
# --------------------------------------------------------------------------


def test_the_first_matching_rule_in_a_dimension_wins(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    """Declaration order is the rule language's only control flow (ADR-0007),
    so a specific rule placed first must beat a general one placed later."""
    ruleset = RuleSet(
        version=1,
        rules=(
            Rule(
                id="loc.specific",
                dimension=LOCATION,
                when={"any_phrase": ["remote in europe"]},
                verdict=Eligibility.ELIGIBLE,
                evidence="specific rule",
            ),
            Rule(
                id="loc.general",
                dimension=LOCATION,
                when={"any_phrase": ["remote"]},
                verdict=Eligibility.INELIGIBLE,
                evidence="general rule",
            ),
        ),
    ).compile()
    job = make_job(location_text="Remote in Europe")

    verdict = resolve_dimension(LOCATION, job, profile, ruleset, now=now)

    assert verdict.rule_id == "loc.specific"
    assert verdict.eligibility is Eligibility.ELIGIBLE


def test_a_user_override_of_a_shipped_rule_keeps_its_evaluation_position() -> None:
    """Merging replaces in place, so correcting a rule cannot accidentally
    change *when* it runs — which, with first-match-wins, would change results
    elsewhere."""
    base = RuleSet(
        version=1,
        rules=(
            Rule(
                id="loc.first",
                dimension=LOCATION,
                when={"any_phrase": ["alpha"]},
                verdict=Eligibility.ELIGIBLE,
                evidence="first",
            ),
            Rule(
                id="loc.second",
                dimension=LOCATION,
                when={"any_phrase": ["beta"]},
                verdict=Eligibility.ELIGIBLE,
                evidence="second",
            ),
        ),
    )
    override = RuleSet(
        version=1,
        rules=(
            Rule(
                id="loc.first",
                dimension=LOCATION,
                when={"any_phrase": ["gamma"]},
                verdict=Eligibility.INELIGIBLE,
                evidence="corrected",
            ),
            Rule(
                id="loc.third",
                dimension=LOCATION,
                when={"any_phrase": ["delta"]},
                verdict=Eligibility.ELIGIBLE,
                evidence="appended",
            ),
        ),
    )

    merged = base.merge(override)

    assert [r.id for r in merged.rules] == ["loc.first", "loc.second", "loc.third"]
    assert merged.rules[0].evidence == "corrected"


def test_disabled_rules_are_not_compiled_and_never_fire(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    ruleset = RuleSet(
        version=1,
        rules=(
            Rule(
                id="loc.off",
                dimension=LOCATION,
                when={"any_phrase": ["remote"]},
                verdict=Eligibility.INELIGIBLE,
                evidence="disabled",
                enabled=False,
            ),
        ),
    ).compile()

    verdict = resolve_eligibility(
        make_job(location_text="Remote"), profile, ruleset, now=now
    )

    assert verdict.eligibility is Eligibility.UNKNOWN


# --------------------------------------------------------------------------
# Word boundaries — a real regression class
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Report status to us weekly and we will discuss priorities.",
        "We are the leading platform in the industry.",
        "Please discuss your availability with us before the interview.",
        "A serious business, focused on customer trust.",
    ],
)
def test_incidental_words_containing_us_do_not_trigger_the_us_only_rule(
    text: str,
    make_job: MakeJob,
    profile: UserProfile,
    ruleset: CompiledRuleSet,
    now: datetime,
) -> None:
    """A naive substring match on "us" marks most of the corpus US-only.

    "status", "industry", "discuss" and "business" all contain it, and a
    listing wrongly labelled INELIGIBLE is hidden by the default filter — the
    exact silent loss ADR-0002 exists to prevent. Phrases are therefore
    compiled with word-boundary anchors, and this is the regression test for
    that.
    """
    job = make_job(description=text, location_text=None)

    verdict = resolve_eligibility(job, profile, ruleset, now=now)

    assert verdict.eligibility is not Eligibility.INELIGIBLE
    assert _location_verdict(verdict).rule_id is None


def test_a_phrase_still_matches_across_line_wrapping_and_odd_spacing(
    make_job: MakeJob,
    profile: UserProfile,
    ruleset: CompiledRuleSet,
    now: datetime,
) -> None:
    """Boards wrap and re-indent text freely; internal whitespace in a phrase
    is flexible so that a real restriction is not missed on a line break."""
    job = make_job(description="This role is\n  US   ONLY, sorry.", location_text=None)

    verdict = resolve_eligibility(job, profile, ruleset, now=now)

    assert _location_verdict(verdict).rule_id == "loc.us_only"


def test_phrase_matching_ignores_case(
    make_job: MakeJob,
    profile: UserProfile,
    ruleset: CompiledRuleSet,
    now: datetime,
) -> None:
    for text in ("US ONLY", "us only", "Us Only"):
        job = make_job(location_text=text)
        verdict = resolve_eligibility(job, profile, ruleset, now=now)
        assert _location_verdict(verdict).rule_id == "loc.us_only", text


def test_rules_scoped_to_a_field_ignore_the_rest_of_the_listing(
    make_job: MakeJob, profile: UserProfile, now: datetime
) -> None:
    """Scoping is why "remote (us)" in a location field is a restriction while
    the same words in a paragraph about the company's offices are not."""
    ruleset = RuleSet(
        version=1,
        rules=(
            Rule(
                id="loc.scoped",
                dimension=LOCATION,
                when={"any_phrase": ["remote (us)"], "fields": ["location_text"]},
                verdict=Eligibility.INELIGIBLE,
                evidence="scoped to location",
            ),
        ),
    ).compile()

    in_description = make_job(
        location_text="Remote", description="Our remote (US) team meets quarterly."
    )
    in_location = make_job(location_text="Remote (US)")

    assert (
        resolve_dimension(LOCATION, in_description, profile, ruleset, now=now).rule_id
        is None
    )
    assert (
        resolve_dimension(LOCATION, in_location, profile, ruleset, now=now).rule_id
        == "loc.scoped"
    )


# --------------------------------------------------------------------------
# The user's own judgement wins (FR-3.6)
# --------------------------------------------------------------------------


def test_a_user_override_beats_the_rules(
    make_job: MakeJob,
    profile: UserProfile,
    ruleset: CompiledRuleSet,
    now: datetime,
) -> None:
    """A user who opened the listing and checked knows more than our phrases.

    Note the direction that matters most: the override can *rescue* a job the
    rules rejected, which is the user's remedy when a rule is wrong.
    """
    job = make_job(location_text="Remote (US)")
    labelled = job.model_copy(
        update={"eligibility": resolve_eligibility(job, profile, ruleset, now=now)}
    )

    assert labelled.effective_eligibility is Eligibility.INELIGIBLE

    rescued = labelled.model_copy(update={"eligibility_override": Eligibility.ELIGIBLE})

    assert rescued.effective_eligibility is Eligibility.ELIGIBLE
    # The rules' own verdict is retained, so the UI can show both.
    assert rescued.eligibility.eligibility is Eligibility.INELIGIBLE


def test_an_override_can_also_reject_a_job_the_rules_allowed(
    make_job: MakeJob,
) -> None:
    job = make_job()
    overridden = job.model_copy(update={"eligibility_override": Eligibility.INELIGIBLE})

    assert overridden.effective_eligibility is Eligibility.INELIGIBLE


def test_clearing_the_override_returns_to_the_rules_verdict(
    make_job: MakeJob,
    profile: UserProfile,
    ruleset: CompiledRuleSet,
    now: datetime,
) -> None:
    job = make_job(location_text="Remote (US)")
    verdict = resolve_eligibility(job, profile, ruleset, now=now)
    labelled = job.model_copy(
        update={"eligibility": verdict, "eligibility_override": Eligibility.ELIGIBLE}
    )

    cleared = labelled.model_copy(update={"eligibility_override": None})

    assert cleared.effective_eligibility is Eligibility.INELIGIBLE
