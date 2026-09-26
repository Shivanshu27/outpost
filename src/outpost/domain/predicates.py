"""Built-in predicates — the escape hatch in the rule language.

Some eligibility constraints are genuinely computation, not text matching.
"Must overlap 4 hours with PST" cannot be answered by looking for a phrase; it
needs the user's timezone, the listing's timezone, and arithmetic across a DST
boundary.

Rather than grow the YAML into an expression language (ADR-0007), rules invoke
a *named* predicate from this registry:

.. code-block:: yaml

    when:
      predicate: timezone.insufficient_overlap

A predicate returns the **evidence string** when its condition holds, or
``None`` when it does not. Returning evidence rather than a boolean keeps the
audit trail intact — the user sees why, not merely that.

Predicates are pure. Anything time-dependent takes ``now`` explicitly, because
the domain does not own a clock (ADR-0009).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from outpost.domain.models import ContractType, Job, UserProfile

__all__ = ["PREDICATES", "Predicate", "PredicateContext", "unknown_predicates"]


class PredicateContext:
    """Everything a predicate may look at."""

    __slots__ = ("job", "now", "profile")

    def __init__(self, job: Job, profile: UserProfile, now: datetime) -> None:
        self.job = job
        self.profile = profile
        self.now = now


Predicate = Callable[[PredicateContext], str | None]


# --------------------------------------------------------------------------
# Timezone overlap
# --------------------------------------------------------------------------

# Abbreviations are ambiguous by nature (CST is both US Central and China
# Standard). We map only the unambiguous-in-context ones and decline the rest —
# guessing here would produce a confident wrong INELIGIBLE, which is the one
# outcome the whole design is built to avoid.
_TZ_ABBREVIATIONS: dict[str, str] = {
    "pst": "America/Los_Angeles",
    "pdt": "America/Los_Angeles",
    "pt": "America/Los_Angeles",
    "pacific": "America/Los_Angeles",
    "est": "America/New_York",
    "edt": "America/New_York",
    "et": "America/New_York",
    "eastern": "America/New_York",
    "mst": "America/Denver",
    "mdt": "America/Denver",
    "cet": "Europe/Berlin",
    "cest": "Europe/Berlin",
    "gmt": "Europe/London",
    "bst": "Europe/London",
    "utc": "UTC",
    "ist": "Asia/Kolkata",
    "aest": "Australia/Sydney",
}

_OVERLAP_HOURS = re.compile(
    r"(?:overlap|overlapping)\s+(?:of\s+)?(?:at\s+least\s+)?(\d{1,2})\s*(?:\+)?\s*"
    r"(?:hours?|hrs?)",
    re.IGNORECASE,
)
_HOURS_OVERLAP = re.compile(
    r"(\d{1,2})\s*(?:\+)?\s*(?:hours?|hrs?)\s+(?:of\s+)?overlap", re.IGNORECASE
)
_TZ_MENTION = re.compile(
    r"\b(" + "|".join(sorted(_TZ_ABBREVIATIONS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

# A conventional working day, used for both sides of the overlap calculation.
_WORKDAY_START = 9
_WORKDAY_END = 18


def _resolve_zone(name: str) -> ZoneInfo | None:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        # Missing tzdata (common on bare Windows) must degrade to UNKNOWN,
        # never to a verdict.
        return None


def _utc_offset_hours(zone: ZoneInfo, at: datetime) -> float | None:
    offset = at.astimezone(zone).utcoffset()
    return None if offset is None else offset.total_seconds() / 3600.0


def _overlap_hours(a_offset: float, b_offset: float) -> float:
    """Working-hours overlap between two zones, given their UTC offsets.

    Both workdays are projected onto the UTC line and intersected. Wrap-around
    is handled by testing the target day shifted by ±24h, which covers the
    common India/US-Pacific case where the windows meet only across midnight.
    """
    a_start, a_end = _WORKDAY_START - a_offset, _WORKDAY_END - a_offset
    b_start, b_end = _WORKDAY_START - b_offset, _WORKDAY_END - b_offset
    best = 0.0
    for shift in (-24.0, 0.0, 24.0):
        overlap = min(a_end, b_end + shift) - max(a_start, b_start + shift)
        best = max(best, overlap)
    return max(0.0, best)


def timezone_insufficient_overlap(ctx: PredicateContext) -> str | None:
    """True when the listing demands more timezone overlap than the user has.

    Declines to decide — returns ``None`` — whenever any input is missing or
    ambiguous: no stated requirement, an unrecognised zone, or absent tzdata.
    A predicate that cannot compute must yield to ``UNKNOWN``.
    """
    text = ctx.job.searchable_text
    match = _OVERLAP_HOURS.search(text) or _HOURS_OVERLAP.search(text)
    if match is None:
        return None

    required = float(match.group(1))

    # Look for a timezone mentioned near the requirement, not anywhere in the
    # listing — a "PST" in the company's address paragraph is not the constraint.
    window = text[max(0, match.start() - 120) : match.end() + 120]
    tz_match = _TZ_MENTION.search(window)
    if tz_match is None:
        return None

    target = _resolve_zone(_TZ_ABBREVIATIONS[tz_match.group(1).lower()])
    user = _resolve_zone(ctx.profile.timezone)
    if target is None or user is None:
        return None

    target_offset = _utc_offset_hours(target, ctx.now)
    user_offset = _utc_offset_hours(user, ctx.now)
    if target_offset is None or user_offset is None:
        return None

    available = _overlap_hours(user_offset, target_offset)
    if available >= required:
        return None

    return (
        f"Requires {required:.0f}h overlap with {tz_match.group(1).upper()}; "
        f"your timezone ({ctx.profile.timezone}) gives about "
        f"{available:.1f}h of standard working-hour overlap"
    )


def timezone_sufficient_overlap(ctx: PredicateContext) -> str | None:
    """Inverse of :func:`timezone_insufficient_overlap`, for positive evidence."""
    text = ctx.job.searchable_text
    match = _OVERLAP_HOURS.search(text) or _HOURS_OVERLAP.search(text)
    if match is None:
        return None
    if timezone_insufficient_overlap(ctx) is not None:
        return None
    return f"Stated overlap requirement of {match.group(1)}h is satisfiable"


# --------------------------------------------------------------------------
# Contract and currency
# --------------------------------------------------------------------------


def contract_type_mismatch(ctx: PredicateContext) -> str | None:
    """True when the listing's contract type is one the user does not want.

    A *preference* rather than a legal barrier, so rulesets should normally map
    this to a filter rather than to INELIGIBLE. It lives here because the
    comparison is set membership, not text.
    """
    wanted = ctx.profile.contract_types
    actual = ctx.job.contract_type
    if not wanted or actual is ContractType.UNKNOWN:
        return None
    if actual in wanted:
        return None
    return (
        f"Listing is {actual.value.replace('_', ' ')}; "
        f"you accept {', '.join(c.value.replace('_', ' ') for c in wanted)}"
    )


def currency_unsupported(ctx: PredicateContext) -> str | None:
    """True when pay is quoted in a currency the user did not accept."""
    wanted = {c.upper() for c in ctx.profile.currencies}
    actual = ctx.job.compensation.currency
    if not wanted or not actual:
        return None
    if actual.upper() in wanted:
        return None
    return f"Pay quoted in {actual.upper()}; you accept {', '.join(sorted(wanted))}"


PREDICATES: dict[str, Predicate] = {
    "timezone.insufficient_overlap": timezone_insufficient_overlap,
    "timezone.sufficient_overlap": timezone_sufficient_overlap,
    "contract.type_mismatch": contract_type_mismatch,
    "currency.unsupported": currency_unsupported,
}


def unknown_predicates(names: set[str]) -> set[str]:
    """Names referenced by a ruleset that this build does not implement.

    Checked at load so a typo fails immediately with a clear message, rather
    than silently never matching — a rule that never fires is indistinguishable
    from a listing with no restrictions, which is the failure mode ADR-0002
    exists to prevent.
    """
    return names - set(PREDICATES)
