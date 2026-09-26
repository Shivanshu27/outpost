# Outpost — Product Requirements

**Status:** Draft v1
**Owner:** Shivanshu Singla
**Last updated:** 2026-09-25

---

## 1. Problem

Remote job boards lie about remoteness.

A listing says **"Remote"**. The body says "Remote (US)". The ATS form rejects
non-US addresses. The recruiter replies three days later with "we can only hire
in EU timezones." For an engineer in India, Brazil, Nigeria, or the Philippines,
the overwhelming majority of listings on a "remote jobs" board are not
applicable — and there is no filter for it, because eligibility is not a field
that boards model.

The result is a search process that looks like this:

1. Open a board filtered to "Remote"
2. Open 40 tabs
3. Read 40 job descriptions looking for a location constraint buried in
   paragraph six, or a `us-only` tag, or an implied timezone requirement
4. Discover that ~5 of the 40 are actually open to you
5. Discover that 2 of those 5 were filled last month and the posting is stale
6. Repeat weekly, forever

Existing tools optimise the wrong axis. Job aggregators optimise for *volume*
(more listings = better product). Job-matching AI tools optimise for *fit*
(skills, seniority, salary). Neither models the single constraint that
invalidates most listings before fit is even worth computing: **can this person
legally and practically hold this job?**

### Who this is for

The primary user is a software engineer **outside the US and EU** looking for
remote work with companies inside them. Concretely: India, LATAM, Southeast
Asia, Africa, Eastern Europe outside the EU.

This is not a niche. It is most of the world's software engineers. It is
under-served because the tools are built by and for people who don't have the
problem.

### Why now

Two things changed:

- **ATS APIs became scrapeable at zero cost.** Greenhouse, Lever and Ashby all
  expose public, unauthenticated JSON endpoints per company. Job data no longer
  requires a paid aggregator.
- **LLM free tiers became good enough for ranking.** Judging fit between a
  resume and 200 job descriptions used to cost real money. It now costs nothing
  if the pipeline is designed to spend tokens only where cheap filters cannot
  decide.

---

## 2. Product thesis

**Eligibility is the product.** Everything else is a feature.

Outpost treats "can this person take this job?" as a first-class, explicitly
modelled, explicitly *uncertain* property of a listing — not a keyword filter.
That single decision drives the data model, the pipeline ordering, the UI, and
what we refuse to build.

Three principles follow from it:

| Principle | What it means | What it rules out |
|---|---|---|
| **Eligibility is tri-state** | `ELIGIBLE` / `INELIGIBLE` / `UNKNOWN` — never collapsed to a boolean | Silently hiding jobs we merely failed to parse |
| **Local-first** | Your resume, your search history, your rejections never leave your machine | A hosted SaaS with an account and a database of user job-searches |
| **Cheap before expensive** | Deterministic filters run before network calls; network calls run before LLM calls | Sending 5,000 listings to an LLM and calling it AI-powered |

### The tri-state rule, stated precisely

A job whose eligibility we could not determine is **`UNKNOWN`, and `UNKNOWN` is
shown to the user.** It is never coerced to `INELIGIBLE`.

This is the most important invariant in the system. The failure mode it prevents
is the one that destroys trust in a filtering tool: the user's dream job was
scraped, was parseable, was a perfect fit, and was silently discarded because a
regex didn't match. A tool that hides good jobs is worse than no tool, because
the user cannot see what they are missing.

Corollary: we would rather show ten ineligible jobs than hide one eligible one.
Precision is sacrificed for recall, deliberately, and the UI is designed to make
the resulting noise cheap to skim.

---

## 3. Goals and non-goals

### Goals

- **G1** — Collect listings from multiple free sources without any paid API.
- **G2** — Resolve eligibility per listing against a declared user profile,
  with an auditable reason for every verdict.
- **G3** — Detect dead and fraudulent listings before the user's attention is
  spent on them.
- **G4** — Rank surviving listings against the user's resume, using an LLM only
  where deterministic logic cannot decide.
- **G5** — Run entirely on a laptop, at zero marginal cost, with no account.
- **G6** — Be genuinely installable by a stranger in under five minutes.

### Non-goals

- **N1 — Auto-applying.** Outpost will never submit an application. Mass
  auto-application is the reason job boards are drowning; we will not add to it,
  and a tool that applies on your behalf cannot be trusted with your reputation.
