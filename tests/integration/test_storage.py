"""SQLite storage — where the user-data guarantee lives.

ADR-0004 puts the system's most important write-time invariant in one
``ON CONFLICT`` clause: a re-scrape refreshes source-derived columns and must
never touch ``status``, ``notes`` or ``eligibility_override``. Those are the
user's own work, accumulated over weeks, and a scrape that quietly resets them
destroys the reason to use the tool at all.

Tested against a real database file rather than a mock, deliberately. The
guarantee *is* the SQL; a mock would only assert our belief about it.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from tests.conftest import MakeJob

from outpost.adapters.storage.migrations import SCHEMA_VERSION, migrate
from outpost.adapters.storage.sqlite import SqliteJobRepository, connect
from outpost.domain.models import (
    CompensationPeriod,
    ContractType,
    DimensionVerdict,
    Eligibility,
    EligibilityDimension,
    EligibilityVerdict,
    JobStatus,
    MatchResult,
    VerificationResult,
    VerificationTier,
)


def _verdict(eligibility: Eligibility) -> EligibilityVerdict:
    return EligibilityVerdict.combine(
        [
            DimensionVerdict(
                dimension=EligibilityDimension.LOCATION,
                eligibility=eligibility,
                rule_id="loc.us_only",
                evidence="Listing restricts hiring to the United States",
                matched_text="US only",
                source_field="location_text",
            ),
            DimensionVerdict.unknown(EligibilityDimension.CURRENCY),
        ]
    )


# --------------------------------------------------------------------------
# THE regression test (ADR-0004)
# --------------------------------------------------------------------------


def test_a_rescrape_refreshes_source_fields_and_preserves_the_users_work(
    repo: SqliteJobRepository, make_job: MakeJob, now: datetime
) -> None:
    """The ADR-0004 guarantee, end to end.

    The omission of ``status``, ``notes`` and ``eligibility_override`` from the
    upsert's conflict clause is the invariant. This test walks the exact
    sequence that would expose its loss: the user shortlists a job and writes a
    note, the board edits the listing, we scrape again.

    If this test ever fails, a weeks-old shortlist has been silently reset and
    the user has no way to recover it.
    """
    original = make_job(
        title="Senior Backend Engineer",
        company="Acme Systems",
        description="We use Python.",
    )
    repo.upsert_many([original])

    # The user does their own work on the listing.
    repo.set_status(original.id, JobStatus.APPLIED)
    repo.set_notes(original.id, "Referred by Priya. Phone screen on the 14th.")
    repo.set_eligibility_override(original.id, Eligibility.ELIGIBLE)

    # The board edits the listing; we scrape again a week later.
    later = now + timedelta(days=7)
    edited = make_job(
        url=str(original.url),
        title="Staff Backend Engineer",
        company="Acme Systems Inc.",
        description="We use Python and Rust.",
        location_text="Remote — Worldwide",
    ).model_copy(update={"last_seen_at": later})
    repo.upsert_many([edited])

    stored = repo.get(original.id)

    assert stored is not None
    # Source-derived fields refreshed...
    assert stored.title == "Staff Backend Engineer"
    assert stored.company == "Acme Systems Inc."
    assert stored.description == "We use Python and Rust."
    assert stored.location_text == "Remote — Worldwide"
    assert stored.content_hash == edited.content_hash
    # ...user-owned fields untouched. This is the guarantee.
    assert stored.status is JobStatus.APPLIED
    assert stored.notes == "Referred by Priya. Phone screen on the 14th."
    assert stored.eligibility_override is Eligibility.ELIGIBLE
    assert stored.effective_eligibility is Eligibility.ELIGIBLE


def test_a_rescrape_preserves_user_state_even_when_nothing_changed(
    repo: SqliteJobRepository, make_job: MakeJob
) -> None:
    """The no-op path is the common one — most re-scrapes see an unchanged
    listing — so it must be covered explicitly rather than assumed."""
    job = make_job()
    repo.upsert_many([job])
    repo.set_status(job.id, JobStatus.SHORTLISTED)
    repo.set_notes(job.id, "Worth a look")

    repo.upsert_many([job])

    stored = repo.get(job.id)
    assert stored is not None
    assert stored.status is JobStatus.SHORTLISTED
    assert stored.notes == "Worth a look"


def test_an_incoming_scrape_cannot_reset_user_state_even_if_it_carries_some(
    repo: SqliteJobRepository, make_job: MakeJob
) -> None:
    """A normalised job always carries the default ``status=new``.

    That default must not be written over a stored value — which is precisely
    what an ORM's ``merge()`` would have done, and why the SQL is explicit.
    """
    job = make_job()
    repo.upsert_many([job])
    repo.set_status(job.id, JobStatus.APPLIED)
    repo.set_eligibility_override(job.id, Eligibility.INELIGIBLE)

    incoming = job.model_copy(
        update={"status": JobStatus.NEW, "eligibility_override": None}
    )
    repo.upsert_many([incoming])

    stored = repo.get(job.id)
    assert stored is not None
    assert stored.status is JobStatus.APPLIED
    assert stored.eligibility_override is Eligibility.INELIGIBLE


def test_derived_fields_survive_a_rescrape_so_a_run_stays_resumable(
    repo: SqliteJobRepository, make_job: MakeJob
) -> None:
    """Scores and verdicts are expensive to recompute — one is paid for in LLM
    quota. Clearing them on re-scrape would make every run start from zero."""
    job = make_job()
    repo.upsert_many([job])
    repo.save_eligibility(job.id, _verdict(Eligibility.INELIGIBLE))
    repo.save_verification(job.id, VerificationResult(tier=VerificationTier.OK))
    repo.save_prescore(job.id, 0.42)
    repo.save_match(
        job.id, MatchResult(score=77, reason="Strong Python overlap", provider="fake")
    )

    repo.upsert_many([make_job(url=str(job.url), description="Edited description.")])

    stored = repo.get(job.id)
    assert stored is not None
    assert stored.eligibility.eligibility is Eligibility.INELIGIBLE
    assert stored.verification is not None
    assert stored.prescore == pytest.approx(0.42)
    assert stored.match is not None and stored.match.score == 77


# --------------------------------------------------------------------------
# Seen timestamps
# --------------------------------------------------------------------------


def test_first_seen_at_is_preserved_and_last_seen_at_advances(
    repo: SqliteJobRepository, make_job: MakeJob, now: datetime
) -> None:
    """``first_seen_at`` is when *we* discovered the listing — the incoming
    object cannot know it, so storage must defend it. "New this week" depends
    on it being right."""
    job = make_job()
    repo.upsert_many([job])

    later = now + timedelta(days=10)
    repo.upsert_many(
        [job.model_copy(update={"first_seen_at": later, "last_seen_at": later})]
    )

    stored = repo.get(job.id)
    assert stored is not None
    assert stored.first_seen_at == now
    assert stored.last_seen_at == later


# --------------------------------------------------------------------------
# UpsertReport
# --------------------------------------------------------------------------


def test_a_first_sighting_is_reported_as_inserted(
    repo: SqliteJobRepository, make_job: MakeJob
) -> None:
    report = repo.upsert_many([make_job(), make_job()])

    assert (report.inserted, report.updated, report.unchanged) == (2, 0, 0)
    assert report.total == 2


def test_seeing_an_identical_listing_again_is_unchanged_not_updated(
    repo: SqliteJobRepository, make_job: MakeJob, now: datetime
) -> None:
    """``updated`` must mean *the listing actually changed*.

    If re-seeing counted as an update, every run would report every job as
    updated and the number would carry no information at all.
    """
    job = make_job()
    repo.upsert_many([job])

    report = repo.upsert_many(
        [job.model_copy(update={"last_seen_at": now + timedelta(days=1)})]
    )

    assert (report.inserted, report.updated, report.unchanged) == (0, 0, 1)


def test_a_changed_content_hash_is_reported_as_updated(
    repo: SqliteJobRepository, make_job: MakeJob
) -> None:
    job = make_job()
    repo.upsert_many([job])

    report = repo.upsert_many(
        [make_job(url=str(job.url), description="A genuinely different description.")]
    )

    assert (report.inserted, report.updated, report.unchanged) == (0, 1, 0)


def test_a_mixed_batch_is_counted_per_job(
    repo: SqliteJobRepository, make_job: MakeJob
) -> None:
    known = make_job()
    edited = make_job()
    repo.upsert_many([known, edited])

    report = repo.upsert_many(
        [
            known,
            make_job(url=str(edited.url), description="Now hiring two engineers."),
            make_job(),
        ]
    )

    assert (report.inserted, report.updated, report.unchanged) == (1, 1, 1)


def test_upserting_nothing_is_a_no_op(repo: SqliteJobRepository) -> None:
    report = repo.upsert_many([])

    assert report.total == 0
    assert repo.count() == 0


# --------------------------------------------------------------------------
# Round-trip fidelity
# --------------------------------------------------------------------------


def test_every_field_survives_a_save_and_load_unchanged(
    repo: SqliteJobRepository, make_job: MakeJob, now: datetime
) -> None:
    """Mapping is explicit in both directions so schema drift surfaces as an
    error rather than a silently absent attribute (ADR-0004). This is the test
    that makes that promise real — including the shapes SQLite has no native
    type for: Decimal money, tuples, and nested verdict JSON.
    """
    job = make_job(
        external_id="ext-42",
        title="Senior Backend Engineer",
        company="Acme Systems",
        description="We use Python.\n\nRemote friendly.",
        location_text="Remote — Worldwide",
        compensation_text="$120,000 - $150,500 per year",
        contract_text="Full-time",
        tags=("Python", "backend"),
        posted_at=now - timedelta(days=3),
    ).model_copy(
        update={
            "eligibility": _verdict(Eligibility.INELIGIBLE),
            "verification": VerificationResult(
                tier=VerificationTier.SUSPICIOUS,
                reasons=("Claims no experience is required",),
                checked_at=now,
                http_status=200,
            ),
            "prescore": 0.625,
            "match": MatchResult(
                score=83,
                reason="Strong Python and AWS overlap",
                gaps=("no Kubernetes", "no Go"),
                provider="fake-provider",
                scored_at=now,
            ),
            "status": JobStatus.SHORTLISTED,
            "notes": "Ask about the on-call rota",
            "eligibility_override": Eligibility.ELIGIBLE,
        }
    )

    repo.upsert_many([job])
    stored = repo.get(job.id)

    assert stored is not None
    assert stored == job


def test_decimal_compensation_survives_without_float_rounding(
    repo: SqliteJobRepository, make_job: MakeJob
) -> None:
    """Money is stored as text precisely so a float round-trip cannot shave a
    salary by a cent and make the min-rate filter disagree with itself."""
    job = make_job(compensation_text="$120,000.45 - $150,000.55 per year")
    repo.upsert_many([job])

    stored = repo.get(job.id)

    assert stored is not None
    assert stored.compensation.minimum == Decimal("120000.45")
    assert stored.compensation.maximum == Decimal("150000.55")
    assert stored.compensation.period is CompensationPeriod.YEARLY
    assert isinstance(stored.compensation.minimum, Decimal)


def test_dimension_evidence_survives_the_round_trip(
    repo: SqliteJobRepository, make_job: MakeJob
) -> None:
    """A verdict the user cannot audit is a bug (ADR-0002), so the evidence has
    to survive storage, not just live in memory during the run."""
    job = make_job()
    repo.upsert_many([job])
    repo.save_eligibility(job.id, _verdict(Eligibility.INELIGIBLE))

    stored = repo.get(job.id)

    assert stored is not None
    blocking = stored.eligibility.blocking
    assert [d.rule_id for d in blocking] == ["loc.us_only"]
    assert blocking[0].matched_text == "US only"
    assert stored.eligibility.dimensions[1].eligibility is Eligibility.UNKNOWN


def test_timestamps_come_back_timezone_aware(
    repo: SqliteJobRepository, make_job: MakeJob, now: datetime
) -> None:
    job = make_job(posted_at=now - timedelta(days=2))
    repo.upsert_many([job])

    stored = repo.get(job.id)

    assert stored is not None
    for value in (stored.first_seen_at, stored.last_seen_at, stored.posted_at):
        assert value is not None
        assert value.tzinfo is not None


def test_an_empty_job_round_trips_with_its_absences_intact(
    repo: SqliteJobRepository, make_job: MakeJob
) -> None:
    """The sparse listing is the common one; None must come back as None rather
    than as an empty string that later reads as a real value."""
    job = make_job(company=None, location_text=None, posted_at_text=None, tags=())
    repo.upsert_many([job])

    stored = repo.get(job.id)

    assert stored is not None
    assert stored.company is None
    assert stored.location_text is None
    assert stored.posted_at is None
    assert stored.tags == ()
    assert stored.compensation.is_empty
    assert stored.verification is None
    assert stored.match is None
    assert stored.notes is None


def test_getting_an_unknown_id_returns_none(repo: SqliteJobRepository) -> None:
    assert repo.get("nosuchjob00000") is None


# --------------------------------------------------------------------------
# Migrations
# --------------------------------------------------------------------------


def test_migrating_is_idempotent(db_path: Path) -> None:
    """Startup migrates every time; running it twice must be a no-op rather
    than an error or a duplicated table."""
    conn = connect(db_path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert migrate(conn) == SCHEMA_VERSION
        assert migrate(conn) == SCHEMA_VERSION
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    finally:
        conn.close()


def test_reopening_an_existing_database_preserves_its_rows(
    db_path: Path, make_job: MakeJob
) -> None:
    """Migrations are forward-only and run at startup; they must never be a
    data-loss event for a database that is already current."""
    first = SqliteJobRepository.open(db_path)
    job = make_job()
    first.upsert_many([job])
    first.set_notes(job.id, "kept across restarts")
    first.close()

    second = SqliteJobRepository.open(db_path)
    try:
        stored = second.get(job.id)
        assert stored is not None
        assert stored.notes == "kept across restarts"
    finally:
        second.close()


def test_a_database_from_a_newer_build_is_refused_rather_than_downgraded(
    db_path: Path,
) -> None:
    """Forward-only means we cannot reason about a schema we have never seen.

    Opening it read-write and hoping would corrupt the user's only copy of
    their job search, so this fails loudly and says what to do instead.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 5}")
        with pytest.raises(RuntimeError, match="newer than this build"):
            migrate(conn)
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Queries
# --------------------------------------------------------------------------


