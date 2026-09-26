"""Source adapter tests.

Every source is exercised against a captured fixture rather than the live
endpoint, so the suite is deterministic and runs offline (ADR-0008). The
trade-off is explicit in that ADR: a fixture can drift from the live board, and
the mitigation is the opt-in ``live`` marker plus per-source yield tracking —
not a network call in here.

Four properties are checked for every source, because the ways a scraper rots
are boringly uniform: it parses the real shape, it survives an empty board, it
survives a record that is missing fields, and it declares a rate limit.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from outpost.adapters import http as http_module
from outpost.adapters.http import HttpClient
from outpost.adapters.sources import (
    SOURCE_REGISTRY,
    AshbySource,
    GreenhouseSource,
    HackerNewsSource,
    LeverSource,
    RemoteOKSource,
    RemotiveSource,
    WeWorkRemotelySource,
    available_sources,
    build_sources,
)
from outpost.adapters.sources.hackernews import _SEARCH_URL as _HN_SEARCH_URL
from outpost.domain.models import RawJob
from outpost.domain.ports import JobSource, SourceError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

REMOTEOK_URL = "https://remoteok.com/api"
REMOTIVE_URL = "https://remotive.com/api/remote-jobs"
WWR_URL = "https://weworkremotely.com/categories/remote-programming-jobs.rss"
# Imported rather than restated so a URL change cannot silently leave these
# mocks pointing at a dead endpoint while the tests still pass. The URL's own
# correctness is asserted separately, in
# test_hackernews_searches_by_date_and_author_not_relevance.
HN_SEARCH_URL = _HN_SEARCH_URL
HN_THREAD_URL = (
    "https://hn.algolia.com/api/v1/search_by_date"
    "?tags=comment,story_45400001&hitsPerPage=1000"
)
HN_STORY_ID = "45400001"


def load_json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def load_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def greenhouse_url(slug: str) -> str:
    return f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"


def lever_url(slug: str) -> str:
    return f"https://api.lever.co/v0/postings/{slug}?mode=json"


def ashby_url(slug: str) -> str:
    return (
        f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    )


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove the per-host wait for the duration of the suite.

    The throttle is real behaviour and is tested where it lives; here it would
    only add several seconds of real sleeping to every multi-request test, which
    is the fastest way to make a test suite stop being run.
    """

    async def _no_wait(self: Any, host: str, min_interval: float) -> None:
        return None

    monkeypatch.setattr(http_module._Throttle, "wait", _no_wait)


@pytest.fixture
async def client() -> AsyncIterator[HttpClient]:
    async with HttpClient() as http_client:
        yield http_client


@pytest.fixture
def mock_router() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


def json_response(payload: Any) -> httpx.Response:
    return httpx.Response(200, json=payload)


def text_response(body: str) -> httpx.Response:
    return httpx.Response(200, text=body)


# --------------------------------------------------------------------------
# RemoteOK
# --------------------------------------------------------------------------


async def test_remoteok_parses_fixture(
    client: HttpClient, mock_router: respx.MockRouter
) -> None:
    mock_router.get(REMOTEOK_URL).mock(json_response(load_json("remoteok.json")))

    jobs = await RemoteOKSource(client).fetch()

    assert [job.external_id for job in jobs] == ["1017421", "1017455"]
    first = jobs[0]
    assert first.source == "remoteok"
    assert first.title == "Senior Backend Engineer"
    assert first.company == "Acme Systems"
    assert first.location_text == "Worldwide"
    assert first.compensation_text == "$100,000 - $140,000"
    assert first.posted_at_text == "2025-09-20T00:00:00+00:00"
    assert first.tags == ("backend", "python", "postgres")
    # Raw text is handed on untouched — parsing it is normalisation's job.
    assert first.description is not None
    assert first.description.startswith("<p>")
    assert first.raw["slug"] == "acme-senior-backend-engineer"


async def test_remoteok_skips_the_legal_notice_element(
    client: HttpClient, mock_router: respx.MockRouter
) -> None:
    mock_router.get(REMOTEOK_URL).mock(
        json_response([{"legal": "terms"}, {"legal": "more terms"}])
    )

    assert await RemoteOKSource(client).fetch() == []


