"""Shared scaffolding for source adapters.

Deliberately thin. This class holds the two things every source needs — the
shared :class:`HttpClient` and its own identity — and nothing else.

In particular it contains **no parsing helpers**. That absence is the point:
ADR-0008 puts every salary, date, location and HTML decision in
``domain/normalisation.py``, and a convenience helper here is how that rule
erodes. The first "just a small ``_parse_salary``" on a shared base is how eight
sources end up parsing salaries eight ways.

A source therefore does one thing: map the provider's field names onto
:class:`RawJob` fields, verbatim.
"""

from __future__ import annotations

from typing import Any

import structlog

from outpost.adapters.http import HttpClient
from outpost.domain.ports import RateLimit

__all__ = ["BaseSource", "as_tags", "as_text"]

logger = structlog.get_logger(__name__)


def as_text(value: Any) -> str | None:
    """Coerce a JSON scalar to ``str | None``.

    This is type coercion, not normalisation: JSON gives us ints for ids and
    ``null`` for absent fields, and :class:`RawJob` wants strings. The value's
    *content* is passed through untouched — no collapsing, no parsing, no
    guessing — so ``normalise()`` still sees exactly what the board published.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, int | float):
        return str(value)
    return None


def as_tags(value: Any) -> tuple[str, ...]:
    """Coerce a JSON list of tags to a tuple of strings, dropping non-scalars."""
    if not isinstance(value, list):
        return ()
    return tuple(text for item in value if (text := as_text(item)))


class BaseSource:
    """Common state for a :class:`~outpost.domain.ports.JobSource`.

    Subclasses set ``name`` and ``rate_limit`` as class attributes and implement
    ``fetch``. They inherit nothing else — the protocol is structural, so this
    base is an implementation convenience and never a requirement.
    """

    name: str = ""
    rate_limit: RateLimit = RateLimit()

    def __init__(self, client: HttpClient) -> None:
        self._client = client
        self._log = logger.bind(source=self.name)

    async def _get_json(
        self, url: str, *, headers: dict[str, str] | None = None
    ) -> Any:
        """GET JSON under this source's declared politeness budget.

        Retries, backoff and throttling belong to the client (ADR-0008); this
        only ensures the source's own ``rate_limit`` is the one applied.
        """
        return await self._client.get_json(
            url, source=self.name, rate_limit=self.rate_limit, headers=headers
        )

    async def _get_text(
        self, url: str, *, headers: dict[str, str] | None = None
    ) -> str:
        return await self._client.get_text(
            url, source=self.name, rate_limit=self.rate_limit, headers=headers
        )
