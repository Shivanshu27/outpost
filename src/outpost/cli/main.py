"""Outpost's command line.

Presentation only. Every command builds the container, hands ports to a
use-case, and renders the result. No business logic lives here — if a decision
is being made in this file, it is in the wrong place (ADR-0003).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from outpost.app.pipeline import Pipeline
from outpost.cli import render
from outpost.config.container import Container, build_container, load_settings
from outpost.domain.models import Eligibility, JobStatus
from outpost.domain.ports import RunReport

app = typer.Typer(
    name="outpost",
    help="Job search for engineers outside the US and EU. Local-first.",
    add_completion=False,
    no_args_is_help=True,
)

console = Console()
err_console = Console(stderr=True)

ConfigDirOption = Annotated[
    Path | None,
    typer.Option("--config-dir", help="Override the config directory."),
]


def _load(config_dir: Path | None) -> Container:
    """Build the container, turning setup problems into actionable messages.

    A missing profile is the overwhelmingly common first-run failure, and a
    traceback is a terrible way to learn you need to run `outpost init`.
    """
    from outpost.adapters.profile import ProfileError
    from outpost.domain.rules import RuleSetError

    try:
        settings = load_settings(config_dir)
        return build_container(settings)
    except ProfileError as exc:
        err_console.print(f"[bold red]Profile problem[/]\n{exc}")
        raise typer.Exit(2) from exc
    except RuleSetError as exc:
        err_console.print(f"[bold red]Ruleset problem[/]\n{exc}")
        raise typer.Exit(2) from exc
    except ValueError as exc:
        err_console.print(f"[bold red]Configuration problem[/]\n{exc}")
        raise typer.Exit(2) from exc


# --------------------------------------------------------------------------
# init
# --------------------------------------------------------------------------


@app.command()
def init(
    config_dir: ConfigDirOption = None,
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite an existing profile.")
    ] = False,
) -> None:
    """Set up your profile. Takes about a minute."""
    from outpost.cli.wizard import run_wizard

    settings = load_settings(config_dir)
    if settings.profile_path.exists() and not force:
        console.print(
            f"A profile already exists at [cyan]{settings.profile_path}[/].\n"
            f"Edit it directly, or re-run with [bold]--force[/] to start over."
        )
        raise typer.Exit(0)

    run_wizard(settings, console)


# --------------------------------------------------------------------------
# run and its stages
# --------------------------------------------------------------------------


@app.command()
def run(
    config_dir: ConfigDirOption = None,
    skip_collect: Annotated[
        bool, typer.Option("--skip-collect", help="Reuse what is already stored.")
    ] = False,
) -> None:
    """Run the full pipeline: collect, label, filter, verify, rank, score."""
    container = _load(config_dir)
    report = asyncio.run(_run_pipeline(container, skip_collect=skip_collect))
    render.run_report(console, report)
    _finish(container)

    if report.quota_exhausted:
        console.print(
            "\n[yellow]Scoring stopped early because the provider's quota ran "
            "out.[/] Everything scored so far is saved; run again later to "
            "continue where it left off."
        )


async def _run_pipeline(container: Container, *, skip_collect: bool) -> RunReport:
    deps = container.pipeline_deps()

    with console.status("[bold]working[/]") as status:

        def progress(stage: str, message: str) -> None:
            status.update(f"[bold]{stage}[/] — {message}")
            console.log(f"[dim]{stage}[/] {message}")

        pipeline = Pipeline(deps, progress=progress)
        try:
            return await pipeline.run(skip_collect=skip_collect)
        finally:
            await container.aclose()


@app.command()
def scrape(config_dir: ConfigDirOption = None) -> None:
    """Collect from sources only."""
    container = _load(config_dir)

    async def go() -> None:
        pipeline = Pipeline(
            container.pipeline_deps(),
            progress=lambda s, m: console.log(f"[dim]{s}[/] {m}"),
        )
        try:
            _, outcomes = await pipeline.collect()
            render.source_table(console, outcomes)
        finally:
            await container.aclose()

    asyncio.run(go())
    _finish(container)


@app.command()
def label(config_dir: ConfigDirOption = None) -> None:
    """Re-resolve eligibility for every stored job.

    Run this after editing your rules or profile — it is cheap and needs no
    network.
    """
    container = _load(config_dir)
    pipeline = Pipeline(
        container.pipeline_deps(),
        progress=lambda s, m: console.log(f"[dim]{s}[/] {m}"),
    )
    counts = pipeline.label_eligibility()
    render.eligibility_counts(console, counts)
    _finish(container)


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------


@app.command(name="list")
def list_jobs(
    config_dir: ConfigDirOption = None,
    limit: Annotated[int, typer.Option("--limit", "-n")] = 25,
    eligibility: Annotated[
        str | None,
        typer.Option("--eligibility", "-e", help="eligible | unknown | ineligible"),
    ] = None,
    status: Annotated[str | None, typer.Option("--status", "-s")] = None,
    source: Annotated[str | None, typer.Option("--source")] = None,
) -> None:
    """List stored jobs, best match first."""
    container = _load(config_dir)
    jobs = container.repository.list_jobs(
        limit=limit,
        eligibilities=[Eligibility(eligibility)] if eligibility else None,
        statuses=[JobStatus(status)] if status else None,
        sources=[source] if source else None,
    )
    render.job_table(console, jobs)
    _finish(container)


@app.command()
def show(
    job_id: Annotated[str, typer.Argument(help="Job id, or a unique prefix.")],
    config_dir: ConfigDirOption = None,
) -> None:
    """Show one job in full, including why it was labelled as it was."""
    container = _load(config_dir)
    job = container.repository.get(job_id)
    if job is None:
        err_console.print(f"No job with id [cyan]{job_id}[/].")
        _finish(container)
        raise typer.Exit(1)
    render.job_detail(console, job)
    _finish(container)


@app.command()
def stats(config_dir: ConfigDirOption = None) -> None:
    """Summarise what is in the database."""
    container = _load(config_dir)
    repo = container.repository

    table = Table(title="Outpost", show_header=False, box=None)
    table.add_row("Total jobs", str(repo.count()))
    for value, count in sorted(repo.counts_by_eligibility().items()):
        table.add_row(f"  {value}", str(count))
    console.print(table)
    _finish(container)


# --------------------------------------------------------------------------
# user actions
# --------------------------------------------------------------------------


@app.command(name="set-status")
def set_status(
    job_id: str,
    status: Annotated[
        str, typer.Argument(help="new|shortlisted|applied|rejected|dismissed")
    ],
    config_dir: ConfigDirOption = None,
) -> None:
    """Move a job through your pipeline."""
    container = _load(config_dir)
    try:
        parsed = JobStatus(status)
    except ValueError as exc:
        err_console.print(
            f"Unknown status [cyan]{status}[/]. "
            f"Valid: {', '.join(s.value for s in JobStatus)}"
        )
        _finish(container)
        raise typer.Exit(1) from exc
    container.repository.set_status(job_id, parsed)
    console.print(f"[green]{job_id}[/] → {parsed.value}")
    _finish(container)


@app.command()
def override(
    job_id: str,
    eligibility: Annotated[
        str, typer.Argument(help="eligible | ineligible | unknown | clear")
    ],
    config_dir: ConfigDirOption = None,
) -> None:
    """Correct an eligibility verdict.

    Your override always wins over the rules, and survives re-scrapes. If you
    are overriding often, the underlying rule is probably wrong — consider
    fixing it in your rules file so it helps everyone.
    """
    container = _load(config_dir)
    value = None if eligibility == "clear" else Eligibility(eligibility)
    container.repository.set_eligibility_override(job_id, value)
    console.print(f"[green]{job_id}[/] → {value.value if value else 'rules apply'}")
    _finish(container)


# --------------------------------------------------------------------------
# ui and diagnostics
# --------------------------------------------------------------------------


@app.command()
def ui(
    config_dir: ConfigDirOption = None,
    port: Annotated[int | None, typer.Option("--port", "-p")] = None,
    no_browser: Annotated[bool, typer.Option("--no-browser")] = False,
) -> None:
    """Open the review interface."""
    import uvicorn

    from outpost.api.app import create_app

    settings = load_settings(config_dir)
    bind_port = port or settings.ui_port
    url = f"http://{settings.ui_host}:{bind_port}"

    console.print(f"Outpost UI → [cyan]{url}[/]  (ctrl-c to stop)")
    if not no_browser:
        import webbrowser

        webbrowser.open(url)

    uvicorn.run(
        create_app(settings),
        host=settings.ui_host,
        port=bind_port,
        log_level=settings.log_level.lower(),
    )


@app.command()
def doctor(config_dir: ConfigDirOption = None) -> None:
    """Check that everything is configured and reachable."""
    from outpost.cli.diagnostics import run_diagnostics

    settings = load_settings(config_dir)
    ok = run_diagnostics(settings, console)
    raise typer.Exit(0 if ok else 1)


def _finish(container: Container) -> None:
    container.repository.close()


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        # Every stage commits before the next begins, so an interrupt is safe
        # and the next run resumes. Say so, rather than dumping a traceback.
        err_console.print("\n[yellow]Interrupted.[/] Progress so far is saved.")
        sys.exit(130)


if __name__ == "__main__":
    main()
