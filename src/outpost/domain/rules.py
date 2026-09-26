"""The eligibility rule language.

Rules are data, not code (ADR-0007). This module defines the schema, validates
it, and compiles it into a form that is cheap to evaluate against thousands of
listings.

The language is deliberately **not** Turing-complete: phrase matching, field
scoping, and profile predicates. Where real computation is genuinely required —
timezone overlap is arithmetic, not text matching — rules invoke a *named
built-in predicate* rather than growing an expression syntax. A config language
that grows into a programming language is a well-documented way to end up
maintaining a bad interpreter.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from outpost.domain.models import Eligibility, EligibilityDimension

__all__ = [
    "SEARCHABLE_FIELDS",
    "CompiledRule",
    "CompiledRuleSet",
    "ProfileMatch",
    "Rule",
    "RuleCondition",
    "RuleSet",
    "RuleSetError",
]

SEARCHABLE_FIELDS = ("title", "company", "location_text", "description")
"""Job fields a rule may be scoped to. Scoping matters: ``"remote (us)"`` in a
location field is a restriction; the same string inside a paragraph about the
company's offices is not."""

CURRENT_SCHEMA_VERSION = 1


class RuleSetError(Exception):
    """A ruleset is malformed.

    Raised at load time, and deliberately fatal. A ruleset that fails to load
    must never degrade to "no rules" — silently evaluating zero rules looks
    exactly like "this listing has no restrictions", which is the failure this
    whole design exists to prevent (ADR-0002).
    """


class ProfileMatch(BaseModel):
    """A predicate over the user's profile.

    Used as ``unless_profile_matches``: the rule fires *unless* the user's own
    situation exempts them. It is how one shared ruleset serves a user in
    Bengaluru and a user in Austin without forking.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    country: tuple[str, ...] = ()
    work_authorisation: tuple[str, ...] = ()
    contract_types: tuple[str, ...] = ()
    currencies: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not (
            self.country
            or self.work_authorisation
            or self.contract_types
            or self.currencies
        )


class RuleCondition(BaseModel):
    """When a rule fires."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    any_phrase: tuple[str, ...] = ()
    all_phrases: tuple[str, ...] = ()
    fields: tuple[str, ...] = SEARCHABLE_FIELDS
    predicate: str | None = None
    """Name of a built-in predicate — the escape hatch for conditions that are
    computation rather than text (see ``predicates.py``)."""

    @model_validator(mode="after")
    def _has_something_to_match(self) -> Self:
        if not (self.any_phrase or self.all_phrases or self.predicate):
            msg = "condition must specify any_phrase, all_phrases, or predicate"
            raise ValueError(msg)
        unknown = set(self.fields) - set(SEARCHABLE_FIELDS)
        if unknown:
            msg = (
                f"unknown field(s) {sorted(unknown)}; "
                f"valid fields are {list(SEARCHABLE_FIELDS)}"
            )
            raise ValueError(msg)
        return self


class Rule(BaseModel):
    """One eligibility rule."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9]+(\.[a-z0-9_]+)+$")
    dimension: EligibilityDimension
    when: RuleCondition
    verdict: Eligibility
    evidence: str = Field(min_length=1)
    unless_profile_matches: ProfileMatch | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def _verdict_must_be_determined(self) -> Self:
        """A rule may only produce evidence, never the absence of it.

        ``UNKNOWN`` is what you get when *no* rule matched. A rule that asserts
        ``UNKNOWN`` would be claiming positive evidence of ignorance, which is
        incoherent, and would let a ruleset author accidentally mask a later
        rule that would have matched.
        """
        if not self.verdict.is_determined:
            msg = (
                f"rule {self.id!r} has verdict 'unknown'; rules must assert "
                f"'eligible' or 'ineligible'. UNKNOWN is the absence of a match."
            )
            raise ValueError(msg)
        return self


class RuleSet(BaseModel):
    """A versioned collection of rules, as loaded from YAML."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int
    rules: tuple[Rule, ...]

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if self.version != CURRENT_SCHEMA_VERSION:
            msg = (
                f"ruleset version {self.version} is not supported "
                f"(this build understands version {CURRENT_SCHEMA_VERSION})"
            )
            raise ValueError(msg)
        seen: set[str] = set()
        for rule in self.rules:
            if rule.id in seen:
                msg = f"duplicate rule id {rule.id!r}"
                raise ValueError(msg)
            seen.add(rule.id)
        return self

    def compile(self) -> CompiledRuleSet:
        return CompiledRuleSet.from_ruleset(self)

    def merge(self, other: RuleSet) -> RuleSet:
        """Overlay another ruleset onto this one.

        Same-id rules are *replaced* in place, preserving the original
        ordering — so a user can correct a shipped rule without also changing
        when it is evaluated. New rules append.

        Order is load-bearing (first match per dimension wins), so this is the
        only merge semantics that keeps a user override predictable.
        """
        by_id = {r.id: r for r in other.rules}
        merged = [by_id.pop(r.id, r) for r in self.rules]
        merged.extend(by_id.values())
        return RuleSet(version=self.version, rules=tuple(merged))


