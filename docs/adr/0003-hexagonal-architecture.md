# ADR-0003 — Ports and adapters, with a pure domain core

**Status:** Accepted
**Date:** 2026-09-25
**Deciders:** Shivanshu Singla

## Context

The pipeline mixes two very different kinds of code:

- **Decisions** — is this user eligible? is this listing a scam? how well does
  this match the resume? These are pure functions over data, they are the
  product, and they must be trivially testable and auditable.
- **Effects** — HTTP fetches against flaky boards, SQLite writes, LLM calls with
  quotas and retries, filesystem reads. These are slow, failure-prone, and
  awkward to test.

The common failure is letting these interleave: a scraper that fetches *and*
normalises *and* filters *and* writes. The symptoms are familiar — you cannot
test the eligibility rule without a network mock; you cannot add a second LLM
provider without editing business logic; `os.getenv` appears three layers deep
inside a decision function and the decision quietly depends on ambient state.

## Decision

**Ports and adapters**, with a strictly pure `domain/`.

```
src/outpost/
├── domain/      pure. no I/O, no env, no clock, no network.
│                models, eligibility, verification rules, ranking, ports
├── adapters/    all I/O. implements the ports.
│                sources/ (boards+ATS), llm/ (providers), storage/ (sqlite), http/
├── app/         use-cases. orchestrates domain + ports. no direct I/O.
├── api/         FastAPI. HTTP delivery only.
├── cli/         Typer. CLI delivery only.
└── config/      the composition root. the ONLY place env is read.
```

**Dependency rule — dependencies point inward, always:**

```
cli/ api/  ──▶  app/  ──▶  domain/  ◀──  adapters/
                            ▲
                  domain defines the interfaces;
                  adapters implement them.
```

`domain/` imports nothing from `adapters/`, `app/`, `api/`, `cli/` or `config/`.
This is enforced by an import-linter contract in CI, not by good intentions.

Ports are `typing.Protocol`, structural rather than nominal, so adapters do not
inherit from domain types and test fakes need no registration:

```python
class JobSource(Protocol):
    name: str
    async def fetch(self) -> Sequence[RawJob]: ...

class LLMProvider(Protocol):
    async def score(self, batch: Sequence[ScoringRequest]) -> Sequence[MatchResult]: ...

class JobRepository(Protocol):
    def upsert_many(self, jobs: Sequence[Job]) -> UpsertReport: ...
```

## Consequences

### Positive

- The eligibility engine — the part that matters — is tested with plain
  function calls. No mocks, no fixtures, no event loop, no database.
- Adding an LLM provider means writing one adapter. Adding a board means
  writing one adapter. Neither touches `app/` or `domain/`.
- Tests use in-memory fakes rather than patching module paths as strings.
  `monkeypatch.setattr("module.get_connection", ...)` is a smell we have
  designed out rather than lived with.
- Swapping SQLite for Postgres later is an adapter, not a rewrite.

### Negative

- More files and more indirection than a flat module layout. For a project this
  size that is a real cost, justified by the domain core being the product and
  the adapters being genuinely swappable — both assumptions hold here, and both
  should be re-examined if they stop holding.
- Protocols are checked statically, not at runtime. A malformed adapter fails
  under `mypy`, not at import. Acceptable given `mypy --strict` in CI (NFR-6).
- Contributors must understand the layering. Documented in CONTRIBUTING and
  CLAUDE.md, enforced by the import linter so the rule is mechanical.

## Alternatives considered

**Flat modules (the shape of the predecessor project).** Rejected. It is what
produced CWD-relative prompt paths, `os.getenv` inside filter logic, and
string-patched tests. It works until the second provider.

**Full Clean Architecture with entities/use-cases/interface-adapters and DTOs at
each boundary.** Rejected as over-engineering at this size. We take the
dependency rule and the ports, and skip the ceremony of translating models
across three layers.

**Framework-native layout (FastAPI routers owning logic).** Rejected — it makes
the CLI a second-class citizen, and the CLI is the primary interface for a
local-first tool.
