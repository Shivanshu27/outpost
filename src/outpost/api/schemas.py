"""Wire schemas for the HTTP API.

Separate from the domain models on purpose. The domain is free to change shape;
the wire contract is consumed by a TypeScript client generated from this
schema, and coupling the two would mean every internal rename becomes a
breaking API change (ADR-0010).

The translation is explicit and one-way — ``JobOut.from_domain`` — so a domain
field is never accidentally exposed just by existing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from outpost.domain.models import Eligibility, Job, JobStatus, VerificationTier

__all__ = [
    "DimensionOut",
    "JobDetailOut",
    "JobOut",
    "JobPatch",
    "JobsPage",
    "MetaOut",
    "StatsOut",
]


class DimensionOut(BaseModel):
    """One eligibility dimension and the evidence behind it.

    Shipped to the UI in full because a verdict the user cannot audit is a bug
    (ADR-0002) — the interface shows the matched phrase, not just the outcome.
    """

    dimension: str
    eligibility: Eligibility
    rule_id: str | None = None
    evidence: str | None = None
    matched_text: str | None = None


class VerificationOut(BaseModel):
    tier: VerificationTier
    reasons: list[str] = Field(default_factory=list)
    checked_at: datetime | None = None


class MatchOut(BaseModel):
    score: int
    reason: str
    gaps: list[str] = Field(default_factory=list)
    provider: str
    """Surfaced because scores are only comparable within a provider
    (ADR-0006). The UI says which model judged this."""


class CompensationOut(BaseModel):
    minimum: float | None = None
    maximum: float | None = None
    currency: str | None = None
    period: str | None = None


class JobOut(BaseModel):
    """A job as the list view needs it."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    source: str
    url: str
    title: str
    company: str | None
    location_text: str | None
    contract_type: str
    compensation: CompensationOut
    tags: list[str]
    posted_at: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime

    eligibility: Eligibility
    """The *effective* value — the user's override if they set one."""
    eligibility_from_rules: Eligibility
    eligibility_override: Eligibility | None
    eligibility_summary: str

    verification: VerificationOut | None
    match: MatchOut | None
    prescore: float | None
    status: JobStatus
    notes: str | None

    @classmethod
    def from_domain(cls, job: Job) -> JobOut:
        comp = job.compensation
        return cls(
            id=job.id,
            source=job.source,
            url=str(job.url),
            title=job.title,
            company=job.company,
            location_text=job.location_text,
            contract_type=job.contract_type.value,
            compensation=CompensationOut(
                minimum=float(comp.minimum) if comp.minimum is not None else None,
                maximum=float(comp.maximum) if comp.maximum is not None else None,
                currency=comp.currency,
                period=comp.period.value if comp.period else None,
            ),
            tags=list(job.tags),
            posted_at=job.posted_at,
            first_seen_at=job.first_seen_at,
            last_seen_at=job.last_seen_at,
            eligibility=job.effective_eligibility,
            eligibility_from_rules=job.eligibility.eligibility,
            eligibility_override=job.eligibility_override,
            eligibility_summary=job.eligibility.summary,
            verification=(
                VerificationOut(
                    tier=job.verification.tier,
                    reasons=list(job.verification.reasons),
                    checked_at=job.verification.checked_at,
                )
                if job.verification
                else None
            ),
            match=(
                MatchOut(
                    score=job.match.score,
                    reason=job.match.reason,
                    gaps=list(job.match.gaps),
                    provider=job.match.provider,
                )
                if job.match
                else None
            ),
            prescore=job.prescore,
            status=job.status,
            notes=job.notes,
        )


class JobDetailOut(JobOut):
    """The list shape plus the heavy fields, for the detail view."""

    description: str
    dimensions: list[DimensionOut]

    @classmethod
    def from_domain(cls, job: Job) -> JobDetailOut:
        base = JobOut.from_domain(job).model_dump()
        return cls(
            **base,
            description=job.description,
            dimensions=[
                DimensionOut(
                    dimension=d.dimension.value,
                    eligibility=d.eligibility,
                    rule_id=d.rule_id,
                    evidence=d.evidence,
                    matched_text=d.matched_text,
                )
                for d in job.eligibility.dimensions
            ],
        )


class JobsPage(BaseModel):
    items: list[JobOut]
    total: int
    limit: int
    offset: int


class JobPatch(BaseModel):
    """User-owned fields only.

    Deliberately narrow: these are the three things the user owns, and nothing
    the pipeline derives is writable over HTTP. ``extra="forbid"`` means a
    typo'd field is a 422 rather than a silently ignored request.
    """

    model_config = ConfigDict(extra="forbid")

    status: JobStatus | None = None
    notes: str | None = None
    eligibility_override: Eligibility | Literal["clear"] | None = None


class StatsOut(BaseModel):
    total: int
    by_eligibility: dict[str, int]
    by_status: dict[str, int]
    by_source: dict[str, int]
    scored: int
    verified: int


class MetaOut(BaseModel):
    """Context the UI needs to render honestly.

    ``llm_provider`` is here so the interface can say *why* scores are missing
    rather than showing an unexplained empty column — a blank score with no
    reason reads as a bug.
    """

    version: str
    country: str
    timezone: str
    llm_provider: str
    has_resume: bool
    sources: list[str]
    rule_count: int
