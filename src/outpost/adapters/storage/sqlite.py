"""SQLite implementation of :class:`~outpost.domain.ports.JobRepository`.

The load-bearing part of this module is :meth:`SqliteJobRepository.upsert_many`.
Its ``ON CONFLICT`` clause enumerates only source-derived columns; ``status``,
``notes`` and ``eligibility_override`` are **absent from that list, deliberately
and permanently** (ADR-0004). That omission is the guarantee that re-scraping
never destroys the user's own work, and it is covered by a regression test.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from outpost.adapters.storage.migrations import migrate
from outpost.domain.models import (
    Compensation,
    CompensationPeriod,
    ContractType,
    Eligibility,
    EligibilityVerdict,
    Job,
    JobStatus,
    MatchResult,
    VerificationResult,
    VerificationTier,
)
from outpost.domain.ports import UpsertReport

__all__ = ["SqliteJobRepository", "connect"]

_SERIALIZED = 3
"""``sqlite3.threadsafety`` value meaning the library serialises access itself."""


# Columns a scrape is allowed to refresh. Everything not listed here is either
# derived by a pipeline stage (written by its own method) or owned by the user.
_REFRESHABLE = (
    "title",
    "company",
    "description",
    "location_text",
    "comp_min",
    "comp_max",
    "comp_currency",
    "comp_period",
    "contract_type",
    "tags",
    "posted_at",
    "last_seen_at",
    "content_hash",
    "external_id",
)

_ALL_COLUMNS = (
    "id",
    "source",
    "url",
    "external_id",
    "title",
    "company",
    "description",
    "location_text",
    "comp_min",
    "comp_max",
    "comp_currency",
    "comp_period",
    "contract_type",
    "tags",
    "posted_at",
    "first_seen_at",
    "last_seen_at",
    "content_hash",
    "eligibility",
    "eligibility_json",
    "verification_tier",
    "verification_json",
    "prescore",
    "match_score",
    "match_json",
    "status",
    "notes",
    "eligibility_override",
)


def connect(path: Path) -> sqlite3.Connection:
    """Open the database, apply pragmas, and migrate.

    WAL matters here for a concrete reason: a scrape holds the connection for
    minutes, and the UI must be able to read the table while it runs.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        path,
        isolation_level=None,
        timeout=30.0,
        # FastAPI runs synchronous endpoints in a worker threadpool, so the
        # thread serving a request is not the thread that opened the database.
        # Without this, every API request fails with "SQLite objects created in
        # a thread can only be used in that same thread".
        #
        # Safe here, and checked rather than assumed: CPython reports
        # sqlite3.threadsafety == 3 (SERIALIZED), meaning the underlying
        # library serialises access itself. Combined with WAL and busy_timeout,
        # concurrent readers and a single writer behave correctly. We assert
        # the mode below rather than trusting the build.
        check_same_thread=False,
    )
    if sqlite3.threadsafety != _SERIALIZED:
        msg = (
            f"This Python's sqlite3 reports threadsafety={sqlite3.threadsafety}; "
            f"Outpost needs {_SERIALIZED} (serialised) because the API serves "
            f"requests from a threadpool."
        )
        raise RuntimeError(msg)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA synchronous = NORMAL")
    migrate(conn)
    return conn


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat()


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _dec(value: str | None) -> Decimal | None:
    return Decimal(value) if value is not None else None


