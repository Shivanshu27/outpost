# ADR-0001 — Local-first, not a hosted service

**Status:** Accepted
**Date:** 2026-09-25
**Deciders:** Shivanshu Singla

## Context

Outpost processes a user's resume, their salary expectations, the jobs they
shortlisted, and — most sensitively — the jobs they *rejected* and the companies
they are quietly leaving their current employer for.

The default architecture for a tool like this is a hosted SaaS: users sign up,
upload a resume, and the server runs the pipeline. That model is easier to
monetise, easier to instrument, and easier to onboard into.

It also means operating a database that is a list of employed engineers who are
secretly job-hunting, indexed by employer. That database has a market value to
parties whose interests are directly opposed to the user's, and it is a
subpoena target, a breach target, and an acquisition asset.

## Decision

**Outpost runs entirely on the user's machine.** There is no Outpost server, no
account, no sync, and no telemetry.

Network traffic is limited to:
1. Public job-board and ATS endpoints
2. The LLM provider the user configured, with the user's own key

State lives in a single SQLite file the user owns and can delete.

## Consequences

### Positive

- The sensitive data problem is not mitigated — it is *structurally absent*.
  There is no server to breach.
- "Your job search never leaves your laptop" is a real differentiator against
  every hosted competitor, and it is verifiable by reading the source.
- No hosting cost means no monetisation pressure, which means no incentive to
  grow the data surface later.
- Users can run it against sources we have never heard of, on rules we did not
  write, without asking us.

### Negative

- **We cannot measure usage.** No funnel, no retention, no "users found this
  filter confusing." Product decisions are made from issues and intuition. This
  is a real, ongoing cost, accepted knowingly. See PRD §7.
- Onboarding is harder: installation instead of a signup form. Mitigated by
  `outpost init` and a Docker path (FR-7.1, FR-7.3).
- Each user independently scrapes the same boards. Inefficient in aggregate and
  slightly rude to the sources. Mitigated by per-source rate limits (FR-1.4) and
  conditional requests.
- No server-side scheduled runs. The user schedules it themselves (cron,
  launchd, Task Scheduler) — documented, not automated.

### Neutral

- SQLite is sufficient at this scale and removes a service dependency, but ties
  us to single-writer semantics. See ADR-0004.

## Alternatives considered

**Hosted SaaS with encryption at rest.** Rejected. Encryption at rest protects
against disk theft, not against the operator, a subpoena, or an acquirer. It
does not change who holds the data.

**Local compute, hosted sync.** Rejected. Sync requires the data to exist on a
server, which reintroduces the entire problem for a convenience most single-device
users don't need.

**Browser extension.** Considered seriously — genuinely local, trivial to
install. Rejected for v1 because the pipeline needs a filesystem, a database,
long-running fetches, and a resume parser, all of which fight the extension
sandbox. Worth revisiting as a companion, not a replacement.