@pytest.fixture
def corpus(
    repo: SqliteJobRepository, make_job: MakeJob, now: datetime
) -> dict[str, str]:
    """Four jobs spanning the axes the list view filters on."""
    jobs = {
        "applied_eligible": make_job(source="remoteok", title="A Engineer"),
        "new_ineligible": make_job(source="remoteok", title="B Engineer"),
        "shortlisted_unknown": make_job(source="remotive", title="C Engineer"),
        "dismissed_unknown": make_job(source="hackernews", title="D Engineer"),
    }
    repo.upsert_many(list(jobs.values()))

    repo.set_status(jobs["applied_eligible"].id, JobStatus.APPLIED)
    repo.save_eligibility(jobs["applied_eligible"].id, _verdict(Eligibility.ELIGIBLE))
    repo.save_match(
        jobs["applied_eligible"].id,
        MatchResult(score=90, reason="great", provider="fake"),
    )

    repo.save_eligibility(jobs["new_ineligible"].id, _verdict(Eligibility.INELIGIBLE))
    repo.save_match(
        jobs["new_ineligible"].id,
        MatchResult(score=10, reason="poor", provider="fake"),
    )

    repo.set_status(jobs["shortlisted_unknown"].id, JobStatus.SHORTLISTED)
    repo.save_prescore(jobs["shortlisted_unknown"].id, 0.9)

    repo.set_status(jobs["dismissed_unknown"].id, JobStatus.DISMISSED)

    return {name: job.id for name, job in jobs.items()}


