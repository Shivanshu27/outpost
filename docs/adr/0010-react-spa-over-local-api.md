# ADR-0010 — React SPA over a local FastAPI, not server-rendered templates

**Status:** Accepted
**Date:** 2026-09-25
**Deciders:** Shivanshu Singla

## Context

The review interface is where the user spends their actual attention: skimming
a few hundred labelled listings, filtering, reading reasons, and moving jobs
through a status lifecycle. It is a dense, interaction-heavy table — filter,
sort, bulk-select, keyboard-navigate, annotate — rather than a document.

The predecessor used server-rendered Jinja plus HTMX, which is a good fit for
document-shaped UIs and a poor one for a stateful data grid: every filter change
is a round trip, client state (multi-select, optimistic status changes) has
nowhere natural to live, and keyboard navigation fights the request cycle.

## Decision

**FastAPI serves a JSON API on localhost; a React + TypeScript SPA consumes it.**

- **Vite + React 18 + TypeScript**, TanStack Query for server state, TanStack
  Table for the grid, Tailwind for styling.
- **The API is the contract.** The UI has no privileged access — everything it
  does is a documented endpoint, which means the CLI, the UI and any future
  client are peers over the same surface.
- **OpenAPI → TypeScript types are generated**, not hand-written, so a backend
  model change breaks the frontend build rather than producing a runtime
  `undefined`.
- **Built assets are served by FastAPI** in production mode — one process, one
  port, `outpost ui` opens a browser. Vite dev server with proxy during
  development only.
- **Optimistic updates** for status changes and overrides, with rollback on
  error. Marking twenty jobs dismissed should feel instant; it is local state
  and a local database.

## Consequences

### Positive

- The interaction model the task actually needs: instant filtering, multi-select,
  keyboard navigation, no round trip per keystroke.
- A real API boundary means the pipeline is scriptable and the UI is replaceable.
- Generated types make the frontend/backend contract mechanically enforced.
- Exercises the TypeScript/React half of the stack properly, which matters for
  this project's secondary purpose as a portfolio artifact.

### Negative

- **A build step.** This is the significant cost: contributors need Node, and
  the release process must ship built assets so end users do not. Mitigated by
  committing built assets to releases (not to git), and by the Docker image
  containing them.
- Two languages, two dependency trees, two lint/test toolchains, two CI jobs.
  Real overhead for a project of this size.
- SPA state complexity — cache invalidation, optimistic rollback — that HTMX
  simply does not have.
- Slightly worse cold start than a rendered page.

### Neutral

- An SPA on localhost has no SEO, bundle-size or network-latency concerns, which
  removes most of the usual arguments against the choice.

## Alternatives considered

**Keep Jinja + HTMX.** Genuinely the right answer for many tools, and cheaper on
every axis except the one that matters here. Rejected because the grid
interactions are the product surface, and HTMX makes exactly those expensive.

**Next.js.** Rejected — SSR, routing and server components solve problems
(SEO, TTFB, data fetching over a network) that do not exist on localhost. It
would add a framework's worth of concepts for no benefit here.

**TUI (Textual).** Seriously considered: no build step, no Node, fits a
CLI-first local tool, and would delight some of the audience. Rejected because
screenshots sell an open-source launch, and a browser UI is what a
non-terminal-native user can evaluate in ten seconds.

**Desktop app (Tauri/Electron).** Best onboarding, largest packaging burden, and
a signing/notarisation story per platform. Deferred; the SPA is a prerequisite
for it anyway, so this decision does not foreclose it.
