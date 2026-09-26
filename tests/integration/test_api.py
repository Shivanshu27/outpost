"""The local HTTP API, against a real temporary database.

The UI has no privileged access: every button goes through an endpoint the CLI
could equally call (ADR-0010). So these tests are also the contract tests for
the UI's behaviour — in particular that a job's eligibility arrives with the
evidence behind it, because a verdict the user cannot audit is a bug
(ADR-0002).

The wire schema is deliberately separate from the domain model, and only
``status``, ``notes`` and ``eligibility_override`` are writable: everything else
is derived by the pipeline, and a client that could write derived state would
make the next run silently disagree with the UI.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.conftest import MakeJob

from outpost.adapters.storage.sqlite import SqliteJobRepository
from outpost.api.app import create_app
from outpost.config.settings import Settings
from outpost.domain.models import (
    DimensionVerdict,
    Eligibility,
    EligibilityDimension,
    EligibilityVerdict,
    JobStatus,
    MatchResult,
    VerificationResult,
    VerificationTier,
)

INELIGIBLE_VERDICT = EligibilityVerdict.combine(
    [
        DimensionVerdict(
            dimension=EligibilityDimension.LOCATION,
            eligibility=Eligibility.INELIGIBLE,
            rule_id="loc.us_only",
            evidence="Listing restricts hiring to the United States",
            matched_text="US only",
            source_field="location_text",
        ),
        DimensionVerdict.unknown(EligibilityDimension.CURRENCY),
    ]
)


@pytest.fixture
def seeded(
    repo: SqliteJobRepository, make_job: MakeJob, now: datetime
) -> dict[str, str]:
    """Three jobs covering the states the list view has to render."""
    scored = make_job(source="remoteok", title="Senior Backend Engineer")
    blocked = make_job(source="remoteok", title="Backend Engineer, US")
    plain = make_job(source="remotive", title="Platform Engineer")
    repo.upsert_many([scored, blocked, plain])

    repo.save_match(
        scored.id,
        MatchResult(
            score=88,
            reason="Strong Python and AWS overlap",
            gaps=("no Kubernetes",),
            provider="fake-llm",
            scored_at=now,
        ),
    )
    repo.set_status(scored.id, JobStatus.SHORTLISTED)
    repo.save_verification(scored.id, VerificationResult(tier=VerificationTier.OK))

    repo.save_eligibility(blocked.id, INELIGIBLE_VERDICT)
    repo.save_prescore(plain.id, 0.7)

    repo.close()
    return {"scored": scored.id, "blocked": blocked.id, "plain": plain.id}


@pytest.fixture
def client(
    seeded: dict[str, str], config_dir: Path, db_path: Path
) -> Iterator[TestClient]:
    """A TestClient over the real app, pointed at temporary directories.

    Settings are constructed explicitly rather than read from the environment,
    which is how a run is isolated (ADR-0009) — the developer's own config
    directory and database are never touched.
    """
    settings = Settings(
        config_dir=config_dir,
        data_dir=db_path.parent,
        _env_file=None,  # type: ignore[call-arg]
    )
    assert settings.db_path == db_path
    with TestClient(create_app(settings)) as test_client:
        yield test_client


# --------------------------------------------------------------------------
# Listing
# --------------------------------------------------------------------------


def test_listing_returns_every_job_with_its_page_metadata(
    client: TestClient,
) -> None:
    response = client.get("/api/jobs")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3
    assert body["limit"] == 50
    assert body["offset"] == 0


def test_listing_can_be_filtered_by_eligibility(
    client: TestClient, seeded: dict[str, str]
) -> None:
    response = client.get("/api/jobs", params={"eligibility": "ineligible"})

    assert [item["id"] for item in response.json()["items"]] == [seeded["blocked"]]


def test_listing_can_be_filtered_by_status_and_source(
    client: TestClient, seeded: dict[str, str]
) -> None:
    by_status = client.get("/api/jobs", params={"status": "shortlisted"}).json()
    by_source = client.get("/api/jobs", params={"source": "remotive"}).json()

    assert [item["id"] for item in by_status["items"]] == [seeded["scored"]]
    assert [item["id"] for item in by_source["items"]] == [seeded["plain"]]


def test_repeating_a_filter_is_an_or(client: TestClient) -> None:
    response = client.get(
        "/api/jobs", params=[("eligibility", "unknown"), ("eligibility", "ineligible")]
    )

    assert len(response.json()["items"]) == 3


def test_pagination_walks_the_corpus_without_repeats_or_gaps(
    client: TestClient,
) -> None:
    """The total is the whole corpus, not the page — otherwise the UI cannot
    render a page count."""
    first = client.get("/api/jobs", params={"limit": 2, "offset": 0}).json()
    second = client.get("/api/jobs", params={"limit": 2, "offset": 2}).json()

    ids = [item["id"] for item in first["items"] + second["items"]]
    assert len(first["items"]) == 2
    assert len(second["items"]) == 1
    assert len(set(ids)) == 3
    assert first["total"] == second["total"] == 3


def test_an_out_of_range_page_is_empty_rather_than_an_error(
    client: TestClient,
) -> None:
    body = client.get("/api/jobs", params={"limit": 10, "offset": 500}).json()

    assert body["items"] == []
    assert body["total"] == 3


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 501},
        {"offset": -1},
        {"order_by": "whatever"},
        {"eligibility": "maybe"},
        {"status": "ghosted"},
    ],
)
def test_invalid_query_parameters_are_rejected(
    client: TestClient, params: dict[str, object]
) -> None:
    """``order_by`` in particular is validated at the edge rather than passed
    through to SQL."""
    assert client.get("/api/jobs", params=params).status_code == 422


@pytest.mark.parametrize(
    "order_by", ["match_score", "prescore", "posted_at", "last_seen"]
)
def test_every_documented_ordering_is_accepted(
    client: TestClient, order_by: str
) -> None:
    response = client.get("/api/jobs", params={"order_by": order_by})

    assert response.status_code == 200
    assert len(response.json()["items"]) == 3


def test_a_listed_job_reports_the_effective_eligibility_and_its_source(
    client: TestClient, seeded: dict[str, str]
) -> None:
    """The UI must be able to say "your override" versus "our rules" — showing
    only the outcome would hide the fact that the user themselves changed it."""
    items = {item["id"]: item for item in client.get("/api/jobs").json()["items"]}
    blocked = items[seeded["blocked"]]

    assert blocked["eligibility"] == "ineligible"
    assert blocked["eligibility_from_rules"] == "ineligible"
    assert blocked["eligibility_override"] is None
    assert blocked["eligibility_summary"] == (
        "Listing restricts hiring to the United States"
    )


def test_a_scored_job_reports_which_provider_judged_it(
    client: TestClient, seeded: dict[str, str]
) -> None:
    """Scores are only comparable within a provider (ADR-0006), so the wire
    shape carries the provider rather than a bare number."""
    items = {item["id"]: item for item in client.get("/api/jobs").json()["items"]}

    match = items[seeded["scored"]]["match"]
    assert match["score"] == 88
    assert match["provider"] == "fake-llm"
    assert match["gaps"] == ["no Kubernetes"]
    assert items[seeded["plain"]]["match"] is None


def test_the_list_shape_omits_the_heavy_description(client: TestClient) -> None:
    """The list view does not need it, and shipping every description would
    make the first paint of a thousand-row table slow for nothing."""
    item = client.get("/api/jobs").json()["items"][0]

    assert "description" not in item


# --------------------------------------------------------------------------
# Detail
# --------------------------------------------------------------------------


def test_the_detail_view_carries_the_evidence_behind_every_dimension(
    client: TestClient, seeded: dict[str, str]
) -> None:
    """ADR-0002 again: the interface shows the matched phrase and the rule id,
    not just the outcome. That is what makes a wrong rule reportable."""
    body = client.get(f"/api/jobs/{seeded['blocked']}").json()

    dimensions = {d["dimension"]: d for d in body["dimensions"]}
    assert dimensions["location"]["eligibility"] == "ineligible"
    assert dimensions["location"]["rule_id"] == "loc.us_only"
    assert dimensions["location"]["matched_text"] == "US only"
    assert dimensions["location"]["evidence"]
    # The undetermined dimension is shipped too, so the UI can show the whole
    # picture rather than only the axis that happened to fire.
    assert dimensions["currency"]["eligibility"] == "unknown"
    assert dimensions["currency"]["rule_id"] is None


def test_the_detail_view_includes_the_description(
    client: TestClient, seeded: dict[str, str]
) -> None:
    body = client.get(f"/api/jobs/{seeded['plain']}").json()

    assert "Python" in body["description"]


def test_an_unknown_job_id_is_a_404_with_a_readable_detail(
    client: TestClient,
) -> None:
    response = client.get("/api/jobs/doesnotexist01")

    assert response.status_code == 404
    assert response.json()["detail"] == "No such job"


# --------------------------------------------------------------------------
# Patching — the three fields the user owns
# --------------------------------------------------------------------------


def test_patching_status_persists_and_is_returned(
    client: TestClient, seeded: dict[str, str]
) -> None:
    response = client.patch(f"/api/jobs/{seeded['plain']}", json={"status": "applied"})

    assert response.status_code == 200
    assert response.json()["status"] == "applied"
    assert client.get(f"/api/jobs/{seeded['plain']}").json()["status"] == "applied"


def test_patching_notes_persists(client: TestClient, seeded: dict[str, str]) -> None:
    client.patch(f"/api/jobs/{seeded['plain']}", json={"notes": "Ask about on-call"})

    assert client.get(f"/api/jobs/{seeded['plain']}").json()["notes"] == (
        "Ask about on-call"
    )


def test_an_override_changes_the_effective_eligibility_but_not_the_rules_verdict(
    client: TestClient, seeded: dict[str, str]
) -> None:
    """The user's remedy for a wrong rule. Both values stay visible so the UI
    can show that the verdict was corrected rather than that it never existed.
    """
    body = client.patch(
        f"/api/jobs/{seeded['blocked']}", json={"eligibility_override": "eligible"}
    ).json()

    assert body["eligibility"] == "eligible"
    assert body["eligibility_from_rules"] == "ineligible"
    assert body["eligibility_override"] == "eligible"


def test_an_override_can_be_cleared(client: TestClient, seeded: dict[str, str]) -> None:
    """ "clear" is a distinct value because ``null`` already means "leave this
    field alone" in a PATCH body."""
    client.patch(
        f"/api/jobs/{seeded['blocked']}", json={"eligibility_override": "eligible"}
    )

    body = client.patch(
        f"/api/jobs/{seeded['blocked']}", json={"eligibility_override": "clear"}
    ).json()

    assert body["eligibility_override"] is None
    assert body["eligibility"] == "ineligible"


