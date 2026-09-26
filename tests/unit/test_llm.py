"""Unit tests for the LLM adapters.

No network: every HTTP interaction is mocked with ``respx``. No API keys, real
or otherwise — the strings below are obvious placeholders.

The emphasis is on the failure modes, because the happy path is the one that
gets exercised by hand anyway. What does not is: a model that fenced its JSON, a
model that skipped a job, a model that returned 1000 as a score, and a provider
that keeps saying "slow down".
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from outpost.adapters.llm import (
    PROVIDER_REGISTRY,
    LLMConfig,
    available_providers,
    build_provider,
)
from outpost.adapters.llm.base import (
    BatchingLLMProvider,
    MalformedResponse,
    RateLimiter,
    extract_json,
    load_prompt,
    parse_batch_response,
    render_scoring_prompt,
)
from outpost.adapters.llm.gemini import GeminiProvider
from outpost.adapters.llm.manual import ManualProvider
from outpost.adapters.llm.null import NullProvider
from outpost.adapters.llm.ollama import OllamaProvider
from outpost.adapters.llm.openai_compatible import OpenAICompatibleProvider
from outpost.domain.models import MatchResult, ScoringRequest
from outpost.domain.ports import ProviderUnavailable, QuotaExhausted

PLACEHOLDER_KEY = "not-a-real-key"

GEMINI_HOST = "generativelanguage.googleapis.com"
ROUTER_URL = "https://router.example/v1"
OLLAMA_URL = "http://localhost:11434"


# --------------------------------------------------------------------------
# Fixtures and helpers
# --------------------------------------------------------------------------


def make_requests(count: int) -> list[ScoringRequest]:
    """A batch whose job ids are all distinct.

    Distinctness is load-bearing, not cosmetic: the contract these tests guard
    is that every result is keyed to the job it was scored for, and a batch
    with repeated ids could not tell a correct mapping from a shifted one.
    """
    return [
        ScoringRequest(
            job_id=f"job-{i}",
            title=f"Backend Engineer {i}",
            company="Example Ltd",
            description="Python, Postgres, async.",
            location_text="Remote",
        )
        for i in range(1, count + 1)
    ]


def results_json(*entries: dict[str, Any]) -> str:
    return json.dumps({"results": list(entries)})


def entry(index: int, score: int = 80) -> dict[str, Any]:
    return {
        "index": index,
        "score": score,
        "reason": "Stack matches closely.",
        "gaps": ["no Kubernetes"],
    }


class SleepSpy:
    """Records requested delays instead of waiting for them.

    The retry policy is tested by what it *asks* to wait, not by making the
    suite take seventeen seconds to find out.
    """

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def gemini_payload(text: str) -> dict[str, Any]:
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def openai_payload(text: str) -> dict[str, Any]:
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def ollama_payload(text: str) -> dict[str, Any]:
    return {"model": "llama3.2", "response": text, "done": True}


GEMINI_429_BODY: dict[str, Any] = {
    "error": {
        "code": 429,
        "message": "Resource has been exhausted",
        "status": "RESOURCE_EXHAUSTED",
        "details": [
            {
                "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                "violations": [{"quotaId": "GenerateRequestsPerMinutePerProject"}],
            },
            {
                "@type": "type.googleapis.com/google.rpc.RetryInfo",
                "retryDelay": "17s",
            },
        ],
    }
}

GEMINI_DAILY_BODY: dict[str, Any] = {
    "error": {
        "code": 429,
        "message": "Quota exceeded for quota metric 'Generate requests per day'",
        "status": "RESOURCE_EXHAUSTED",
        "details": [
            {
                "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                "violations": [{"quotaId": "GenerateRequestsPerDayPerProject"}],
            }
        ],
    }
}


@pytest.fixture
def sleeper() -> SleepSpy:
    return SleepSpy()


def gemini(sleeper: SleepSpy, **kwargs: Any) -> GeminiProvider:
    return GeminiProvider(api_key=PLACEHOLDER_KEY, sleep=sleeper, **kwargs)


def router(sleeper: SleepSpy, **kwargs: Any) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        base_url=ROUTER_URL,
        model="some/model",
        api_key=PLACEHOLDER_KEY,
        sleep=sleeper,
        **kwargs,
    )


def ollama(**kwargs: Any) -> OllamaProvider:
    return OllamaProvider(base_url=OLLAMA_URL, **kwargs)


# --------------------------------------------------------------------------
# Prompt resource
# --------------------------------------------------------------------------


def test_prompt_is_packaged_data_not_code() -> None:
    prompt = load_prompt()
    assert "{{profile}}" in prompt
    assert "{{jobs}}" in prompt
    # The rubric must be calibrated, not vibes — anchors are what make scores
    # comparable between runs.
    assert "90-100" in prompt
    assert "0-19" in prompt


def test_render_substitutes_profile_and_numbers_jobs() -> None:
    rendered = render_scoring_prompt(make_requests(3), "Ten years of Python.")
    assert "Ten years of Python." in rendered
    assert "{{profile}}" not in rendered
    assert "{{jobs}}" not in rendered
    for i in (1, 2, 3):
        assert f"### {i}. Backend Engineer {i}" in rendered
    # The JSON example must survive substitution intact.
    assert '{"results":' in rendered


def test_render_truncates_a_runaway_description() -> None:
    request = ScoringRequest(
        job_id="j",
        title="T",
        company=None,
        description="x" * 10_000,
        location_text=None,
    )
    rendered = render_scoring_prompt([request], "profile")
    assert "[truncated]" in rendered
    assert len(rendered) < 10_000


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "wrapped",
    [
        '```json\n{"results": [{"index": 1, "score": 70, "reason": "ok"}]}\n```',
        '```\n{"results": [{"index": 1, "score": 70, "reason": "ok"}]}\n```',
        'Here you go:\n{"results": [{"index": 1, "score": 70, "reason": "ok"}]}',
    ],
)
def test_extract_json_survives_how_models_actually_reply(wrapped: str) -> None:
    assert json.loads(extract_json(wrapped))["results"][0]["score"] == 70


def test_parse_happy_path_builds_match_results() -> None:
    requests = make_requests(2)
    results = parse_batch_response(
        results_json(entry(1, 91), entry(2, 44)), requests, "test"
    )
    assert results["job-1"].score == 91
    assert results["job-2"].score == 44
    assert all(isinstance(r, MatchResult) for r in results.values())
    # Ordered by batch index, so iteration order is reproducible.
    assert list(results) == ["job-1", "job-2"]
    assert results["job-1"].provider == "test"
    assert results["job-1"].gaps == ("no Kubernetes",)
    assert results["job-1"].scored_at is not None
    assert results["job-1"].scored_at.tzinfo is not None


def test_parse_omits_a_missing_index_rather_than_inventing_one() -> None:
    """The port is explicit: a provider that cannot score an item omits it.

    And the survivors must still name their own jobs: with a positional
    contract, dropping index 2 slid index 3's score onto job-2.
    """
    requests = make_requests(3)
    results = parse_batch_response(
        results_json(entry(1, 90), entry(3, 30)), requests, "test"
    )
    assert {job_id: r.score for job_id, r in results.items()} == {
        "job-1": 90,
        "job-3": 30,
    }
    assert "job-2" not in results
    assert len(results) == 2


def test_omitted_entry_does_not_shift_scores_onto_wrong_jobs() -> None:
    """The regression the keyed contract exists to prevent.

    A model that skips index 2 of a four-job batch used to produce a list whose
    second element was index 3's score — so job-2 was reported with job-3's
    judgement and nothing downstream could tell. Keyed by job id, the skipped
    job is simply absent and every surviving score still names its own listing.
    """
    requests = make_requests(4)
    results = parse_batch_response(
        results_json(entry(1, 91), entry(3, 12)), requests, "test"
    )

    assert results[requests[0].job_id].score == 91
    assert results[requests[2].job_id].score == 12
    assert requests[1].job_id not in results
    assert requests[3].job_id not in results
    assert set(results) == {"job-1", "job-3"}


@pytest.mark.parametrize(("raw", "expected"), [(1000, 100), (-40, 0), (100, 100)])
def test_parse_clamps_an_out_of_range_score(raw: int, expected: int) -> None:
    results = parse_batch_response(
        results_json(entry(1, raw)), make_requests(1), "test"
    )
    assert results["job-1"].score == expected


def test_parse_drops_an_entry_whose_index_is_not_in_the_batch() -> None:
    """A score attached to the wrong listing is worse than no score."""
    results = parse_batch_response(
        results_json(entry(1), entry(9)), make_requests(2), "test"
    )
    assert set(results) == {"job-1"}


def test_parse_keeps_the_first_of_a_duplicated_index() -> None:
    results = parse_batch_response(
        results_json(entry(1, 90), entry(1, 10)), make_requests(1), "test"
    )
    assert {job_id: r.score for job_id, r in results.items()} == {"job-1": 90}


@pytest.mark.parametrize(
    "raw",
    ["not json at all", "", '{"oops": []}', '{"results": "nope"}'],
)
def test_parse_raises_a_typed_error_on_malformed_json(raw: str) -> None:
    with pytest.raises(MalformedResponse):
        parse_batch_response(raw, make_requests(1), "test")


def test_parse_accepts_the_numeric_shapes_models_emit() -> None:
    entries = [
        {"index": 1, "score": 80.0, "reason": "a"},
        {"index": "2", "score": "65", "reason": "b"},
    ]
    results = parse_batch_response(
        json.dumps({"results": entries}), make_requests(2), "test"
    )
    assert {job_id: r.score for job_id, r in results.items()} == {
        "job-1": 80,
        "job-2": 65,
    }


def test_parse_tolerates_a_missing_reason_and_null_gaps() -> None:
    entries = [{"index": 1, "score": 50, "gaps": None}]
    results = parse_batch_response(
        json.dumps({"results": entries}), make_requests(1), "test"
    )
    assert results["job-1"].gaps == ()
    assert results["job-1"].reason


# --------------------------------------------------------------------------
# Rate limiter
# --------------------------------------------------------------------------


async def test_rate_limiter_allows_a_burst_up_to_capacity(sleeper: SleepSpy) -> None:
    limiter = RateLimiter(3, sleep=sleeper, monotonic=lambda: 100.0)
    for _ in range(3):
        await limiter.acquire()
    assert sleeper.delays == []


async def test_rate_limiter_waits_for_the_window_to_slide(
    sleeper: SleepSpy,
) -> None:
    clock = iter([100.0, 100.0, 110.0, 161.0, 161.0])
    limiter = RateLimiter(2, sleep=sleeper, monotonic=lambda: next(clock))
    await limiter.acquire()
    await limiter.acquire()
    await limiter.acquire()
    # Third request at t=110 must wait out the remainder of the first's minute.
    assert sleeper.delays == [50.0]


def test_rate_limiter_rejects_a_nonsense_budget() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        RateLimiter(0)


# --------------------------------------------------------------------------
# Gemini
# --------------------------------------------------------------------------


@respx.mock
async def test_gemini_happy_path(sleeper: SleepSpy) -> None:
    route = respx.post(host=GEMINI_HOST).mock(
        return_value=httpx.Response(
            200, json=gemini_payload(results_json(entry(1, 88), entry(2, 55)))
        )
    )
    provider = gemini(sleeper)
    results = await provider.score(make_requests(2), "profile")
    assert {job_id: r.score for job_id, r in results.items()} == {
        "job-1": 88,
        "job-2": 55,
    }
    assert all(r.provider == "gemini" for r in results.values())
    assert route.called


@respx.mock
async def test_gemini_handles_markdown_fenced_json(sleeper: SleepSpy) -> None:
    fenced = f"```json\n{results_json(entry(1, 77))}\n```"
    respx.post(host=GEMINI_HOST).mock(
        return_value=httpx.Response(200, json=gemini_payload(fenced))
    )
    results = await gemini(sleeper).score(make_requests(1), "profile")
    assert {job_id: r.score for job_id, r in results.items()} == {"job-1": 77}


@respx.mock
async def test_gemini_omits_a_job_the_model_skipped(sleeper: SleepSpy) -> None:
    """The one score that came back belongs to job-2 and to nothing else — the
    jobs on either side of it stay unscored rather than borrowing it."""
    respx.post(host=GEMINI_HOST).mock(
        return_value=httpx.Response(
            200, json=gemini_payload(results_json(entry(2, 60)))
        )
    )
    results = await gemini(sleeper).score(make_requests(3), "profile")
    assert {job_id: r.score for job_id, r in results.items()} == {"job-2": 60}


@respx.mock
async def test_gemini_clamps_an_out_of_range_score(sleeper: SleepSpy) -> None:
    respx.post(host=GEMINI_HOST).mock(
        return_value=httpx.Response(
            200, json=gemini_payload(results_json(entry(1, 250)))
        )
    )
    results = await gemini(sleeper).score(make_requests(1), "profile")
    assert results["job-1"].score == 100


@respx.mock
async def test_gemini_honours_retry_delay_then_gives_up(sleeper: SleepSpy) -> None:
    """The 17s hint lives in the body, not in Retry-After. Ignoring it is how a
    rate limit becomes a suspended key."""
    route = respx.post(host=GEMINI_HOST).mock(
        return_value=httpx.Response(429, json=GEMINI_429_BODY)
    )
    provider = gemini(sleeper, max_rate_limit_retries=2)

    with pytest.raises(QuotaExhausted) as excinfo:
        await provider.score(make_requests(1), "profile")

    assert route.call_count == 3  # bounded: initial attempt + 2 retries
    assert sleeper.delays == [17.0, 17.0]
    assert excinfo.value.provider == "gemini"
    assert excinfo.value.retry_after is not None
    assert excinfo.value.retry_after.tzinfo is not None


@respx.mock
async def test_gemini_daily_quota_does_not_retry_at_all(sleeper: SleepSpy) -> None:
    route = respx.post(host=GEMINI_HOST).mock(
        return_value=httpx.Response(429, json=GEMINI_DAILY_BODY)
    )
    with pytest.raises(QuotaExhausted):
        await gemini(sleeper).score(make_requests(1), "profile")
    assert route.call_count == 1
    assert sleeper.delays == []


@respx.mock
async def test_gemini_recovers_when_a_retry_succeeds(sleeper: SleepSpy) -> None:
    respx.post(host=GEMINI_HOST).mock(
        side_effect=[
            httpx.Response(429, json=GEMINI_429_BODY),
            httpx.Response(200, json=gemini_payload(results_json(entry(1, 72)))),
        ]
    )
    results = await gemini(sleeper).score(make_requests(1), "profile")
    assert {job_id: r.score for job_id, r in results.items()} == {"job-1": 72}
    assert sleeper.delays == [17.0]


@pytest.mark.parametrize("status", [401, 403])
@respx.mock
async def test_gemini_bad_key_is_provider_unavailable(
    sleeper: SleepSpy, status: int
) -> None:
    respx.post(host=GEMINI_HOST).mock(
        return_value=httpx.Response(status, json={"error": {"message": "denied"}})
    )
    with pytest.raises(ProviderUnavailable):
        await gemini(sleeper).score(make_requests(1), "profile")


async def test_gemini_missing_key_fails_at_construction() -> None:
    with pytest.raises(ProviderUnavailable, match="API key"):
        GeminiProvider(api_key="")


@respx.mock
async def test_gemini_network_failure_is_provider_unavailable(
    sleeper: SleepSpy,
) -> None:
    respx.post(host=GEMINI_HOST).mock(side_effect=httpx.ConnectError("no route"))
    with pytest.raises(ProviderUnavailable, match="unreachable"):
        await gemini(sleeper).score(make_requests(1), "profile")


@respx.mock
async def test_gemini_filtered_response_yields_nothing_and_does_not_raise(
    sleeper: SleepSpy,
) -> None:
    respx.post(host=GEMINI_HOST).mock(
        return_value=httpx.Response(200, json={"promptFeedback": {"blockReason": "X"}})
    )
    assert await gemini(sleeper).score(make_requests(1), "profile") == {}


@respx.mock
async def test_gemini_splits_a_large_batch(sleeper: SleepSpy) -> None:
    route = respx.post(host=GEMINI_HOST).mock(
        return_value=httpx.Response(
            200, json=gemini_payload(results_json(entry(1), entry(2)))
        )
    )
    provider = gemini(sleeper, batch_size=2)
    results = await provider.score(make_requests(4), "profile")
    assert route.call_count == 2
    # Each batch's indices are resolved against *its own* requests, so the
    # second batch's index 1 is job-3 and not a second copy of job-1.
    assert set(results) == {"job-1", "job-2", "job-3", "job-4"}


# --------------------------------------------------------------------------
# OpenAI-compatible
# --------------------------------------------------------------------------


@respx.mock
async def test_openai_compatible_happy_path(sleeper: SleepSpy) -> None:
    route = respx.post(f"{ROUTER_URL}/chat/completions").mock(
        return_value=httpx.Response(
            200, json=openai_payload(results_json(entry(1, 66), entry(2, 21)))
        )
    )
    results = await router(sleeper).score(make_requests(2), "profile")
    assert {job_id: r.score for job_id, r in results.items()} == {
        "job-1": 66,
        "job-2": 21,
    }
    assert route.calls[0].request.headers["Authorization"].startswith("Bearer ")


@respx.mock
async def test_openai_compatible_handles_markdown_fenced_json(
    sleeper: SleepSpy,
) -> None:
    fenced = f"```json\n{results_json(entry(1, 33))}\n```"
    respx.post(f"{ROUTER_URL}/chat/completions").mock(
        return_value=httpx.Response(200, json=openai_payload(fenced))
    )
    results = await router(sleeper).score(make_requests(1), "profile")
    assert {job_id: r.score for job_id, r in results.items()} == {"job-1": 33}


@respx.mock
async def test_openai_compatible_omits_a_skipped_index(sleeper: SleepSpy) -> None:
    respx.post(f"{ROUTER_URL}/chat/completions").mock(
        return_value=httpx.Response(
            200, json=openai_payload(results_json(entry(1, 80)))
        )
    )
    results = await router(sleeper).score(make_requests(2), "profile")
    assert {job_id: r.score for job_id, r in results.items()} == {"job-1": 80}
    assert "job-2" not in results


@respx.mock
async def test_openai_compatible_clamps_an_out_of_range_score(
    sleeper: SleepSpy,
) -> None:
    respx.post(f"{ROUTER_URL}/chat/completions").mock(
        return_value=httpx.Response(
            200, json=openai_payload(results_json(entry(1, -5)))
        )
    )
    results = await router(sleeper).score(make_requests(1), "profile")
    assert results["job-1"].score == 0


@respx.mock
async def test_openai_compatible_honours_retry_after_then_gives_up(
    sleeper: SleepSpy,
) -> None:
    route = respx.post(f"{ROUTER_URL}/chat/completions").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "9"}, text="slow down")
    )
    with pytest.raises(QuotaExhausted):
        await router(sleeper, max_rate_limit_retries=1).score(
            make_requests(1), "profile"
        )
    assert route.call_count == 2
    assert sleeper.delays == [9.0]


@respx.mock
async def test_openai_compatible_hard_quota_does_not_retry(
    sleeper: SleepSpy,
) -> None:
    route = respx.post(f"{ROUTER_URL}/chat/completions").mock(
        return_value=httpx.Response(
            429, json={"error": {"code": "insufficient_quota", "message": "no credit"}}
        )
    )
    with pytest.raises(QuotaExhausted):
        await router(sleeper).score(make_requests(1), "profile")
    assert route.call_count == 1


@respx.mock
async def test_openai_compatible_401_is_provider_unavailable(
    sleeper: SleepSpy,
) -> None:
    respx.post(f"{ROUTER_URL}/chat/completions").mock(
        return_value=httpx.Response(401, json={"error": {"message": "bad key"}})
    )
    with pytest.raises(ProviderUnavailable, match="credentials"):
        await router(sleeper).score(make_requests(1), "profile")


@respx.mock
async def test_openai_compatible_records_its_own_provider_name(
    sleeper: SleepSpy,
) -> None:
    """Scores are only comparable within a provider (ADR-0006)."""
    respx.post(f"{ROUTER_URL}/chat/completions").mock(
        return_value=httpx.Response(
            200, json=openai_payload(results_json(entry(1, 50)))
        )
    )
    provider = OpenAICompatibleProvider(
        base_url=ROUTER_URL, model="m", name="groq", sleep=sleeper
    )
    results = await provider.score(make_requests(1), "profile")
    assert results["job-1"].provider == "groq"


@respx.mock
async def test_openai_compatible_works_without_a_key(sleeper: SleepSpy) -> None:
    """LM Studio and friends serve unauthenticated on localhost."""
    route = respx.post(f"{ROUTER_URL}/chat/completions").mock(
        return_value=httpx.Response(
            200, json=openai_payload(results_json(entry(1, 40)))
        )
    )
    provider = OpenAICompatibleProvider(base_url=ROUTER_URL, model="m", sleep=sleeper)
    assert len(await provider.score(make_requests(1), "profile")) == 1
    assert "Authorization" not in route.calls[0].request.headers


# --------------------------------------------------------------------------
# Ollama
# --------------------------------------------------------------------------


@respx.mock
async def test_ollama_happy_path() -> None:
    respx.post(f"{OLLAMA_URL}/api/generate").mock(
        return_value=httpx.Response(
            200, json=ollama_payload(results_json(entry(1, 64), entry(2, 12)))
        )
    )
    results = await ollama().score(make_requests(2), "profile")
    assert {job_id: r.score for job_id, r in results.items()} == {
        "job-1": 64,
        "job-2": 12,
    }
    assert all(r.provider == "ollama" for r in results.values())


@respx.mock
async def test_ollama_handles_markdown_fenced_json() -> None:
    fenced = f"```json\n{results_json(entry(1, 55))}\n```"
    respx.post(f"{OLLAMA_URL}/api/generate").mock(
        return_value=httpx.Response(200, json=ollama_payload(fenced))
    )
    results = await ollama().score(make_requests(1), "profile")
    assert {job_id: r.score for job_id, r in results.items()} == {"job-1": 55}


@respx.mock
async def test_ollama_omits_a_skipped_index_and_clamps() -> None:
    respx.post(f"{OLLAMA_URL}/api/generate").mock(
        return_value=httpx.Response(
            200, json=ollama_payload(results_json(entry(2, 400)))
        )
    )
    results = await ollama().score(make_requests(2), "profile")
    # The clamped score lands on job-2, the job index 2 names — not on job-1,
    # which is what a positional result would have implied.
    assert {job_id: r.score for job_id, r in results.items()} == {"job-2": 100}


@respx.mock
async def test_ollama_unreachable_tells_the_user_to_start_the_server() -> None:
    respx.post(f"{OLLAMA_URL}/api/generate").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    with pytest.raises(ProviderUnavailable, match="ollama serve"):
        await ollama().score(make_requests(1), "profile")


@respx.mock
async def test_ollama_missing_model_says_which_one_to_pull() -> None:
    respx.post(f"{OLLAMA_URL}/api/generate").mock(
        return_value=httpx.Response(404, json={"error": "model not found"})
    )
    with pytest.raises(ProviderUnavailable, match="ollama pull"):
        await ollama(model="mistral").score(make_requests(1), "profile")


@respx.mock
async def test_ollama_never_raises_quota_exhausted() -> None:
    """There is no quota on your own hardware; mapping a local failure onto
    'budget spent' would make a failed run report as a clean one."""
    respx.post(f"{OLLAMA_URL}/api/generate").mock(
        return_value=httpx.Response(429, text="unexpected but not a quota")
    )
    with pytest.raises(ProviderUnavailable):
        await ollama().score(make_requests(1), "profile")


@respx.mock
async def test_ollama_unreadable_output_loses_only_that_batch() -> None:
    respx.post(f"{OLLAMA_URL}/api/generate").mock(
        side_effect=[
            httpx.Response(200, json=ollama_payload("I cannot help with that.")),
            httpx.Response(200, json=ollama_payload(results_json(entry(1, 70)))),
        ]
    )
    results = await ollama(batch_size=1).score(make_requests(2), "profile")
    # The surviving score is the *second* batch's job, so a lost batch cannot
    # promote a later result into an earlier job's place.
    assert {job_id: r.score for job_id, r in results.items()} == {"job-2": 70}


# --------------------------------------------------------------------------
# Null
# --------------------------------------------------------------------------


async def test_null_scores_nothing_and_never_raises() -> None:
    provider = NullProvider()
    assert await provider.score(make_requests(5), "profile") == {}
    assert await provider.score([], "") == {}
    assert provider.name == "none"  # matches the config value the user writes


# --------------------------------------------------------------------------
# Manual
# --------------------------------------------------------------------------


async def test_manual_score_writes_an_export_and_returns_nothing(
    tmp_path: Path,
) -> None:
    """The empty mapping is documented behaviour: nothing is scored until a
    human pastes the reply back."""
    export = tmp_path / "batch.md"
    provider = ManualProvider(export_path=export)

    assert await provider.score(make_requests(2), "Ten years of Python.") == {}

    written = export.read_text(encoding="utf-8")
    assert "Outpost — manual scoring batch" in written
    assert "job-1" in written and "job-2" in written
    # The exported prompt is the same one every other provider gets, so manual
    # scores stay calibrated with automated ones.
    assert "90-100" in written
    assert "Ten years of Python." in written


async def test_manual_round_trips_results(tmp_path: Path) -> None:
    provider = ManualProvider(export_path=tmp_path / "batch.md")
    requests = make_requests(2)
    await provider.score(requests, "profile")

    pasted = f"```json\n{results_json(entry(1, 95), entry(2, 15))}\n```"
    results = provider.import_results(pasted)
    assert {job_id: r.score for job_id, r in results.items()} == {
        "job-1": 95,
        "job-2": 15,
    }
    assert all(r.provider == "manual" for r in results.values())


async def test_manual_import_omits_a_missing_index(tmp_path: Path) -> None:
    """A human's paste is as skippable as a model's reply, and the indices in
    it are resolved against the exported batch — so a partial paste maps to the
    jobs it actually named."""
    provider = ManualProvider(export_path=tmp_path / "batch.md")
    requests = make_requests(3)
    await provider.score(requests, "profile")
    assert set(provider.import_results(results_json(entry(2)))) == {"job-2"}


def test_manual_import_without_a_batch_is_an_error(tmp_path: Path) -> None:
    provider = ManualProvider(export_path=tmp_path / "batch.md")
    with pytest.raises(MalformedResponse, match="no batch"):
        provider.import_results(results_json(entry(1)))


def test_manual_import_accepts_an_explicit_batch(tmp_path: Path) -> None:
    provider = ManualProvider(export_path=tmp_path / "batch.md")
    results = provider.import_results(results_json(entry(1, 88)), make_requests(1))
    assert {job_id: r.score for job_id, r in results.items()} == {"job-1": 88}


async def test_manual_creates_missing_parent_directories(tmp_path: Path) -> None:
    export = tmp_path / "nested" / "deeper" / "batch.md"
    await ManualProvider(export_path=export).score(make_requests(1), "profile")
    assert export.is_file()


# --------------------------------------------------------------------------
# Registry and the shared contract
# --------------------------------------------------------------------------


def config(provider: str, **overrides: Any) -> LLMConfig:
    """A buildable config for every provider, with no real credentials."""
    defaults: dict[str, Any] = {
        "provider": provider,
        "api_key": PLACEHOLDER_KEY,
        "model": "some/model",
        "base_url": ROUTER_URL,
        "batch_size": 3,
        "requests_per_minute": 5,
    }
    return LLMConfig(**{**defaults, **overrides})


@pytest.mark.parametrize("name", sorted(PROVIDER_REGISTRY))
def test_every_registered_provider_satisfies_the_port(name: str) -> None:
    """The shared contract test ADR-0006 requires of every adapter.

    Structural, like the port itself: the right attribute, the right method, the
    right signature, awaitable.
    """
    provider = build_provider(config(name))

    assert isinstance(provider.name, str) and provider.name
    assert inspect.iscoroutinefunction(provider.score)

    signature = inspect.signature(provider.score)
    assert list(signature.parameters) == ["requests", "profile_text"]


@pytest.mark.parametrize("name", sorted(PROVIDER_REGISTRY))
async def test_every_provider_returns_an_empty_mapping_for_an_empty_batch(
    name: str,
) -> None:
    """No provider may issue a request for nothing — a wasted call against a
    free tier is a real cost.

    The mapping type is part of the shared contract too: every adapter answers
    with job ids, so no caller has to know which one it is talking to.
    """
    provider = build_provider(config(name))
    with respx.mock:
        result = await provider.score([], "profile")
    assert isinstance(result, Mapping)
    assert result == {}


def test_none_is_the_zero_config_default() -> None:
    """A first run must need no key, no signup and no decision (ADR-0006)."""
    assert LLMConfig().provider == "none"
    assert isinstance(build_provider(LLMConfig()), NullProvider)


def test_unknown_provider_names_the_available_ones() -> None:
    with pytest.raises(ProviderUnavailable, match="Unknown LLM provider"):
        build_provider(config("gpt5-turbo-max"))


def test_gemini_without_a_key_is_refused_at_build_time() -> None:
    """Raised at startup, not at stage 8 after a long scrape."""
    with pytest.raises(ProviderUnavailable, match="API key"):
        build_provider(config("gemini", api_key=None))


def test_gemini_falls_back_to_its_own_default_model() -> None:
    provider = build_provider(config("gemini", model=None))
    assert isinstance(provider, GeminiProvider)
    assert GeminiProvider.default_model() in provider.url


@pytest.mark.parametrize("missing", ["base_url", "model"])
def test_openai_compatible_needs_a_base_url_and_a_model(missing: str) -> None:
    with pytest.raises(ProviderUnavailable):
        build_provider(config("openai_compatible", **{missing: None}))


def test_registry_passes_the_shared_client_through() -> None:
    """One client, injected by the composition root (ADR-0009)."""
    client = httpx.AsyncClient()
    provider = build_provider(config("openai_compatible"), client)
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider._client is client


def test_ollama_uses_its_own_defaults_when_the_config_is_bare() -> None:
    provider = build_provider(LLMConfig(provider="ollama", base_url=None, model=None))
    assert isinstance(provider, OllamaProvider)
    assert provider.url == f"{OLLAMA_URL}/api/generate"


def test_available_providers_is_sorted_and_complete() -> None:
    assert available_providers() == tuple(sorted(PROVIDER_REGISTRY))
    assert "none" in available_providers()


# --------------------------------------------------------------------------
# Base class
# --------------------------------------------------------------------------


async def test_base_provider_requires_a_complete_implementation() -> None:
    class Incomplete(BatchingLLMProvider):
        name = "incomplete"

    with pytest.raises(NotImplementedError):
        await Incomplete().score(make_requests(1), "profile")
