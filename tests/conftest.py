"""Shared fixtures for the core suite.

The core (domain, storage, pipeline, API) is deterministic by construction: the
clock is injected (ADR-0009), rules are data (ADR-0007) and storage is a file.
These fixtures supply the three, so a test states the *situation* it cares about
and nothing else.

Nothing here touches the network, the real clock, the user's home directory or
``os.environ``.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from outpost.adapters.clock import FixedClock
from outpost.adapters.rules_loader import load_default_ruleset
from outpost.adapters.storage.sqlite import SqliteJobRepository
from outpost.domain.models import ContractType, Job, RawJob, UserProfile
from outpost.domain.normalisation import normalise
from outpost.domain.rules import CompiledRuleSet

FROZEN_NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
"""A fixed instant in March: outside both US and EU DST, so timezone-overlap
arithmetic in the predicate tests is stated against standard time rather than
against whatever the real date happens to be."""

MakeJob = Callable[..., Job]


@pytest.fixture
def frozen_clock() -> FixedClock:
    """A clock that does not move, so ``first_seen``/``last_seen`` and recency
    are assertable without sleeping."""
    return FixedClock(FROZEN_NOW)


@pytest.fixture
def now(frozen_clock: FixedClock) -> datetime:
    return frozen_clock.now()


@pytest.fixture
def profile() -> UserProfile:
    """A realistic user: India, IST, not US-authorised.

    The country and timezone are the two fields that decide most verdicts, so
    tests that vary the user vary *this* fixture rather than the ruleset — that
    is the whole point of ``unless_profile_matches`` (ADR-0007).
    """
    return UserProfile(
        country="IN",
        country_name="India",
        timezone="Asia/Kolkata",
        work_authorisation=(),
        contract_types=(ContractType.FULL_TIME, ContractType.CONTRACT),
        currencies=("USD", "EUR", "GBP"),
        min_hourly_rate_usd=None,
        skills=("Python", "TypeScript", "PostgreSQL", "AWS", "Docker"),
        titles=("Senior Software Engineer", "Backend Engineer", "Platform Engineer"),
        seniority="senior",
        resume_text="Senior backend engineer. Python, PostgreSQL, AWS, Docker.",
    )


@pytest.fixture
def us_profile(profile: UserProfile) -> UserProfile:
    """The same user, relocated. Used to prove one ruleset serves both."""
    return profile.model_copy(
        update={
            "country": "US",
            "country_name": "United States",
            "timezone": "America/Los_Angeles",
            "work_authorisation": ("US",),
        }
    )


@pytest.fixture
def ruleset() -> CompiledRuleSet:
    """The ruleset Outpost actually ships, compiled.

    Tested as shipped rather than as a hand-written miniature: the default rules
    are the product, and a phrase regression in them is exactly what these tests
    are for.
    """
    return load_default_ruleset().compile()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "db" / "outpost.db"


@pytest.fixture
def repo(db_path: Path) -> Iterator[SqliteJobRepository]:
    """A real SQLite repository on a throwaway file.

    A real database rather than a mock, deliberately (ADR-0004): the guarantee
    under test lives in an ``ON CONFLICT`` clause, and a mock would assert our
    belief about that clause instead of the clause.
    """
    repository = SqliteJobRepository.open(db_path)
    try:
        yield repository
    finally:
        repository.close()


_DEFAULT_DESCRIPTION = (
    "We are hiring a backend engineer to work on our Python and PostgreSQL "
    "platform. You will deploy to AWS using Docker."
)


@pytest.fixture
def make_job(now: datetime) -> MakeJob:
    """Build a ``Job`` the way production does — through ``normalise()``.

    Constructing ``Job`` directly in tests would let a test assert against a
    shape normalisation never produces. Any ``RawJob`` field may be overridden;
    any remaining keyword is applied to the normalised job, which is how a test
    sets derived or user-owned state (``status``, ``eligibility``, ``match``).
    """
    counter = itertools.count(1)
    raw_fields = set(RawJob.model_fields)

    def _make(**overrides: Any) -> Job:
        first_seen_at = overrides.pop("first_seen_at", None)
        raw_kwargs: dict[str, Any] = {
            "source": "testboard",
            "url": f"https://jobs.example.com/{next(counter)}",
            "title": "Senior Backend Engineer",
            "company": "Acme Systems",
            "description": _DEFAULT_DESCRIPTION,
            "location_text": "Remote",
        }
        for key in list(overrides):
            if key in raw_fields:
                raw_kwargs[key] = overrides.pop(key)

        job = normalise(RawJob(**raw_kwargs), now=now, first_seen_at=first_seen_at)
        if job is None:  # pragma: no cover — a fixture that cannot build is a bug
            msg = f"make_job produced an unusable listing: {raw_kwargs}"
            raise AssertionError(msg)
        return job.model_copy(update=overrides) if overrides else job

    return _make


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """A throwaway config directory holding a profile Outpost can load.

    Written to ``tmp_path`` rather than monkeypatched into the real one: the
    config directory is resolved from settings, and pointing settings at a
    temporary path is the supported way to isolate a run (ADR-0009).
    """
    directory = tmp_path / "config"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "profile.yml").write_text(
        "country: IN\n"
        "country_name: India\n"
        "timezone: Asia/Kolkata\n"
        "contract_types: [full_time, contract]\n"
        "currencies: [USD, EUR, GBP]\n"
        "titles: [Senior Software Engineer, Backend Engineer]\n"
        "skills: [Python, TypeScript, PostgreSQL, AWS, Docker]\n",
        encoding="utf-8",
    )
    return directory


@pytest.fixture(autouse=True)
def _no_ambient_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip ``OUTPOST_*`` from the environment for every test.

    A developer with ``OUTPOST_LLM__API_KEY`` exported must not get a different
    test run from CI — and must certainly not have a real key picked up by a
    suite that is supposed to make no network calls.
    """
    import os

    for name in [k for k in os.environ if k.startswith("OUTPOST_")]:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def money() -> Callable[[str], Decimal]:
    return Decimal
