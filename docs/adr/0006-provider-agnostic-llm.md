# ADR-0006 — Provider-agnostic LLM layer with a no-LLM fallback

**Status:** Accepted
**Date:** 2026-09-25
**Deciders:** Shivanshu Singla

## Context

Stage 8 of the pipeline (ADR-0005) scores listings against the user's resume
using an LLM. The obvious implementation is to call whichever provider has the
best free tier today and move on.

That couples the product to a commercial decision made by someone else. Free
tiers are marketing budgets: they get cut, rate-limited, geo-restricted, or
retired. A tool whose core feature dies when one vendor changes a pricing page
is not a tool a stranger should depend on — and "requires an API key from a
specific vendor, in a specific set of countries" is a hard onboarding barrier
for exactly our user base (PRD §4).

## Decision

**One port, several adapters, and a path that needs no provider at all.**

```python
class LLMProvider(Protocol):
    name: str
    async def score(
        self, batch: Sequence[ScoringRequest]
    ) -> Sequence[MatchResult]: ...
```

Adapters shipped in v1:

| Adapter | Cost | Role |
|---|---|---|
| **Gemini** | free tier | default when a key is present |
| **OpenAI-compatible** | BYO key / free routers | covers OpenRouter, Groq, Together, local servers |
| **Ollama** | free, local | fully offline; the privacy-maximal path |
| **Manual** | free | exports batches to markdown, re-imports results |
| **Null** | free | no scoring; pipeline stops after stage 7 |

Supporting decisions:

- **Prompts are data**, in `prompts/*.txt`, resolved relative to the *package*
  (via `importlib.resources`), never relative to the current working directory.
  A prompt that silently degrades because you ran the tool from another
  directory is a failure mode we are designing out, not discovering later.
- **The wire contract is owned by us**, not by a provider. Every adapter returns
  the same validated `MatchResult`; malformed output is a provider-adapter
  problem and never reaches the domain.
- **Retry, backoff and rate limiting live in a shared decorator**, not
  duplicated per adapter. Provider-specific hints (e.g. a `retryDelay` in a 429
  body) are parsed by the adapter and surfaced to the shared policy.
- **Quota exhaustion is a typed domain signal** (`QuotaExhausted`), not a
  generic exception — the pipeline catches it explicitly and ends the run
  cleanly (ADR-0005).

## Consequences

### Positive

- A provider change is one file. The product survives any single vendor.
- Ollama makes a fully offline, fully private run possible, which is the logical
  endpoint of ADR-0001 and a strong story for privacy-conscious users.
- The Null adapter means the tool installs and runs usefully with zero
  configuration — no key, no signup, no decision on first use.
- Contributors can add a provider without understanding the pipeline.

### Negative

- **Output quality varies sharply across providers**, and the same profile will
  rank differently on Gemini Flash than on a 7B local model. Scores are
  therefore only comparable *within* a provider. This is a genuine product wart;
  we mitigate it by recording which provider produced each score and surfacing
  it in the UI, rather than pretending scores are absolute.
- The abstraction is the lowest common denominator: no structured-output APIs,
  no provider-specific tool calling. Accepted — we need one JSON object per job,
  which every provider can do.
- More adapters is more surface to test. Mitigated by a shared contract test
  suite every adapter must pass.

## Alternatives considered

**Gemini only.** Simplest, and what the predecessor project did. Rejected — it
is the single point of failure this ADR exists to remove, and its own docs
flagged it as the project's main risk.

**LangChain / LiteLLM as the abstraction.** Rejected. Our surface is one method
returning one validated shape; a general-purpose abstraction layer would add a
large dependency and its own breaking changes to avoid writing roughly 40 lines
per provider. Reasonable for a project that needs agents, chains or tools — we
need none of those.

**Local model only.** Rejected as the default: it requires a multi-GB download
before first useful output, which is a worse onboarding barrier than an API key.
Retained as a first-class option, not the default.