def test_an_omitted_field_is_left_alone(
    client: TestClient, seeded: dict[str, str]
) -> None:
    client.patch(f"/api/jobs/{seeded['plain']}", json={"notes": "keep me"})

    body = client.patch(
        f"/api/jobs/{seeded['plain']}", json={"status": "rejected"}
    ).json()

    assert body["notes"] == "keep me"
    assert body["status"] == "rejected"


@pytest.mark.parametrize(
    "payload",
    [
        {"match_score": 100},
        {"title": "A better title"},
        {"prescore": 1.0},
        {"eligibility": "eligible"},
        {"notes": "fine", "stauts": "applied"},
    ],
    ids=["match_score", "title", "prescore", "eligibility", "typo"],
)
def test_writing_a_field_the_user_does_not_own_is_a_422(
    client: TestClient, seeded: dict[str, str], payload: dict[str, object]
) -> None:
    """``extra="forbid"`` on the patch schema.

    A typo'd or derived field must fail loudly rather than be silently ignored
    — a silently dropped write is a UI that appears to work and does not.
    """
    response = client.patch(f"/api/jobs/{seeded['plain']}", json=payload)

    assert response.status_code == 422


def test_an_invalid_value_for_an_owned_field_is_a_422(
    client: TestClient, seeded: dict[str, str]
) -> None:
    response = client.patch(
        f"/api/jobs/{seeded['plain']}", json={"status": "interviewing"}
    )

    assert response.status_code == 422