class SqliteJobRepository:
    """Durable job storage.

    Satisfies :class:`~outpost.domain.ports.JobRepository` structurally — it
    does not inherit from it, because the port is a ``Protocol`` (ADR-0003).
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    @classmethod
    def open(cls, path: Path) -> SqliteJobRepository:
        return cls(connect(path))

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """Explicit transaction. ``isolation_level=None`` means autocommit
        otherwise, so batch writes must opt in."""
        self._conn.execute("BEGIN")
        try:
            yield self._conn
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def upsert_many(self, jobs: Sequence[Job]) -> UpsertReport:
        """Insert or refresh jobs, preserving everything the user owns.

        Existing rows are compared on ``content_hash`` so that ``updated`` in
        the report means *the listing actually changed*, not merely that we saw
        it again. Without that distinction every run would report every job as
        updated and the number would carry no information.
        """
        if not jobs:
            return UpsertReport()

        existing: dict[str, tuple[str, str]] = {
            row["id"]: (row["content_hash"], row["first_seen_at"])
            for row in self._conn.execute(
                f"SELECT id, content_hash, first_seen_at FROM jobs "
                f"WHERE id IN ({','.join('?' * len(jobs))})",
                [job.id for job in jobs],
            )
        }

        inserted = updated = unchanged = 0
        rows: list[tuple[Any, ...]] = []

        for job in jobs:
            prior = existing.get(job.id)
            if prior is None:
                inserted += 1
            elif prior[0] != job.content_hash:
                updated += 1
            else:
                unchanged += 1
            # Preserve the original first_seen_at: it is when *we* first saw
            # the listing, which the incoming object cannot know.
            first_seen = prior[1] if prior else _iso(job.first_seen_at)
            rows.append(self._to_row(job, first_seen_at=first_seen))

        assignments = ",\n                ".join(
            f"{col} = excluded.{col}" for col in _REFRESHABLE
        )
        sql = f"""
            INSERT INTO jobs ({", ".join(_ALL_COLUMNS)})
            VALUES ({", ".join("?" * len(_ALL_COLUMNS))})
            ON CONFLICT(id) DO UPDATE SET
                {assignments}
            -- status, notes and eligibility_override are absent above by
            -- design. A re-scrape must never destroy the user's own work.
        """

        with self._tx() as conn:
            conn.executemany(sql, rows)

        return UpsertReport(inserted=inserted, updated=updated, unchanged=unchanged)

    def save_eligibility(self, job_id: str, verdict: EligibilityVerdict) -> None:
        self._conn.execute(
            "UPDATE jobs SET eligibility = ?, eligibility_json = ? WHERE id = ?",
            (verdict.eligibility.value, verdict.model_dump_json(), job_id),
        )

    def save_eligibility_many(
        self, verdicts: Sequence[tuple[str, EligibilityVerdict]]
    ) -> None:
        with self._tx() as conn:
            conn.executemany(
                "UPDATE jobs SET eligibility = ?, eligibility_json = ? WHERE id = ?",
                [
                    (v.eligibility.value, v.model_dump_json(), jid)
                    for jid, v in verdicts
                ],
            )

    def save_verification(self, job_id: str, result: VerificationResult) -> None:
        self._conn.execute(
            "UPDATE jobs SET verification_tier = ?, verification_json = ? WHERE id = ?",
            (result.tier.value, result.model_dump_json(), job_id),
        )

    def save_match(self, job_id: str, result: MatchResult) -> None:
        self._conn.execute(
            "UPDATE jobs SET match_score = ?, match_json = ? WHERE id = ?",
            (result.score, result.model_dump_json(), job_id),
        )

    def save_prescore(self, job_id: str, score: float) -> None:
        self._conn.execute("UPDATE jobs SET prescore = ? WHERE id = ?", (score, job_id))

    def save_prescore_many(self, scores: Sequence[tuple[str, float]]) -> None:
        with self._tx() as conn:
            conn.executemany(
                "UPDATE jobs SET prescore = ? WHERE id = ?",
                [(score, jid) for jid, score in scores],
            )

    def set_status(self, job_id: str, status: JobStatus) -> None:
        self._conn.execute(
            "UPDATE jobs SET status = ? WHERE id = ?", (status.value, job_id)
        )

    def set_notes(self, job_id: str, notes: str | None) -> None:
        self._conn.execute("UPDATE jobs SET notes = ? WHERE id = ?", (notes, job_id))

    def set_eligibility_override(self, job_id: str, value: Eligibility | None) -> None:
        self._conn.execute(
            "UPDATE jobs SET eligibility_override = ? WHERE id = ?",
            (value.value if value else None, job_id),
        )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return self._from_row(row) if row else None

    def list_jobs(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        unscored_only: bool = False,
        unverified_only: bool = False,
        statuses: Sequence[JobStatus] | None = None,
        eligibilities: Sequence[Eligibility] | None = None,
        sources: Sequence[str] | None = None,
        order_by: str = "match_score",
    ) -> list[Job]:
        clauses: list[str] = []
        params: list[Any] = []

        if unscored_only:
            clauses.append("match_score IS NULL")
        if unverified_only:
            clauses.append("verification_tier IS NULL")
        if statuses:
            clauses.append(f"status IN ({','.join('?' * len(statuses))})")
            params.extend(s.value for s in statuses)
        if eligibilities:
            values = [e.value for e in eligibilities]
            clauses.append(
                f"COALESCE(eligibility_override, eligibility) "
                f"IN ({','.join('?' * len(values))})"
            )
            params.extend(values)
        if sources:
            clauses.append(f"source IN ({','.join('?' * len(sources))})")
            params.extend(sources)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        # Whitelisted, never interpolated from user input.
        order = {
            "match_score": "match_score DESC NULLS LAST, prescore DESC",
            "prescore": "prescore DESC NULLS LAST",
            "posted_at": "posted_at DESC NULLS LAST",
            "last_seen": "last_seen_at DESC",
        }.get(order_by, "match_score DESC NULLS LAST, prescore DESC")

        sql = f"SELECT * FROM jobs {where} ORDER BY {order}, id"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])

        return [self._from_row(row) for row in self._conn.execute(sql, params)]

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])

    def counts_by_eligibility(self) -> dict[str, int]:
        return {
            row[0]: row[1]
            for row in self._conn.execute(
                "SELECT COALESCE(eligibility_override, eligibility), COUNT(*) "
                "FROM jobs GROUP BY 1"
            )
        }

    def known_content_hashes(self) -> set[str]:
        return {row[0] for row in self._conn.execute("SELECT content_hash FROM jobs")}

    # ------------------------------------------------------------------
    # Mapping
    #
    # Explicit in both directions, in one place. Schema drift then surfaces as
    # a mapping error rather than a silently absent attribute (ADR-0004).
    # ------------------------------------------------------------------

    @staticmethod
    def _to_row(job: Job, *, first_seen_at: str | None) -> tuple[Any, ...]:
        comp = job.compensation
        return (
            job.id,
            job.source,
            str(job.url),
            job.external_id,
            job.title,
            job.company,
            job.description,
            job.location_text,
            str(comp.minimum) if comp.minimum is not None else None,
            str(comp.maximum) if comp.maximum is not None else None,
            comp.currency,
            comp.period.value if comp.period else None,
            job.contract_type.value,
            json.dumps(list(job.tags)),
            _iso(job.posted_at),
            first_seen_at or _iso(job.first_seen_at),
            _iso(job.last_seen_at),
            job.content_hash,
            job.eligibility.eligibility.value,
            job.eligibility.model_dump_json(),
            job.verification.tier.value if job.verification else None,
            job.verification.model_dump_json() if job.verification else None,
            job.prescore,
            job.match.score if job.match else None,
            job.match.model_dump_json() if job.match else None,
            job.status.value,
            job.notes,
            job.eligibility_override.value if job.eligibility_override else None,
        )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Job:
        period = row["comp_period"]
        return Job(
            id=row["id"],
            source=row["source"],
            url=row["url"],
            external_id=row["external_id"],
            title=row["title"],
            company=row["company"],
            description=row["description"],
            location_text=row["location_text"],
            compensation=Compensation(
                minimum=_dec(row["comp_min"]),
                maximum=_dec(row["comp_max"]),
                currency=row["comp_currency"],
                period=CompensationPeriod(period) if period else None,
            ),
            contract_type=ContractType(row["contract_type"]),
            tags=tuple(json.loads(row["tags"])),
            posted_at=_dt(row["posted_at"]),
            first_seen_at=_dt(row["first_seen_at"]) or datetime.now(UTC),
            last_seen_at=_dt(row["last_seen_at"]) or datetime.now(UTC),
            content_hash=row["content_hash"],
            eligibility=(
                EligibilityVerdict.model_validate_json(row["eligibility_json"])
                if row["eligibility_json"]
                else EligibilityVerdict(eligibility=Eligibility(row["eligibility"]))
            ),
            verification=(
                VerificationResult.model_validate_json(row["verification_json"])
                if row["verification_json"]
                else (
                    VerificationResult(tier=VerificationTier(row["verification_tier"]))
                    if row["verification_tier"]
                    else None
                )
            ),
            prescore=row["prescore"],
            match=(
                MatchResult.model_validate_json(row["match_json"])
                if row["match_json"]
                else None
            ),
            status=JobStatus(row["status"]),
            notes=row["notes"],
            eligibility_override=(
                Eligibility(row["eligibility_override"])
                if row["eligibility_override"]
                else None
            ),
        )
