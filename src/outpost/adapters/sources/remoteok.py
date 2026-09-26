"""RemoteOK — a single public JSON endpoint, no auth, no pagination.

The feed returns the whole current board on every call, so ``since`` is
ignored; deduplication downstream makes re-fetching harmless (ADR-0008).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from outpost.adapters.sources.base import BaseSource, as_tags, as_text
from outpost.domain.models import RawJob
from outpost.domain.ports import RateLimit, SourceError

__all__ = ["RemoteOKSource"]

_ENDPOINT = "https://remoteok.com/api"


class RemoteOKSource(BaseSource):
    """RemoteOK's public board feed."""

    name = "remoteok"
    rate_limit = RateLimit(requests_per_minute=20, concurrency=1)

    async def fetch(self, since: datetime | None = None) -> Sequence[RawJob]:
        payload = await self._get_json(_ENDPOINT)
        if not isinstance(payload, list):
            msg = f"expected a JSON array, got {type(payload).__name__}"
            raise SourceError(self.name, msg)

        jobs: list[RawJob] = []
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            # The feed's first element is a legal notice, not a listing. It is
            # matched by shape rather than by index so a future second metadata
            # entry does not silently become a job titled ``None``.
            if "legal" in entry:
                continue
            if job := _to_raw(entry):
                jobs.append(job)
        return jobs


def _to_raw(entry: dict[str, Any]) -> RawJob | None:
    """Map one feed entry onto ``RawJob``, or ``None`` if it has no URL.

    A listing with no URL cannot be identified, deduplicated or opened, so it is
    the one field whose absence drops the record. Everything else is passed
    through as-is, including fields we cannot make sense of — deciding what a
    salary or a date *means* is normalisation's job.
    """
    url = as_text(entry.get("url")) or as_text(entry.get("apply_url"))
    if not url:
        return None
    return RawJob(
        source=RemoteOKSource.name,
        url=url,
        external_id=as_text(entry.get("id")),
        title=as_text(entry.get("position")),
        company=as_text(entry.get("company")),
        description=as_text(entry.get("description")),
        location_text=as_text(entry.get("location")),
        compensation_text=as_text(entry.get("salary")),
        posted_at_text=as_text(entry.get("date")),
        tags=as_tags(entry.get("tags")),
        raw=entry,
    )
