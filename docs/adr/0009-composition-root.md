# ADR-0009 — A single composition root; no ambient configuration

**Status:** Accepted
**Date:** 2026-09-25
**Deciders:** Shivanshu Singla

## Context

The convenient way to read configuration in Python is `os.getenv` at the point
of use. It requires no wiring and no parameters. It is also how business logic
acquires invisible dependencies on process state.

The concrete symptoms, observed in the predecessor project:

- `os.getenv("MIN_HOURLY_RATE_USD")` read inside filter logic — the filter's
  behaviour depends on ambient state its signature does not mention, so it
  cannot be tested without mutating the environment.
- `Path("prompts")` resolved relative to the **current working directory** — the
  tool silently fell back to a degraded inline prompt when run from anywhere
  else. Silent degradation, no error, wrong output.
- Tests compensating with `monkeypatch.setattr("module.get_connection", ...)` —
  patching by string path, which is the reliable tell that a dependency should
  have been a parameter.

There is also a documentation consequence: when config is read at point of use,
there is no single place that enumerates what the program is configured by, and
the README drifts from reality.

## Decision

**Configuration is read exactly once, at the edge, and passed inward.**

- `config/` is the **composition root** and the *only* package permitted to
  touch `os.environ`, read config files, or construct adapters. This is enforced
  by an import-linter contract and a grep test in CI.
- It produces a frozen, validated `Settings` object plus a `Container` holding
  constructed ports:

  ```python
  def build_container(settings: Settings) -> Container:
      return Container(
          repository=SqliteJobRepository(settings.db_path),
          llm=make_llm_provider(settings.llm),
          sources=make_sources(settings.sources),
          clock=SystemClock(),
      )
  ```

- `cli/` and `api/` build the container at startup and hand it to use-cases.
  Nothing below the edge reads the environment.
- **Resource paths resolve against the package**, via `importlib.resources` —
  never against the CWD. A default prompt or ruleset is found identically
  regardless of where the process was launched.
- **The clock is a port.** `datetime.now()` is a dependency like any other;
  injecting it makes staleness and expiry logic testable without sleeping or
  freezing time globally.

## Consequences

### Positive

- Every function's dependencies are in its signature. Domain functions are
  testable by calling them with values.
- Tests construct real objects with fakes rather than patching import paths.
  `monkeypatch.setattr("...")` disappears from the suite, and when it reappears
  in a PR it is a visible smell.
- `Settings` is a single validated schema, which makes configuration
  self-documenting and makes a bad config fail at startup with a clear message
  instead of misbehaving at stage 8.
- The CWD-relative resource bug is structurally impossible.

### Negative

- More explicit wiring. Adding a dependency to a deep function means threading
  it through, which is friction — deliberately, because that friction is the
  signal that a function is acquiring responsibilities.
- `Container` can drift toward a service locator if code starts passing the
  whole container around instead of the ports it needs. The rule is: use-cases
  receive the *ports they use*, not the container. Reviewed in PRs.
- Slightly higher barrier for a casual contributor than "just read the env var."
  Documented in CONTRIBUTING with an example.

## Alternatives considered

**A DI framework (`dependency-injector`, `wired`).** Rejected — a dependency and
a DSL to replace roughly thirty lines of explicit construction. Manual wiring at
this size is clearer than any framework's magic.

**Pydantic `BaseSettings` read wherever needed.** Better than raw `os.getenv`
(validated, typed), but still ambient: any module can import and instantiate it,
so dependencies stay invisible in signatures. We use `BaseSettings` *inside* the
composition root to parse, and pass the result inward.

**Module-level singletons initialised at import.** Rejected. Import-time side
effects, untestable, and order-dependent.
