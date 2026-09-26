# ADR-0004 — SQLite with explicit SQL and versioned migrations, not an ORM

**Status:** Accepted
**Date:** 2026-09-25
**Deciders:** Shivanshu Singla

## Context

Outpost is local-first (ADR-0001), single-user, single-writer, and stores on the
order of 10⁴–10⁵ job rows. It needs durable state that survives between runs,
supports ad-hoc user queries, and is trivially inspectable and deletable.

The storage layer also carries the system's most important write-time invariant:
**re-scraping must never destroy user-owned state** (FR-6.4). Status, notes,
eligibility overrides and scores are the user's work; source-derived fields are
not. A re-scrape refreshes the latter and must leave the former untouched.

## Decision

**SQLite**, accessed through the stdlib `sqlite3` driver with **explicit SQL**
behind the `JobRepository` port (ADR-0003). No ORM.

Specifics:

- **WAL mode**, `foreign_keys=ON`, `busy_timeout` set — a long scrape must not
  block the UI reading concurrently.
- **Versioned forward-only migrations** keyed on `PRAGMA user_version`, applied
  in a transaction at startup. Each migration is an integer-numbered module.
- **Idempotent upsert** where the conflict clause enumerates *only*
  source-derived columns:

  ```sql
  INSERT INTO jobs (...) VALUES (...)
  ON CONFLICT(id) DO UPDATE SET
      title = excluded.title,
      description = excluded.description,
      last_seen_at = excluded.last_seen_at
      -- status, notes, eligibility_override, match_score are ABSENT
      -- from this list, deliberately and permanently.
  ```

  The omission is the invariant. It is commented at the call site and covered by
  a regression test that asserts a re-scrape preserves user fields.

- **Row → model mapping is explicit**, in one place per aggregate, so schema
  drift surfaces as a mapping error rather than a silently missing attribute.

## Consequences

### Positive

- Zero infrastructure. One file. `docker compose` needs no database service, and
  a user can delete their entire history with `rm`.
- The user can open `outpost.db` in any SQLite browser and answer their own
  questions. For a personal tool this is a genuine feature.
- Explicit SQL means the exact conflict-resolution semantics are visible in the
  source, which matters because that clause *is* the user-data guarantee. An
  ORM's `merge()` or `upsert()` would hide the one thing that must not be hidden.
- No lazy-loading surprises, no session lifecycle, no N+1 emitted from a
  template.

### Negative

- Hand-written SQL is more code than declarative models, and typos are caught at
  runtime rather than compile time. Mitigated by keeping SQL confined to the
  repository adapter and testing it against a real in-memory database rather
  than a mock.
- Single-writer. Two concurrent `scrape` runs will contend; WAL plus
  `busy_timeout` degrades this to waiting rather than corruption, and a process
  lock makes it an explicit error.
- Migrations are forward-only: no rollback. A bad migration is fixed by shipping
  another one. Acceptable for local single-user data; would not be for a
  multi-tenant service.
- Porting to Postgres later means rewriting the adapter's SQL dialect — the port
  boundary contains the damage, but does not eliminate it.

## Alternatives considered

**SQLAlchemy ORM + Alembic.** The conventional choice, and genuinely good for
multi-developer services with evolving schemas. Rejected here: it adds a
significant dependency and a large conceptual surface to a single-file,
single-writer, ~10-table local database, and it obscures the upsert semantics
that carry our most important guarantee. The cost/benefit inverts at this size.

**SQLAlchemy Core (expression language, no ORM).** The closest call. Would give
dialect portability and composable query building while keeping SQL semantics
visible. Rejected for v1 on dependency-weight grounds, but this is the migration
path if Postgres support is ever needed, and the repository port is drawn so
that it stays available.

**Postgres via Docker.** Rejected — it contradicts local-first by requiring a
running service for a personal tool, and buys concurrency we do not need.

**JSON/JSONL files.** Rejected. No query capability, no transactions, and the
user-owned-field merge becomes hand-rolled read-modify-write with a race.
