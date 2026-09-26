# Architecture Decision Records

Each ADR records one decision, the context that forced it, and what it cost.

We keep them because the *reasoning* is the part that decays fastest. Code shows
what we do; ADRs show what we considered and rejected, so that a future
contributor (or a future maintainer who has forgotten) can tell the difference
between a deliberate constraint and an accident.

## Index

| # | Decision | Status |
|---|---|---|
| [0001](0001-local-first-not-hosted.md) | Local-first, not a hosted service | Accepted |
| [0002](0002-tri-state-eligibility.md) | Eligibility is tri-state and never collapses to a boolean | Accepted |
| [0003](0003-hexagonal-architecture.md) | Ports and adapters, with a pure domain core | Accepted |
| [0004](0004-sqlite-explicit-sql.md) | SQLite with explicit SQL and versioned migrations | Accepted |
| [0005](0005-cost-gated-pipeline.md) | Cost-gated pipeline ordering | Accepted |
| [0006](0006-provider-agnostic-llm.md) | Provider-agnostic LLM layer with a no-LLM fallback | Accepted |
| [0007](0007-eligibility-rules-as-data.md) | Eligibility rules are data, not code | Accepted |
| [0008](0008-source-plugin-contract.md) | Sources are plugins behind a fetch-only contract | Accepted |
| [0009](0009-composition-root.md) | A single composition root; no ambient configuration | Accepted |
| [0010](0010-react-spa-over-local-api.md) | React SPA over a local FastAPI | Accepted |
| [0011](0011-no-auto-apply.md) | Outpost will never submit an application | Accepted |

## The load-bearing ones

If you read three, read these:

- **[0002](0002-tri-state-eligibility.md)** — the core product invariant.
  `UNKNOWN` is never coerced to `INELIGIBLE`. Everything else is downstream of
  the asymmetry between a visible annoyance and an invisible loss.
- **[0005](0005-cost-gated-pipeline.md)** — why the pipeline is ordered the way
  it is, and why the tool stays free and interruptible.
- **[0003](0003-hexagonal-architecture.md)** — where code belongs, and the
  dependency rule CI enforces.

## Writing a new one

Copy [`TEMPLATE.md`](TEMPLATE.md), take the next number, add a row above.

Rules:

- **One decision per ADR.** If the title needs "and", it is two.
- **Status is never edited away.** A decision that is reversed gets a new ADR
  marked `Supersedes: NNNN`, and the old one is marked `Superseded by: NNNN`.
  The history is the point; rewriting it defeats the purpose.
- **Consequences must include the negative ones**, specifically and honestly.
  An ADR with only upsides is marketing, not a record — and it is useless to the
  person who later needs to know what this decision cost.
- **Record what you rejected and why.** The alternatives section is usually the
  most valuable part six months later.
