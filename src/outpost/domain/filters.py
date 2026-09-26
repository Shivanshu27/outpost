"""Stage 5 — deterministic filtering.

Pure predicates over a job and the user's criteria. This runs before anything
that costs a network call (ADR-0005), so it must stay cheap.

Every drop is *reported with a reason*, never silent. The run report shows what
each filter removed, which is how a user discovers that their `min_rate` is set
too high and is quietly costing them two hundred listings — and how a broken
source parser becomes visible as "everything from this board died at the title
filter".
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from outpost.domain.models import (
    CompensationPeriod,
    ContractType,
    Eligibility,
    Job,
    VerificationTier,
)

__all__ = ["DropReason", "FilterCriteria", "FilterOutcome", "apply_filters"]


# Roles that are not individual-contributor engineering. Matched on the title
# only — "manager" in a description usually means "you will work with managers".
_DEFAULT_EXCLUDED_TITLES = (
    "recruiter",
    "sales",
    "account executive",
    "business development",
    "marketing",
    "customer success",
    "support engineer",
    "solutions architect",
    "engineering manager",
    "director",
    "vp ",
    "vice president",
    "head of",
    "intern",
    "internship",
    "teacher",
    "instructor",
    "tutor",
)

# Rough USD-per-unit conversions, used only to compare a listing's pay against
# the user's floor. Deliberately approximate: a wrong rejection here is
# recoverable (the user widens the filter), and live FX rates would mean a
# network call in a stage that must not make one.
_APPROX_USD = {
    "USD": 1.0,
    "EUR": 1.08,
    "GBP": 1.27,
    "CAD": 0.73,
    "AUD": 0.66,
    "SGD": 0.74,
    "INR": 0.012,
}

_PERIOD_HOURS = {
    CompensationPeriod.HOURLY: 1,
    CompensationPeriod.DAILY: 8,
    CompensationPeriod.MONTHLY: 160,
    CompensationPeriod.YEARLY: 1920,
}


class DropReason:
    """Stable identifiers for why a job was filtered out."""

    INELIGIBLE = "ineligible"
    UNKNOWN_ELIGIBILITY = "unknown_eligibility"
    """Distinct from INELIGIBLE on purpose. Collapsing the two would tell the
    user their rules found the job ineligible, when in fact their own opt-in
    filter hid one we simply could not determine. ADR-0002 says that cost must
    stay visible, and the funnel is where it becomes visible."""
    VERIFICATION = "verification"
    TITLE_EXCLUDED = "title_excluded"
    TITLE_NOT_MATCHED = "title_not_matched"
    TOO_OLD = "too_old"
    BELOW_MIN_RATE = "below_min_rate"
    CONTRACT_TYPE = "contract_type"
    STATUS_DISMISSED = "status_dismissed"
    NO_SIGNAL = "no_signal"


@dataclass(frozen=True, slots=True)
class FilterCriteria:
    """What the user wants to see.

    Defaults are permissive on purpose: a first-time user should see their
    listings, not an empty table caused by a filter they did not know existed.
    """

    exclude_ineligible: bool = True
    exclude_unknown_eligibility: bool = False
    """Off by default, and it should stay off for most users. Turning it on
    re-creates exactly the silent-loss failure ADR-0002 exists to prevent — but
    it is the user's own informed choice, which is the difference."""

    excluded_tiers: frozenset[VerificationTier] = frozenset(
        {VerificationTier.SCAM, VerificationTier.EXPIRED}
    )
    excluded_title_phrases: tuple[str, ...] = _DEFAULT_EXCLUDED_TITLES
    required_title_phrases: tuple[str, ...] = ()
    max_age_days: int | None = None
    min_hourly_rate_usd: Decimal | None = None
    contract_types: frozenset[ContractType] = frozenset()
    require_tech_signal: bool = True
    tech_keywords: tuple[str, ...] = ()
    include_dismissed: bool = False