async def test_remoteok_empty_payload(
    client: HttpClient, mock_router: respx.MockRouter
) -> None:
    mock_router.get(REMOTEOK_URL).mock(json_response([]))

    assert await RemoteOKSource(client).fetch() == []


async def test_remoteok_survives_malformed_records(
    client: HttpClient, mock_router: respx.MockRouter
) -> None:
    mock_router.get(REMOTEOK_URL).mock(
        json_response(load_json("remoteok_malformed.json"))
    )

    jobs = await RemoteOKSource(client).fetch()

    # The URL-less record and the bare string are dropped; the one usable
    # record survives with empty fields rather than invented ones.
    assert len(jobs) == 1
    assert jobs[0].title is None
    assert jobs[0].company is None
    assert jobs[0].tags == ()
    assert jobs[0].compensation_text == "120000"


async def test_remoteok_rejects_a_non_array_payload(
    client: HttpClient, mock_router: respx.MockRouter
) -> None:
    mock_router.get(REMOTEOK_URL).mock(json_response({"jobs": []}))

    with pytest.raises(SourceError):
        await RemoteOKSource(client).fetch()


# --------------------------------------------------------------------------
# Remotive
# --------------------------------------------------------------------------


async def test_remotive_parses_fixture(
    client: HttpClient, mock_router: respx.MockRouter
) -> None:
    mock_router.get(REMOTIVE_URL).mock(json_response(load_json("remotive.json")))

    jobs = await RemotiveSource(client).fetch()

    assert len(jobs) == 2
    first = jobs[0]
    assert first.source == "remotive"
    assert first.external_id == "1902345"
    assert first.title == "Senior Python Engineer"
    assert first.company == "Initech"
    assert first.location_text == "Worldwide"
    assert first.contract_text == "full_time"
    assert first.posted_at_text == "2025-09-18T11:04:52"
    assert first.tags == ("python", "django", "aws")
    assert jobs[1].compensation_text is None


async def test_remotive_empty_payload(
    client: HttpClient, mock_router: respx.MockRouter
) -> None:
    mock_router.get(REMOTIVE_URL).mock(json_response({"jobs": [], "job-count": 0}))

    assert await RemotiveSource(client).fetch() == []


async def test_remotive_survives_malformed_records(
    client: HttpClient, mock_router: respx.MockRouter
) -> None:
    mock_router.get(REMOTIVE_URL).mock(
        json_response(load_json("remotive_malformed.json"))
    )

    jobs = await RemotiveSource(client).fetch()

    assert len(jobs) == 1
    assert jobs[0].external_id == "1902999"
    assert jobs[0].title is None


async def test_remotive_missing_jobs_key_is_an_error_not_an_empty_board(
    client: HttpClient, mock_router: respx.MockRouter
) -> None:
    mock_router.get(REMOTIVE_URL).mock(json_response({"job-count": 0}))

    with pytest.raises(SourceError):
        await RemotiveSource(client).fetch()


# --------------------------------------------------------------------------
# We Work Remotely
# --------------------------------------------------------------------------


async def test_wwr_parses_fixture(
    client: HttpClient, mock_router: respx.MockRouter
) -> None:
    mock_router.get(WWR_URL).mock(text_response(load_text("weworkremotely.xml")))

    jobs = await WeWorkRemotelySource(client).fetch()

    assert len(jobs) == 3
    assert jobs[0].company == "Hooli"
    assert jobs[0].title == "Senior Rails Engineer"
    assert jobs[0].location_text == "Anywhere in the World"
    assert jobs[0].contract_text == "Full-Time"
    assert jobs[0].posted_at_text == "Fri, 19 Sep 2025 14:12:03 +0000"


async def test_wwr_splits_on_the_first_colon_only(
    client: HttpClient, mock_router: respx.MockRouter
) -> None:
    mock_router.get(WWR_URL).mock(text_response(load_text("weworkremotely.xml")))

    jobs = await WeWorkRemotelySource(client).fetch()

    assert jobs[1].company == "Stark Industries"
    assert jobs[1].title == "Staff Engineer: Distributed Systems"


async def test_wwr_headline_without_a_colon_is_all_title(
    client: HttpClient, mock_router: respx.MockRouter
) -> None:
    mock_router.get(WWR_URL).mock(text_response(load_text("weworkremotely.xml")))

    jobs = await WeWorkRemotelySource(client).fetch()

    assert jobs[2].company is None
    assert jobs[2].title == "Frontend Developer"
    assert jobs[2].location_text is None


