"""We Work Remotely — RSS, not an API.

Parsed with stdlib :mod:`xml.etree.ElementTree`: the feed is small, flat and
well-formed, and a parser dependency would exceed the code it replaces
(ADR-0008's reasoning against a scraping framework applies at this scale too).

The feed carries no incremental filter, so ``since`` is ignored.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from xml.etree import ElementTree

from outpost.adapters.sources.base import BaseSource, as_text
from outpost.domain.models import RawJob
from outpost.domain.ports import RateLimit, SourceError

__all__ = ["WeWorkRemotelySource"]

_ENDPOINT = "https://weworkremotely.com/categories/remote-programming-jobs.rss"


class WeWorkRemotelySource(BaseSource):
    """We Work Remotely's remote-programming RSS category feed."""

    name = "weworkremotely"
    rate_limit = RateLimit(requests_per_minute=15, concurrency=1)

    async def fetch(self, since: datetime | None = None) -> Sequence[RawJob]:
        body = await self._get_text(_ENDPOINT)
        try:
            root = ElementTree.fromstring(body)
        except ElementTree.ParseError as exc:
            raise SourceError(self.name, f"malformed RSS: {exc}") from exc

        jobs: list[RawJob] = []
        for item in root.iter("item"):
            if job := _to_raw(item):
                jobs.append(job)
        return jobs


def _local_fields(item: ElementTree.Element) -> dict[str, Any]:
    """Child elements as a flat dict, keyed by namespace-stripped tag name.

    Namespaces are dropped because the feed mixes bare RSS tags with prefixed
    ones (``media:``, ``dc:``) and the prefix carries no meaning we use.
    """
    fields: dict[str, Any] = {}
    for child in item:
        key = child.tag.rsplit("}", 1)[-1]
        if (text := as_text(child.text)) is not None:
            fields.setdefault(key, text)
    return fields


def _split_company_and_title(value: str) -> tuple[str | None, str]:
    """Split WWR's ``"Company: Title"`` headline.

    Split on the *first* colon only, so ``"Acme: Engineer: Platform"`` yields
    the company ``Acme`` and the title ``Engineer: Platform`` — titles contain
    colons far more often than company names do.

    A headline with no colon is treated as all title and no company. Guessing
    would be worse than admitting we do not know: ``normalise()`` keeps a
    listing with no company, and a wrong company name would poison both the
    displayed record and the content hash.
    """
    company, separator, title = value.partition(":")
    if not separator or not title.strip():
        return None, value
    return company.strip() or None, title.strip()


def _to_raw(item: ElementTree.Element) -> RawJob | None:
    """Map one ``<item>`` onto ``RawJob``, or ``None`` if it has no link."""
    fields = _local_fields(item)
    url = fields.get("link")
    if not url:
        return None

    headline = fields.get("title")
    company, title = _split_company_and_title(headline) if headline else (None, None)

    return RawJob(
        source=WeWorkRemotelySource.name,
        url=url,
        external_id=fields.get("guid"),
        title=title,
        company=fields.get("company") or company,
        description=fields.get("description"),
        # ``region`` is the feed's own location field and is often absent;
        # ``category`` is a job family, not a place, so it is not a fallback.
        location_text=fields.get("region"),
        contract_text=fields.get("type"),
        posted_at_text=fields.get("pubDate"),
        raw=fields,
    )
