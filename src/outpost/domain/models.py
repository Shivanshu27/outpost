"""Core domain models.

This module is pure: no I/O, no environment access, no clock, no network.
Everything here is data plus the invariants that data must satisfy.

The two shapes that matter most:

``RawJob``
    What a source returns. Deliberately loose — almost every field is optional,
    because a source's job is to fetch, not to guarantee (ADR-0008).

``Job``
    The canonical shape everything downstream operates on. Produced only by
    :func:`outpost.domain.normalisation.normalise`, which is the single place
    where looseness becomes strictness.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

__all__ = [
    "Compensation",
    "ContractType",
    "DimensionVerdict",
    "Eligibility",
    "EligibilityDimension",
    "EligibilityVerdict",
    "Job",
    "JobStatus",
    "MatchResult",
    "RawJob",
    "ScoringRequest",
    "UserProfile",
    "VerificationResult",
    "VerificationTier",
]


class _Frozen(BaseModel):
    """Base for immutable value objects."""

    model_config = ConfigDict(frozen=True, extra="forbid")


# --------------------------------------------------------------------------
# Eligibility — the core product concept (ADR-0002)
# --------------------------------------------------------------------------


class Eligibility(StrEnum):
    """Whether the user can actually hold this job.

    Tri-state, and the third state is load-bearing. See ADR-0002.

    ``UNKNOWN`` means *we could not determine this*, and it is the default.
    It is shown to the user, never hidden, and **no code path may map it to
    INELIGIBLE**. The failure we are preventing is the invisible one: a job the
    user could have taken, silently dropped because a rule did not match.

    We would rather show ten ineligible jobs than hide one eligible one.
    """

    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    UNKNOWN = "unknown"

    @property
    def is_determined(self) -> bool:
        """True when positive evidence produced this verdict."""
        return self is not Eligibility.UNKNOWN


class EligibilityDimension(StrEnum):
    """Independent axes along which eligibility is assessed.

    Each is resolved separately, then combined by
    :meth:`EligibilityVerdict.combine`. Keeping them independent means a
    listing blocked on location still reports what its timezone requirement
    was, which is what makes a verdict auditable.
    """

    LOCATION = "location"
    TIMEZONE = "timezone"
    AUTHORISATION = "authorisation"
    CONTRACT = "contract"
    CURRENCY = "currency"


class DimensionVerdict(_Frozen):
    """One dimension's verdict, with the evidence that produced it.

    ``evidence`` and ``matched_text`` are required for any determined verdict —
    a verdict we cannot explain is a bug, not a result (ADR-0002). This is
    enforced below rather than left to discipline.
    """

    dimension: EligibilityDimension
    eligibility: Eligibility
    rule_id: str | None = None
    evidence: str | None = None
    matched_text: str | None = None
    source_field: str | None = None

    @model_validator(mode="after")
    def _determined_verdicts_carry_evidence(self) -> Self:
        if self.eligibility.is_determined and not (self.rule_id and self.evidence):
            msg = (
                f"{self.dimension} verdict {self.eligibility!r} lacks rule_id or "
                f"evidence. Determined verdicts must be explainable (ADR-0002)."
            )
            raise ValueError(msg)
        return self

    @classmethod
    def unknown(cls, dimension: EligibilityDimension) -> DimensionVerdict:
        """The default verdict: no evidence either way."""
        return cls(dimension=dimension, eligibility=Eligibility.UNKNOWN)


class EligibilityVerdict(_Frozen):
    """The combined eligibility decision for a job, across all dimensions."""

    eligibility: Eligibility
    dimensions: tuple[DimensionVerdict, ...] = ()

    @property
    def blocking(self) -> tuple[DimensionVerdict, ...]:
        """Dimensions that returned INELIGIBLE — the reasons, for display."""
        return tuple(
            d for d in self.dimensions if d.eligibility is Eligibility.INELIGIBLE
        )

    @property
    def summary(self) -> str:
        """One line explaining the verdict, for UI and CLI."""
        if blocking := self.blocking:
            return "; ".join(d.evidence or d.dimension for d in blocking)
        if self.eligibility is Eligibility.ELIGIBLE:
            supporting = [
                d.evidence
                for d in self.dimensions
                if d.eligibility is Eligibility.ELIGIBLE and d.evidence
            ]
            return "; ".join(supporting) or "No restrictions found"
        return "Could not determine eligibility from the listing"

    @classmethod
    def combine(cls, dimensions: Sequence[DimensionVerdict]) -> EligibilityVerdict:
        """Combine per-dimension verdicts into one.

        Precedence, in order (ADR-0007):

        1. Any ``INELIGIBLE`` dominates — one hard blocker is enough.
        2. Otherwise any ``ELIGIBLE`` wins — we found positive evidence on some
           axis and no blocker on any.
        3. ``UNKNOWN`` only when *no* dimension matched anything at all.

        The subtlety is in what an unmatched dimension means. A dimension with
        no matching rule is **"no constraint found on this axis"**, not "we do
        not know whether the user qualifies". Treating it as the latter makes
        ``ELIGIBLE`` nearly unreachable — a listing saying "work from anywhere"
        would come back ``UNKNOWN`` merely because it said nothing about
        currency — and it would contradict the recall-over-precision stance the
        rest of the design is built on (ADR-0002).

        The residual risk is real and accepted: if a listing carries a blocker
        our rules failed to recognise, a positive signal elsewhere can produce a
        wrong ``ELIGIBLE``. That is the *safe* direction of error here. A job
        wrongly shown costs a few seconds of reading; a job wrongly hidden costs
        an opportunity the user never learns existed.
        """
        if not dimensions:
            return cls(eligibility=Eligibility.UNKNOWN)

        verdicts = tuple(dimensions)
        if any(d.eligibility is Eligibility.INELIGIBLE for d in verdicts):
            combined = Eligibility.INELIGIBLE
        elif any(d.eligibility is Eligibility.ELIGIBLE for d in verdicts):
            combined = Eligibility.ELIGIBLE
        else:
            combined = Eligibility.UNKNOWN

        return cls(eligibility=combined, dimensions=verdicts)

    @classmethod
    def unknown(cls) -> EligibilityVerdict:
        return cls(eligibility=Eligibility.UNKNOWN)


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------


class VerificationTier(StrEnum):
    """How much we trust that this listing is real and still open.

    Only ``SCAM`` and ``EXPIRED`` are excluded by default. ``SUSPICIOUS`` is
    shown with its reason attached — the same recall-over-precision stance as
    eligibility (FR-4.4).
    """

    OK = "ok"
    SUSPICIOUS = "suspicious"
    SCAM = "scam"
    EXPIRED = "expired"
    UNKNOWN = "unknown"

    @property
    def excluded_by_default(self) -> bool:
        return self in (VerificationTier.SCAM, VerificationTier.EXPIRED)


class VerificationResult(_Frozen):
    """Outcome of the liveness and legitimacy checks."""

    tier: VerificationTier
    reasons: tuple[str, ...] = ()
    checked_at: datetime | None = None
    http_status: int | None = None

    @classmethod
    def unknown(cls, reason: str | None = None) -> VerificationResult:
        """Used when a check could not complete.

        A network failure must never penalise a listing (FR-4.5) — we could not
        check, which is not the same as having found a problem.
        """
        return cls(
            tier=VerificationTier.UNKNOWN,
            reasons=(reason,) if reason else (),
        )


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------


class ContractType(StrEnum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    INTERNSHIP = "internship"
    UNKNOWN = "unknown"


class CompensationPeriod(StrEnum):
    HOURLY = "hourly"
    DAILY = "daily"
    MONTHLY = "monthly"
    YEARLY = "yearly"


class Compensation(_Frozen):
    """Parsed pay, or nothing.

    We never guess. A listing with unparseable compensation carries ``None``
    rather than an inferred midpoint — a wrong number here directly misleads
    the ranking and the user's decision to apply (FR-2.3).
    """

    minimum: Decimal | None = None
    maximum: Decimal | None = None
    currency: str | None = None
    period: CompensationPeriod | None = None

    @property
    def is_empty(self) -> bool:
        return self.minimum is None and self.maximum is None


class JobStatus(StrEnum):
    """Where the *user* has taken this job. User-owned; never touched by a
    re-scrape (ADR-0004)."""

    NEW = "new"
    SHORTLISTED = "shortlisted"
    APPLIED = "applied"
    REJECTED = "rejected"
    DISMISSED = "dismissed"


class RawJob(BaseModel):
    """What a source returns, before normalisation.

    Deliberately permissive: sources fetch, they do not guarantee (ADR-0008).
    Every field except ``source`` and ``url`` is optional, and ``raw`` retains
    the original payload so a parsing bug can be diagnosed and re-run without
    re-fetching.
    """

    model_config = ConfigDict(extra="forbid")

    source: str
    url: str
    external_id: str | None = None
    title: str | None = None
    company: str | None = None
    description: str | None = None
    location_text: str | None = None
    compensation_text: str | None = None
    contract_text: str | None = None
    posted_at_text: str | None = None
    posted_at: datetime | None = None
    tags: tuple[str, ...] = ()
    raw: dict[str, Any] = Field(default_factory=dict)


class Job(BaseModel):
    """The canonical job. Produced only by ``normalise()``.

    Field ownership matters here and is enforced at the storage boundary
    (ADR-0004):

    *Source-derived* — refreshed on every scrape:
        ``title``, ``company``, ``description``, ``location_text``,
        ``compensation``, ``contract_type``, ``tags``, ``posted_at``,
        ``last_seen_at``

    *Derived* — recomputed by pipeline stages:
        ``eligibility``, ``verification``, ``prescore``, ``match``

    *User-owned* — *never* overwritten by a scrape:
        ``status``, ``notes``, ``eligibility_override``
    """

    model_config = ConfigDict(extra="forbid")

    # identity
    id: str
    source: str
    url: HttpUrl
    external_id: str | None = None

    # source-derived
    title: str
    company: str | None = None
    description: str = ""
    location_text: str | None = None
    compensation: Compensation = Field(default_factory=Compensation)
    contract_type: ContractType = ContractType.UNKNOWN
    tags: tuple[str, ...] = ()
    posted_at: datetime | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    content_hash: str

    # derived by pipeline stages
    eligibility: EligibilityVerdict = Field(default_factory=EligibilityVerdict.unknown)
    verification: VerificationResult | None = None
    prescore: float | None = None
    match: MatchResult | None = None

    # user-owned
    status: JobStatus = JobStatus.NEW
    notes: str | None = None
    eligibility_override: Eligibility | None = None

    @property
    def effective_eligibility(self) -> Eligibility:
        """What the UI should display: the user's override wins.

        A user who has checked the listing themselves knows better than our
        rules (FR-3.6).
        """
        return self.eligibility_override or self.eligibility.eligibility

    @property
    def searchable_text(self) -> str:
        """Concatenated text the rule engine matches against.

        Field boundaries are preserved with newlines so a rule scoped to one
        field can still be evaluated against the whole blob.
        """
        parts = [self.title, self.company, self.location_text, self.description]
        return "\n".join(p for p in parts if p)


# --------------------------------------------------------------------------
# Profile and matching
# --------------------------------------------------------------------------


class UserProfile(_Frozen):
    """The user's declared position, against which eligibility is resolved.

    This is a *declaration*, not an inference. We do not geolocate the user or
    guess from their resume — both would be creepy and wrong (ADR-0001).
    """

    country: str
    country_name: str | None = None
    timezone: str = "UTC"
    work_authorisation: tuple[str, ...] = ()
    contract_types: tuple[ContractType, ...] = ()
    currencies: tuple[str, ...] = ()
    min_hourly_rate_usd: Decimal | None = None
    skills: tuple[str, ...] = ()
    titles: tuple[str, ...] = ()
    seniority: str | None = None
    resume_text: str = ""

    @property
    def has_resume(self) -> bool:
        return bool(self.resume_text.strip())


class ScoringRequest(_Frozen):
    """One job presented to an LLM provider for scoring."""

    job_id: str
    title: str
    company: str | None
    description: str
    location_text: str | None


class MatchResult(_Frozen):
    """How well a job fits the user, per the scoring provider.

    ``provider`` is recorded because scores are only comparable *within* a
    provider — a 7B local model and a hosted frontier model do not share a
    scale, and pretending otherwise would silently corrupt the ranking
    (ADR-0006).
    """

    score: int = Field(ge=0, le=100)
    reason: str
    gaps: tuple[str, ...] = ()
    provider: str
    scored_at: datetime | None = None


Job.model_rebuild()
