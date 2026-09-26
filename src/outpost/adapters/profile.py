"""Loading and saving the user's profile.

The profile is a *declaration*, not an inference: Outpost does not geolocate the
user or guess their situation from a resume. Both would be creepy, and both
would be wrong often enough to produce confident bad verdicts (ADR-0001).

Resume text is kept in a separate plain-text file rather than embedded in the
YAML. It is large, it is frequently re-edited, and keeping it separate means a
user can open it in a real editor without navigating around quoting rules.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from outpost.domain.models import ContractType, UserProfile

__all__ = ["ProfileError", "load_profile", "profile_template", "save_profile"]


class ProfileError(Exception):
    """The profile is missing or unusable."""


def load_profile(path: Path, resume_path: Path | None = None) -> UserProfile:
    """Load the profile, attaching resume text when present.

    A missing resume is not an error — the pipeline still resolves eligibility,
    verifies listings and pre-ranks them. Only LLM scoring genuinely needs it,
    and that stage is optional (ADR-0006).
    """
    if not path.exists():
        msg = (
            f"No profile at {path}. Run `outpost init` to create one — it takes "
            f"about a minute and only asks where you are and what you want."
        )
        raise ProfileError(msg)

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        msg = f"{path}: could not read profile — {exc}"
        raise ProfileError(msg) from exc

    if not isinstance(raw, dict):
        msg = f"{path}: expected a mapping at the top level"
        raise ProfileError(msg)

    resume_text = ""
    if resume_path and resume_path.exists():
        try:
            resume_text = resume_path.read_text(encoding="utf-8")
        except OSError as exc:
            msg = f"{resume_path}: could not read resume — {exc}"
            raise ProfileError(msg) from exc

    try:
        return UserProfile(
            country=str(raw["country"]).upper(),
            country_name=raw.get("country_name"),
            timezone=raw.get("timezone", "UTC"),
            work_authorisation=_tuple(raw.get("work_authorisation")),
            contract_types=tuple(
                ContractType(c) for c in _tuple(raw.get("contract_types"))
            ),
            currencies=tuple(c.upper() for c in _tuple(raw.get("currencies"))),
            min_hourly_rate_usd=(
                Decimal(str(raw["min_hourly_rate_usd"]))
                if raw.get("min_hourly_rate_usd") is not None
                else None
            ),
            skills=_tuple(raw.get("skills")),
            titles=_tuple(raw.get("titles")),
            seniority=raw.get("seniority"),
            resume_text=resume_text,
        )
    except KeyError as exc:
        msg = f"{path}: missing required field {exc.args[0]!r}"
        raise ProfileError(msg) from exc
    except (ValueError, TypeError) as exc:
        msg = f"{path}: invalid profile — {exc}"
        raise ProfileError(msg) from exc


def _tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(v) for v in value)


def save_profile(path: Path, profile: UserProfile) -> None:
    """Write the profile as YAML, without the resume text.

    Resume text is excluded deliberately: it lives in its own file, and writing
    it here would silently duplicate it and let the two drift.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "country": profile.country,
        "timezone": profile.timezone,
        "work_authorisation": list(profile.work_authorisation),
        "contract_types": [c.value for c in profile.contract_types],
        "currencies": list(profile.currencies),
        "titles": list(profile.titles),
        "skills": list(profile.skills),
    }
    if profile.country_name:
        data["country_name"] = profile.country_name
    if profile.seniority:
        data["seniority"] = profile.seniority
    if profile.min_hourly_rate_usd is not None:
        data["min_hourly_rate_usd"] = float(profile.min_hourly_rate_usd)

    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


def profile_template() -> str:
    """A commented starter profile, for users who prefer editing to prompts."""
    return """\
# Outpost profile — this is what eligibility is resolved against.
#
# Nothing here is inferred or uploaded. Outpost does not geolocate you and does
# not read your situation out of your resume; you declare it, and rules are
# evaluated against what you declared.

# ISO 3166-1 alpha-2 country code. This is the single most important field:
# it is what exempts you from region-restriction rules.
country: IN

# IANA timezone. Used to compute whether stated overlap requirements are
# actually achievable from where you are.
timezone: Asia/Kolkata

# Countries/regions where you already hold the right to work, if any.
# Leave empty if none — that is the common case and it is handled.
work_authorisation: []

# What you will accept. Empty means "no preference".
contract_types:
  - full_time
  - contract

currencies:
  - USD
  - EUR
  - GBP

# Roles you are actually looking for. Used for local pre-ranking.
titles:
  - Senior Software Engineer
  - Backend Engineer
  - Platform Engineer

# Your technical skills. Matched word-boundary-exact against listings, so
# write them the way listings write them.
skills:
  - Python
  - TypeScript
  - PostgreSQL
  - AWS
  - Docker

# Optional. Omit rather than guessing — a wrong floor silently hides listings.
# min_hourly_rate_usd: 40
"""
