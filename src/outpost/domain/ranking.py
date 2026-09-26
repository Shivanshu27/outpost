"""Stage 7 — local pre-scoring.

A cheap, deterministic heuristic that **orders** rather than filters.

Its whole purpose is to make stage 8 interruptible (ADR-0005). The LLM stage is
capped and quota-limited; by scoring the best candidates first, running out of
quota costs the user the *tail* of the ranking rather than a random slice. That
is the difference between "we scored your 200 most promising jobs" and "we
scored 200 arbitrary jobs and stopped".

It is deliberately not clever. It needs to be *roughly* right and very fast —
the LLM does the actual judgement.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime

from outpost.domain.models import Eligibility, Job, UserProfile

__all__ = ["prescore", "rank"]

# Weights sum to 1.0. Skill overlap dominates because it is the signal most
# predictive of the LLM's eventual judgement; the rest break ties.
_W_SKILLS = 0.55
_W_TITLE = 0.25
_W_RECENCY = 0.10
_W_ELIGIBILITY = 0.10

_RECENCY_FULL_DAYS = 7.0
_RECENCY_ZERO_DAYS = 60.0


def _token_pattern(token: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w+#]){re.escape(token.lower())}(?![\w+#])")


def _skill_overlap(job: Job, profile: UserProfile) -> float:
    """Fraction of the user's skills the listing mentions.

    Word-boundary matched so that ``go`` does not match ``going`` and ``r``
    does not match every word containing the letter. The lookarounds allow
    ``c++`` and ``c#`` to match as written.
    """
    if not profile.skills:
        return 0.0
    text = job.searchable_text.lower()
    hits = sum(1 for skill in profile.skills if _token_pattern(skill).search(text))
    # Saturate: matching 12 of 30 skills is already a strong signal, and
    # dividing by the full list would flatten every job toward zero.
    return min(1.0, hits / max(1, min(len(profile.skills), 8)))


def _title_affinity(job: Job, profile: UserProfile) -> float:
    if not profile.titles:
        return 0.0
    title = job.title.lower()
    for wanted in profile.titles:
        if wanted.lower() in title:
            return 1.0
    # Partial credit for sharing a significant word ("platform", "backend").
    wanted_words = {w for t in profile.titles for w in t.lower().split() if len(w) > 3}
    title_words = {w for w in re.findall(r"[a-z+#]+", title) if len(w) > 3}
    if wanted_words & title_words:
        return 0.5
    return 0.0


def _recency(job: Job, now: datetime) -> float:
    """1.0 for fresh, decaying to 0.0 at ~60 days.

    Unknown posting dates score mid — an undated listing should not be punished
    for a field the source simply did not provide.
    """
    if job.posted_at is None:
        return 0.5
    age_days = (now - job.posted_at).total_seconds() / 86400.0
    if age_days <= _RECENCY_FULL_DAYS:
        return 1.0
    if age_days >= _RECENCY_ZERO_DAYS:
        return 0.0
    span = _RECENCY_ZERO_DAYS - _RECENCY_FULL_DAYS
    return 1.0 - (age_days - _RECENCY_FULL_DAYS) / span


def _eligibility_weight(job: Job) -> float:
    """Confirmed-eligible sorts above unknown, which sorts above ineligible.

    This is ordering, not filtering. An ineligible job still gets a score and
    still appears; it simply is not what we spend the LLM budget on first.
    """
    match job.effective_eligibility:
        case Eligibility.ELIGIBLE:
            return 1.0
        case Eligibility.UNKNOWN:
            return 0.6
        case _:
            return 0.0


def prescore(job: Job, profile: UserProfile, *, now: datetime) -> float:
    """Score a job in [0, 1]. Cheap, deterministic, order-only."""
    return round(
        _W_SKILLS * _skill_overlap(job, profile)
        + _W_TITLE * _title_affinity(job, profile)
        + _W_RECENCY * _recency(job, now)
        + _W_ELIGIBILITY * _eligibility_weight(job),
        6,
    )


def rank(
    jobs: Sequence[Job], profile: UserProfile, *, now: datetime
) -> list[tuple[Job, float]]:
    """Return jobs with their pre-scores, best first.

    Ties break on job id so that ordering is stable across runs — an unstable
    sort would silently change which jobs fall inside the LLM cap from one run
    to the next.
    """
    scored = [(job, prescore(job, profile, now=now)) for job in jobs]
    scored.sort(key=lambda pair: (-pair[1], pair[0].id))
    return scored
