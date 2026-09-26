# ADR-0008 — Sources are plugins behind a fetch-only contract

**Status:** Accepted
**Date:** 2026-09-25
**Deciders:** Shivanshu Singla

## Context

Sources are the highest-churn, lowest-reliability part of the system. Boards
change their HTML, retire endpoints, add rate limits, and occasionally
disappear. This is the identified top risk in the PRD (§9): **scrapers rot, and
a rotting scraper degrades the product silently.**

Sources are also the most likely external contribution — "add my favourite
board" is the obvious first PR, and it should be possible without understanding
eligibility, storage, or scoring.

## Decision

A source implements exactly one method and nothing else:

```python
class JobSource(Protocol):
    name: str
    rate_limit: RateLimit
    async def fetch(self, since: datetime | None) -> Sequence[RawJob]: ...
```

**Sources fetch. They do not normalise, filter, dedupe, or write.** They return
`RawJob` — a loose, mostly-optional shape that preserves the original payload.
All normalisation happens in one place in `domain/normalisation.py`, shared by
every source.

This is the inverse of the usual scraper design, where each scraper produces
finished records. It is the single most important decision here: normalisation
logic that lives per-source drifts per-source, and then `remoteok` parses
salaries one way and `lever` another, and the bug is in eight places.

Supporting decisions:

- **Registry + factory.** Sources self-register by name; the pipeline resolves
  them from config. Adding one touches the new file and a registry entry.
- **Shared HTTP client** in the adapter layer owns retries, exponential backoff,
  timeouts, conditional requests and per-source rate limiting. A source author
  writes none of that.
- **Failure is isolated and reported.** One source raising cannot fail the run;
  the run report names it, and per-source yield is recorded so a source that
  returns zero listings for two consecutive runs is *visibly* broken rather than
  quietly absent.
- **Contract tests apply to all sources uniformly** — every registered source
  runs the same suite against a captured fixture: stable ids, no crash on empty,
  no crash on malformed, rate limit declared.
- **ATS sources share a base** (`Greenhouse`/`Lever`/`Ashby` differ only in URL
  shape and JSON keys), so per-company ATS support is a config entry, not code.

## Consequences

### Positive

- "Add a board" is a well-defined ~50-line task with a test template — a good
  first issue, which is how this project gets contributors.
- Normalisation bugs are fixed once, for every source.
- Rot is *visible* rather than silent, which is the actual mitigation for the
  top risk. A source cannot fail quietly.
- Captured fixtures let the whole suite run offline, in CI, deterministically.

### Negative

- `RawJob` is deliberately loose (most fields optional), so it carries weaker
  type guarantees than the domain `Job`. The boundary where looseness becomes
  strictness is `normalise()`, and that function is therefore the most
  heavily-tested in the codebase.
- Some sources genuinely have structured data others lack (ATS APIs know the
  department; an RSS feed does not). Source-specific richness has to survive as
  `extra: dict`, which is a typed hole we accept and confine.
- Fixtures go stale relative to live endpoints — a contract test can pass while
  production breaks. Mitigated by an opt-in `--live` test marker, excluded from
  CI, plus per-source yield tracking in the run report.

## Alternatives considered

**Sources produce finished `Job` objects.** Rejected: guarantees normalisation
drift across sources, which is the bug class we most want to prevent.

**Entry-point-based plugin discovery (separate pip packages).** Over-engineered
for v1 and hostile to the "one PR adds a board" contribution path. The registry
is an implementation detail we can swap for entry points later without changing
the protocol.

**Scrapy or a scraping framework.** Rejected — most sources are JSON APIs, not
crawls. The dependency would exceed the code it replaces.
