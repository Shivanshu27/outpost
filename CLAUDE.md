# Working on Outpost

Context for AI assistants and for humans who want the rules without reading
every file. If something here contradicts the code, the code is right and this
file is stale — say so.

---

## What this is

A local-first job-search tool for engineers **outside the US and EU**. The
product thesis is that *eligibility* — can this person actually hold this job —
is a first-class, explicitly uncertain property, not a keyword filter.

Read [`docs/PRD.md`](docs/PRD.md) for the problem, and
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the shape.

---

## The invariants

These are not style preferences. Breaking any of them is a bug, and each has an
ADR behind it.

### 1. `UNKNOWN` never becomes `INELIGIBLE`

`Eligibility` is tri-state. A listing we could not parse is `UNKNOWN`, and
`UNKNOWN` is **shown to the user**. No code path may coerce it.

The failure this prevents is invisible and unbounded: the user cannot tell the
difference between "there were no good jobs this week" and "the parser dropped
them". A filtering tool that silently hides good results destroys the trust
that is the only reason to use it — and does so without ever producing a bug
report.

*Corollary:* we would rather show ten ineligible jobs than hide one eligible
one. Recall over precision, deliberately. ([ADR-0002](docs/adr/0002-tri-state-eligibility.md))

### 2. Every determined verdict carries its evidence

A verdict you cannot explain is a bug. `DimensionVerdict` rejects construction
without a `rule_id` and `evidence`. The UI renders the matched phrase verbatim.

### 3. A re-scrape never destroys user work

`status`, `notes` and `eligibility_override` are **absent from the `ON
CONFLICT` clause** in `upsert_many`, deliberately and permanently. If you add a
column, decide which of the three ownership classes it belongs to before you
add it. ([ADR-0004](docs/adr/0004-sqlite-explicit-sql.md))

### 4. `domain/` is pure

No I/O, no `os.environ`, no clock, no network. Time is injected via the `Clock`
port. CI fails on violation via `import-linter` — this is mechanical, not
aspirational. ([ADR-0003](docs/adr/0003-hexagonal-architecture.md))

### 5. Only `config/` reads configuration

`os.getenv` anywhere else is a bug. Resources resolve against the **package**
via `importlib.resources`, never the CWD. ([ADR-0009](docs/adr/0009-composition-root.md))

### 6. Sources fetch; they do not parse

A source returns `RawJob` with raw text fields. All parsing happens in
`domain/normalisation.py`. Per-source parsing drifts per-source.
([ADR-0008](docs/adr/0008-source-plugin-contract.md))

### 7. LLM results are keyed by job id, never positional

Providers omit entries they cannot score. A positional contract lets one
omission shift every later score onto the wrong job — silently.
([ADR-0006](docs/adr/0006-provider-agnostic-llm.md))

### 8. Never guess a number

Unparseable compensation yields `None`, not an inferred midpoint. A wrong
number feeds the ranking and the user's decision to apply.

### 9. Outpost never submits an application

Not behind a flag, not opt-in. The boundary is at *composition*: we may draft,
we never send. ([ADR-0011](docs/adr/0011-no-auto-apply.md))

---

## Layout

```
src/outpost/
├── domain/      pure core — the product
├── adapters/    all I/O behind ports
├── app/         use-cases (the pipeline)
├── api/         FastAPI
├── cli/         Typer
└── config/      composition root
web/             React + TypeScript SPA
```

The dependency rule: `cli → api → config → app → adapters → domain`.

---

## Commands

```bash
uv sync                                   # install
uv run pytest -q                          # 444 tests, ~3s, no network
uv run ruff check src tests               # lint
uv run ruff format src tests              # format
uv run mypy                               # strict, must be clean
uv run lint-imports                        # the three architecture contracts
uv run pytest --cov=outpost --cov-report=term-missing

cd web && npm run dev                     # frontend dev server
cd web && npm run build                   # builds into src/outpost/resources/web/

uv run outpost doctor                     # check a setup
uv run outpost run                        # the pipeline
uv run outpost ui                         # review interface
```

**All five checks must pass before anything ships.** CI runs them on Python
3.11, 3.12 and 3.13.

---

## Adding things

### A job source

1. Create `adapters/sources/<name>.py`, subclass `BaseSource`.
2. Implement `fetch()` only. Return `RawJob` with **raw text** — no parsing.
3. Declare a `RateLimit`.
4. Register it in `adapters/sources/__init__.py`.
5. Add a captured fixture under `tests/fixtures/` and tests. The shared
   contract suite runs against every registered source automatically.

About fifty lines. Do not add retry, backoff or throttling — the shared
`HttpClient` owns those.

### An eligibility rule

Edit `src/outpost/resources/rules/default.yml`. Rules are data, ordered, and
first-match-wins per dimension, so **declare specific rules before general
ones**. Add a case to the eligibility test corpus.

Phrase matching is word-boundary anchored — without that, `"us"` matches inside
`"status"`, `"industry"` and `"discuss"`, which marks most of the corpus
US-only. If you add a short phrase, test the false-positive case.

### An LLM provider

1. Create `adapters/llm/<name>.py`, subclass `BatchingLLMProvider`.
2. Implement `_complete()` — the wire format only.
3. Map the provider's throttling dialect onto `ProviderRateLimited` /
   `QuotaExhausted`. Do not implement your own retry loop.
4. Register in `adapters/llm/__init__.py`.

---

## Conventions

- **Python 3.11+**, `from __future__ import annotations`, fully annotated.
  `mypy --strict` must pass.
- **Timezone-aware datetimes only.** Ruff's `DTZ` rules are on.
- **Docstrings explain _why_.** The *what* is usually visible in the code; the
  reasoning is not, and it is what decays. Comment the non-obvious decision,
  not the obvious line.
- **Typed failure signals, not generic exceptions.** The pipeline branches on
  `QuotaExhausted` (a clean end) versus an unexpected error (not). A bare
  `except Exception` cannot make that distinction — where one is genuinely
  right (isolating a source, a failed liveness check), comment why.
- **Line length 88.** Ruff format is authoritative.
- **No `print()`** outside `cli/`. The pipeline reports through a progress
  callback; the presentation layer decides how to render.

---

## Traps

Things that have already bitten, kept here so they bite only once.

- **FastAPI + `from __future__ import annotations`:** a `Annotated[...,
  Depends(...)]` alias defined *inside* a function is a local, so
  `get_type_hints()` cannot resolve it and FastAPI silently treats the
  parameter as a **query parameter** — 422 on every endpoint. Keep dependency
  aliases at module scope.
- **SQLite across threads:** FastAPI runs sync endpoints in a threadpool, so
  the connection needs `check_same_thread=False`. We assert
  `sqlite3.threadsafety == 3` rather than assuming it.
- **Epoch milliseconds:** Lever returns 13-digit timestamps. Read as seconds,
  they date listings to roughly the year 56,000. Handled by magnitude in
  `parse_posted_at`.
- **Algolia relevance ranking:** searching HN for "Who is hiring" by relevance
  returns the most-upvoted threads *of all time*. A live run ingested the April
  2020 thread. Query `search_by_date` + `author_whoishiring` instead.
- **Indian digit grouping:** `₹25,00,000` is 25 lakh. A western-only grouping
  regex reads it as `25`.
- **Prescore is 0–1; match score is 0–100.** Do not render them on the same
  scale without converting.
- **`filterwarnings = ["error"]`** in pytest means a new library deprecation
  fails the suite. That is intentional.

---

## Session log

Work in progress and design history: [`docs/sessions/`](docs/sessions/).
