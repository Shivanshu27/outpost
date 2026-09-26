"""The `outpost init` wizard.

Onboarding is the difference between a tool a stranger installs and a tool a
stranger abandons (FR-7.1). The design rules here:

* **Nothing is mandatory except the country.** Everything else has a working
  default, because a wizard that interrogates you for five minutes is one more
  reason to close the terminal.
* **Defaults are detected where honest** — the timezone comes from the system,
  offered as a default rather than assumed.
* **It writes files the user can read and edit.** The wizard is a convenience,
  not the only way in; the YAML it produces is commented and hand-editable.
"""

from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from rich.console import Console
from rich.prompt import Confirm, Prompt

from outpost.adapters.profile import profile_template, save_profile
from outpost.config.settings import Settings
from outpost.domain.models import ContractType, UserProfile

__all__ = ["detect_timezone", "run_wizard"]

_COMMON_SKILLS = (
    "Python",
    "TypeScript",
    "JavaScript",
    "Go",
    "Java",
    "Rust",
    "React",
    "Node.js",
    "PostgreSQL",
    "AWS",
    "Docker",
    "Kubernetes",
)


def detect_timezone() -> str:
    """Best-effort IANA timezone for this machine, falling back to UTC.

    Offered as a *default*, never silently applied: the timezone drives overlap
    verdicts, and a wrong one produces confident wrong answers.
    """
    localtime = Path("/etc/localtime")
    if localtime.is_symlink():
        parts = localtime.resolve().parts
        if "zoneinfo" in parts:
            candidate = "/".join(parts[parts.index("zoneinfo") + 1 :])
            try:
                ZoneInfo(candidate)
            except (ZoneInfoNotFoundError, ValueError, KeyError):
                return "UTC"
            return candidate

    tz_file = Path("/etc/timezone")
    if tz_file.exists():
        candidate = tz_file.read_text(encoding="utf-8").strip()
        try:
            ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            return "UTC"
        return candidate

    return "UTC"


def run_wizard(settings: Settings, console: Console) -> None:
    """Collect a profile interactively and write it to disk."""
    console.print()
    console.print("[bold]Outpost setup[/]")
    console.print(
        "[dim]Everything stays on this machine. Nothing is uploaded, and there "
        "is no account.[/]\n"
    )

    country = (
        Prompt.ask("Your country [dim](ISO code, e.g. IN, BR, NG, PH)[/]", default="IN")
        .strip()
        .upper()
    )

    timezone = Prompt.ask("Your timezone", default=detect_timezone()).strip()

    console.print(
        "\n[dim]Do you already have the right to work in any of these? "
        "Leave blank if not — that is the common case.[/]"
    )
    authorisation = _csv(
        Prompt.ask("Work authorisation [dim](e.g. US, GB, EU)[/]", default="")
    )

    console.print()
    contract_raw = Prompt.ask(
        "What will you accept [dim](full_time, contract, part_time)[/]",
        default="full_time,contract",
    )
    contract_types = tuple(
        ContractType(value)
        for value in _csv(contract_raw)
        if value in {c.value for c in ContractType}
    )

    currencies = tuple(
        c.upper()
        for c in _csv(Prompt.ask("Currencies you accept", default="USD,EUR,GBP"))
    )

    console.print()
    titles = _csv(
        Prompt.ask(
            "Roles you want [dim](comma separated)[/]",
            default="Senior Software Engineer,Backend Engineer",
        )
    )

    console.print(f"[dim]Common: {', '.join(_COMMON_SKILLS[:8])}…[/]")
    skills = _csv(
        Prompt.ask("Your main skills", default="Python,TypeScript,PostgreSQL,AWS")
    )

    profile = UserProfile(
        country=country,
        timezone=timezone,
        work_authorisation=authorisation,
        contract_types=contract_types,
        currencies=currencies,
        titles=titles,
        skills=skills,
    )

    settings.config_dir.mkdir(parents=True, exist_ok=True)
    save_profile(settings.profile_path, profile)
    console.print(f"\n[green]✓[/] profile → [cyan]{settings.profile_path}[/]")

    _maybe_write_resume(settings, console)
    _maybe_write_rules_stub(settings, console)

    console.print(
        "\n[bold]Ready.[/] Next:\n"
        "  [cyan]outpost run[/]   collect and rank\n"
        "  [cyan]outpost ui[/]    review what it found\n"
    )
    console.print(
        "[dim]No LLM is configured, which is fine — you still get eligibility, "
        "scam and liveness checks, and local ranking. Add a provider later in "
        f"{settings.config_file} if you want match scoring.[/]"
    )


def _maybe_write_resume(settings: Settings, console: Console) -> None:
    """Offer to import a resume.

    Plain text only, and by *copy* — we read the file once and write our own
    copy, so the user's original is never touched and Outpost never holds a
    path to somewhere outside its own config directory.
    """
    if settings.resume_path.exists():
        return
    if not Confirm.ask(
        "\nAdd your resume now? [dim](improves ranking; plain text)[/]", default=False
    ):
        console.print(f"[dim]Later: put plain text at {settings.resume_path}[/]")
        return

    raw = Prompt.ask("Path to a .txt resume").strip()
    source = Path(raw).expanduser()
    if not source.exists():
        console.print(f"[yellow]Not found: {source} — skipping.[/]")
        return
    try:
        text = source.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        console.print(f"[yellow]Could not read it ({exc}) — skipping.[/]")
        return

    settings.resume_path.write_text(text, encoding="utf-8")
    console.print(f"[green]✓[/] resume → [cyan]{settings.resume_path}[/]")


def _maybe_write_rules_stub(settings: Settings, console: Console) -> None:
    """Write a commented, empty override file.

    An empty file that explains itself is far more discoverable than
    documentation saying a file could exist.
    """
    if settings.rules_path.exists():
        return
    settings.rules_path.write_text(
        "# Your eligibility rule overrides.\n"
        "#\n"
        "# These merge over the shipped defaults. A rule with the same id\n"
        "# replaces the default one in place; new ids are appended.\n"
        "#\n"
        "# Order matters — first match per dimension wins.\n"
        "#\n"
        "# version: 1\n"
        "# rules:\n"
        "#   - id: loc.my_country_welcome\n"
        "#     dimension: location\n"
        "#     when:\n"
        '#       any_phrase: ["hiring in brazil", "latam welcome"]\n'
        "#     verdict: eligible\n"
        '#     evidence: "Listing welcomes candidates in my region"\n',
        encoding="utf-8",
    )
    console.print(
        f"[green]✓[/] rules  → [cyan]{settings.rules_path}[/] [dim](empty)[/]"
    )


def _csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def write_profile_template(path: Path) -> None:
    """Write the commented template, for users who would rather not be asked."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(profile_template(), encoding="utf-8")