def test_counting_reports_every_stored_job(
    repo: SqliteJobRepository, corpus: dict[str, str]
) -> None:
    assert repo.count() == 4


def test_listing_can_be_filtered_by_status(
    repo: SqliteJobRepository, corpus: dict[str, str]
) -> None:
    listed = repo.list_jobs(statuses=[JobStatus.APPLIED, JobStatus.SHORTLISTED])

    assert {job.id for job in listed} == {
        corpus["applied_eligible"],
        corpus["shortlisted_unknown"],
    }


def test_listing_can_be_filtered_by_eligibility(
    repo: SqliteJobRepository, corpus: dict[str, str]
) -> None:
    listed = repo.list_jobs(eligibilities=[Eligibility.UNKNOWN])

    assert {job.id for job in listed} == {
        corpus["shortlisted_unknown"],
        corpus["dismissed_unknown"],
    }


def test_eligibility_filtering_honours_the_users_override(
    repo: SqliteJobRepository, corpus: dict[str, str]
) -> None:
    """The user's correction has to reach the query, not just the display —
    otherwise a job they rescued stays missing from their eligible list."""
    repo.set_eligibility_override(corpus["new_ineligible"], Eligibility.ELIGIBLE)

    listed = repo.list_jobs(eligibilities=[Eligibility.ELIGIBLE])

    assert {job.id for job in listed} == {
        corpus["applied_eligible"],
        corpus["new_ineligible"],
    }


