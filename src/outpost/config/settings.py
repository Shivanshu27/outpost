"""Settings — parsed once, at the edge, then passed inward.

This module and :mod:`outpost.config.container` are the **only** places in the
codebase permitted to read ``os.environ`` or resolve user paths (ADR-0009). An
import-linter contract in CI enforces that; it is not a convention.

Everything below the edge receives what it needs as a parameter, so a
function's dependencies are visible in its signature rather than acquired
invisibly from process state.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Literal

from platformdirs import user_config_dir, user_data_dir
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = [
    "FilterSettings",
    "LLMSettings",
    "Settings",
    "SourceSettings",
    "default_config_dir",
    "default_data_dir",
]

APP_NAME = "outpost"


def default_config_dir() -> Path:
    """Where the user's profile, rules and .env live."""
    return Path(user_config_dir(APP_NAME, appauthor=False))


def default_data_dir() -> Path:
    """Where the database lives. Separate from config because one is the
    user's authored input and the other is regenerable state."""
    return Path(user_data_dir(APP_NAME, appauthor=False))


class SourceSettings(BaseModel):
    """Which boards to collect from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: tuple[str, ...] = (
        "remoteok",
        "remotive",
        "weworkremotely",
        "hackernews",
    )
    greenhouse_companies: tuple[str, ...] = ()
    lever_companies: tuple[str, ...] = ()
    ashby_companies: tuple[str, ...] = ()
    timeout_seconds: float = 20.0


class LLMSettings(BaseModel):
    """Scoring provider configuration.

    ``provider="none"`` is the default and is a real, supported mode — the
    pipeline stops after stage 7 and remains useful (ADR-0006). A first run
    should never require a signup.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: Literal["none", "gemini", "openai_compatible", "ollama", "manual"] = (
        "none"
    )
    api_key: str | None = Field(default=None, repr=False)
    """``repr=False`` so a key can never reach a log line or traceback through
    an accidental ``print(settings)``."""

    model: str | None = None
    base_url: str | None = None
    batch_size: int = Field(default=10, ge=1, le=50)
    max_jobs_per_run: int = Field(default=200, ge=1)
    """The cap that makes stage 8 bounded. Combined with pre-score ordering,
    exhausting it costs the tail of the ranking, not a random slice."""

    requests_per_minute: int = Field(default=15, ge=1)
    timeout_seconds: float = 120.0


class FilterSettings(BaseModel):
    """Defaults for stage 5. Permissive on purpose — a new user should see
    their listings, not an empty table caused by a filter they never set."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    exclude_ineligible: bool = True
    exclude_unknown_eligibility: bool = False
    max_age_days: int | None = 45
    min_hourly_rate_usd: Decimal | None = None
    require_tech_signal: bool = True
    excluded_title_phrases: tuple[str, ...] = ()
    required_title_phrases: tuple[str, ...] = ()


class Settings(BaseSettings):
    """The whole of Outpost's configuration.

    Sources, in increasing precedence: defaults, ``config.yml``, the ``.env``
    file, then real environment variables. Nested values use a double
    underscore — ``OUTPOST_LLM__PROVIDER=gemini``.
    """

    model_config = SettingsConfigDict(
        env_prefix="OUTPOST_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    config_dir: Path = Field(default_factory=default_config_dir)
    data_dir: Path = Field(default_factory=default_data_dir)

    sources: SourceSettings = Field(default_factory=SourceSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    filters: FilterSettings = Field(default_factory=FilterSettings)

    ui_host: str = "127.0.0.1"
    """Loopback by default, and it should stay there. Outpost has no
    authentication because it is local-first (ADR-0001); binding it to 0.0.0.0
    would expose an unauthenticated view of the user's job search to their
    network."""

    ui_port: int = 8420
    log_level: str = "INFO"
    verbose: bool = False

    @property
    def db_path(self) -> Path:
        return self.data_dir / "outpost.db"

    @property
    def profile_path(self) -> Path:
        return self.config_dir / "profile.yml"

    @property
    def rules_path(self) -> Path:
        return self.config_dir / "rules.yml"

    @property
    def resume_path(self) -> Path:
        return self.config_dir / "resume.txt"

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.yml"

    @property
    def export_dir(self) -> Path:
        return self.data_dir / "exports"
