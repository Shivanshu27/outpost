"""Normalisation — the single place looseness becomes strictness.

Every source funnels through ``normalise()`` (ADR-0008), so a bug here has one
home instead of eight. Two themes run through these tests:

* **Identity must be stable.** The same listing reached by three campaign links
  is one job, and the same job seen twice must not look edited.
* **Nothing is ever guessed.** "Competitive salary" yields no number, because a
  fabricated figure feeds the ranking and the user's decision to apply.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from tests.conftest import FROZEN_NOW

from outpost.domain.models import CompensationPeriod, ContractType, RawJob
from outpost.domain.normalisation import (
    canonical_url,
    content_hash,
    html_to_text,
    job_id,
    normalise,
    parse_compensation,
    parse_posted_at,
)

NOW = FROZEN_NOW


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "variant",
    [
        "https://jobs.example.com/roles/123",
        "https://jobs.example.com/roles/123/",
        "https://JOBS.EXAMPLE.com/roles/123",
        "HTTPS://jobs.example.com/roles/123",
        "https://jobs.example.com/roles/123#apply-now",
        "https://jobs.example.com/roles/123?utm_source=newsletter",
        "https://jobs.example.com/roles/123?utm_campaign=q1&gclid=abc&ref=hn",
        "  https://jobs.example.com/roles/123  ",
    ],
)
def test_cosmetic_url_differences_collapse_to_one_identity(variant: str) -> None:
    """Campaign links, trailing slashes and casing are not different jobs.

    Without this, the same listing shared three ways becomes three rows, and
    the user reads it three times.
    """
    canonical = "https://jobs.example.com/roles/123"

    assert canonical_url(variant) == canonical
    assert job_id(variant) == job_id(canonical)


def test_meaningful_query_parameters_survive_canonicalisation() -> None:
    """Some boards route by query string, so stripping everything would merge
    genuinely different listings into one."""
    assert (
        canonical_url("https://boards.example.com/jobs?gh_jid=42&utm_source=x")
        == "https://boards.example.com/jobs?gh_jid=42"
    )


def test_query_parameters_are_sorted_so_ordering_does_not_change_identity() -> None:
    assert job_id("https://x.example.com/j?b=2&a=1") == job_id(
        "https://x.example.com/j?a=1&b=2"
    )


def test_job_id_is_deterministic_and_stable_across_calls() -> None:
    """Re-running the pipeline must be idempotent, which requires the id to be
    a pure function of the canonical URL."""
    url = "https://jobs.example.com/roles/123"

    assert job_id(url) == job_id(url)
    assert job_id(url) == "fd884619d708448f"


def test_different_listings_get_different_ids() -> None:
    assert job_id("https://x.example.com/1") != job_id("https://x.example.com/2")


# --------------------------------------------------------------------------
# Content hashing
# --------------------------------------------------------------------------


def test_content_hash_ignores_whitespace_and_case_changes() -> None:
    """Boards re-render constantly. If cosmetic churn read as an edit, every
    run would report every job as updated and the number would mean nothing."""
    original = content_hash("Backend Engineer", "Acme", "We use Python  and Go.")

    assert original == content_hash(
        "backend   engineer", "ACME", "We use\nPython and Go."
    )


def test_content_hash_detects_a_real_edit() -> None:
    original = content_hash("Backend Engineer", "Acme", "We use Python and Go.")

    assert original != content_hash(
        "Backend Engineer", "Acme", "We use Python and Rust."
    )
    assert original != content_hash("Staff Engineer", "Acme", "We use Python and Go.")
    assert original != content_hash("Backend Engineer", "Globex", "We use Python.")


def test_a_company_appearing_later_changes_the_content_hash() -> None:
    """Gaining a field the source previously omitted is a genuine change the
    run report should show."""
    assert content_hash("Engineer", None, "desc") != content_hash(
        "Engineer", "Acme", "desc"
    )


# --------------------------------------------------------------------------
# Compensation — never guess (FR-2.3)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Competitive salary",
        "Competitive salary and equity",
        "3+ years experience",
        "5+ years of experience required",
        "Salary: DOE",
        "",
    ],
)
def test_text_that_states_no_pay_yields_no_compensation(text: str) -> None:
    """The critical negative case.

    A plausible invented midpoint would flow straight into the ranking and into
    the user's decision to apply. Silence is strictly better than a number
    nobody wrote.
    """
    comp = parse_compensation(text)

    assert comp.is_empty
    assert comp.minimum is None and comp.maximum is None


def test_a_missing_compensation_string_yields_no_compensation() -> None:
    assert parse_compensation(None).is_empty


def test_a_range_yields_both_ends() -> None:
    comp = parse_compensation("$120,000 - $150,000 per year")

    assert comp.minimum == Decimal(120000)
    assert comp.maximum == Decimal(150000)
    assert comp.currency == "USD"
    assert comp.period is CompensationPeriod.YEARLY


def test_a_single_figure_yields_a_minimum_only() -> None:
    """A lone number is a floor, not a range; inventing a maximum would be a
    guess."""
    comp = parse_compensation("$85 per hour")

    assert comp.minimum == Decimal(85)
    assert comp.maximum is None
    assert comp.period is CompensationPeriod.HOURLY


@pytest.mark.parametrize(
    ("text", "minimum", "maximum"),
    [
        ("120k-150k USD", Decimal(120000), Decimal(150000)),
        ("£60k", Decimal(60000), None),
        ("1.2m INR annually", Decimal("1200000.0"), None),
    ],
)
def test_k_and_m_suffixes_are_expanded(
    text: str, minimum: Decimal, maximum: Decimal | None
) -> None:
    comp = parse_compensation(text)

    assert comp.minimum == minimum
    assert comp.maximum == maximum


@pytest.mark.parametrize(
    ("text", "currency"),
    [
        ("$120,000 per year", "USD"),
        ("€90,000 per year", "EUR"),
        ("£75,000 per year", "GBP"),
        ("C$130,000 per year", "CAD"),
        ("A$140,000 per year", "AUD"),
        ("150,000 USD per year", "USD"),
        ("90,000 EUR per year", "EUR"),
    ],
)
def test_currencies_are_recognised_from_symbols_and_codes(
    text: str, currency: str
) -> None:
    assert parse_compensation(text).currency == currency


def test_an_amount_with_no_currency_marker_still_parses_without_inventing_one() -> None:
    comp = parse_compensation("120,000 - 150,000 per year")

    assert comp.minimum == Decimal(120000)
    assert comp.currency is None


@pytest.mark.parametrize(
    ("text", "period"),
    [
        ("$85 per hour", CompensationPeriod.HOURLY),
        ("$85/hr", CompensationPeriod.HOURLY),
        ("$700 day rate", CompensationPeriod.DAILY),
        ("$9,000 monthly", CompensationPeriod.MONTHLY),
        ("$150,000 per annum", CompensationPeriod.YEARLY),
    ],
)
def test_a_stated_period_is_used_verbatim(
    text: str, period: CompensationPeriod
) -> None:
    assert parse_compensation(text).period is period


def test_an_unlabelled_small_figure_is_inferred_hourly_and_a_large_one_yearly() -> None:
    """The inference is a documented heuristic, not a guess about the number
    itself: the figure is whatever was written, only its period is inferred."""
    assert parse_compensation("$95").period is CompensationPeriod.HOURLY
    assert parse_compensation("$150,000").period is CompensationPeriod.YEARLY


def test_a_bare_small_integer_is_not_treated_as_pay() -> None:
    """Without a currency symbol or a k/m suffix, a small number in prose is a
    years-of-experience count far more often than a salary."""
    assert parse_compensation("Minimum 3 years, up to 8 years").is_empty


def test_a_bare_year_is_not_treated_as_pay() -> None:
    assert parse_compensation("Posted 2024").is_empty


def test_indian_digit_grouping_does_not_produce_a_wrong_number() -> None:
    comp = parse_compensation("₹25,00,000 per annum")

    assert comp.is_empty or comp.minimum == Decimal(2500000)


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-02-01T10:00:00Z", datetime(2026, 2, 1, 10, 0, tzinfo=UTC)),
        ("2026-02-01T10:00:00+00:00", datetime(2026, 2, 1, 10, 0, tzinfo=UTC)),
        ("2026-02-01", datetime(2026, 2, 1, 0, 0, tzinfo=UTC)),
    ],
)
def test_iso_timestamps_parse_to_aware_utc(text: str, expected: datetime) -> None:
    assert parse_posted_at(text, now=NOW) == expected


def test_a_naive_iso_timestamp_comes_back_timezone_aware() -> None:
    """A naive datetime downstream would make every age comparison raise, so
    the boundary is where it must be fixed."""
    parsed = parse_posted_at("2026-02-01T10:00:00", now=NOW)

    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed == datetime(2026, 2, 1, 10, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "delta"),
    [
        ("3 days ago", timedelta(days=3)),
        ("1 day ago", timedelta(days=1)),
        ("2 weeks ago", timedelta(weeks=2)),
        ("5 hours ago", timedelta(hours=5)),
        ("30 minutes ago", timedelta(minutes=30)),
    ],
)
def test_relative_english_is_resolved_against_the_injected_now(
    text: str, delta: timedelta
) -> None:
    """``now`` is a parameter, not a call to the clock — which is what makes
    this assertable at all (ADR-0009)."""
    assert parse_posted_at(text, now=NOW) == NOW - delta


def test_epoch_seconds_parse_to_the_right_instant() -> None:
    assert parse_posted_at("1740830400", now=NOW) == datetime(
        2025, 3, 1, 12, 0, tzinfo=UTC
    )


def test_thirteen_digit_epoch_milliseconds_are_not_read_as_seconds() -> None:
    """Lever returns milliseconds. Reading them as seconds dates every listing
    to roughly the year 56,000, which silently defeats every recency and
    max-age rule downstream — so magnitude decides, not caller convention.
    """
    assert parse_posted_at("1740830400000", now=NOW) == parse_posted_at(
        "1740830400", now=NOW
    )


@pytest.mark.parametrize("text", ["not a date", "yesterday", "Q3", "12345678"])
def test_unparseable_dates_yield_none_rather_than_a_guess(text: str) -> None:
    assert parse_posted_at(text, now=NOW) is None


def test_an_absent_date_yields_none() -> None:
    assert parse_posted_at(None, now=NOW) is None
    assert parse_posted_at("", now=NOW) is None


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------


def test_script_and_style_content_is_stripped() -> None:
    """Analytics blobs and CSS are not listing text, and leaving them in would
    feed junk to phrase matching and to the LLM prompt alike."""
    html = (
        "<div><script>track('us only');</script>"
        "<style>.x { color: red; }</style>"
        "<p>We are hiring.</p></div>"
    )

    text = html_to_text(html)

    assert text == "We are hiring."
    assert "track" not in text
    assert "color" not in text


def test_paragraph_breaks_survive_but_runs_of_blank_lines_collapse() -> None:
    """Paragraph structure is what makes the description readable to a human
    and scopeable by a rule; unbounded blank runs are just noise."""
    text = html_to_text("<p>First paragraph.</p><p>Second paragraph.</p>")

    assert text == "First paragraph.\n\nSecond paragraph."


def test_list_items_become_separate_lines() -> None:
    text = html_to_text("<ul><li>Python</li><li>PostgreSQL</li></ul>")

    assert "Python" in text
    assert "PostgreSQL" in text
    assert text.index("Python") < text.index("PostgreSQL")


def test_html_entities_are_decoded() -> None:
    assert html_to_text("<p>R&amp;D team</p>") == "R&D team"


def test_plain_text_passes_through_unharmed() -> None:
    assert html_to_text("Just a sentence.") == "Just a sentence."


# --------------------------------------------------------------------------
# normalise() — what is kept, and what is not
# --------------------------------------------------------------------------


def test_a_full_listing_normalises_into_every_canonical_field() -> None:
    raw = RawJob(
        source="remoteok",
        url="https://remoteok.com/remote-jobs/123?utm_source=hn",
        external_id="123",
        title="  Senior   Backend Engineer  ",
        company=" Acme Systems ",
        description="<p>We use Python.</p><p>Full-time.</p>",
        location_text=" Remote — Worldwide ",
        compensation_text="$120,000 - $150,000 per year",
        contract_text="Full-time",
        posted_at_text="3 days ago",
        tags=("Python", " backend ", "python", ""),
    )

    job = normalise(raw, now=NOW)

    assert job is not None
    assert job.title == "Senior Backend Engineer"
    assert job.company == "Acme Systems"
    assert job.description == "We use Python.\n\nFull-time."
    assert job.location_text == "Remote — Worldwide"
    assert job.compensation.minimum == Decimal(120000)
    assert job.contract_type is ContractType.FULL_TIME
    # Tags are deduplicated, lowercased and sorted, so two sources spelling
    # them differently do not produce two different content hashes.
    assert job.tags == ("backend", "python")
    assert job.posted_at == NOW - timedelta(days=3)
    assert str(job.url) == "https://remoteok.com/remote-jobs/123"


@pytest.mark.parametrize(
    "raw",
    [
        RawJob(source="s", url="https://x.example.com/1", title=None),
        RawJob(source="s", url="https://x.example.com/1", title="   "),
        RawJob(source="s", url="not-a-url", title="Engineer"),
        RawJob(source="s", url="ftp://x.example.com/1", title="Engineer"),
    ],
    ids=["no title", "blank title", "unparseable url", "non-http scheme"],
)
def test_only_unusable_listings_are_discarded(raw: RawJob) -> None:
    """A listing with no title or no usable link cannot be shown or opened, so
    it is the one thing normalisation drops."""
    assert normalise(raw, now=NOW) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("company", None),
        ("compensation_text", None),
        ("location_text", None),
        ("description", None),
        ("posted_at_text", None),
        ("external_id", None),
    ],
)
def test_a_listing_missing_an_optional_field_is_still_kept(
    field: str, value: None
) -> None:
    """Discarding a listing for a field its board omitted would be exactly the
    silent loss ADR-0002 forbids — the user can still read it and decide."""
    fields: dict[str, object] = {
        "source": "s",
        "url": "https://x.example.com/1",
        "title": "Backend Engineer",
        "company": "Acme",
        "description": "Python role.",
        field: value,
    }

    raw = RawJob(**fields)

    job = normalise(raw, now=NOW)

    assert job is not None
    assert job.title == "Backend Engineer"


def test_normalisation_stamps_both_seen_timestamps_from_the_injected_now() -> None:
    job = normalise(
        RawJob(source="s", url="https://x.example.com/1", title="Engineer"), now=NOW
    )

    assert job is not None
    assert job.first_seen_at == NOW
    assert job.last_seen_at == NOW


def test_a_supplied_first_seen_at_is_preserved() -> None:
    """Re-normalising a known listing must not claim we discovered it today."""
    earlier = NOW - timedelta(days=30)

    job = normalise(
        RawJob(source="s", url="https://x.example.com/1", title="Engineer"),
        now=NOW,
        first_seen_at=earlier,
    )

    assert job is not None
    assert job.first_seen_at == earlier
    assert job.last_seen_at == NOW


def test_a_naive_posted_at_from_a_source_is_made_aware() -> None:
    raw = RawJob(
        source="s",
        url="https://x.example.com/1",
        title="Engineer",
        posted_at=datetime(2026, 2, 1, 9, 0),  # noqa: DTZ001 — sources do this
    )

    job = normalise(raw, now=NOW)

    assert job is not None
    assert job.posted_at == datetime(2026, 2, 1, 9, 0, tzinfo=UTC)


def test_the_same_listing_from_two_sources_normalises_to_the_same_id() -> None:
    """Deduplication downstream depends entirely on this."""
    first = normalise(
        RawJob(
            source="remoteok",
            url="https://jobs.example.com/x?utm_source=remoteok",
            title="Engineer",
        ),
        now=NOW,
    )
    second = normalise(
        RawJob(
            source="remotive",
            url="https://jobs.example.com/x/",
            title="Engineer",
        ),
        now=NOW,
    )

    assert first is not None and second is not None
    assert first.id == second.id