def test_listing_can_be_filtered_by_source(
    repo: SqliteJobRepository, corpus: dict[str, str]
) -> None:
    listed = repo.list_jobs(sources=["remotive", "hackernews"])

    assert {job.id for job in listed} == {
        corpus["shortlisted_unknown"],
        corpus["dismissed_unknown"],
    }


def test_filters_combine_conjunctively(
    repo: SqliteJobRepository, corpus: dict[str, str]
) -> None:
    listed = repo.list_jobs(sources=["remoteok"], statuses=[JobStatus.APPLIED])

    assert [job.id for job in listed] == [corpus["applied_eligible"]]


def test_unscored_jobs_can_be_asked_for_which_is_what_makes_a_run_resumable(
    repo: SqliteJobRepository, corpus: dict[str, str]
) -> None:
    """Stage 8 only touches rows with no match, so the whole pipeline is
    resumable by construction rather than by bookkeeping (ADR-0005)."""
    listed = repo.list_jobs(unscored_only=True)

    assert {job.id for job in listed} == {
        corpus["shortlisted_unknown"],
        corpus["dismissed_unknown"],
    }


def test_unverified_jobs_can_be_asked_for(
    repo: SqliteJobRepository, corpus: dict[str, str]
) -> None:
    assert len(repo.list_jobs(unverified_only=True)) == 4

    repo.save_verification(
        corpus["applied_eligible"], VerificationResult(tier=VerificationTier.OK)
    )

    assert len(repo.list_jobs(unverified_only=True)) == 3


