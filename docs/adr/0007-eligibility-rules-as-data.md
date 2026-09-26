# ADR-0007 — Eligibility rules are data, not code

**Status:** Accepted
**Date:** 2026-09-25
**Deciders:** Shivanshu Singla

## Context

Eligibility rules are the part of Outpost that changes fastest and varies most
per user. A listing saying `"US only"` is disqualifying for someone in Bengaluru
and irrelevant for someone in Austin. Boards invent new phrasings constantly
(`"US-based only"`, `"must reside in the United States"`, `"USA 🇺🇸"`,
`"Remote — North America"`). A rule that is right for one user is wrong for
another.

The predecessor project hardcoded this as `INDIA_BLOCKED_PATTERNS` and
`INDIA_ALLOWED_PATTERNS` — module-level constants in `utils.py`. That works
exactly as long as the author is the only user. It cannot express "I'm in
Brazil", it cannot be fixed without a release, and it cannot be contributed to
by someone who doesn't write Python.

## Decision

Rules are **declarative YAML**, shipped as a default ruleset and overridable by
the user. The engine that evaluates them is code; the rules themselves are data.

```yaml
# rules/location.yml
version: 1
rules:
  - id: loc.us_only
    dimension: location
    when:
      any_phrase:
        - "us only"
        - "us-based only"
        - "must reside in the united states"
        - "authorized to work in the us"
    verdict: ineligible
    unless_profile_matches:
      country: [US]
    evidence: "Listing restricts hiring to the United States"

  - id: loc.worldwide
    dimension: location
    when:
      any_phrase: ["work from anywhere", "anywhere in the world", "globally remote"]
    verdict: eligible
    evidence: "Listing states worldwide eligibility"
```

Design constraints on the rule language, all deliberate:

- **It is not Turing-complete.** Phrase matching, field scoping, and profile
  predicates. No loops, no arithmetic, no user-supplied regex by default. A
  config language that grows into a programming language is a well-known
  failure; we would rather be occasionally insufficient than accidentally
  become one.
- **Rules are evaluated in declared order**, first match per dimension wins, and
  the winning rule's id and phrase become the verdict's evidence (ADR-0002).
- **A ruleset is versioned and validated** against a schema at load. An invalid
  ruleset is a startup error, not a silent fallback to no rules — failing to
  load rules must never look like "no restrictions found."
- **Dimensions are independent** (location, timezone, authorisation, contract,
  currency) and combined by a documented precedence: any `INELIGIBLE` dominates;
  otherwise any `ELIGIBLE` wins; `UNKNOWN` only when no dimension matched
  anything at all.

  The precedence turns on what an *unmatched* dimension means. A dimension with
  no matching rule is "no constraint found on this axis" — not "we cannot tell".
  The stricter reading (any `UNKNOWN` dominates) was implemented first and
  rejected on contact with real data: it made `ELIGIBLE` nearly unreachable,
  because a listing saying "work from anywhere" still says nothing about
  currency, and so came back `UNKNOWN`. It also contradicted ADR-0002 — it
  optimised for a caution the rest of the design deliberately rejects.

  The residual risk is accepted: an unrecognised blocker plus a positive signal
  elsewhere yields a wrong `ELIGIBLE`. That is the safe direction. A job wrongly
  shown costs seconds; a job wrongly hidden costs an opportunity the user never
  learns existed.

## Consequences

### Positive

- A user in São Paulo edits YAML instead of forking Python.
- Rule fixes ship without a release, and users can fix their own.
- Rules become a natural community contribution — the highest-value one, and
  open to non-Python contributors (PRD §9, Q1).
- The corpus of rules is directly testable as data: a labelled fixture set of
  real listing text plus expected verdicts, run as a regression suite.

### Negative

- A config language is a real API with real compatibility obligations. `version`
  in the file is how we avoid regretting that.
- Expressiveness ceiling: some constraints genuinely need code (timezone overlap
  is arithmetic on offsets, not phrase matching). Those are implemented as named
  built-in predicates the YAML can *invoke* — an escape hatch that keeps the
  language declarative while admitting real computation.
- Validation and good error messages are non-trivial work; a bad ruleset must
  fail loudly with a line number.
- Users can write rules that hide jobs from themselves. Mitigated by ADR-0002 —
  rule-produced `INELIGIBLE` is labelled and visible, not deleted.

## Alternatives considered

**Python predicate functions in a registry.** More expressive and type-checked,
and genuinely tempting. Rejected because it excludes non-Python contributors
from the most contribution-friendly part of the system, and reintroduces
"edit code to fix your own search."

**Embedded expression language (CEL, JSONLogic, Starlark).** Rejected for v1 as
more surface than the problem warrants. Revisit if the built-in predicate
escape hatch starts collecting one-offs.

**LLM-resolved eligibility.** See ADR-0002. Non-deterministic, unauditable, and
unavailable when the quota is gone.
