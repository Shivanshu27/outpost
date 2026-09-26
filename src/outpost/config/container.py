"""The composition root.

Everything is constructed here, once, from validated settings, and passed
inward. This module and :mod:`outpost.config.settings` are the only places
permitted to read the environment or resolve user paths (ADR-0009).

Note what the container is *not*: it is never passed to a use-case. Use-cases
receive the ports they use. Handing the whole container downward would turn it
into a service locator and re-hide the dependencies this design exists to make
visible.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self

import structlog
import yaml

from outpost.adapters.clock import SystemClock
from outpost.adapters.http import HttpClient
from outpost.adapters.llm import LLMConfig, build_provider
from outpost.adapters.profile import load_profile
from outpost.adapters.rules_loader import load_rules
from outpost.adapters.sources import build_sources
from outpost.adapters.storage.sqlite import SqliteJobRepository
from outpost.adapters.verifier import HttpVerifier
from outpost.app.pipeline import PipelineDeps
from outpost.config.settings import Settings
from outpost.domain.filters import FilterCriteria
from outpost.domain.models import UserProfile
from outpost.domain.ports import Clock, JobSource, LLMProvider, Verifier
from outpost.domain.rules import CompiledRuleSet

__all__ = ["Container", "build_container", "configure_logging", "load_settings"]


def load_settings(config_dir: Path | None = None) -> Settings:
    """Build settings from defaults, ``config.yml``, ``.env`` and the
    environment, in increasing order of precedence.

    ``config.yml`` is read explicitly rather than through pydantic-settings so
    that a malformed file produces a clear error naming the file, instead of a
    validation error about a field the user never typed.
    """
    probe = Settings() if config_dir is None else Settings(config_dir=config_dir)
    config_file = probe.config_file

    overrides: dict[str, object] = {}
    if config_file.exists():
        try:
            loaded = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            msg = f"{config_file}: could not read config — {exc}"
            raise ValueError(msg) from exc
        if loaded is not None:
            if not isinstance(loaded, dict):
                msg = f"{config_file}: expected a mapping at the top level"
                raise ValueError(msg)
            overrides = loaded

    if config_dir is not None:
        overrides["config_dir"] = config_dir

    # Environment wins: pydantic-settings applies it over these values.
    return Settings(**overrides)  # type: ignore[arg-type]


def configure_logging(*, level: str = "INFO", verbose: bool = False) -> None:
    """Wire structlog.

    Console rendering when attached to a terminal, JSON otherwise — so piping
    a run into a file produces something greppable rather than ANSI escapes.
    """
    import sys

    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, level.upper(), logging.INFO),
        stream=sys.stderr,
    )

    # httpx logs every request at INFO. A run makes hundreds, which buries the
    # pipeline's own progress output in a wall of URLs — the user's actual
    # signal becomes unreadable. Raise these to WARNING unless verbose.
    for noisy in ("httpx", "httpcore", "hpack", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.DEBUG if verbose else logging.WARNING)
    renderer: structlog.types.Processor = (
        structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
        if sys.stderr.isatty()
        else structlog.processors.JSONRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.DEBUG if verbose else getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )


@dataclass(slots=True)
class Container:
    """Constructed ports, ready to be handed to use-cases."""

    settings: Settings
    repository: SqliteJobRepository
    sources: tuple[JobSource, ...]
    llm: LLMProvider
    verifier: Verifier
    clock: Clock
    ruleset: CompiledRuleSet
    profile: UserProfile
    http: HttpClient

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.repository.close()

    async def aclose(self) -> None:
        await self.http.aclose()

    def pipeline_deps(self) -> PipelineDeps:
        """Assemble the pipeline's dependencies.

        Note that this hands over individual ports, not ``self``.
        """
        settings = self.settings
        return PipelineDeps(
            repository=self.repository,
            sources=self.sources,
            clock=self.clock,
            ruleset=self.ruleset,
            profile=self.profile,
            filter_criteria=self.filter_criteria(),
            verifier=self.verifier,
            llm=self.llm,
            llm_batch_size=settings.llm.batch_size,
            max_llm_jobs=settings.llm.max_jobs_per_run,
        )

    def filter_criteria(self) -> FilterCriteria:
        """Translate settings and profile into stage-5 criteria.

        Tech keywords default to the user's own declared skills: the "is this
        even a software job?" gate should be calibrated to the person using it,
        not to a hardcoded list that would quietly drop unusual stacks.
        """
        f = self.settings.filters
        defaults = FilterCriteria()
        return FilterCriteria(
            exclude_ineligible=f.exclude_ineligible,
            exclude_unknown_eligibility=f.exclude_unknown_eligibility,
            excluded_title_phrases=(
                f.excluded_title_phrases or defaults.excluded_title_phrases
            ),
            required_title_phrases=f.required_title_phrases,
            max_age_days=f.max_age_days,
            min_hourly_rate_usd=(
                f.min_hourly_rate_usd
                if f.min_hourly_rate_usd is not None
                else self.profile.min_hourly_rate_usd
            ),
            contract_types=frozenset(self.profile.contract_types),
            require_tech_signal=f.require_tech_signal,
            tech_keywords=self.profile.skills,
        )


def build_container(settings: Settings) -> Container:
    """Construct everything from settings. The only wiring in the codebase."""
    configure_logging(level=settings.log_level, verbose=settings.verbose)

    profile = load_profile(settings.profile_path, settings.resume_path)
    ruleset = load_rules(settings.rules_path).compile()

    repository = SqliteJobRepository.open(settings.db_path)
    http = HttpClient(timeout=settings.sources.timeout_seconds)
    clock = SystemClock()

    sources = build_sources(
        names=settings.sources.enabled,
        client=http,
        ats_companies={
            "greenhouse": settings.sources.greenhouse_companies,
            "lever": settings.sources.lever_companies,
            "ashby": settings.sources.ashby_companies,
        },
    )

    llm = build_provider(
        LLMConfig(
            provider=settings.llm.provider,
            api_key=settings.llm.api_key,
            model=settings.llm.model,
            base_url=settings.llm.base_url,
            batch_size=settings.llm.batch_size,
            requests_per_minute=settings.llm.requests_per_minute,
            timeout_seconds=settings.llm.timeout_seconds,
            export_path=settings.export_dir / "scoring-batch.md",
        ),
        client=http.raw,
    )

    return Container(
        settings=settings,
        repository=repository,
        sources=tuple(sources),
        llm=llm,
        verifier=HttpVerifier(http, clock),
        clock=clock,
        ruleset=ruleset,
        profile=profile,
        http=http,
    )