def test_ordering_by_match_score_puts_scored_jobs_first(
    repo: SqliteJobRepository, corpus: dict[str, str]
) -> None:
    """Unscored is not zero — an unscored job must not sink below a job the LLM
    actively judged poor."""
    listed = repo.list_jobs(order_by="match_score")

    assert [job.id for job in listed][:2] == [
        corpus["applied_eligible"],
        corpus["new_ineligible"],
    ]


def test_ordering_by_prescore_is_available_for_runs_with_no_llm(
    repo: SqliteJobRepository, corpus: dict[str, str]
) -> None:
    listed = repo.list_jobs(order_by="prescore")

    assert listed[0].id == corpus["shortlisted_unknown"]


def test_an_unrecognised_order_falls_back_rather_than_failing(
    repo: SqliteJobRepository, corpus: dict[str, str]
) -> None:
    """The order key is whitelisted and never interpolated, so an unexpected
    value is a default, not a SQL injection point."""
    assert [j.id for j in repo.list_jobs(order_by="'; DROP TABLE jobs; --")] == [
        j.id for j in repo.list_jobs(order_by="match_score")
    ]
    assert repo.count() == 4


def test_ordering_is_total_so_pagination_cannot_repeat_or_skip_a_row(
    repo: SqliteJobRepository, corpus: dict[str, str]
) -> None:
    """Rows with equal scores must still have a deterministic order, or paging
    through the list shows one job twice and hides another."""
    everything = [job.id for job in repo.list_jobs()]
    paged = [
        job.id for offset in (0, 2) for job in repo.list_jobs(limit=2, offset=offset)
    ]

    assert paged == everything
    assert len(set(paged)) == 4


