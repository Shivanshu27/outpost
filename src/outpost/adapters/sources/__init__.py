"""The source registry.

A dict of name to factory, and two functions over it. That is the whole plugin
system, and deliberately so: ADR-0008 rejected entry-point discovery because
adding a board should be one PR touching one new file and one line here, not a
separate distribution. The registry is an implementation detail; the protocol is
the contract, so this can be swapped for entry points later without any source
changing.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence

import structlog

from outpost.adapters.http import HttpClient
from outpost.adapters.sources.ats import AshbySource, GreenhouseSource, LeverSource
from outpost.adapters.sources.hackernews import HackerNewsSource
from outpost.adapters.sources.remoteok import RemoteOKSource
from outpost.adapters.sources.remotive import RemotiveSource
from outpost.adapters.sources.weworkremotely import WeWorkRemotelySource
from outpost.domain.ports import JobSource

__all__ = [
    "SOURCE_REGISTRY",
    "AshbySource",
    "GreenhouseSource",
    "HackerNewsSource",
    "LeverSource",
    "RemoteOKSource",
    "RemotiveSource",
    "SourceFactory",
    "WeWorkRemotelySource",
    "available_sources",
    "build_sources",
]

logger = structlog.get_logger(__name__)

SourceFactory = Callable[[HttpClient, Sequence[str]], JobSource]
"""Every factory takes the same two arguments so the caller needs no per-source
knowledge. Boards with a fixed endpoint ignore ``companies``; ATS sources are
meaningless without it."""


def _simple(build: Callable[[HttpClient], JobSource]) -> SourceFactory:
    """Adapt a whole-board source to the uniform factory signature."""

    def factory(client: HttpClient, companies: Sequence[str]) -> JobSource:
        return build(client)

    return factory


SOURCE_REGISTRY: dict[str, SourceFactory] = {
    RemoteOKSource.name: _simple(RemoteOKSource),
    RemotiveSource.name: _simple(RemotiveSource),
    WeWorkRemotelySource.name: _simple(WeWorkRemotelySource),
    HackerNewsSource.name: _simple(HackerNewsSource),
    GreenhouseSource.name: GreenhouseSource,
    LeverSource.name: LeverSource,
    AshbySource.name: AshbySource,
}

_ATS_SOURCES = frozenset({GreenhouseSource.name, LeverSource.name, AshbySource.name})


def available_sources() -> tuple[str, ...]:
    """Registered source names, sorted — for config validation and ``--help``."""
    return tuple(sorted(SOURCE_REGISTRY))


def build_sources(
    names: Iterable[str],
    client: HttpClient,
    ats_companies: Mapping[str, Sequence[str]] | None = None,
) -> list[JobSource]:
    """Instantiate the configured sources, sharing one HTTP client.

    The client is shared rather than one per source because throttling is
    per-host, and two sources hitting the same ATS host are one load from that
    host's point of view (see ``adapters/http.py``).

    Raises:
        ValueError: on an unknown name. Failing at construction beats
            discovering a typo'd source as a missing board three runs later.
    """
    companies = ats_companies or {}
    sources: list[JobSource] = []

    for name in names:
        factory = SOURCE_REGISTRY.get(name)
        if factory is None:
            known = ", ".join(available_sources())
            msg = f"unknown source {name!r}; available: {known}"
            raise ValueError(msg)

        slugs = tuple(companies.get(name, ()))
        if name in _ATS_SOURCES and not slugs:
            # Not an error — an ATS source with no companies is a configuration
            # the user may be mid-way through writing. It is logged rather than
            # built, because a source that can only ever return zero jobs would
            # read as a dead board in the run report.
            logger.warning("source.skipped_no_companies", source=name)
            continue

        sources.append(factory(client, slugs))

    return sources
