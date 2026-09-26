"""Rendering helpers for the CLI.

Presentation decisions only. The colour choices here carry meaning and are
consistent with the UI: green means confirmed eligible, yellow means we could
not tell, red means blocked. Yellow is deliberately *not* treated as a warning
to be cleared — ``UNKNOWN`` is a normal, expected outcome (ADR-0002), and
styling it as an error would push users toward hiding it.
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from outpost.domain.models import Eligibility, Job, VerificationTier
from outpost.domain.ports import RunReport, SourceOutcome

__all__ = [
    "eligibility_counts",
    "job_detail",
    "job_table",
    "run_report",
    "source_table",
]

_ELIGIBILITY_STYLE = {
    Eligibility.ELIGIBLE: "green",
    Eligibility.UNKNOWN: "yellow",
    Eligibility.INELIGIBLE: "red",
}

_TIER_STYLE = {
    VerificationTier.OK: "green",
    VerificationTier.SUSPICIOUS: "yellow",
    VerificationTier.SCAM: "red",
    VerificationTier.EXPIRED: "dim",
    VerificationTier.UNKNOWN: "dim",
}


def job_table(console: Console, jobs: Sequence[Job]) -> None:
    if not jobs:
        console.print("[dim]No jobs match.[/]")
        return

    table = Table(show_lines=False, header_style="bold")
    table.add_column("id", style="dim", no_wrap=True)
    table.add_column("score", justify="right", no_wrap=True)
    table.add_column("title", overflow="ellipsis", max_width=44)
    table.add_column("company", overflow="ellipsis", max_width=22)
    table.add_column("eligibility", no_wrap=True)
    table.add_column("source", style="dim", no_wrap=True)

    for job in jobs:
        eligibility = job.effective_eligibility
        score = str(job.match.score) if job.match else "—"
        marker = "*" if job.eligibility_override else ""
        table.add_row(
            job.id[:8],
            score,
            job.title,
            job.company or "—",
            Text(eligibility.value + marker, style=_ELIGIBILITY_STYLE[eligibility]),
            job.source,
        )

    console.print(table)
    if any(job.eligibility_override for job in jobs):
        console.print("[dim]* your override, not the rules[/]")


def job_detail(console: Console, job: Job) -> None:
    eligibility = job.effective_eligibility
    header = Text(job.title, style="bold")
    if job.company:
        header.append(f"  ·  {job.company}", style="dim")

    body = Table(show_header=False, box=None, padding=(0, 1))
    body.add_row("url", str(job.url))
    body.add_row("source", job.source)
    body.add_row("posted", job.posted_at.date().isoformat() if job.posted_at else "—")
    body.add_row("location", job.location_text or "—")
    body.add_row(
        "eligibility",
        Text(eligibility.value, style=_ELIGIBILITY_STYLE[eligibility]),
    )

    # The evidence is the point: a verdict the user cannot audit is a bug.
    for dimension in job.eligibility.dimensions:
        if dimension.eligibility.is_determined:
            detail = f"{dimension.evidence}"
            if dimension.matched_text and dimension.matched_text != dimension.evidence:
                detail += f'  [dim](matched "{dimension.matched_text}")[/]'
            body.add_row(f"  {dimension.dimension.value}", detail)

    if job.eligibility_override:
        body.add_row("override", f"[bold]{job.eligibility_override.value}[/] (yours)")

    if job.verification:
        body.add_row(
            "verification",
            Text(job.verification.tier.value, style=_TIER_STYLE[job.verification.tier]),
        )
        for reason in job.verification.reasons:
            body.add_row("", f"[dim]{reason}[/]")

    if job.match:
        body.add_row("match", f"{job.match.score}/100 [dim]via {job.match.provider}[/]")
        body.add_row("", job.match.reason)
        for gap in job.match.gaps:
            body.add_row("  gap", f"[dim]{gap}[/]")

    body.add_row("status", job.status.value)
    if job.notes:
        body.add_row("notes", job.notes)

    console.print(Panel(body, title=header, title_align="left"))


def eligibility_counts(console: Console, counts: dict[Eligibility, int]) -> None:
    table = Table(show_header=False, box=None)
    for value in (Eligibility.ELIGIBLE, Eligibility.UNKNOWN, Eligibility.INELIGIBLE):
        table.add_row(
            Text(value.value, style=_ELIGIBILITY_STYLE[value]),
            str(counts.get(value, 0)),
        )
    console.print(table)


def source_table(console: Console, outcomes: Sequence[SourceOutcome]) -> None:
    table = Table(header_style="bold")
    table.add_column("source")
    table.add_column("fetched", justify="right")
    table.add_column("time", justify="right")
    table.add_column("status")

    for outcome in outcomes:
        if outcome.error:
            state = Text("failed", style="red")
        elif outcome.fetched == 0:
            # Not an error, but almost always a broken parser. Say so.
            state = Text("empty — check this source", style="yellow")
        else:
            state = Text("ok", style="green")
        table.add_row(
            outcome.source,
            str(outcome.fetched),
            f"{outcome.duration_seconds:.1f}s",
            state,
        )

    console.print(table)
    for outcome in outcomes:
        if outcome.error:
            console.print(f"[dim]{outcome.source}: {outcome.error}[/]")


def run_report(console: Console, report: RunReport) -> None:
    """Render the run as a funnel.

    A funnel rather than a list of totals, because the *shape* is the signal: a
    stage that swallows almost everything is how a broken parser or an
    over-tight filter announces itself.
    """
    if report.sources:
        source_table(console, report.sources)

    table = Table(title="run", show_header=False, box=None, padding=(0, 2))
    rows = [
        ("fetched", report.fetched),
        ("eligible", report.eligible),
        ("unknown", report.unknown_eligibility),
        ("ineligible", report.ineligible),
        ("filtered out", report.filtered_out),
        ("verified", report.verified),
        ("scored", report.scored),
    ]
    for label, value in rows:
        table.add_row(label, str(value))
    console.print(table)
