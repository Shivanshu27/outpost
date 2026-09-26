"""Remotive — public JSON API, one request for the whole board.

Remotive supports a ``limit`` parameter but no "changed since" filter, so
``since`` is ignored and the full board is re-fetched each run.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from outpost.adapters.sources.base import BaseSource, as_tags, as_text
from outpost.domain.models import RawJob
from outpost.domain.ports import RateLimit, SourceError

__all__ = ["RemotiveSource"]

_ENDPOINT = "https://remotive.com/api/remote-jobs"


class RemotiveSource(BaseSource):
    """Remotive's public remote-jobs API."""

    name = "remotive"
    rate_limit = RateLimit(requests_per_minute=20, concurrency=1)

    async def fetch(self, since: datetime | None = None) -> Sequence[RawJob]:
        payload = await self._get_json(_ENDPOINT)
        if not isinstance(payload, dict):
            msg = f"expected a JSON object, got {type(payload).__name__}"
            raise SourceError(self.name, msg)

        entries = payload.get("jobs")
        if entries is None:
            # A 200 without the one key we need is a contract change, not an
            # empty board — reporting it as zero jobs is exactly the silent rot
            # ADR-0008 exists to prevent.
            raise SourceError(self.name, "response has no 'jobs' array")
        if not isinstance(entries, list):
            msg = f"'jobs' is {type(entries).__name__}, expected an array"
            raise SourceError(self.name, msg)

        jobs: list[RawJob] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if job := _to_raw(entry):
                jobs.append(job)
        return jobs


def _to_raw(entry: dict[str, Any]) -> RawJob | None:
    """Map one Remotive job onto ``RawJob``, or ``None`` if it has no URL."""
    url = as_text(entry.get("url"))
    if not url:
        return None
    return RawJob(
        source=RemotiveSource.name,
        url=url,
        external_id=as_text(entry.get("id")),
        title=as_text(entry.get("title")),
        company=as_text(entry.get("company_name")),
        description=as_text(entry.get("description")),
        location_text=as_text(entry.get("candidate_required_location")),
        compensation_text=as_text(entry.get("salary")),
        contract_text=as_text(entry.get("job_type")),
        posted_at_text=as_text(entry.get("publication_date")),
        tags=as_tags(entry.get("tags")),
        raw=entry,
    )