async def test_wwr_empty_feed(
    client: HttpClient, mock_router: respx.MockRouter
) -> None:
    mock_router.get(WWR_URL).mock(text_response(load_text("weworkremotely_empty.xml")))

    assert await WeWorkRemotelySource(client).fetch() == []


async def test_wwr_survives_incomplete_items(
    client: HttpClient, mock_router: respx.MockRouter
) -> None:
    mock_router.get(WWR_URL).mock(
        text_response(load_text("weworkremotely_malformed.xml"))
    )

    jobs = await WeWorkRemotelySource(client).fetch()

    assert len(jobs) == 1
    assert jobs[0].title is None
    assert jobs[0].url.endswith("soylent-data-engineer")


async def test_wwr_unparseable_xml_raises(
    client: HttpClient, mock_router: respx.MockRouter
) -> None:
    mock_router.get(WWR_URL).mock(text_response("<rss><channel>"))

    with pytest.raises(SourceError):
        await WeWorkRemotelySource(client).fetch()


# --------------------------------------------------------------------------
# Hacker News
# --------------------------------------------------------------------------


def _mock_hn(
    router: respx.MockRouter, *, comments: Any, search: Any | None = None
) -> None:
    stories = search if search is not None else load_json("hn_story_search.json")
    router.get(HN_SEARCH_URL).mock(json_response(stories))
    router.get(HN_THREAD_URL).mock(json_response(comments))


async def test_hackernews_parses_fixture(
    client: HttpClient, mock_router: respx.MockRouter
) -> None:
    _mock_hn(mock_router, comments=load_json("hn_comments.json"))

    jobs = await HackerNewsSource(client).fetch()

    assert [job.external_id for job in jobs] == ["45400111", "45400222"]
    first = jobs[0]
    assert first.source == "hackernews"
    assert first.url == "https://news.ycombinator.com/item?id=45400111"
    assert first.title == (
        "Acme Systems | Senior Backend Engineer | REMOTE (EU timezones) | EUR 80-110k"
    )
    # The title line is a guess; the company is left unset rather than guessed
    # a second time from the same line.
    assert first.company is None
    assert first.description is not None
    assert "ingestion end to end" in first.description
    assert first.posted_at_text == "2025-09-01T15:22:40.000Z"


async def test_hackernews_picks_the_newest_matching_story(
    client: HttpClient, mock_router: respx.MockRouter
) -> None:
    """The August thread outranks September's in the fixture's hit order."""
    _mock_hn(mock_router, comments={"hits": []})

    await HackerNewsSource(client).fetch()

    assert mock_router.get(HN_THREAD_URL).called


async def test_hackernews_skips_replies_and_short_comments(
    client: HttpClient, mock_router: respx.MockRouter
) -> None:
    _mock_hn(mock_router, comments=load_json("hn_comments.json"))

    jobs = await HackerNewsSource(client).fetch()

    ids = {job.external_id for job in jobs}
    assert "45400333" not in ids  # a reply, not a posting
    assert "45400444" not in ids  # too short to be an ad


async def test_hackernews_empty_thread(
    client: HttpClient, mock_router: respx.MockRouter
) -> None:
    _mock_hn(mock_router, comments={"hits": [], "nbHits": 0})

    assert await HackerNewsSource(client).fetch() == []


async def test_hackernews_survives_malformed_comments(
    client: HttpClient, mock_router: respx.MockRouter
) -> None:
    _mock_hn(mock_router, comments=load_json("hn_comments_malformed.json"))

    assert await HackerNewsSource(client).fetch() == []


async def test_hackernews_raises_when_no_thread_is_found(
    client: HttpClient, mock_router: respx.MockRouter
) -> None:
    _mock_hn(
        mock_router,
        comments={"hits": []},
        search={"hits": [{"objectID": "1", "title": "Ask HN: Who wants to be hired?"}]},
    )

    with pytest.raises(SourceError):
        await HackerNewsSource(client).fetch()


# --------------------------------------------------------------------------
# ATS sources
# --------------------------------------------------------------------------