@dataclass(frozen=True, slots=True)
class FilterOutcome:
    """What survived, and why the rest did not."""

    kept: tuple[Job, ...] = ()
    dropped: tuple[tuple[Job, str], ...] = ()

    @property
    def drop_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for _, reason in self.dropped:
            counts[reason] = counts.get(reason, 0) + 1
        return counts


def _matches_any(text: str, phrases: Sequence[str]) -> bool:
    lowered = text.lower()
    return any(phrase.lower() in lowered for phrase in phrases)


def _approx_hourly_usd(job: Job) -> float | None:
    """Best-effort hourly USD equivalent, or None if we cannot tell.

    Returns ``None`` rather than a guess whenever currency or period is missing
    — an unparseable salary must not cause a drop (FR-2.3).
    """
    comp = job.compensation
    if comp.minimum is None or comp.currency is None or comp.period is None:
        return None
    rate = _APPROX_USD.get(comp.currency.upper())
    if rate is None:
        return None
    hours = _PERIOD_HOURS.get(comp.period)
    if not hours:
        return None
    return float(comp.minimum) * rate / hours


def _has_tech_signal(job: Job, keywords: Sequence[str]) -> bool:
    """Whether the listing looks like a software role at all.

    Aggregators carry a lot of non-engineering noise. Requiring at least one
    technical token is a cheap, high-yield gate — but it is matched against the
    whole listing, so a genuinely technical role phrased unusually still
    survives.
    """
    if not keywords:
        return True
    text = job.searchable_text.lower()
    return any(re.search(rf"\b{re.escape(k.lower())}\b", text) for k in keywords)


def apply_filters(
    jobs: Sequence[Job],
    criteria: FilterCriteria,
    *,
    now: datetime,
) -> FilterOutcome:
    """Partition jobs into kept and dropped-with-reason.

    Checks are ordered cheapest-first, and the *first* failing check is the
    reported reason — so the drop counts read as a funnel rather than as
    overlapping totals.
    """
    kept: list[Job] = []
    dropped: list[tuple[Job, str]] = []

    cutoff = (
        now - timedelta(days=criteria.max_age_days)
        if criteria.max_age_days is not None
        else None
    )

    for job in jobs:
        if reason := _first_failure(job, criteria, cutoff):
            dropped.append((job, reason))
        else:
            kept.append(job)

    return FilterOutcome(kept=tuple(kept), dropped=tuple(dropped))


def _first_failure(
    job: Job, criteria: FilterCriteria, cutoff: datetime | None
) -> str | None:
    if not criteria.include_dismissed and job.status.value == "dismissed":
        return DropReason.STATUS_DISMISSED

    eligibility = job.effective_eligibility
    if criteria.exclude_ineligible and eligibility is Eligibility.INELIGIBLE:
        return DropReason.INELIGIBLE
    if criteria.exclude_unknown_eligibility and eligibility is Eligibility.UNKNOWN:
        return DropReason.UNKNOWN_ELIGIBILITY

    if job.verification and job.verification.tier in criteria.excluded_tiers:
        return DropReason.VERIFICATION

    if _matches_any(job.title, criteria.excluded_title_phrases):
        return DropReason.TITLE_EXCLUDED

    if criteria.required_title_phrases and not _matches_any(
        job.title, criteria.required_title_phrases
    ):
        return DropReason.TITLE_NOT_MATCHED

    if cutoff and job.posted_at and job.posted_at < cutoff:
        return DropReason.TOO_OLD

    # An UNKNOWN contract type never drops: the source simply did not say, and
    # a listing is not disqualified by a field its board omitted.
    if (
        criteria.contract_types
        and job.contract_type is not ContractType.UNKNOWN
        and job.contract_type not in criteria.contract_types
    ):
        return DropReason.CONTRACT_TYPE

    if criteria.min_hourly_rate_usd is not None:
        hourly = _approx_hourly_usd(job)
        # None means "could not determine" — never a drop.
        if hourly is not None and hourly < float(criteria.min_hourly_rate_usd):
            return DropReason.BELOW_MIN_RATE

    if criteria.require_tech_signal and not _has_tech_signal(
        job, criteria.tech_keywords
    ):
        return DropReason.NO_SIGNAL

    return None
