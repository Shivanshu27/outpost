"""The one place a ``RawJob`` becomes a ``Job``.

Every source funnels through here (ADR-0008). That is the whole point: parsing
logic that lives per-source drifts per-source, and then salaries are parsed one
way by the RemoteOK adapter and another by the Lever adapter, and the bug has
eight homes.

Pure, and stdlib-only apart from pydantic — the domain core carries no parsing
dependencies of its own.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from outpost.domain.models import (
    Compensation,
    CompensationPeriod,
    ContractType,
    Job,
    RawJob,
)

__all__ = [
    "canonical_url",
    "content_hash",
    "html_to_text",
    "job_id",
    "normalise",
    "parse_compensation",
    "parse_posted_at",
]


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------

# Tracking parameters are stripped before hashing so that the same listing
# reached via three different campaign links is one job, not three.
_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "ref",
        "referrer",
        "source",
        "src",
        "gh_src",
        "lever-source",
        "gclid",
        "fbclid",
        "mc_cid",
        "mc_eid",
    }
)


def canonical_url(url: str) -> str:
    """Normalise a URL so the same listing always hashes identically.

    Lowercases scheme and host, drops the fragment, removes tracking
    parameters, sorts what remains, and strips a trailing slash.
    """
    parts = urlsplit(url.strip())
    query = urlencode(
        sorted(
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=False)
            if k.lower() not in _TRACKING_PARAMS
        )
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def job_id(url: str) -> str:
    """Deterministic id for a listing.

    Derived from the canonical URL alone, so re-running the pipeline is
    idempotent and the same listing from two sources collapses to one row
    (FR-2.2).
    """
    return hashlib.sha256(canonical_url(url).encode()).hexdigest()[:16]


def content_hash(title: str, company: str | None, description: str) -> str:
    """Hash of the meaningful content, for detecting genuine changes.

    Whitespace is collapsed and case folded so that cosmetic re-rendering by a
    board does not read as an edit — otherwise every scrape would report every
    job as updated, and the run report would be useless.
    """
    blob = " ".join(
        _collapse_whitespace(part).lower()
        for part in (title, company or "", description)
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# --------------------------------------------------------------------------
# Text
# --------------------------------------------------------------------------


class _TextExtractor(HTMLParser):
    """Minimal HTML-to-text.

    Stdlib rather than a parsing library: descriptions are small, the output
    feeds phrase matching rather than rendering, and keeping the domain free of
    C-extension dependencies is worth more than perfect fidelity.
    """

    _BLOCK_TAGS = frozenset(
        {
            "p",
            "div",
            "br",
            "li",
            "tr",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "section",
            "article",
            "ul",
            "ol",
            "table",
            "blockquote",
        }
    )
    _SKIP_CONTENT = frozenset({"script", "style", "head"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in self._SKIP_CONTENT:
            self._skip_depth += 1
        elif tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_CONTENT and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(data)

    @property
    def text(self) -> str:
        joined = "".join(self._parts)
        # Collapse runs of blank lines, but keep paragraph breaks: they are
        # what makes field-scoped rules and human reading work.
        joined = re.sub(r"[ \t\r\f\v]+", " ", joined)
        joined = re.sub(r"\n\s*\n\s*", "\n\n", joined)
        return joined.strip()


def html_to_text(value: str) -> str:
    """Convert an HTML fragment to readable plain text."""
    if "<" not in value:
        return _collapse_blank_lines(value.strip())
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    return parser.text


def _collapse_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _collapse_blank_lines(value: str) -> str:
    return re.sub(r"\n\s*\n\s*", "\n\n", value)


# --------------------------------------------------------------------------
# Compensation
# --------------------------------------------------------------------------

_CURRENCY_SYMBOLS = {
    "$": "USD",
    "US$": "USD",
    "usd": "USD",
    "£": "GBP",
    "gbp": "GBP",
    "€": "EUR",
    "eur": "EUR",
    "₹": "INR",
    "inr": "INR",
    "c$": "CAD",
    "cad": "CAD",
    "a$": "AUD",
    "aud": "AUD",
    "sgd": "SGD",
    "s$": "SGD",
}

_PERIOD_HINTS: tuple[tuple[re.Pattern[str], CompensationPeriod], ...] = (
    (
        re.compile(r"\b(?:per\s+hour|/\s*hour|/\s*hr|hourly|an\s+hour)\b", re.I),
        CompensationPeriod.HOURLY,
    ),
    (
        re.compile(r"\b(?:per\s+day|/\s*day|daily|day\s+rate)\b", re.I),
        CompensationPeriod.DAILY,
    ),
    (
        re.compile(r"\b(?:per\s+month|/\s*month|monthly|/\s*mo)\b", re.I),
        CompensationPeriod.MONTHLY,
    ),
    (
        re.compile(
            r"\b(?:per\s+year|/\s*year|annually|annual|per\s+annum|/\s*yr)\b", re.I
        ),
        CompensationPeriod.YEARLY,
    ),
)

_AMOUNT = re.compile(
    r"(?P<symbol>US\$|C\$|A\$|S\$|[$£€₹])?\s*"
    r"(?P<number>"
    # Indian grouping first: 25,00,000 is 25 lakh. Western grouping cannot
    # match it (the middle groups are two digits), and without this branch the
    # regex falls through to the bare-number alternative and reads it as "25" —
    # a hundred-thousand-fold error, in the currency of a large part of this
    # tool's audience.
    r"\d{1,2}(?:,\d{2})+,\d{3}"
    r"|\d{1,3}(?:[,\s]\d{3})+(?:\.\d+)?"
    r"|\d+(?:\.\d+)?"
    r")"
    r"\s*(?P<suffix>[kKmM])?",
)
_CODE = re.compile(r"\b(usd|gbp|eur|inr|cad|aud|sgd)\b", re.I)

_YEAR_RANGE = (Decimal(1900), Decimal(2100))
"""Unmarked numbers in this range are dates, not pay."""


def _to_decimal(number: str, suffix: str | None) -> Decimal | None:
    try:
        value = Decimal(number.replace(",", "").replace(" ", ""))
    except InvalidOperation:
        return None
    if suffix and suffix.lower() == "k":
        value *= 1000
    elif suffix and suffix.lower() == "m":
        value *= 1_000_000
    return value


def parse_compensation(text: str | None) -> Compensation:
    """Parse a compensation string, or return empty.

    Never guesses (FR-2.3). A range yields min and max; a single figure yields
    min only; anything ambiguous yields nothing at all. A wrong number here
    feeds the ranking and the user's decision to apply, so silence is strictly
    better than a plausible invention.
    """
    if not text:
        return Compensation()

    matches = [m for m in _AMOUNT.finditer(text) if m.group("number")]
    # Bare years ("2024") and tiny integers ("3+ years") are not salaries.
    amounts: list[Decimal] = []
    for match in matches:
        value = _to_decimal(match.group("number"), match.group("suffix"))
        if value is None or value <= 0:
            continue
        has_marker = bool(match.group("symbol") or match.group("suffix"))
        if not has_marker and value < 1000:
            continue
        # An unmarked four-digit number in the year range is a date far more
        # often than pay — "Posted 2024", "© 2025", "since 1998". Requiring a
        # currency symbol or a k/m suffix to accept one keeps us on the right
        # side of never-guess (FR-2.3); a genuine salary of exactly 2024 with
        # no currency attached is not worth the false positives.
        if not has_marker and _YEAR_RANGE[0] <= value <= _YEAR_RANGE[1]:
            continue
        amounts.append(value)

    if not amounts:
        return Compensation()

    currency: str | None = None
    for match in matches:
        if symbol := match.group("symbol"):
            currency = _CURRENCY_SYMBOLS.get(symbol.lower())
            break
    if currency is None and (code := _CODE.search(text)):
        currency = code.group(1).upper()

    period: CompensationPeriod | None = None
    for pattern, candidate in _PERIOD_HINTS:
        if pattern.search(text):
            period = candidate
            break
    if period is None and amounts:
        # An unlabelled figure under ~2,000 in a pay context is an hourly rate
        # far more often than an annual salary.
        period = (
            CompensationPeriod.HOURLY
            if max(amounts) < 2000
            else CompensationPeriod.YEARLY
        )

    minimum = min(amounts)
    maximum = max(amounts) if len(amounts) > 1 else None
    return Compensation(
        minimum=minimum, maximum=maximum, currency=currency, period=period
    )


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------

_RELATIVE = re.compile(
    r"(?P<count>\d+)\s*(?P<unit>second|minute|hour|day|week|month|year)s?\s*ago",
    re.IGNORECASE,
)
_MAX_PLAUSIBLE_EPOCH_SECONDS = 4_102_444_800
"""2100-01-01. Anything larger is milliseconds, not a job posted in the 26th
century."""

_UNIT_DELTAS = {
    "second": timedelta(seconds=1),
    "minute": timedelta(minutes=1),
    "hour": timedelta(hours=1),
    "day": timedelta(days=1),
    "week": timedelta(weeks=1),
    "month": timedelta(days=30),
    "year": timedelta(days=365),
}


def parse_posted_at(value: str | None, *, now: datetime) -> datetime | None:
    """Parse a posted-at value into an aware UTC datetime.

    Handles ISO 8601, epoch seconds, and relative English ("3 days ago").
    ``now`` is injected rather than read from the clock so the function stays
    pure and testable (ADR-0009).
    """
    if not value:
        return None
    text = value.strip()

    if match := _RELATIVE.search(text):
        delta = _UNIT_DELTAS[match.group("unit").lower()] * int(match.group("count"))
        return now - delta

    if text.isdigit() and len(text) >= 9:
        # Epoch, in seconds or milliseconds depending on the source — Lever
        # returns ms, most others return seconds. Distinguished by magnitude
        # rather than by string length, because both forms vary in width.
        # Getting this wrong is not subtle: reading ms as seconds dates every
        # listing to roughly the year 56,000.
        epoch = int(text)
        if epoch > _MAX_PLAUSIBLE_EPOCH_SECONDS:
            epoch //= 1000
        try:
            return datetime.fromtimestamp(epoch, tz=UTC)
        except (ValueError, OSError, OverflowError):
            return None

    candidate = text.replace("Z", "+00:00")
    parsed: datetime | None
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        parsed = _parse_rfc2822(text)
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _parse_rfc2822(text: str) -> datetime | None:
    """Parse an RSS-style date: ``Fri, 11 Sep 2026 07:30:48 +0000``.

    Every RSS feed uses this format, so without it an entire class of source
    silently loses its dates — which then reads as "undated" downstream and
    quietly changes how those listings rank. Found exactly that way: a live run
    produced 25 WeWorkRemotely jobs with no ``posted_at`` at all.
    """
    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Contract type
# --------------------------------------------------------------------------

_CONTRACT_HINTS: tuple[tuple[re.Pattern[str], ContractType], ...] = (
    (re.compile(r"\b(?:intern|internship)\b", re.I), ContractType.INTERNSHIP),
    (
        re.compile(r"\b(?:contract|contractor|freelance|b2b|consulting)\b", re.I),
        ContractType.CONTRACT,
    ),
    (re.compile(r"\bpart[\s-]?time\b", re.I), ContractType.PART_TIME),
    (re.compile(r"\bfull[\s-]?time\b", re.I), ContractType.FULL_TIME),
)


def parse_contract_type(*values: str | None) -> ContractType:
    """Infer contract type from any available text, most specific first."""
    haystack = " ".join(v for v in values if v)
    if not haystack:
        return ContractType.UNKNOWN
    for pattern, contract in _CONTRACT_HINTS:
        if pattern.search(haystack):
            return contract
    return ContractType.UNKNOWN


# --------------------------------------------------------------------------
# The funnel
# --------------------------------------------------------------------------


def normalise(
    raw: RawJob, *, now: datetime, first_seen_at: datetime | None = None
) -> Job | None:
    """Turn a ``RawJob`` into a ``Job``, or ``None`` if it is unusable.

    Returns ``None`` only for listings that cannot be represented at all — no
    title, or an unparseable URL. Everything else is kept: a listing missing a
    company or a salary is still a listing the user may want to see, and
    discarding it here would be exactly the silent loss ADR-0002 forbids.
    """
    title = _collapse_whitespace(raw.title or "")
    if not title:
        return None

    try:
        url = canonical_url(raw.url)
    except ValueError:
        return None
    if not url.startswith(("http://", "https://")):
        return None

    description = html_to_text(raw.description or "")
    company = _collapse_whitespace(raw.company or "") or None
    location_text = _collapse_whitespace(raw.location_text or "") or None

    posted_at = raw.posted_at or parse_posted_at(raw.posted_at_text, now=now)
    if posted_at is not None and posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=UTC)

    return Job(
        id=job_id(url),
        source=raw.source,
        url=url,
        external_id=raw.external_id,
        title=title,
        company=company,
        description=description,
        location_text=location_text,
        compensation=parse_compensation(raw.compensation_text),
        contract_type=parse_contract_type(raw.contract_text, title, " ".join(raw.tags)),
        tags=tuple(sorted({t.strip().lower() for t in raw.tags if t.strip()})),
        posted_at=posted_at,
        first_seen_at=first_seen_at or now,
        last_seen_at=now,
        content_hash=content_hash(title, company, description),
    )