async def test_greenhouse_parses_fixture(
    client: HttpClient, mock_router: respx.MockRouter
) -> None:
    mock_router.get(greenhouse_url("examplecorp")).mock(
        json_response(load_json("greenhouse.json"))
    )

    jobs = await GreenhouseSource(client, ["examplecorp"]).fetch()

    assert len(jobs) == 2
    first = jobs[0]
    assert first.source == "greenhouse"
    assert first.external_id == "4551221"
    assert first.title == "Backend Engineer, Payments"
    assert first.company == "examplecorp"
    assert first.location_text == "Remote - Americas"
    assert first.posted_at_text == "2025-09-17T12:31:04-04:00"
    # Greenhouse double-escapes its HTML; it is decoded once so the shared
    # HTML parser sees the same input it gets from every other source.
    assert first.description is not None
    assert first.description.startswith("<p>We are looking for")


async def test_lever_parses_fixture(
    client: HttpClient, mock_router: respx.MockRouter
) -> None:
    mock_router.get(lever_url("examplelabs")).mock(
        json_response(load_json("lever.json"))
    )

    jobs = await LeverSource(client, ["examplelabs"]).fetch()

    assert len(jobs) == 2
    first = jobs[0]
    assert first.title == "Senior Software Engineer, Data"
    assert first.location_text == "Remote - Worldwide"
    assert first.contract_text == "Full-time"
    # Millisecond epoch decoded here, because the shared parser reads bare
    # integers as seconds.
    assert first.posted_at is not None
    assert first.posted_at.tzinfo is not None
    assert first.posted_at.year == 2025


async def test_ashby_parses_fixture(
    client: HttpClient, mock_router: respx.MockRouter
) -> None:
    mock_router.get(ashby_url("examplehq")).mock(json_response(load_json("ashby.json")))

    jobs = await AshbySource(client, ["examplehq"]).fetch()

    assert len(jobs) == 2
    first = jobs[0]
    assert first.title == "Staff Engineer, Search"
    assert first.company == "Example HQ"
    assert first.location_text == "Remote - Worldwide"
    assert first.contract_text == "FullTime"
    expected = load_json("ashby.json")["jobs"][0]["compensationTierSummary"]
    assert first.compensation_text == expected
    assert first.posted_at_text == "2025-09-16T10:04:00.000Z"


@pytest.mark.parametrize(
    ("build", "url", "payload"),
    [
        (GreenhouseSource, greenhouse_url("examplecorp"), {"jobs": []}),
        (LeverSource, lever_url("examplecorp"), []),
        (AshbySource, ashby_url("examplecorp"), {"jobs": []}),
    ],
)
async def test_ats_empty_board(
    client: HttpClient,
    mock_router: respx.MockRouter,
    build: Any,
    url: str,
    payload: Any,
) -> None:
    mock_router.get(url).mock(json_response(payload))

    assert await build(client, ["examplecorp"]).fetch() == []


@pytest.mark.parametrize(
    ("build", "url", "fixture"),
    [
        (GreenhouseSource, greenhouse_url("examplecorp"), "greenhouse_malformed.json"),
        (LeverSource, lever_url("examplecorp"), "lever_malformed.json"),
        (AshbySource, ashby_url("examplecorp"), "ashby_malformed.json"),
    ],
)
async def test_ats_survives_malformed_records(
    client: HttpClient,
    mock_router: respx.MockRouter,
    build: Any,
    url: str,
    fixture: str,
) -> None:
    mock_router.get(url).mock(json_response(load_json(fixture)))

    jobs = await build(client, ["examplecorp"]).fetch()

    assert len(jobs) == 1
    assert jobs[0].title is None


async def test_ats_one_failing_company_does_not_lose_the_others(
    client: HttpClient, mock_router: respx.MockRouter
) -> None:
    mock_router.get(greenhouse_url("gone")).mock(httpx.Response(404))
    mock_router.get(greenhouse_url("examplecorp")).mock(
        json_response(load_json("greenhouse.json"))
    )

    jobs = await GreenhouseSource(client, ["gone", "examplecorp"]).fetch()

    assert len(jobs) == 2


async def test_ats_all_companies_failing_raises_rather_than_reporting_zero(
    client: HttpClient, mock_router: respx.MockRouter
) -> None:
    mock_router.get(greenhouse_url("gone")).mock(httpx.Response(404))
    mock_router.get(greenhouse_url("also-gone")).mock(httpx.Response(410))

    with pytest.raises(SourceError) as excinfo:
        await GreenhouseSource(client, ["gone", "also-gone"]).fetch()

    assert "gone" in str(excinfo.value)
    assert "also-gone" in str(excinfo.value)