- **N2 — A hosted multi-tenant service.** Explicitly rejected. See ADR-0001.
- **N3 — Being a job board.** We index; we do not host listings or talk to
  employers.
- **N4 — Scraping behind authentication.** No LinkedIn session cookies, no
  logged-in scraping, no ToS violations. Public endpoints only.
- **N5 — Salary/comp estimation.** Out of scope for v1; the data is unreliable.

---

## 4. Users and jobs-to-be-done

### Primary persona — "Priya, SSE in Bengaluru"

5–8 years experience, strong CV, currently employed. Wants remote USD-denominated
work, full-time or contract. Has ~3 hours a week for job searching and resents
spending 2.5 of them on filtering.

| Job to be done | Success looks like |
|---|---|
| "Show me only jobs I can actually take" | Ineligible listings are labelled, not silently dropped |
| "Don't waste my time on dead postings" | Stale and closed listings are flagged before I open them |
| "Don't let me get scammed" | Fee-upfront and impersonation listings are surfaced as suspicious |
| "Tell me which of these are worth my evening" | A ranked shortlist with a one-line reason per job |
| "Keep my search private" | Nothing leaves my machine except job-board fetches |

### Secondary persona — "Tomás, contractor in São Paulo"

Same shape, but optimises for hourly rate and contract terms over full-time
roles, and cares about payment currency and invoicing jurisdiction.

---

## 5. Functional requirements

### 5.1 Sources (G1)

| Requirement | Detail |
|---|---|
| **FR-1.1** | Collect from at least: RemoteOK, Remotive, WeWorkRemotely, Hacker News "Who is Hiring", and the Greenhouse / Lever / Ashby ATS APIs |
| **FR-1.2** | Adding a source must require implementing one interface and registering it — no changes to pipeline code |
| **FR-1.3** | A failing source must not fail the run. Errors are isolated, recorded, and reported per source |
| **FR-1.4** | Every source declares a rate limit; the fetch layer enforces it |
| **FR-1.5** | All sources are public and unauthenticated |

### 5.2 Normalisation

| Requirement | Detail |
|---|---|
| **FR-2.1** | Every listing is reduced to one canonical `Job` shape regardless of source |
| **FR-2.2** | Job identity is a deterministic hash of the canonical URL — re-running never duplicates |
| **FR-2.3** | Compensation is normalised to a single currency and period where parseable, and left null where not — never guessed |
| **FR-2.4** | Raw source payload is retained for debugging and re-parsing |

### 5.3 Eligibility (G2) — *the core*

| Requirement | Detail |
|---|---|
| **FR-3.1** | Eligibility resolves to `ELIGIBLE` / `INELIGIBLE` / `UNKNOWN` against a declared user profile |
| **FR-3.2** | Every verdict carries a machine-readable reason code and the evidence that produced it (matched phrase + source field) |
| **FR-3.3** | A listing that cannot be resolved is `UNKNOWN` and is **shown**, never hidden |
| **FR-3.4** | The rule set is data, not code — user-editable without touching Python |
| **FR-3.5** | Dimensions modelled: country/region restriction, timezone overlap requirement, work authorisation, contract type, payment currency |
| **FR-3.6** | The user can override any verdict, and overrides survive re-scrapes |

### 5.4 Verification (G3)

| Requirement | Detail |
|---|---|
| **FR-4.1** | Liveness: detect HTTP 404/410 and in-page "position closed" phrasing |
| **FR-4.2** | Legitimacy: heuristic scam detection (upfront fees, off-platform contact, implausible rates, repost patterns) with zero LLM cost |
| **FR-4.3** | Verification tiers: `OK` / `SUSPICIOUS` / `SCAM` / `EXPIRED` / `UNKNOWN` |
| **FR-4.4** | `SUSPICIOUS` is surfaced with its reason, not dropped. Only `SCAM` and `EXPIRED` are excluded by default |
| **FR-4.5** | A network failure during verification yields `UNKNOWN` — never a penalty |

### 5.5 Ranking and scoring (G4)

| Requirement | Detail |
|---|---|
| **FR-5.1** | A deterministic local pre-score orders listings before any paid call |
| **FR-5.2** | LLM scoring is batched and provider-agnostic |
| **FR-5.3** | Quota exhaustion must degrade gracefully: stop cleanly, keep what was scored, resume later |
| **FR-5.4** | Because pre-scoring orders best-first, quota exhaustion costs the user the *tail*, not a random slice |
| **FR-5.5** | At least one zero-cost scoring path must exist (local model, or manual export/import) |
| **FR-5.6** | Every score carries a human-readable reason and identified gaps |

