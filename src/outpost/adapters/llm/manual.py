"""Scoring by hand, through whatever chat UI the user already pays for.

Many users have a ChatGPT or Claude subscription and no API key, and getting one
means a billing page, a card and a country that the vendor serves — the exact
onboarding barrier ADR-0006 exists to route around. This adapter turns the
subscription they already have into a provider: it writes the batch out as
markdown, the user pastes it into the chat, pastes the JSON reply back, and the
pipeline imports it.

It is a two-step provider, and that shape is the point, not a limitation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import structlog

from outpost.adapters.llm.base import (
    MalformedResponse,
    parse_batch_response,
    render_scoring_prompt,
)
from outpost.domain.models import MatchResult, ScoringRequest

__all__ = ["ManualProvider"]

logger = structlog.get_logger(__name__)

_DEFAULT_EXPORT: Final = Path("outpost-scoring-batch.md")


class ManualProvider:
    """Exports a batch to a file; imports the pasted-back JSON separately.

    Satisfies :class:`~outpost.domain.ports.LLMProvider` structurally.
    """

    name = "manual"

    def __init__(self, export_path: Path | None = None) -> None:
        self.export_path = export_path or _DEFAULT_EXPORT
        self._last_requests: tuple[ScoringRequest, ...] = ()

    async def score(
        self,
        requests: Sequence[ScoringRequest],
        profile_text: str,
    ) -> Mapping[str, MatchResult]:
        """Write the export and return ``[]``.

        **The empty list is the correct answer, not a stub.** At this point
        nothing has been scored — a human has not yet looked at the batch — and
        the port is explicit that a provider which cannot score an item omits it
        rather than inventing a value. Returning placeholder scores here would
        put fabricated numbers into the ranking and the database, which is the
        one outcome the whole scoring layer is built to prevent (ADR-0006).

        The user's next step is to paste the exported file into a chat UI and
        feed the reply to :meth:`import_results`, which is where results
        actually appear.
        """
        if not requests:
            return {}

        self._last_requests = tuple(requests)
        self.export_path.parent.mkdir(parents=True, exist_ok=True)
        self.export_path.write_text(
            self._render_export(requests, profile_text), encoding="utf-8"
        )
        logger.info("manual.exported", path=str(self.export_path), jobs=len(requests))
        return {}

    def import_results(
        self,
        text: str,
        requests: Sequence[ScoringRequest] | None = None,
    ) -> dict[str, MatchResult]:
        """Parse a reply pasted back from the chat UI.

        ``requests`` defaults to the batch this instance last exported, so a
        single long-lived provider round-trips without the caller re-supplying
        it. A fresh process must pass the batch explicitly — the indices in the
        pasted JSON mean nothing without the batch they were generated for.

        Raises:
            MalformedResponse: the pasted text is not our wire contract. Raised
                rather than swallowed because here a human is present to fix it,
                which is the opposite of the automated case.
        """
        batch = tuple(requests) if requests is not None else self._last_requests
        if not batch:
            msg = "no batch to import against; export one first or pass requests"
            raise MalformedResponse(self.name, msg)
        return parse_batch_response(text, batch, self.name)

    def _render_export(
        self, requests: Sequence[ScoringRequest], profile_text: str
    ) -> str:
        """The file the user pastes.

        It carries the same prompt every automated provider gets, so a manual
        run is calibrated identically — a different prompt here would make
        manual scores quietly incomparable with the rest.
        """
        prompt = render_scoring_prompt(requests, profile_text)
        exported_at = datetime.now(UTC).isoformat(timespec="seconds")
        job_lines = "\n".join(
            f"{i}. `{r.job_id}` — {r.title}" for i, r in enumerate(requests, start=1)
        )
        return (
            f"# Outpost — manual scoring batch\n\n"
            f"Exported {exported_at} · {len(requests)} job(s)\n\n"
            f"## How to use this\n\n"
            f"1. Copy everything under **Prompt** into ChatGPT, Claude, or any\n"
            f"   chat assistant.\n"
            f"2. Copy the JSON it replies with.\n"
            f"3. Run `outpost score --import <file>` with that JSON saved to a\n"
            f"   file, or paste it when prompted.\n\n"
            f"## Batch contents\n\n{job_lines}\n\n"
            f"## Prompt\n\n"
            f"---\n\n{prompt}\n"
        )
