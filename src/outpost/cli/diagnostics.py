"""`outpost doctor` — check the setup and say what is wrong.

Written because the alternative is a user filing "it doesn't work" with no way
for either of us to find out why. Each check reports what it found *and what to
do about it*; a diagnostic that only says "FAIL" has done half a job.

Deliberately read-only and offline: it inspects configuration, never fetches.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from rich.console import Console
from rich.table import Table

from outpost.config.settings import Settings

__all__ = ["Check", "run_diagnostics"]


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    ok: bool
    detail: str
    fix: str | None = None
    fatal: bool = True
    """Non-fatal checks report as warnings and do not fail the command — a
    missing resume degrades ranking but does not stop the tool working."""


def run_diagnostics(settings: Settings, console: Console) -> bool:
    checks = list(_checks(settings))

    table = Table(header_style="bold", box=None, padding=(0, 2))
    table.add_column("")
    table.add_column("check")
    table.add_column("detail", overflow="fold")

    for check in checks:
        if check.ok:
            mark, style = "[green]✓[/]", ""
        elif check.fatal:
            mark, style = "[red]✗[/]", "red"
        else:
            mark, style = "[yellow]•[/]", "yellow"
        table.add_row(
            mark, check.name, f"[{style}]{check.detail}[/]" if style else check.detail
        )

    console.print(table)

    problems = [c for c in checks if not c.ok and c.fix]
    if problems:
        console.print("\n[bold]To fix:[/]")
        for check in problems:
            console.print(f"  {check.name}: {check.fix}")

    return not any(not c.ok and c.fatal for c in checks)


def _checks(settings: Settings) -> Iterator[Check]:
    yield _profile_check(settings)
    yield _resume_check(settings)
    yield _rules_check(settings)
    yield _database_check(settings)
    yield _llm_check(settings)
    yield _sources_check(settings)
    yield _bind_check(settings)


def _profile_check(settings: Settings) -> Check:
    from outpost.adapters.profile import ProfileError, load_profile

    if not settings.profile_path.exists():
        return Check(
            "profile",
            False,
            f"missing at {settings.profile_path}",
            fix="run `outpost init`",
        )
    try:
        profile = load_profile(settings.profile_path, settings.resume_path)
    except ProfileError as exc:
        return Check(
            "profile",
            False,
            str(exc),
            fix="edit the file, or re-run `outpost init --force`",
        )
    return Check(
        "profile",
        True,
        f"{profile.country} · {profile.timezone} · {len(profile.skills)} skills",
    )


def _resume_check(settings: Settings) -> Check:
    if not settings.resume_path.exists():
        return Check(
            "resume",
            False,
            "not set — LLM scoring will be skipped",
            fix=f"put plain text at {settings.resume_path}",
            fatal=False,
        )
    size = settings.resume_path.stat().st_size
    if size < 200:
        return Check(
            "resume",
            False,
            f"only {size} bytes — probably not a real resume",
            fix="check the file actually contains your resume text",
            fatal=False,
        )
    return Check("resume", True, f"{size:,} bytes")


def _rules_check(settings: Settings) -> Check:
    from outpost.adapters.rules_loader import load_rules
    from outpost.domain.rules import RuleSetError

    try:
        ruleset = load_rules(settings.rules_path)
    except RuleSetError as exc:
        return Check(
            "rules",
            False,
            str(exc),
            fix=f"fix or delete {settings.rules_path}",
        )
    overrides = settings.rules_path.exists()
    return Check(
        "rules",
        True,
        f"{len(ruleset.rules)} rules" + (" (with your overrides)" if overrides else ""),
    )


def _database_check(settings: Settings) -> Check:
    if not settings.db_path.exists():
        return Check(
            "database",
            True,
            "not created yet — the first run will create it",
        )
    try:
        from outpost.adapters.storage.sqlite import SqliteJobRepository

        repo = SqliteJobRepository.open(settings.db_path)
        count = repo.count()
        repo.close()
    except Exception as exc:
        return Check(
            "database",
            False,
            f"could not open: {exc}",
            fix=f"move {settings.db_path} aside and re-run",
        )
    return Check("database", True, f"{count:,} jobs at {settings.db_path}")


def _llm_check(settings: Settings) -> Check:
    llm = settings.llm
    if llm.provider == "none":
        return Check(
            "llm",
            True,
            "not configured — stages 1-7 still run, scoring is skipped",
            fatal=False,
        )
    if llm.provider in {"gemini", "openai_compatible"} and not llm.api_key:
        return Check(
            "llm",
            False,
            f"{llm.provider} selected but no API key",
            fix="set OUTPOST_LLM__API_KEY, or set the provider to `none`",
            fatal=False,
        )
    return Check(
        "llm", True, f"{llm.provider}" + (f" · {llm.model}" if llm.model else "")
    )


def _sources_check(settings: Settings) -> Check:
    enabled = settings.sources.enabled
    ats = (
        len(settings.sources.greenhouse_companies)
        + len(settings.sources.lever_companies)
        + len(settings.sources.ashby_companies)
    )
    if not enabled and not ats:
        return Check(
            "sources",
            False,
            "none enabled",
            fix="add sources under `sources.enabled` in config.yml",
        )
    detail = f"{len(enabled)} boards"
    if ats:
        detail += f" · {ats} ATS companies"
    return Check("sources", True, detail)


def _bind_check(settings: Settings) -> Check:
    """The UI has no authentication because it is local-first. Binding it off
    loopback would publish the user's job search to their network."""
    if settings.ui_host not in {"127.0.0.1", "localhost", "::1"}:
        return Check(
            "ui bind",
            False,
            f"bound to {settings.ui_host} — the UI has no authentication",
            fix="set ui_host back to 127.0.0.1 unless you really mean this",
            fatal=False,
        )
    return Check("ui bind", True, f"{settings.ui_host}:{settings.ui_port}")