### 5.6 Review interface

| Requirement | Detail |
|---|---|
| **FR-6.1** | Web UI listing jobs with eligibility, verification tier, score, and reason |
| **FR-6.2** | Filter by eligibility, tier, score, source, date, status |
| **FR-6.3** | Per-job status lifecycle: `NEW` → `SHORTLISTED` → `APPLIED` → `REJECTED` / `DISMISSED` |
| **FR-6.4** | User-owned fields (status, notes, overrides) are never overwritten by a re-scrape |
| **FR-6.5** | Export to CSV |

### 5.7 Onboarding (G6)

| Requirement | Detail |
|---|---|
| **FR-7.1** | `outpost init` interactively produces a working config and profile |
| **FR-7.2** | The tool runs with zero LLM configuration, in degraded-but-useful mode |
| **FR-7.3** | Docker Compose path requires no local Python |
| **FR-7.4** | Sample/demo data lets a user see the UI before running a real scrape |

---

## 6. Non-functional requirements

| ID | Requirement | Target |
|---|---|---|
| **NFR-1** | Zero paid dependencies | No service requiring a credit card |
| **NFR-2** | Privacy | No telemetry. No outbound traffic except job sources and the user's chosen LLM |
| **NFR-3** | Idempotency | Any command re-runnable without duplication or data loss |
| **NFR-4** | Full run latency | < 10 min for ~2,000 listings on a laptop |
| **NFR-5** | Test coverage | ≥ 80% on `domain/`, ≥ 60% overall, enforced in CI |
| **NFR-6** | Type safety | `mypy --strict` clean on `src/` |
| **NFR-7** | Reproducibility | Lockfile-pinned; CI on Python 3.11–3.13 |
| **NFR-8** | Cold start | `docker compose up` to a usable UI in < 3 min |

---

## 7. Success metrics

Because Outpost is local-first and collects no telemetry, **we cannot measure
users.** This is a deliberate trade — see ADR-0001 — and it means success is
measured by proxy.

| Metric | Target (90 days post-launch) |
|---|---|
| GitHub stars | 500 |
| Forks with commits | 10 |
| External contributors | 5 |
| Community-contributed sources | 3 |
| Product Hunt | Top 10 on launch day |
| Issues reporting *hidden* eligible jobs | 0 — this is the trust-critical bug class |

---

## 8. Release plan

| Milestone | Contents | Gate |
|---|---|---|
| **M1 — Core** | Domain model, eligibility engine, SQLite storage, 3 sources, CLI | Tests green, `mypy --strict` clean |
| **M2 — Pipeline** | Verification, pre-score, LLM scoring, all 7 sources | End-to-end run on real data |
| **M3 — Interface** | FastAPI + React UI, status lifecycle, export | Usable without touching the CLI |
| **M4 — Onboarding** | `init` wizard, Docker Compose, demo data, docs | A stranger installs it unaided |
| **M5 — Launch** | LICENSE, CI, CONTRIBUTING, screenshots | Public repo + Product Hunt |

---

## 9. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| **Scrapers rot** | High — the product silently degrades | Per-source health in every run report; contract tests against captured fixtures; sources are plugins so one breaking is contained |
| **LLM free tiers change** | High | Provider abstraction from day one; local-model path; manual export/import fallback |
| **Eligibility parsing is wrong** | **Critical** — hidden jobs destroy trust | Tri-state invariant; evidence on every verdict; user override; a labelled regression corpus |
| **Maintenance burden after launch** | Medium | Sources as plugins with a documented contract; CONTRIBUTING aimed at source authors |
| **Perceived as a scraping/ToS tool** | Medium | Public endpoints only; documented stance; no auto-apply; rate limits respected |
| **"Yet another AI job tool"** | Medium | Lead with eligibility and privacy, not AI. The LLM is one stage of nine |

---

## 10. Open questions

- **Q1** — Should eligibility rules ship as a community-maintained ruleset repo,
  separate from the code, so they can update faster than releases?
- **Q2** — Is there a defensible zero-cost way to detect reposted-but-unfilled
  listings across sources? (Same role, three boards, six weeks — signal or noise?)
- **Q3** — Timezone overlap needs the user's own timezone. Does it belong in the
  profile, or inferred from the system?
- **Q4** — Do we ship a curated default ATS company list, and if so, who curates
  it and on what criteria?