def test_patching_an_unknown_job_is_a_404(client: TestClient) -> None:
    response = client.patch("/api/jobs/doesnotexist01", json={"status": "applied"})

    assert response.status_code == 404


# --------------------------------------------------------------------------
# Metadata
# --------------------------------------------------------------------------


def test_stats_summarise_the_corpus_across_every_axis(
    client: TestClient,
) -> None:
    body = client.get("/api/stats").json()

    assert body["total"] == 3
    assert body["by_eligibility"] == {"unknown": 2, "ineligible": 1}
    assert body["by_status"] == {"new": 2, "shortlisted": 1}
    assert body["by_source"] == {"remoteok": 2, "remotive": 1}
    assert body["scored"] == 1
    assert body["verified"] == 1


def test_stats_follow_a_user_override(
    client: TestClient, seeded: dict[str, str]
) -> None:
    client.patch(
        f"/api/jobs/{seeded['blocked']}", json={"eligibility_override": "eligible"}
    )

    body = client.get("/api/stats").json()

    assert body["by_eligibility"] == {"unknown": 2, "eligible": 1}


def test_meta_tells_the_ui_enough_to_render_honestly(client: TestClient) -> None:
    """``llm_provider`` is here so the interface can say *why* scores are
    missing; a blank column with no explanation reads as a bug."""
    body = client.get("/api/meta").json()

    assert body["country"] == "IN"
    assert body["timezone"] == "Asia/Kolkata"
    # "none", matching the value the user writes in config — not the class's
    # internal name. The UI renders this verbatim in its header.
    assert body["llm_provider"] == "none"
    assert body["has_resume"] is False
    assert body["rule_count"] > 0
    assert "remoteok" in body["sources"]


def test_health_reports_a_version(client: TestClient) -> None:
    body = client.get("/api/health").json()

    assert body["status"] == "ok"
    assert body["version"]


def test_the_openapi_schema_is_served_for_the_generated_client(
    client: TestClient,
) -> None:
    """The TypeScript client is generated from this, so it has to exist and it
    has to describe the endpoints the UI calls (ADR-0010)."""
    schema = client.get("/api/openapi.json").json()

    assert "/api/jobs" in schema["paths"]
    assert "/api/jobs/{job_id}" in schema["paths"]
