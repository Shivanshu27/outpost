"""The local HTTP API.

Delivery only: parse, call, serialise. The UI has no privileged access — every
button in the interface goes through an endpoint the CLI could equally call,
which keeps the two honest with each other (ADR-0010).

This binds to loopback and has **no authentication**, deliberately: it is a
local-first tool and there is no account system to authenticate against
(ADR-0001). ``outpost doctor`` warns if the bind address is changed, because
exposing this on a network would publish the user's job search.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from outpost.api.schemas import (
    JobDetailOut,
    JobOut,
    JobPatch,
    JobsPage,
    MetaOut,
    StatsOut,
)
from outpost.config.container import Container, build_container
from outpost.config.settings import Settings
from outpost.domain.models import Eligibility, JobStatus

__all__ = ["create_app"]


def _version() -> str:
    try:
        return version("outpost")
    except PackageNotFoundError:
        return "0.0.0+dev"


def get_container(request: Request) -> Container:
    """The container built once at startup, for endpoints to depend on."""
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]
"""Must live at module scope.

``from __future__ import annotations`` turns every annotation into a string,
and FastAPI resolves those with ``get_type_hints()`` against the *module*
globals. An alias defined inside ``create_app`` is a local, so resolution
fails, FastAPI treats the parameter as an ordinary one, and every endpoint
starts demanding a ``container`` **query parameter** — a 422 on every request.
The failure is silent at import time and total at runtime, which is a nasty
combination, so the placement is load-bearing rather than stylistic.
"""


def create_app(settings: Settings) -> FastAPI:
    """Build the ASGI app.

    The container is constructed once at startup rather than per request:
    SQLite is single-writer and the ruleset compilation is not free, so
    rebuilding it per request would be both slower and pointless for a
    single-user local server.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = build_container(settings)
        app.state.container = container
        try:
            yield
        finally:
            await container.aclose()
            container.repository.close()

    app = FastAPI(
        title="Outpost",
        version=_version(),
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    # ------------------------------------------------------------------
    # Jobs
    # ------------------------------------------------------------------

    @app.get("/api/jobs", response_model=JobsPage)
    def list_jobs(
        container: ContainerDep,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
        eligibility: Annotated[list[Eligibility] | None, Query()] = None,
        status: Annotated[list[JobStatus] | None, Query()] = None,
        source: Annotated[list[str] | None, Query()] = None,
        order_by: Annotated[
            str, Query(pattern="^(match_score|prescore|posted_at|last_seen)$")
        ] = "match_score",
    ) -> JobsPage:
        repo = container.repository
        jobs = repo.list_jobs(
            limit=limit,
            offset=offset,
            eligibilities=eligibility,
            statuses=status,
            sources=source,
            order_by=order_by,
        )
        return JobsPage(
            items=[JobOut.from_domain(job) for job in jobs],
            total=repo.count(),
            limit=limit,
            offset=offset,
        )

    @app.get("/api/jobs/{job_id}", response_model=JobDetailOut)
    def get_job(job_id: str, container: ContainerDep) -> JobDetailOut:
        job = container.repository.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="No such job")
        return JobDetailOut.from_domain(job)

    @app.patch("/api/jobs/{job_id}", response_model=JobDetailOut)
    def patch_job(
        job_id: str, patch: JobPatch, container: ContainerDep
    ) -> JobDetailOut:
        """Update the fields the user owns.

        Only these three are writable. Everything else is derived by the
        pipeline, and letting a client write derived state would make the next
        run silently disagree with the UI.
        """
        repo = container.repository
        if repo.get(job_id) is None:
            raise HTTPException(status_code=404, detail="No such job")

        if patch.status is not None:
            repo.set_status(job_id, patch.status)
        if patch.notes is not None:
            repo.set_notes(job_id, patch.notes or None)
        if patch.eligibility_override is not None:
            repo.set_eligibility_override(
                job_id,
                None
                if patch.eligibility_override == "clear"
                else Eligibility(patch.eligibility_override),
            )

        updated = repo.get(job_id)
        if updated is None:  # pragma: no cover — deleted mid-request
            raise HTTPException(status_code=404, detail="No such job")
        return JobDetailOut.from_domain(updated)

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    @app.get("/api/stats", response_model=StatsOut)
    def stats(container: ContainerDep) -> StatsOut:
        repo = container.repository
        jobs = repo.list_jobs()
        by_status: dict[str, int] = {}
        by_source: dict[str, int] = {}
        scored = verified = 0
        for job in jobs:
            by_status[job.status.value] = by_status.get(job.status.value, 0) + 1
            by_source[job.source] = by_source.get(job.source, 0) + 1
            scored += job.match is not None
            verified += job.verification is not None
        return StatsOut(
            total=len(jobs),
            by_eligibility=repo.counts_by_eligibility(),
            by_status=by_status,
            by_source=by_source,
            scored=scored,
            verified=verified,
        )

    @app.get("/api/meta", response_model=MetaOut)
    def meta(container: ContainerDep) -> MetaOut:
        return MetaOut(
            version=_version(),
            country=container.profile.country,
            timezone=container.profile.timezone,
            llm_provider=container.llm.name if container.llm else "none",
            has_resume=container.profile.has_resume,
            sources=[s.name for s in container.sources],
            rule_count=len(container.ruleset),
        )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": _version()}

    _mount_ui(app)
    return app


def _mount_ui(app: FastAPI) -> None:
    """Serve the built SPA, when it exists.

    Absent in a source checkout that has not run the frontend build — the API
    still works, and the message says how to fix it rather than 404ing
    mysteriously.
    """
    dist = Path(__file__).parent.parent / "resources" / "web"
    index = dist / "index.html"

    if not index.exists():

        @app.get("/")
        def missing_ui() -> dict[str, str]:
            return {
                "status": "api only",
                "detail": (
                    "The web UI is not built. Run `npm install && npm run build` "
                    "in web/, or use the CLI. The API is available under /api."
                ),
                "docs": "/api/docs",
            }

        return

    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str) -> FileResponse:
        """Serve the SPA, letting client-side routing own every non-API path."""
        candidate = dist / path
        if path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)
