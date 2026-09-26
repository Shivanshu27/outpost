"""The provider that scores nothing — and is shipped on purpose.

This is **not** a test double. It is the zero-config default (ADR-0006): with no
key, no signup and no decision on first use, `outpost run` still fetches,
normalises, deduplicates, resolves eligibility and verifies. Stages 1-7 are most
of the product; LLM scoring is the optional last one.

The alternative default — refuse to run without a provider — turns a tool that
works immediately into a tool that starts with a signup form, for users who may
not be able to complete one (PRD §4).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import structlog

from outpost.domain.models import MatchResult, ScoringRequest

__all__ = ["NullProvider"]

logger = structlog.get_logger(__name__)


class NullProvider:
    """Returns no scores, ever. Satisfies the port structurally."""

    name = "none"
    """Matches the value the user writes in config.

    Calling it "null" would leak an implementation detail into the UI header,
    where it reads as an error rather than as the deliberate no-LLM mode this
    provider exists to be (ADR-0006).
    """

    async def score(
        self,
        requests: Sequence[ScoringRequest],
        profile_text: str,
    ) -> Mapping[str, MatchResult]:
        """Return ``[]``.

        Never raises. In particular it does not raise
        :class:`~outpost.domain.ports.QuotaExhausted` — there is no quota, and
        the run's report should say "no provider configured", not "budget
        spent". Jobs stay unscored and stay eligible for scoring later, so
        adding a key and re-running picks them up with no migration.
        """
        if requests:
            logger.debug("null.skipped", jobs=len(requests))
        return {}
