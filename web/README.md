# Outpost web UI

The review interface: a Vite + React + TypeScript SPA that consumes the local
FastAPI on `127.0.0.1:8420` ([ADR-0010](../docs/adr/0010-react-spa-over-local-api.md)).

It has no privileged access to anything — every button here is an endpoint the
CLI could equally call.

## Development

```bash
npm install
npm run dev        # http://localhost:5173, proxies /api to 127.0.0.1:8420
```

Run the API alongside it, in another terminal:

```bash
outpost ui         # or: uv run outpost ui
```

The dev server proxies `/api` through, so the SPA talks to the real database
and the real rules.

## Build

```bash
npm run build
```

Vite writes to `../src/outpost/resources/web` (`index.html` plus `assets/`),
which is where `_mount_ui` in `src/outpost/api/app.py` looks for it. After a
build, `outpost ui` serves the UI and the API from one process on one port.
`base: './'` keeps asset URLs relative so the mount path does not matter.

Built assets are **not** committed — they ship in releases and in the Docker
image.

## Scripts

| | |
|---|---|
| `npm run dev` | Vite dev server with the `/api` proxy |
| `npm run build` | Typecheck, then build into the Python package |
| `npm run typecheck` | `tsc --noEmit` |
| `npm run lint` | ESLint (flat config, typescript-eslint) |
| `npm run format` | Prettier, write |

## How it is laid out

```
src/
├── types.ts            mirrors api/schemas.py — the wire contract
├── api/
│   ├── client.ts       typed fetch wrappers, ApiError / NetworkError
│   └── queries.ts      TanStack Query hooks + optimistic PATCH with rollback
├── lib/
│   ├── eligibility.ts  the visual language for the tri-state verdict
│   └── format.ts       labels and value formatting
├── hooks/              theme preference, debounce
└── components/         table, filter bar, detail panel, badges
```

There is no state-management library. TanStack Query holds server state, and
the handful of genuinely local things — which row is selected, the filter
values, the search box — are `useState` in `App.tsx`.

`src/types.ts` is hand-written and mirrors `src/outpost/api/schemas.py` field
for field. A change to the Python schema must be reflected there; it is not yet
generated from the OpenAPI document, so the compiler will not catch it for you.

## The one UI decision that matters

Eligibility is tri-state and `unknown` is a real, expected verdict, not an
error ([ADR-0002](../docs/adr/0002-tri-state-eligibility.md)). Three things
follow, and they are easy to break by accident:

1. The eligibility filter **defaults to `eligible` and `unknown`**
   (`DEFAULT_QUERY` in `App.tsx`).
2. `unknown` is styled as neutral-informative — a soft amber with a question
   glyph, at full text contrast. Not a warning triangle, not something to
   dismiss, not visually subordinate to `eligible`.
3. Switching `unknown` off shows a line explaining what is now hidden, with a
   one-click way back. That is the only nudge in the interface and it points
   towards showing more, not less.

Every eligibility colour is paired with a text label and a shape-distinct icon,
so none of the meaning is carried by colour alone.

The detail panel renders every `dimensions[]` entry with its `evidence`,
`rule_id` and `matched_text` — quoted verbatim. A verdict the user cannot audit
is a bug, and this is where they audit it.

## Accessibility

- The table is keyboard-driven: arrow keys move between rows, Enter opens one,
  Escape closes the panel.
- Sortable headers are buttons and carry `aria-sort`.
- Focus rings are visible in both themes.
- Dark mode follows `prefers-color-scheme` and can be overridden manually; the
  preference is stored in `localStorage` behind a `try`/`catch`, because
  storage can be unavailable and a theme is not worth failing a boot over.

Comfortable from 1280px; below `lg` the detail panel becomes a drawer over the
table rather than a split pane.
