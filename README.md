<div align="center">

# Outpost

**Job search for engineers outside the US and EU.**

Every board says *"Remote"*. Most of them mean *"Remote, US only"*.
Outpost works out which ones actually mean you.

[![CI](https://github.com/Shivanshu27/outpost/actions/workflows/ci.yml/badge.svg)](https://github.com/Shivanshu27/outpost/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

</div>

---

## The problem

You are a good engineer in Bengaluru, São Paulo, Lagos or Manila. You open a
remote job board, filter to "Remote", and get 400 results. Then you spend three
hours discovering that:

- 280 of them say *"Remote (US)"* somewhere in paragraph six
- 60 need work authorisation you do not have
- 30 require eight hours of overlap with Pacific time
- 20 were filled last month and nobody took the posting down
- 5 want a "registration fee"

The five jobs you could actually take are in there somewhere. You found them by
reading everything, which is why you only do this once a month.

**Outpost does the reading.**

## What it does

```
  2,400 listings scraped from 8 sources
    ↓  deduplicate
  1,600 unique
    ↓  resolve eligibility          ← the part nobody else does
    800 ineligible · 240 unknown · 560 eligible
    ↓  filter to what you want
    310 worth considering
    ↓  check they are alive and real
    285 live  (18 expired, 7 flagged as scams)
    ↓  rank against your resume
     20 worth your evening
```

Everything runs on your laptop. Your resume, your shortlist and the companies
you rejected never leave it.

## What makes it different

**Eligibility is the product, not a keyword filter.** Outpost models *can this
person actually hold this job* across five independent dimensions — location,
timezone overlap, work authorisation, contract type, payment currency — and
tells you which rule produced each verdict and what text it matched.

**It never silently hides a job.** Eligibility is tri-state: `eligible`,
`ineligible`, `unknown`. A listing we could not parse is `unknown`, and
`unknown` is **shown**. A filtering tool that quietly drops your dream job
because a regex missed is worse than no tool at all, because you never find
out. We would rather show you ten jobs you cannot take than hide one you can.
([ADR-0002](docs/adr/0002-tri-state-eligibility.md))

**It is free to run, by design.** Nine pipeline stages ordered by cost: the
expensive one runs last, on the smallest set, in priority order. Run out of
free-tier quota and you lose the *tail* of the ranking, not a random slice.
([ADR-0005](docs/adr/0005-cost-gated-pipeline.md))

**It will never apply on your behalf.** Not behind a flag, not opt-in. Mass
auto-application is why the market you are competing in is degrading, and an
application sent in your name that you did not read is your reputation, not
ours. ([ADR-0011](docs/adr/0011-no-auto-apply.md))

## Quickstart

```bash
uv tool install outpost        # or: pipx install outpost
outpost init                   # interactive: where you are, what you want
outpost run                    # scrape → filter → verify → rank
outpost ui                     # review at http://localhost:8420
```

No account. No API key required — without one you still get everything through
stage 7 (deduplicated, eligibility-labelled, liveness-checked, roughly ranked),
which is most of the value.

<details>
<summary><b>Docker instead</b></summary>

```bash
git clone https://github.com/Shivanshu27/outpost && cd outpost
docker compose run --rm outpost init
docker compose up
```

Builds the frontend and the Python app, runs as a non-root user, and keeps
your config and database in named volumes — so `docker compose down -v` erases
everything Outpost knows about you. Built and smoke-tested in CI on every push.

</details>

<details>
<summary><b>From source</b></summary>

```bash
git clone https://github.com/Shivanshu27/outpost && cd outpost
uv sync
uv run outpost init
```

</details>

## Where your data lives

Outpost strictly separates **code** from **user state**. Nothing about your job search, your resume, or your notes is ever stored in the repository folder, preventing accidental leaks to GitHub:

| File | Purpose | Location |
|---|---|---|
| `profile.yml` | Your location, timezone, skills, and target titles | Config dir (created by `outpost init`) |
| `resume.txt` | Plain-text resume for Stage 8 LLM matching | Config dir (optional) |
| `config.yml` | API keys, provider choices, and custom filters | Config dir (see `config.example.yml`) |
| `rules.yml` | Custom eligibility overrides | Config dir (optional) |
| `outpost.db` | Local SQLite database of scraped jobs & notes | Data dir (created on first run) |

**Default paths by OS:**
* **macOS:** `~/Library/Application Support/outpost/`
* **Linux:** `~/.config/outpost/` (config) and `~/.local/share/outpost/` (database)
* **Windows:** `%LOCALAPPDATA%\outpost\`

> Run `outpost doctor` at any time to print the exact paths for your system and verify your setup.

## Configuring eligibility

Rules are YAML, not code. The shipped ruleset covers the common phrasings;
override any of it by id, or add your own.

```yaml
# ~/.config/outpost/rules.yml
version: 1
rules:
  - id: loc.brazil_friendly
    dimension: location
    when:
      any_phrase: ["hiring in brazil", "latam welcome", "americas timezone"]
    verdict: eligible
    evidence: "Listing welcomes candidates in Latin America"
```

Rules are evaluated in order, first match per dimension wins, and every verdict
records the rule id and the exact text it matched — so when a verdict is wrong,
you can see why and fix it yourself.

See [ADR-0007](docs/adr/0007-eligibility-rules-as-data.md) for the rule
language, and [`default.yml`](src/outpost/resources/rules/default.yml) for the
shipped set.

## Sources

| Source | Type | Auth |
|---|---|---|
| RemoteOK | Board API | none |
| Remotive | Board API | none |
| WeWorkRemotely | RSS | none |
| Hacker News "Who is hiring" | API | none |
| Greenhouse | ATS, per company | none |
| Lever | ATS, per company | none |
| Ashby | ATS, per company | none |

All public, all unauthenticated, all rate-limited politely. No logged-in
scraping, no session cookies, no ToS violations
([ADR-0008](docs/adr/0008-source-plugin-contract.md)).

Adding one is about fifty lines and one registry entry — see
[CONTRIBUTING.md](CONTRIBUTING.md).

## Scoring providers

| Provider | Cost | Notes |
|---|---|---|
| Gemini | free tier | default when a key is present |
| OpenAI-compatible | BYO key | OpenRouter, Groq, Together, local servers |
| Ollama | free, local | fully offline — nothing leaves your machine |
| Manual | free | exports markdown batches, re-imports results |
| None | free | pipeline stops after stage 7 and is still useful |

Scores are only comparable *within* a provider, so the provider is recorded on
every score and shown in the UI
([ADR-0006](docs/adr/0006-provider-agnostic-llm.md)).

## Architecture

Ports and adapters, with a pure domain core. Dependencies point inward, and
CI enforces it with an import linter rather than trusting convention.

```
src/outpost/
├── domain/      pure. no I/O, no env, no clock, no network.
│                models · eligibility · rules · normalisation · ranking
├── adapters/    all I/O. implements the ports the domain declares.
│                sources/ · llm/ · storage/ · http/
├── app/         use-cases. orchestrates domain + ports.
├── api/         FastAPI — JSON over localhost.
├── cli/         Typer.
└── config/      the composition root. the only place env is read.
```

The full picture is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). The
reasoning — including what we rejected and what each decision cost — is in
[docs/adr/](docs/adr/README.md).

## Privacy

Outpost is local-first ([ADR-0001](docs/adr/0001-local-first-not-hosted.md)).
There is no Outpost server, no account, and no telemetry. State is one SQLite
file you own and can delete.

Outbound traffic is limited to the job boards being scraped and, if you
configure one, your chosen LLM provider with your own key. Configure Ollama and
there is no third party at all.

The cost of this is real and worth stating: **we cannot measure anything.** No
usage data, no funnel, no "users found this confusing." Product decisions come
from GitHub issues and nothing else. If Outpost is useful to you, saying so is
the only way we can know.

## Documentation

| | |
|---|---|
| [PRD](docs/PRD.md) | What this is, who for, what it deliberately is not |
| [Architecture](docs/ARCHITECTURE.md) | How it fits together, with diagrams |
| [ADRs](docs/adr/README.md) | Every significant decision and its cost |
| [Contributing](CONTRIBUTING.md) | Adding sources, rules and providers |

## Status

**Alpha.** The pipeline works end to end and is in daily use by its author.
Expect rough edges in onboarding and source coverage.

The most useful things you can contribute: **eligibility rules for your own
country**, and **bug reports where a job was labelled `ineligible` but you could
actually have taken it** — that last one is the failure that matters most, and
it is the hardest for us to find on our own.

## License

MIT — see [LICENSE](LICENSE).
