"""Forward-only schema migrations, keyed on ``PRAGMA user_version``.

Each migration is a numbered step applied inside a transaction. There is no
rollback: a bad migration is fixed by shipping the next one (ADR-0004). For a
local, single-user database that is the right trade — the alternative is
maintaining reversibility for data only one person owns.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

__all__ = ["SCHEMA_VERSION", "migrate"]

Migration = Callable[[sqlite3.Connection], None]


def _v1_initial(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE jobs (
            id                   TEXT PRIMARY KEY,
            source               TEXT NOT NULL,
            url                  TEXT NOT NULL,
            external_id          TEXT,

            -- source-derived: refreshed on every scrape
            title                TEXT NOT NULL,
            company              TEXT,
            description          TEXT NOT NULL DEFAULT '',
            location_text        TEXT,
            comp_min             TEXT,
            comp_max             TEXT,
            comp_currency        TEXT,
            comp_period          TEXT,
            contract_type        TEXT NOT NULL DEFAULT 'unknown',
            tags                 TEXT NOT NULL DEFAULT '[]',
            posted_at            TEXT,
            first_seen_at        TEXT NOT NULL,
            last_seen_at         TEXT NOT NULL,
            content_hash         TEXT NOT NULL,

            -- derived by pipeline stages
            eligibility          TEXT NOT NULL DEFAULT 'unknown',
            eligibility_json     TEXT,
            verification_tier    TEXT,
            verification_json    TEXT,
            prescore             REAL,
            match_score          INTEGER,
            match_json           TEXT,

            -- user-owned: NEVER written by a scrape (ADR-0004)
            status               TEXT NOT NULL DEFAULT 'new',
            notes                TEXT,
            eligibility_override TEXT
        );

        CREATE INDEX idx_jobs_status       ON jobs(status);
        CREATE INDEX idx_jobs_eligibility  ON jobs(eligibility);
        CREATE INDEX idx_jobs_source       ON jobs(source);
        CREATE INDEX idx_jobs_posted_at    ON jobs(posted_at DESC);
        CREATE INDEX idx_jobs_match_score  ON jobs(match_score DESC);
        CREATE INDEX idx_jobs_content_hash ON jobs(content_hash);

        -- Partial index: the scoring stage only ever asks for unscored rows,
        -- and this keeps that query off a full scan as the table grows.
        CREATE INDEX idx_jobs_unscored ON jobs(prescore DESC)
            WHERE match_score IS NULL;

        CREATE TABLE runs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at    TEXT NOT NULL,
            finished_at   TEXT,
            report_json   TEXT
        );

        -- Per-source yield history. Silent rot is the top risk (ADR-0008):
        -- a source returning zero twice running is broken, and this is what
        -- makes that visible rather than merely absent.
        CREATE TABLE source_runs (
            run_id        INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            source        TEXT NOT NULL,
            fetched       INTEGER NOT NULL DEFAULT 0,
            error         TEXT,
            duration_s    REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (run_id, source)
        );
        """
    )


MIGRATIONS: tuple[Migration, ...] = (_v1_initial,)
SCHEMA_VERSION = len(MIGRATIONS)


def migrate(conn: sqlite3.Connection) -> int:
    """Apply any pending migrations. Returns the resulting version.

    Each step runs in its own transaction together with its version bump, so an
    interrupted upgrade never leaves a half-applied schema claiming to be
    complete.
    """
    current: int = conn.execute("PRAGMA user_version").fetchone()[0]

    if current > SCHEMA_VERSION:
        msg = (
            f"database schema version {current} is newer than this build "
            f"understands ({SCHEMA_VERSION}). Upgrade Outpost, or point at a "
            f"different database file."
        )
        raise RuntimeError(msg)

    for index in range(current, SCHEMA_VERSION):
        with conn:
            MIGRATIONS[index](conn)
            # PRAGMA cannot be parameterised; the value is a loop counter.
            conn.execute(f"PRAGMA user_version = {index + 1}")

    return SCHEMA_VERSION
