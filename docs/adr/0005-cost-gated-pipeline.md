# ADR-0005 — Cost-gated pipeline ordering

**Status:** Accepted
**Date:** 2026-09-25
**Deciders:** Shivanshu Singla

## Context

A weekly run collects roughly 2,000–5,000 listings. Sending all of them to an
LLM for scoring is the naive design, and it fails on every axis that matters
here: it exhausts a free tier within minutes, takes many minutes of wall clock,
and spends the majority of its effort on listings that a one-line rule could
have discarded — the user is not eligible, the posting is dead, the role is not
even engineering.

Outpost must be free to run (NFR-1) and must remain useful when the LLM is
unavailable or exhausted (FR-5.3). That is a constraint on pipeline *ordering*,
not on any single component.

## Decision

Stages are ordered by **cost per item, ascending**, and each stage may only
reduce the working set. Nothing expensive runs before everything cheap has run.

| # | Stage | Cost | Typical survivors |
|---|---|---|---|
| 1 | **Collect** | network, per *source* (not per job) | 5,000 |
| 2 | **Normalise** | CPU only | 5,000 |
| 3 | **Deduplicate** | CPU, hash | 3,200 |
| 4 | **Eligibility** | CPU, rules | 3,200 *(labelled, not dropped — ADR-0002)* |
| 5 | **Filter** | CPU, predicates | 900 |
| 6 | **Verify** | network, per job, cheap | 780 |
| 7 | **Pre-score** | CPU, local heuristic | 780 *(ordered, not dropped)* |
| 8 | **LLM score** | **paid / quota-limited** | 200 (capped) |
| 9 | **Review** | human attention — the scarcest resource | ~20 |

Two design rules fall out, and both matter more than the ordering itself:

**Stage 7 orders rather than filters.** The pre-score is a cheap local
skill-overlap heuristic. It does not decide anything; it sorts best-first. This
exists solely so that stage 8 can be interrupted. Combined with the cap, quota
exhaustion costs the user **the tail of the ranking, not a random slice** — the
difference between "we scored your 200 most promising jobs" and "we scored 200
arbitrary jobs and stopped."

**Stage 8 must degrade, not fail.** Quota exhaustion is an expected condition,
not an error. It commits everything scored so far, reports clearly, and exits
zero. The next run resumes on unscored rows, because scoring only touches rows
where `match_score IS NULL` — which makes the whole pipeline resumable by
construction rather than by bookkeeping.

## Consequences

### Positive

- A full run costs nothing on a free tier, reliably, without the user tuning
  anything.
- The tool remains useful with no LLM configured at all: stages 1–7 alone
  produce a deduplicated, eligibility-labelled, liveness-verified, roughly-ranked
  list. That is most of the value, and it is the mode a first-time user sees
  before they have a key.
- Interruption at any point leaves consistent, committed state.

### Negative

- The ordering is a real constraint on future work: any new expensive stage must
  justify its position, and any cheap stage that needs LLM output creates a
  cycle. This has already been felt — LLM-assisted eligibility for `UNKNOWN`
  listings (ADR-0002, alternatives) would need to run *after* stage 8 as a
  separate enrichment pass, not inside stage 4.
- The pre-score is a heuristic and can mis-order, pushing a genuinely good match
  below the cap. Mitigated by the cap being generous relative to what a human
  reviews (200 scored vs ~20 read) and user-configurable.
- Stage boundaries add bookkeeping: each stage records what it dropped and why,
  which is more code than a single pass.

### Neutral

- Stage-level counts make the run report a natural health signal — a source
  whose listings all die at stage 5 is a source whose parser has broken.

## Alternatives considered

**Score everything, cache aggressively.** Rejected: the first run still costs
thousands of calls, which is exactly the barrier we are removing.

**Let the user pick what to score.** Rejected as backwards — choosing what to
score is the work the tool exists to do.

**A single LLM pass doing eligibility, verification and scoring together.**
Tempting, and fewer moving parts. Rejected because it makes the core guarantee
(ADR-0002) depend on a non-deterministic external service, makes verdicts
unauditable, and produces no usable output at all when the quota is gone.
