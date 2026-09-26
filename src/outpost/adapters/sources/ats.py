"""Applicant tracking systems — Greenhouse, Lever, Ashby.

These three differ only in URL shape and JSON key names, so they share a base
and per-company support is a config entry rather than code (ADR-0008).

Each source fans out over a list of company slugs. **One company's failure must
not lose the others**: a slug that has been renamed, a board that has been taken
private, or a single malformed payload is isolated, logged and skipped. The
run-level failure case is narrower and deliberate — if *every* configured
company failed, the source raises, because returning ``[]`` there is
indistinguishable from "these companies are not hiring" and that is precisely
the silent rot ADR-0008 is written against.
"""

from __future__ import annotations

import html
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from outpost.adapters.http import HttpClient
from outpost.adapters.sources.base import BaseSource, as_text
from outpost.domain.models import RawJob
from outpost.domain.ports import RateLimit, SourceError

__all__ = ["AshbySource", "GreenhouseSource", "LeverSource"]


class _ATSBaseSource(BaseSource, ABC):
    """Fan-out over configured company boards, isolating each one."""

    rate_limit = RateLimit(requests_per_minute=30, concurrency=2)

    def __init__(self, client: HttpClient, companies: Iterable[str]) -> None:
        super().__init__(client)
        self._companies = tuple(
            slug for company in companies if (slug := company.strip())
        )

    async def fetch(self, since: datetime | None = None) -> Sequence[RawJob]:
        jobs: list[RawJob] = []
        failures: list[str] = []

        for slug in self._companies:
            try:
                payload = await self._get_json(self._board_url(slug))
                jobs.extend(self._parse(payload, slug))
            except (SourceError, ValueError, TypeError, KeyError) as exc:
                # Per-company isolation. Logged at warning rather than swallowed
                # so a slug that has quietly stopped working is visible in the
                # run's output instead of just shrinking the job count.
                failures.append(f"{slug} ({exc})")
                self._log.warning("source.company_failed", company=slug, error=str(exc))

        if failures and not jobs:
            msg = f"all {len(failures)} configured boards failed: {'; '.join(failures)}"
            raise SourceError(self.name, msg)
        return jobs

    def _parse(self, payload: Any, slug: str) -> list[RawJob]:
        jobs: list[RawJob] = []
        for entry in self._records(payload):
            if not isinstance(entry, dict):
                continue
            if job := self._to_raw(entry, slug):
                jobs.append(job)
        return jobs

    @abstractmethod
    def _board_url(self, slug: str) -> str: ...

    @abstractmethod
    def _records(self, payload: Any) -> list[Any]:
        """Pull the list of postings out of the provider's envelope.

        Raises ``TypeError``/``ValueError`` on an unexpected shape; ``fetch``
        turns that into a per-company failure rather than a run failure.
        """

    @abstractmethod
    def _to_raw(self, entry: dict[str, Any], slug: str) -> RawJob | None: ...


def _envelope_list(payload: Any, key: str) -> list[Any]:
    if not isinstance(payload, dict):
        msg = f"expected a JSON object, got {type(payload).__name__}"
        raise TypeError(msg)
    records = payload.get(key)
    if not isinstance(records, list):
        msg = f"'{key}' is {type(records).__name__}, expected an array"
        raise TypeError(msg)
    return records


class GreenhouseSource(_ATSBaseSource):
    """Greenhouse job boards, one public endpoint per company."""

    name = "greenhouse"

    def _board_url(self, slug: str) -> str:
        return f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"

    def _records(self, payload: Any) -> list[Any]:
        return _envelope_list(payload, "jobs")

    def _to_raw(self, entry: dict[str, Any], slug: str) -> RawJob | None:
        url = as_text(entry.get("absolute_url"))
        if not url:
            return None

        content = as_text(entry.get("content"))
        location = entry.get("location")
        return RawJob(
            source=self.name,
            url=url,
            external_id=as_text(entry.get("id")),
            title=as_text(entry.get("title")),
            # The board API has no company name, only the slug we asked for.
            company=slug,
            # Greenhouse returns HTML with its entities escaped a second time.
            # Undoing that here is transport decoding, not normalisation: it
            # hands the shared HTML parser the same input every other source
            # gives it, instead of a document full of visible ``&lt;p&gt;``.
            description=html.unescape(content) if content else None,
            location_text=(
                as_text(location.get("name")) if isinstance(location, dict) else None
            ),
            posted_at_text=as_text(entry.get("updated_at")),
            raw=entry,
        )


class LeverSource(_ATSBaseSource):
    """Lever postings, one JSON endpoint per company."""

    name = "lever"

    def _board_url(self, slug: str) -> str:
        return f"https://api.lever.co/v0/postings/{slug}?mode=json"

    def _records(self, payload: Any) -> list[Any]:
        # Lever returns a bare array rather than an envelope.
        if not isinstance(payload, list):
            msg = f"expected a JSON array, got {type(payload).__name__}"
            raise TypeError(msg)
        return payload

    def _to_raw(self, entry: dict[str, Any], slug: str) -> RawJob | None:
        url = as_text(entry.get("hostedUrl")) or as_text(entry.get("applyUrl"))
        if not url:
            return None

        categories = entry.get("categories")
        categories = categories if isinstance(categories, dict) else {}
        return RawJob(
            source=self.name,
            url=url,
            external_id=as_text(entry.get("id")),
            title=as_text(entry.get("text")),
            company=slug,
            description=as_text(entry.get("description")),
            location_text=as_text(categories.get("location")),
            compensation_text=as_text(entry.get("salaryRange")),
            contract_text=as_text(categories.get("commitment")),
            posted_at=_from_epoch_millis(entry.get("createdAt")),
            raw=entry,
        )


class AshbySource(_ATSBaseSource):
    """Ashby job boards, one posting API endpoint per company."""

    name = "ashby"

    def _board_url(self, slug: str) -> str:
        return (
            "https://api.ashbyhq.com/posting-api/job-board/"
            f"{slug}?includeCompensation=true"
        )

    def _records(self, payload: Any) -> list[Any]:
        return _envelope_list(payload, "jobs")

    def _to_raw(self, entry: dict[str, Any], slug: str) -> RawJob | None:
        url = as_text(entry.get("jobUrl")) or as_text(entry.get("applyUrl"))
        if not url:
            return None

        compensation = entry.get("compensation")
        compensation = compensation if isinstance(compensation, dict) else {}
        return RawJob(
            source=self.name,
            url=url,
            external_id=as_text(entry.get("id")),
            title=as_text(entry.get("title")),
            company=as_text(entry.get("organizationName")) or slug,
            description=as_text(entry.get("descriptionHtml")),
            location_text=as_text(entry.get("location")),
            compensation_text=(
                as_text(entry.get("compensationTierSummary"))
                or as_text(compensation.get("compensationTierSummary"))
            ),
            contract_text=as_text(entry.get("employmentType")),
            posted_at_text=as_text(entry.get("publishedAt")),
            raw=entry,
        )


def _from_epoch_millis(value: Any) -> datetime | None:
    """Decode Lever's millisecond epoch.

    Set here rather than passed on as text because the shared date parser reads
    a bare integer as *seconds*, which would silently place every Lever posting
    tens of thousands of years in the future. Which unit an API writes its
    integers in is a wire-format detail only this adapter can know.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None
