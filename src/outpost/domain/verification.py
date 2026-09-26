"""Stage 6 (part one) — legitimacy heuristics.

Pure text analysis, zero cost. The *liveness* half of verification needs an
HTTP request and lives in the adapter layer; the judgement lives here so it is
testable without a network and auditable without a provider.

Design stance, consistent with eligibility (ADR-0002): this classifies, it does
not delete. ``SUSPICIOUS`` is shown to the user with its reasons attached
(FR-4.4), because a heuristic confident enough to hide a listing would need to
be far better than any set of phrase rules can be. Only unambiguous ``SCAM``
signals and confirmed ``EXPIRED`` responses are excluded by default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from outpost.domain.models import Job, VerificationResult, VerificationTier

__all__ = [
    "CLOSED_PHRASES",
    "Signal",
    "assess_legitimacy",
    "looks_closed",
]


@dataclass(frozen=True, slots=True)
class Signal:
    """One scam indicator."""

    id: str
    pattern: re.Pattern[str]
    weight: int
    reason: str


def _p(expr: str) -> re.Pattern[str]:
    return re.compile(expr, re.IGNORECASE)


# Weight 3 alone is enough for SCAM; weight 2 needs corroboration. Nothing here
# is weighted to auto-condemn on a single soft signal — the cost of wrongly
# hiding a real job is higher than the cost of showing a suspicious one.
_SIGNALS: tuple[Signal, ...] = (
    Signal(
        "fee.upfront",
        _p(r"\b(?:registration|processing|application|training|onboarding)\s+fee\b"),
        3,
        "Asks for an upfront fee — legitimate employers never charge candidates",
    ),
    Signal(
        "fee.deposit",
        _p(r"\b(?:security\s+deposit|refundable\s+deposit|pay\s+a\s+deposit)\b"),
        3,
        "Requires a deposit from the candidate",
    ),
    Signal(
        "fee.equipment",
        _p(
            r"\bpurchase\s+(?:your\s+own\s+)?(?:equipment|software|laptop)\s+"
            r"(?:upfront|in\s+advance|from\s+us)\b"
        ),
        3,
        "Requires buying equipment from the employer up front",
    ),
    Signal(
        "contact.messaging_app",
        _p(
            r"\b(?:contact|message|reach|apply|interview)\w*\s+(?:us\s+|me\s+)?"
            r"(?:on|via|through)\s+(?:telegram|whatsapp|signal)\b"
        ),
        3,
        "Directs applicants to an off-platform messaging app",
    ),
    Signal(
        "contact.telegram_handle",
        _p(r"(?:t\.me/|@[a-z0-9_]{5,32}\s+on\s+telegram)"),
        2,
        "Contains a Telegram handle",
    ),
    Signal(
        "pay.crypto",
        _p(
            r"\b(?:paid|payment|salary|compensation)\s+(?:in|via)\s+"
            r"(?:bitcoin|btc|usdt|crypto|cryptocurrency|ethereum)\b"
        ),
        2,
        "Offers payment in cryptocurrency",
    ),
    Signal(
        "cred.bank_details",
        _p(
            r"\b(?:bank\s+account|routing\s+number|card\s+details?)\s+"
            r"(?:required|needed|to\s+start|for\s+verification)\b"
        ),
        3,
        "Requests banking details as part of applying",
    ),
    Signal(
        "claim.no_experience_high_pay",
        _p(r"\bno\s+experience\s+(?:required|necessary|needed)\b"),
        2,
        "Claims no experience is required",
    ),
    Signal(
        "claim.guaranteed",
        _p(r"\b(?:guaranteed|immediate)\s+(?:income|earnings|hire|placement)\b"),
        2,
        "Guarantees income or placement",
    ),
    Signal(
        "claim.weekly_earnings",
        _p(
            r"\bearn\s+(?:up\s+to\s+)?\$?\d[\d,]*\s*(?:\+)?\s*(?:per|a|/)\s*"
            r"(?:week|day)\b"
        ),
        2,
        "Advertises headline weekly or daily earnings",
    ),
    Signal(
        "contact.personal_email",
        _p(
            r"\b(?:send|email)\s+(?:your\s+)?(?:cv|resume|details)\s+to\s+"
            r"[\w.+-]+@(?:gmail|yahoo|hotmail|outlook|aol)\.com\b"
        ),
        2,
        "Applications go to a free personal email address",
    ),
)

_MIN_SCAM_SCORE = 3
_MIN_SUSPICIOUS_SCORE = 2

CLOSED_PHRASES: tuple[str, ...] = (
    "this position has been filled",
    "this job has been filled",
    "no longer accepting applications",
    "no longer available",
    "this posting has expired",
    "this job is closed",
    "position closed",
    "applications are closed",
    "we are no longer hiring for this role",
    "job not found",
    "this role has been filled",
)

_CLOSED_PATTERN = re.compile(
    "|".join(re.escape(phrase) for phrase in CLOSED_PHRASES), re.IGNORECASE
)


def looks_closed(page_text: str) -> bool:
    """Whether a fetched page says the role is no longer open.

    Applied to *fetched page text*, not to the indexed description — boards
    keep serving a stale description long after the ATS page says "filled".
    """
    return _CLOSED_PATTERN.search(page_text) is not None


def assess_legitimacy(job: Job) -> VerificationResult:
    """Classify a listing as OK, SUSPICIOUS or SCAM from its text alone.

    Scores are additive: one strong signal, or two soft ones, is enough to flag.
    Every triggered signal contributes its reason, so the UI can explain the
    flag rather than just asserting it.
    """
    text = job.searchable_text
    triggered = [s for s in _SIGNALS if s.pattern.search(text)]

    if not triggered:
        return VerificationResult(tier=VerificationTier.OK)

    score = sum(signal.weight for signal in triggered)
    reasons = tuple(signal.reason for signal in triggered)

    if score >= _MIN_SCAM_SCORE:
        tier = VerificationTier.SCAM
    elif score >= _MIN_SUSPICIOUS_SCORE:
        tier = VerificationTier.SUSPICIOUS
    else:
        tier = VerificationTier.OK

    return VerificationResult(tier=tier, reasons=reasons)
