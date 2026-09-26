"""Time as an injectable dependency.

``datetime.now()`` is a dependency like any other. Injecting it means staleness,
expiry and ``first_seen``/``last_seen`` logic can be tested by passing a value
instead of sleeping or freezing time process-wide (ADR-0009).
"""

from __future__ import annotations

from datetime import UTC, datetime

__all__ = ["FixedClock", "SystemClock"]


class SystemClock:
    """The real clock. Always timezone-aware, always UTC."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """A clock that does not move. For tests and deterministic replays.

    Shipped in ``src`` rather than ``tests`` because it is also useful for
    reproducing a run against a captured dataset.
    """

    def __init__(self, at: datetime) -> None:
        if at.tzinfo is None:
            msg = "FixedClock requires an aware datetime"
            raise ValueError(msg)
        self._at = at.astimezone(UTC)

    def now(self) -> datetime:
        return self._at

    def advance(self, seconds: float) -> None:
        from datetime import timedelta

        self._at += timedelta(seconds=seconds)
