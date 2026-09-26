# 2026-09-25 — Initial build

First session. Design docs, domain core, adapters, pipeline, API, CLI, SPA, and
the first live run.

---

## What was built

| Layer | State |
|---|---|
| `docs/` | PRD + 11 ADRs + template |
| `domain/` | models, eligibility, rules, predicates, normalisation, filters, ranking, verification, ports |
| `adapters/` | 7 sources, 5 LLM providers, SQLite storage, HTTP client, verifier, profile, clock |
| `app/` | the nine-stage pipeline |
| `api/` + `cli/` | FastAPI + Typer |
| `web/` | React 18 + TypeScript SPA |
| CI | Python 3.11–3.13, web build, secret scan |

Final state: **444 tests passing**, `mypy --strict` clean across 48 files, 3
architecture contracts enforced, 75% coverage overall (pipeline 100%, filters
97%, storage 96%).

---

## Design decisions worth remembering

Recorded as ADRs 0001–0011. The three that shaped everything else:

- **Tri-state eligibility** ([0002](../adr/0002-tri-state-eligibility.md)) — the
  product invariant. `UNKNOWN` is shown, never coerced.
- **Cost-gated pipeline** ([0005](../adr/0005-cost-gated-pipeline.md)) — why the
  tool is free to run and interruptible.
- **Ports and adapters** ([0003](../adr/0003-hexagonal-architecture.md)) —
  enforced by `import-linter` in CI rather than by convention.

---

## Bugs found and fixed

Kept in full because the *how they were found* is the useful part.

### 1. `ELIGIBLE` was nearly unreachable — found by first smoke test

`EligibilityVerdict.combine` said "any `UNKNOWN` dominates". A listing saying
*"work from anywhere, we hire globally"* came back `UNKNOWN`, because it said
nothing about currency.

The rule optimised for a caution the rest of the design explicitly rejects. An
unmatched dimension means *"no constraint found on this axis"*, not *"we cannot
tell"*. ADR-0007 now records the wrong version and why it lost.

### 2. Epoch milliseconds — found by a subagent working around it

Lever returns 13-digit timestamps. Read as seconds, every Lever posting dated
to roughly the year 56,000. The workaround was in the adapter; the fix belongs
in the shared funnel (ADR-0008), keyed on magnitude rather than string length.

### 3. Silent score misattribution — found by reading a contradiction

The `LLMProvider` port promised results were *"positionally aligned with
requests"* **and** that unscorable items are omitted. Both cannot be true.
`MatchResult` carried no job id, so a model skipping index 3 shifted every
later score onto the **wrong job** — no error, no symptom, wrong data.

Now keyed by job id end to end, so misalignment is structurally impossible.
Regression test: `test_omitted_entry_does_not_shift_scores_onto_wrong_jobs`.

### 4. FastAPI dependency resolution — found by the API test suite

`ContainerDep = Annotated[Container, Depends(...)]` was defined *inside*
`create_app`. With `from __future__ import annotations`, FastAPI resolves
annotations via `get_type_hints()` against **module** globals; a local alias is
invisible there, so FastAPI treated the container as a required **query
parameter**. Every endpoint returned 422.

Silent at import, total at runtime. The alias now lives at module scope with a
comment saying why the placement is load-bearing.

### 5. SQLite across threads — found by the API test suite

FastAPI runs sync endpoints in a worker threadpool, so the request thread is
not the thread that opened the database. **Would have broken `outpost ui` on
every request in production**, not only in tests. Fixed with
`check_same_thread=False`, guarded by an assertion that
`sqlite3.threadsafety == 3`.

### 6–8. Three found by the test agent, reported as `xfail`

- `DropReason.INELIGIBLE` was reported when a job was hidden for *unknown*
  eligibility — telling the user their rules rejected it when their own opt-in
  filter hid it. Now a distinct `UNKNOWN_ELIGIBILITY` reason.
- `parse_compensation` read a bare `2024` as pay. The source comment claimed
  the intent; only half was implemented.
- **`₹25,00,000` (25 lakh) parsed as `25`.** Indian digit grouping uses
  two-digit middle groups, which a western-only regex cannot match. Especially
  bad in a tool aimed at engineers in India.

### 9–11. Three found only by the live run

The unit tests could not have caught these, which is the argument for doing a
real run before calling anything done.

- **Hacker News was ingesting the April 2020 thread.** Algolia's `/search`
  sorts by *relevance*, and relevance for "Who is hiring" means the
  most-upvoted threads of all time — so re-sorting the hits by date still gave
  2020. 476 six-year-old listings. Fixed by querying `search_by_date` scoped to
  `author_whoishiring`. `too_old` drops fell 447 → 22.