def test_counts_by_eligibility_summarise_the_corpus(
    repo: SqliteJobRepository, corpus: dict[str, str]
) -> None:
    assert repo.counts_by_eligibility() == {
        "eligible": 1,
        "ineligible": 1,
        "unknown": 2,
    }


def test_known_content_hashes_reports_what_is_already_stored(
    repo: SqliteJobRepository, corpus: dict[str, str], make_job: MakeJob
) -> None:
    hashes = repo.known_content_hashes()

    assert len(hashes) == 4
    assert make_job().content_hash not in hashes


# --------------------------------------------------------------------------
# Batch writes
# --------------------------------------------------------------------------


def test_batched_eligibility_writes_land_for_every_job(
    repo: SqliteJobRepository, make_job: MakeJob
) -> None:
    jobs = [make_job() for _ in range(3)]
    repo.upsert_many(jobs)

    repo.save_eligibility_many(
        [(job.id, _verdict(Eligibility.INELIGIBLE)) for job in jobs]
    )

    assert repo.counts_by_eligibility() == {"ineligible": 3}


def test_batched_prescore_writes_land_for_every_job(
    repo: SqliteJobRepository, make_job: MakeJob
) -> None:
    jobs = [make_job() for _ in range(3)]
    repo.upsert_many(jobs)

    repo.save_prescore_many([(job.id, 0.5) for job in jobs])

    assert all(job.prescore == pytest.approx(0.5) for job in repo.list_jobs())


def test_clearing_an_override_returns_the_job_to_the_rules_verdict(
    repo: SqliteJobRepository, make_job: MakeJob
) -> None:
    job = make_job()
    repo.upsert_many([job])
    repo.save_eligibility(job.id, _verdict(Eligibility.INELIGIBLE))
    repo.set_eligibility_override(job.id, Eligibility.ELIGIBLE)

    repo.set_eligibility_override(job.id, None)

    stored = repo.get(job.id)
    assert stored is not None
    assert stored.eligibility_override is None
    assert stored.effective_eligibility is Eligibility.INELIGIBLE


def test_notes_can_be_cleared(repo: SqliteJobRepository, make_job: MakeJob) -> None:
    job = make_job()
    repo.upsert_many([job])
    repo.set_notes(job.id, "something")

    repo.set_notes(job.id, None)

    stored = repo.get(job.id)
    assert stored is not None
    assert stored.notes is None


def test_contract_type_round_trips_through_its_stored_value(
    repo: SqliteJobRepository, make_job: MakeJob
) -> None:
    job = make_job(contract_text="This is a contract role")
    repo.upsert_many([job])

    stored = repo.get(job.id)

    assert stored is not None
    assert stored.contract_type is ContractType.CONTRACT