# --------------------------------------------------------------------------
# Compilation
#
# A run evaluates ~50 rules against a few thousand jobs. Compiling phrases to
# regexes once at load, rather than per job, is the difference between a
# sub-second stage and a visibly slow one.
# --------------------------------------------------------------------------


def _phrase_to_pattern(phrase: str) -> re.Pattern[str]:
    """Compile a phrase to a word-boundary-anchored, whitespace-tolerant regex.

    Boundaries are essential. Without them ``"us"`` matches inside ``"status"``,
    ``"industry"`` and ``"discuss"`` — which would mark most of the corpus
    US-only. Internal whitespace is made flexible so that ``"us only"`` still
    matches ``"US  only"`` and a newline-wrapped ``"US\\nonly"``.
    """
    tokens = [re.escape(t) for t in phrase.lower().split()]
    if not tokens:
        msg = "empty phrase"
        raise RuleSetError(msg)
    body = r"\s+".join(tokens)
    # \b is wrong next to non-word characters (e.g. a phrase ending in ')'),
    # so only anchor where the adjacent character is a word character.
    prefix = r"\b" if phrase.lower()[0].isalnum() else ""
    suffix = r"\b" if phrase.lower()[-1].isalnum() else ""
    return re.compile(prefix + body + suffix, re.IGNORECASE)


class CompiledRule:
    """A rule with its phrases pre-compiled."""

    __slots__ = ("all_patterns", "any_patterns", "rule")

    def __init__(self, rule: Rule) -> None:
        self.rule = rule
        self.any_patterns = tuple(_phrase_to_pattern(p) for p in rule.when.any_phrase)
        self.all_patterns = tuple(_phrase_to_pattern(p) for p in rule.when.all_phrases)

    @property
    def id(self) -> str:
        return self.rule.id

    @property
    def dimension(self) -> EligibilityDimension:
        return self.rule.dimension

    def match(self, text: str) -> str | None:
        """Return the literal text that matched, or None.

        The *matched substring* is returned rather than a boolean because it
        becomes the verdict's evidence (ADR-0002) — the user sees the exact
        phrase from the listing that produced the decision, which is what makes
        a wrong rule reportable instead of merely mysterious.
        """
        if self.all_patterns and not all(p.search(text) for p in self.all_patterns):
            return None
        if self.any_patterns:
            for pattern in self.any_patterns:
                if found := pattern.search(text):
                    return found.group(0)
            return None
        # all_phrases matched and there were no any_phrase alternatives
        if self.all_patterns:
            first = self.all_patterns[0].search(text)
            return first.group(0) if first else None
        return None


class CompiledRuleSet:
    """Rules grouped by dimension, in declaration order."""

    __slots__ = ("_by_dimension", "version")

    def __init__(
        self,
        version: int,
        by_dimension: dict[EligibilityDimension, tuple[CompiledRule, ...]],
    ) -> None:
        self.version = version
        self._by_dimension = by_dimension

    @classmethod
    def from_ruleset(cls, ruleset: RuleSet) -> CompiledRuleSet:
        grouped: dict[EligibilityDimension, list[CompiledRule]] = {}
        for rule in ruleset.rules:
            if not rule.enabled:
                continue
            grouped.setdefault(rule.dimension, []).append(CompiledRule(rule))
        return cls(
            version=ruleset.version,
            by_dimension={k: tuple(v) for k, v in grouped.items()},
        )

    @property
    def dimensions(self) -> tuple[EligibilityDimension, ...]:
        return tuple(self._by_dimension)

    def for_dimension(self, dimension: EligibilityDimension) -> Sequence[CompiledRule]:
        return self._by_dimension.get(dimension, ())

    def __len__(self) -> int:
        return sum(len(v) for v in self._by_dimension.values())