- **WeWorkRemotely jobs had no dates at all.** The adapter read `pubDate`
  correctly; `parse_posted_at` did not handle RFC 2822, which every RSS feed
  uses. Fixed in the shared funnel with `email.utils.parsedate_to_datetime`.
- **httpx logged every request at INFO**, burying the pipeline's own progress
  output under hundreds of URLs.

### 12. A corporate npm registry baked into the lockfile — found by the Docker build

**The most consequential bug of the session, and it was nearly shipped.**

`web/package-lock.json` contained **288 URLs pointing at the author's employer's
internal Nexus mirror**, because `npm install` had inherited a global
`~/.npmrc` setting `registry=` to it. Published, that would have leaked an
internal hostname and broken the build for every person who cloned the repo.

It surfaced as a Docker failure: inside a container there is no VPN, so all
~400 fetches hit ETIMEDOUT, three attempts each, ≈478 seconds, and then npm's
exit-handler bug fired with *"This is an error with npm itself"* — a message
that points at the wrong thing entirely.

Three wrong diagnoses came first, and the reason they were wrong is worth
keeping:

| Hypothesis | Disproved by |
|---|---|
| Alpine/musl npm bug | failed identically on `node:22-slim` |
| OOM (Kafka eating the VM) | failed again with 3.4 GiB free |
| QEMU multiarch emulation | the active buildx builder is native arm64 |

**The signal was in the timing all along: 480s, 478s, 477.9s.** Memory
exhaustion varies with load; that consistency was a fixed network timeout
multiplied by a fixed retry count. Chasing resources for three attempts instead
of asking why a failure was *so reproducible* cost the most time in this
session.

Fixed by removing `node_modules` and the lockfile and reinstalling against the
public registry — a plain `--registry` flag was not enough, because npm
reconstructs the lockfile from `node_modules/.package-lock.json`, which carried
the same URLs. `web/.npmrc` now pins the public registry, and CI greps the
lockfile so it cannot regress.

### 13–14. Two found by looking at the UI

- Header read **"Scoring null"** — `NullProvider.name` leaking an
  implementation detail into a place users read it as an error. Now `"none"`,
  matching what they write in config.
- Score column showed **`~0` / `~1` for every row**: `prescore` is a 0–1 float,
  `match.score` is 0–100, and the cell rounded the raw value.

---

## Two verifications that nearly went wrong

### The Docker build that "passed"

The build was backgrounded as `docker build ... ; echo "EXIT=$?"`. The
semicolon means the shell reports the **echo's** exit status, so the harness
recorded exit 0 while the build had failed. The success was an artifact of how
the command was chained.

Same shape as the `Succeed`-on-the-failure-path bug in the notes that inspired
this project: the status said green, the work had failed. Fixed by chaining
with `&&`/`||` so the build's own result is what gets recorded.

### The UI that served someone else's data

While checking the UI, `curl` returned healthy JSON with jobs in it — but the
data was `example.com/jobs/1`, "Vector Labs". Demo fixtures.

The server had failed to bind (`address already in use`) and the requests were
hitting a *different* Outpost instance left running earlier. The log line said
so; the curl output looked like success.

Worth remembering: a green response is not evidence that *your* process
produced it.

---

## Known limitations

Honest list, not a roadmap.


- **HN title extraction is heuristic** and frequently wrong — "Location:
  London, UK" appears as a job title. The comment format is freeform; the
  adapter documents this rather than pretending otherwise.
- **WeWorkRemotely and Remotive return 403** to the liveness verifier's
  user-agent. Correctly degrades to `UNKNOWN` (never a penalty), but it is
  wasted requests and a misleadingly empty "Verified" column.
- **`UNKNOWN` rate is ~73%** on a real run, dominated by Hacker News free text.
  Expected, but it is the metric to watch: a rising share means the rules are
  falling behind.
- **Mojibake in some RemoteOK titles** (`Attributeâ`) — double-encoded at
  source or mis-decoded in transit. Not yet diagnosed.
- **No live-run coverage of the Greenhouse/Lever/Ashby sources** — tested
  against fixtures only, since they need a configured company list.
- **Stage 8 has never run against a real provider.** The adapters are tested
  against mocked wire formats; no real key has been used.

---

## Next

- Diagnose the mojibake; likely an encoding assumption in the RemoteOK adapter.
- Consider skipping liveness checks for hosts that reliably 403, to stop
  spending requests on a known-unanswerable question.
- Run stage 8 against a real free-tier key and check the scoring prompt's
  calibration — the rubric is written but unvalidated.
- Screenshots for the README before any launch.
