"""The eligibility engine.

Pure: given a job, a profile, a compiled ruleset and an instant, produce a
verdict with its evidence. No I/O, no clock, no configuration.

Evaluation model
----------------

Dimensions are independent and evaluated separately. Within a dimension, rules
run in declaration order and **the first match wins** — so ordering in the
ruleset is meaningful, and a specific rule must be declared before a general
one. Verdicts are then combined by :meth:`EligibilityVerdict.combine`.

The invariant this module exists to uphold (ADR-0002): a dimension with no
matching rule is ``UNKNOWN``. It is never ``INELIGIBLE``, and it is never
quietly upgraded to ``ELIGIBLE`` because nothing objected.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from outpost.domain.models import (
    DimensionVerdict,
    Eligibility,
    EligibilityDimension,
    EligibilityVerdict,
    Job,
    UserProfile,
)
from outpost.domain.predicates import PREDICATES, PredicateContext
from outpost.domain.rules import CompiledRule, CompiledRuleSet

__all__ = ["resolve_dimension", "resolve_eligibility"]


def _field_text(job: Job, fields: tuple[str, ...]) -> str:
    """Concatenate the job fields a rule is scoped to.

    Scoping is why ``"remote (US)"`` in ``location_text`` can be treated as a
    restriction while the same phrase buried in a description paragraph is not.
    """
    values = []
    for name in fields:
        value = getattr(job, name, None)
        if isinstance(value, str) and value:
            values.append(value)
    return "\n".join(values)


def _profile_exempts(rule: CompiledRule, profile: UserProfile) -> bool:
    """True when the user's own situation exempts them from this rule.

    ``unless_profile_matches`` is how one shared ruleset serves users in
    different countries: ``loc.us_only`` blocks everyone except a user whose
    declared country is ``US``.

    Semantics are OR across the specified facets — any single match exempts.
    """
    match = rule.rule.unless_profile_matches
    if match is None or match.is_empty:
        return False

    facets: tuple[tuple[Iterable[str], Iterable[str]], ...] = (
        (match.country, (profile.country,)),
        (match.work_authorisation, profile.work_authorisation),
        (match.contract_types, (c.value for c in profile.contract_types)),
        (match.currencies, profile.currencies),
    )
    return any(
        required and _upper(required) & _upper(held) for required, held in facets
    )


def _upper(values: Iterable[str]) -> set[str]:
    """Case-fold for comparison. Country codes and currencies are written
    inconsistently across profiles and rulesets, and a case mismatch here would
    silently fail to exempt a user who *is* exempt."""
    return {v.upper() for v in values}


def _evaluate(
    rule: CompiledRule,
    job: Job,
    profile: UserProfile,
    now: datetime,
) -> tuple[str, str] | None:
    """Evaluate one rule. Returns ``(matched_text, evidence)`` or ``None``.

    A predicate's own message becomes the evidence when it provides one, since
    a computed predicate can explain itself better than a static string ("gives
    about 2.5h of overlap" beats "timezone requirement not met").
    """
    if _profile_exempts(rule, profile):
        return None

    condition = rule.rule.when

    if condition.predicate is not None:
        predicate = PREDICATES.get(condition.predicate)
        if predicate is None:
            # Unreachable when the ruleset was validated at load; defensive
            # because a missing predicate must not silently never match.
            msg = (
                f"rule {rule.id!r} references unknown predicate {condition.predicate!r}"
            )
            raise KeyError(msg)
        detail = predicate(PredicateContext(job=job, profile=profile, now=now))
        if detail is None:
            return None
        return (detail, detail)

    text = _field_text(job, condition.fields)
    if not text:
        return None

    matched = rule.match(text)
    if matched is None:
        return None
    return (matched, rule.rule.evidence)


def resolve_dimension(
    dimension: EligibilityDimension,
    job: Job,
    profile: UserProfile,
    ruleset: CompiledRuleSet,
    *,
    now: datetime,
) -> DimensionVerdict:
    """Resolve one dimension. First matching rule wins."""
    for rule in ruleset.for_dimension(dimension):
        result = _evaluate(rule, job, profile, now)
        if result is None:
            continue
        matched_text, evidence = result
        return DimensionVerdict(
            dimension=dimension,
            eligibility=rule.rule.verdict,
            rule_id=rule.id,
            evidence=evidence,
            matched_text=matched_text[:200],
            source_field=",".join(rule.rule.when.fields)
            if rule.rule.when.predicate is None
            else rule.rule.when.predicate,
        )

    # No rule matched. This is UNKNOWN — not INELIGIBLE, and not ELIGIBLE.
    return DimensionVerdict.unknown(dimension)


def resolve_eligibility(
    job: Job,
    profile: UserProfile,
    ruleset: CompiledRuleSet,
    *,
    now: datetime,
) -> EligibilityVerdict:
    """Resolve eligibility across every dimension the ruleset covers.

    Dimensions absent from the ruleset are omitted rather than reported as
    ``UNKNOWN``: a ruleset that says nothing about currency should not drag the
    combined verdict down to ``UNKNOWN`` on that account. Only dimensions the
    ruleset actually attempts count toward the combination.
    """
    verdicts = [
        resolve_dimension(dimension, job, profile, ruleset, now=now)
        for dimension in ruleset.dimensions
    ]
    if not verdicts:
        return EligibilityVerdict.unknown()
    return EligibilityVerdict.combine(verdicts)


def summarise(verdicts: list[EligibilityVerdict]) -> dict[Eligibility, int]:
    """Count verdicts by outcome, for the run report.

    A rising ``UNKNOWN`` share is the health signal that the rules are falling
    behind the boards' phrasing (ADR-0002, negative consequences).
    """
    counts = dict.fromkeys(Eligibility, 0)
    for verdict in verdicts:
        counts[verdict.eligibility] += 1
    return counts