async def test_ats_with_no_companies_fetches_nothing(client: HttpClient) -> None:
    assert await GreenhouseSource(client, []).fetch() == []


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------


def test_available_sources_is_sorted_and_complete() -> None:
    assert available_sources() == tuple(sorted(SOURCE_REGISTRY))
    assert set(available_sources()) == {
        "ashby",
        "greenhouse",
        "hackernews",
        "lever",
        "remoteok",
        "remotive",
        "weworkremotely",
    }


def test_build_sources_builds_what_was_asked_for(client: HttpClient) -> None:
    sources = build_sources(
        ["remoteok", "greenhouse"], client, {"greenhouse": ["examplecorp"]}
    )

    assert [source.name for source in sources] == ["remoteok", "greenhouse"]


def test_build_sources_rejects_an_unknown_name(client: HttpClient) -> None:
    with pytest.raises(ValueError, match="unknown source"):
        build_sources(["remotoek"], client)


def test_build_sources_skips_an_ats_source_with_no_companies(
    client: HttpClient,
) -> None:
    assert build_sources(["greenhouse"], client) == []


# --------------------------------------------------------------------------
# Contract — applied uniformly to every registered source (ADR-0008)
# --------------------------------------------------------------------------


@pytest.fixture(params=sorted(SOURCE_REGISTRY))
def registered_source(request: pytest.FixtureRequest, client: HttpClient) -> JobSource:
    factory = SOURCE_REGISTRY[request.param]
    return factory(client, ["examplecorp"])


def test_contract_source_satisfies_the_protocol(registered_source: JobSource) -> None:
    assert isinstance(registered_source, JobSource)


def test_contract_source_name_matches_its_registry_key(
    registered_source: JobSource,
) -> None:
    assert registered_source.name in SOURCE_REGISTRY
    assert SOURCE_REGISTRY[registered_source.name] is not None


def test_contract_source_declares_a_usable_rate_limit(
    registered_source: JobSource,
) -> None:
    limit = registered_source.rate_limit
    assert limit.requests_per_minute > 0
    assert limit.concurrency > 0
    assert limit.min_interval_seconds > 0


async def test_contract_fetch_returns_raw_jobs_stamped_with_the_source_name(
    registered_source: JobSource, mock_router: respx.MockRouter
) -> None:
    """Whatever a source returns must be attributable to it.

    Routed against every fixture at once so this one test covers all sources
    without knowing which is which.
    """
    mock_router.get(REMOTEOK_URL).mock(json_response(load_json("remoteok.json")))
    mock_router.get(REMOTIVE_URL).mock(json_response(load_json("remotive.json")))
    mock_router.get(WWR_URL).mock(text_response(load_text("weworkremotely.xml")))
    _mock_hn(mock_router, comments=load_json("hn_comments.json"))
    mock_router.get(greenhouse_url("examplecorp")).mock(
        json_response(load_json("greenhouse.json"))
    )
    mock_router.get(lever_url("examplecorp")).mock(
        json_response(load_json("lever.json"))
    )
    mock_router.get(ashby_url("examplecorp")).mock(
        json_response(load_json("ashby.json"))
    )

    jobs = await registered_source.fetch()

    assert jobs, f"{registered_source.name} parsed nothing from its fixture"
    for job in jobs:
        assert isinstance(job, RawJob)
        assert job.source == registered_source.name
        assert job.url.startswith("https://")


def test_hackernews_searches_by_date_and_author_not_relevance() -> None:
    """Regression: a live run returned the April 2020 thread.

    The original query was ``/search?query=Ask HN Who is hiring``, which
    Algolia sorts by *relevance* — and relevance for that phrase means the
    most-upvoted threads of all time. Every hit on the first page was years
    old, so re-sorting them by date still yielded 2020, and the pipeline
    ingested 476 six-year-old listings.

    Both properties below are load-bearing: ``search_by_date`` sorts by
    recency, and ``author_whoishiring`` scopes to the bot account that posts
    these threads monthly.
    """
    assert "search_by_date" in _HN_SEARCH_URL
    assert "author_whoishiring" in _HN_SEARCH_URL
    assert "query=" not in _HN_SEARCH_URL
