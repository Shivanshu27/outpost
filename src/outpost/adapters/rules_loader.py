"""Loading rulesets from package resources and user files.

This is an adapter because it does I/O; the rule *language* and its evaluation
are domain (ADR-0003).

Resources resolve against the **package**, via ``importlib.resources``, never
against the current working directory. A default ruleset that is found when you
run from the repo root and silently missing when you run from elsewhere is
exactly the class of bug ADR-0009 exists to prevent.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import yaml
from pydantic import ValidationError

from outpost.domain.predicates import unknown_predicates
from outpost.domain.rules import RuleSet, RuleSetError

__all__ = ["DEFAULT_RULESET_NAME", "load_default_ruleset", "load_rules", "load_ruleset"]

DEFAULT_RULESET_NAME = "default.yml"
_RESOURCE_PACKAGE = "outpost.resources.rules"


def _parse(text: str, origin: str) -> RuleSet:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        msg = f"{origin}: invalid YAML — {exc}"
        raise RuleSetError(msg) from exc

    if not isinstance(data, dict):
        msg = (
            f"{origin}: expected a mapping at the top level, got {type(data).__name__}"
        )
        raise RuleSetError(msg)

    try:
        ruleset = RuleSet.model_validate(data)
    except ValidationError as exc:
        msg = f"{origin}: {_format_validation_error(exc)}"
        raise RuleSetError(msg) from exc

    # A typo'd predicate name would otherwise never match, and a rule that never
    # matches is indistinguishable from a listing with no restrictions — the
    # exact silent failure ADR-0002 forbids. So it is fatal, at load.
    referenced = {
        rule.when.predicate for rule in ruleset.rules if rule.when.predicate is not None
    }
    if missing := unknown_predicates(referenced):
        msg = (
            f"{origin}: unknown predicate(s) {sorted(missing)}. "
            f"Predicates must be built in; see outpost.domain.predicates."
        )
        raise RuleSetError(msg)

    return ruleset


def _format_validation_error(exc: ValidationError) -> str:
    """Render pydantic's errors in a form a ruleset author can act on."""
    lines = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        lines.append(f"  at {location}: {error['msg']}")
    return "ruleset is invalid\n" + "\n".join(lines)


def load_default_ruleset() -> RuleSet:
    """The ruleset shipped with Outpost."""
    text = (
        resources.files(_RESOURCE_PACKAGE)
        .joinpath(DEFAULT_RULESET_NAME)
        .read_text(encoding="utf-8")
    )
    return _parse(text, f"<packaged {DEFAULT_RULESET_NAME}>")


def load_ruleset(path: Path) -> RuleSet:
    """Load a ruleset from a file.

    Raises:
        RuleSetError: if the file is missing or malformed. Deliberately fatal —
            falling back to "no rules" would look identical to "no restrictions
            found" for every listing.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"{path}: could not read ruleset — {exc}"
        raise RuleSetError(msg) from exc
    return _parse(text, str(path))


def load_rules(user_path: Path | None = None) -> RuleSet:
    """The effective ruleset: the shipped defaults, with user overrides merged.

    Same-id rules replace in place so a user correction keeps its original
    evaluation position; new rules append (see :meth:`RuleSet.merge`).
    """
    ruleset = load_default_ruleset()
    if user_path is None:
        return ruleset
    if not user_path.exists():
        return ruleset
    return ruleset.merge(load_ruleset(user_path))
