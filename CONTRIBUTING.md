# Contributing

Thanks for looking. The two most valuable contributions are both small, and
neither requires understanding the whole codebase.

---

## The two things we most need

### 1. Eligibility rules for your country

The shipped ruleset was written by someone in India and covers the phrasings he
ran into. It is certainly missing yours.

Rules are YAML — no Python required:

```yaml
- id: loc.latam_welcome
  dimension: location
  when:
    any_phrase: ["latam welcome", "hiring across the americas"]
  verdict: eligible
  evidence: "Listing welcomes candidates in Latin America"
```

Edit [`src/outpost/resources/rules/default.yml`](src/outpost/resources/rules/default.yml),
add a case to the test corpus, open a PR.

### 2. Bug reports where a job was wrongly marked `ineligible`

**This is the failure that matters most**, and it is the one we cannot find on
our own — a job wrongly hidden is invisible to us by definition.

If Outpost said `ineligible` and you could actually have taken the role, please
open an issue with the listing URL and what it said. `outpost show <job-id>`
prints the rule and the exact matched phrase, which usually identifies the bug
immediately.

The opposite direction — a job wrongly shown — is a much lower priority. We
deliberately prefer showing ten you cannot take to hiding one you can.

---

## Setup

```bash
git clone https://github.com/Shivanshu27/outpost && cd outpost
uv sync
uv run outpost init
uv run pytest -q
```

Frontend:

```bash
cd web && npm install && npm run dev
```

---

## Before you open a PR

All five must pass. CI runs them on Python 3.11, 3.12 and 3.13.

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
uv run lint-imports
uv run pytest -q
```

`lint-imports` enforces the architecture, not style — see below.

---

## Architecture rules that CI enforces

Three contracts, from [ADR-0003](docs/adr/0003-hexagonal-architecture.md) and
[ADR-0009](docs/adr/0009-composition-root.md):

1. **`domain/` is pure.** No I/O, no `os.environ`, no clock, no network.
2. **Dependencies point inward.** `cli → api → config → app → adapters → domain`.
3. **Only `config/` reads configuration.**

If the linter fails you, it has almost certainly found a real design problem
rather than a technicality. It caught one in the original code: the CLI imports
the API to start the server, and the contract had declared them siblings — the
contract was the lie, not the code.

[`CLAUDE.md`](CLAUDE.md) has the full invariant list and a "traps" section of
bugs that have already bitten once.

---

## Adding a job source

About fifty lines.

1. `src/outpost/adapters/sources/<name>.py`, subclass `BaseSource`.
2. Implement `fetch()` — **fetch only**. Return `RawJob` with raw text fields.
   Do not parse dates, salaries or contract types; `domain/normalisation.py`
   does that for every source so it cannot drift between them.
3. Declare a `RateLimit`. Do not write retry or throttling code — the shared
   `HttpClient` owns it.
4. Register in `adapters/sources/__init__.py`.
5. Capture a small real payload into `tests/fixtures/` and add tests. The
   shared contract suite picks up your source automatically.

**Public, unauthenticated endpoints only.** No logged-in scraping, no session
cookies, no ToS violations. If it needs an account, we do not want it.

---

## Adding an LLM provider

1. `src/outpost/adapters/llm/<name>.py`, subclass `BatchingLLMProvider`.
2. Implement `_complete()` — the wire format, nothing else.
3. Map the provider's throttling onto `ProviderRateLimited` / `QuotaExhausted`.
   The shared policy decides whether to wait or give up, so that decision
   cannot drift between providers.
4. Register in `adapters/llm/__init__.py`.

Results must be keyed by `job_id`. Never return a positional list — providers
omit entries, and a positional contract shifts scores onto the wrong jobs.

---

## Things we will decline

Stated up front so nobody wastes an afternoon:

- **Auto-apply, in any form.** Not behind a flag, not opt-in.
  [ADR-0011](docs/adr/0011-no-auto-apply.md) explains why, and it is not going
  to be re-litigated per maintainer.
- **A hosted version, sync, or accounts.** [ADR-0001](docs/adr/0001-local-first-not-hosted.md).
- **Telemetry of any kind**, including "anonymous" usage counts.
- **Scraping behind authentication.**
- **Defaulting `exclude_unknown_eligibility` to true.** It exists as a
  user-controlled option; making it the default re-creates the silent-loss
  failure the whole design prevents.

---

## Writing an ADR

Changing something significant? Record it. Copy
[`docs/adr/TEMPLATE.md`](docs/adr/TEMPLATE.md) and take the next number.

The rules: one decision per ADR; the **negative** consequences are required and
must be specific; and record what you rejected and why. The alternatives
section is usually the most valuable part six months later.

A superseded decision gets a *new* ADR and a status update on the old one. We
do not edit history — the history is the point.

---

## Code of conduct

Be decent. This project exists because the job market treats people outside the
US and EU as an afterthought; behaving that way toward contributors would be a
poor look.
