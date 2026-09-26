"""Hacker News "Ask HN: Who is hiring?" — one comment, one job.

Read through the Algolia HN API, which needs no auth and, unlike the firebase
API, can return a whole thread in one request.

This source is the least structured one we have: a comment is free-form prose
written by whoever posted it, and there are no fields at all. What we can
extract is therefore a *guess*, and the guessing is confined to
:func:`_extract_title` — everything else is passed through verbatim.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from outpost.adapters.sources.base import BaseSource, as_text
from outpost.domain.models import RawJob
from outpost.domain.normalisation import html_to_text
from outpost.domain.ports import RateLimit, SourceError

__all__ = ["HackerNewsSource"]

_SEARCH_URL = (
    "https://hn.algolia.com/api/v1/search_by_date"
    "?tags=story,author_whoishiring&hitsPerPage=20"
)
"""Find the thread by *author and date*, not by relevance.

The obvious query — ``/search?query=Ask HN Who is hiring`` — is sorted by
relevance, and relevance for this phrase means the most-upvoted threads of all
time. A live run returned the **April 2020** thread and 476 six-year-old
listings; re-sorting the hits by date does not help, because every hit on the
first page is already old.

``author_whoishiring`` is the bot account that posts these threads monthly, and
``search_by_date`` sorts by recency at the source. The title pattern below then
separates "Who is hiring?" from the "Who wants to be hired?" and freelancer
threads the same account posts on the same day.
"""
_THREAD_URL = (
    "https://hn.algolia.com/api/v1/search_by_date"
    "?tags=comment,story_{story_id}&hitsPerPage=1000"
)
_ITEM_URL = "https://news.ycombinator.com/item?id={item_id}"

_TITLE_PATTERN = re.compile(r"ask\s+hn:.*who\s+is\s+hiring", re.IGNORECASE)

_MIN_COMMENT_CHARS = 100
"""Below this, a comment is a "great thread!" reply or a one-line ping, not a
posting. Chosen to be generous: a real ad that loses out here is a listing the
user never sees, which is the expensive direction of error (ADR-0002)."""


class HackerNewsSource(BaseSource):
    """The current month's "Who is hiring?" thread."""

    name = "hackernews"
    rate_limit = RateLimit(requests_per_minute=30, concurrency=2)

    async def fetch(self, since: datetime | None = None) -> Sequence[RawJob]:
        story_id = await self._latest_story_id()
        payload = await self._get_json(_THREAD_URL.format(story_id=story_id))
        hits = _hits(payload, self.name)

        jobs: list[RawJob] = []
        for hit in hits:
            if job := _to_raw(hit, story_id=story_id):
                jobs.append(job)
        return jobs

    async def _latest_story_id(self) -> str:
        """Find the newest "Who is hiring?" thread.

        Hits arrive newest-first from ``search_by_date`` (see ``_SEARCH_URL``
        for why that matters), but they are still re-sorted by ``created_at``
        here: relying on an API's documented ordering for a correctness
        property is how you end up shipping stale data when it quietly
        changes, and the sort costs nothing.
        """
        payload = await self._get_json(_SEARCH_URL)
        candidates = [
            hit
            for hit in _hits(payload, self.name)
            if _TITLE_PATTERN.search(str(hit.get("title") or ""))
            and as_text(hit.get("objectID"))
        ]
        if not candidates:
            raise SourceError(self.name, "no 'Who is hiring?' story found")

        newest = max(candidates, key=lambda hit: str(hit.get("created_at") or ""))
        story_id = as_text(newest.get("objectID"))
        if story_id is None:  # pragma: no cover - filtered above
            raise SourceError(self.name, "story hit has no objectID")
        return story_id


def _hits(payload: Any, source: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        msg = f"expected a JSON object, got {type(payload).__name__}"
        raise SourceError(source, msg)
    hits = payload.get("hits")
    if not isinstance(hits, list):
        raise SourceError(source, "response has no 'hits' array")
    return [hit for hit in hits if isinstance(hit, dict)]


def _extract_title(comment_html: str) -> str | None:
    """Take the first line of the comment as the job title.

    **This is a heuristic and it is frequently wrong.** The convention in these
    threads is a first line like ``Acme | Senior Backend | Remote (EU) | 120k``,
    but nothing enforces it: some posters open with a paragraph about the
    company, some with a bare role, some with an emoji banner. We take the whole
    first line rather than trying to split it on ``|`` — the field order is not
    a convention we can rely on, and a confidently mislabelled company is worse
    than a verbose title.

    Plain text is needed because the title must be readable as-is; the shared
    ``html_to_text`` does it so the source does not grow a second HTML parser
    that disagrees with normalisation's.
    """
    text = html_to_text(comment_html)
    for line in text.splitlines():
        if stripped := line.strip():
            return stripped
    return None


def _to_raw(hit: dict[str, Any], *, story_id: str) -> RawJob | None:
    """Map one comment onto ``RawJob``, or ``None`` if it is not a posting.

    Only top-level comments are postings; replies are questions and follow-ups,
    so anything whose parent is not the story itself is dropped.
    """
    if as_text(hit.get("parent_id")) != story_id:
        return None

    comment_id = as_text(hit.get("objectID"))
    body = as_text(hit.get("comment_text"))
    if not comment_id or not body or len(body) < _MIN_COMMENT_CHARS:
        return None

    title = _extract_title(body)
    if not title:
        return None

    return RawJob(
        source=HackerNewsSource.name,
        url=_ITEM_URL.format(item_id=comment_id),
        external_id=comment_id,
        title=title,
        # No company field exists, and inferring one from the title line is the
        # same guess that makes ``_extract_title`` unreliable — it is left unset
        # rather than filled with something plausible.
        company=None,
        description=body,
        posted_at_text=as_text(hit.get("created_at")),
        raw=hit,
    )
