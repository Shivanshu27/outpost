# ADR-0002 — Eligibility is tri-state and never collapses to a boolean

**Status:** Accepted
**Date:** 2026-09-25
**Deciders:** Shivanshu Singla

## Context

Outpost's core job is deciding whether a user can actually hold a given job.
The obvious model is a boolean: eligible, or not. Filter on it, show the true
ones.

The obvious model is wrong, because the input is unstructured prose written by
someone who was not thinking about this question. Real listings say:

- `"Remote"` — and nothing else, anywhere
- `"Remote (US preferred)"` — preference or requirement?
- `"Must overlap 4 hours with PST"` — satisfiable from India, barely
- `"Remote — EMEA"` — India is technically in neither EMEA nor not-EMEA depending
  on who wrote it
- `"Work from anywhere!"` — followed by a US-only ATS form

A boolean model must assign every one of these to `true` or `false`. Whichever
default is chosen produces a bad failure:

- Default `false` → **jobs the user could have taken are silently hidden.**
- Default `true` → the filter is useless; every unparseable listing shows up.

Note the asymmetry. The second failure is visible and annoying. The first is
**invisible and unbounded** — the user cannot tell the difference between
"there were no good jobs this week" and "the parser dropped them." A filtering
tool that silently hides good results does not degrade gracefully; it destroys
the trust that is the entire reason to use it, and it does so without ever
producing a bug report.

## Decision

Eligibility is an enum with three values, and `UNKNOWN` is a first-class,
*user-visible* verdict:

```python
class Eligibility(StrEnum):
    ELIGIBLE   = "eligible"      # positive evidence the user qualifies
    INELIGIBLE = "ineligible"    # positive evidence the user does not
    UNKNOWN    = "unknown"       # insufficient evidence — DEFAULT
```

Three rules bind it:

1. **`UNKNOWN` is the default.** A verdict requires positive evidence. Absence
   of evidence never produces `INELIGIBLE`.
2. **`UNKNOWN` is shown by default** in the UI, visually distinct from `ELIGIBLE`.
   The user opts into hiding it; we never opt them in.
3. **Every non-`UNKNOWN` verdict carries its evidence** — the matched phrase,
   the field it came from, and the rule id. A verdict you cannot explain is a
   bug, and the UI renders the evidence so the user can audit it.

This invariant is enforced in the type system where possible, and by test
(`tests/unit/test_eligibility_invariants.py`) where not. **No code path may map
`UNKNOWN` to `INELIGIBLE`.**

## Consequences

### Positive

- The catastrophic failure mode is structurally prevented, not merely unlikely.
- Users can audit any verdict, which makes rule bugs reportable instead of
  invisible.
- We can ship aggressive rules without fear: a wrong rule produces a *visible*
  mislabel, which generates a bug report, which improves the corpus.
- Honest UX. "We don't know" is information, and users trust a tool that admits
  it far more than one that pretends to certainty.

### Negative

- More listings shown per run — the user skims more. Accepted deliberately;
  recall over precision (PRD §2).
- Three-way state complicates filters, counts, and the UI. Every surface
  displaying eligibility must handle all three, which is more work than a
  checkbox.
- `UNKNOWN` can become a dumping ground that hides poor rule coverage. Mitigated
  by tracking the `UNKNOWN` rate per source as a health metric — a rising rate
  means the rules are falling behind, and it should be visible.

## Alternatives considered

**Confidence score (0.0–1.0) with a threshold.** Rejected. It moves the problem
rather than solving it: the threshold is still a boolean collapse, now with a
magic number and no evidence trail. It also implies a calibration we do not have
— what would 0.73 eligible actually mean?

**Boolean plus a separate `parsed_successfully` flag.** Rejected as the same
tri-state with extra steps, and worse: two fields can disagree, and nothing
forces callers to check the second one. The enum makes ignoring the third case
a type error rather than an oversight.

**LLM-resolved eligibility for ambiguous cases.** Rejected *as the primary
mechanism* — it is slow, costly, non-deterministic, and unauditable, and it
would make the core guarantee depend on an external service. Reconsidered as an
optional enrichment for `UNKNOWN` listings only, where a wrong answer is an
upgrade over no answer. Tracked as a post-v1 idea.
