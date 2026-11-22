"""Helpers and constants shared by several ``Mod`` layers."""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Sequence, TypeVar, cast

from ..errors import Hoi4Error
from ..paths import (
    resolve_mod_output_path,
)
from ..types import (
    ValidationIssue,
)


class ExternalModificationError(Hoi4Error, RuntimeError):
    """A source file changed on disk after this ``Mod`` instance loaded it."""


class _Missing(enum.Enum):
    """Marks "no ``default=`` given", so ``default=None`` is a real default."""

    MISSING = enum.auto()


#: The default of every lookup's ``default`` keyword.
MISSING = _Missing.MISSING

#: The type of a lookup's ``default``.
_D = TypeVar("_D")


#: Model fields that identify an object or record where it came from. No
#: ``update_x`` accepts them.
_INTERNAL_FIELDS = frozenset(
    {
        "id",
        "tag",
        "raw_block",
        "raw_definition",
        "raw_history",
        "raw_character",
        "source_path",
        "source_occurrence",
        "definition_path",
        "definition_raw_block",
        "history_path",
        "character_path",
        "touched",
        "touched_fields",
        "modifier_merge",
        "category_raw_block",
        "source_index",
        "paths",
        "is_vanilla",
    }
)


def _settable_fields(
    obj: object, *, allow_path: bool = False, exclude: Iterable[str] = ()
) -> set[str]:
    """The public fields of a model that ``update_x(**fields)`` may set."""
    if not dataclasses.is_dataclass(obj):
        raise TypeError(f"Expected dataclass instance, got {type(obj).__name__}")
    internal = set(_INTERNAL_FIELDS) | set(exclude)
    if not allow_path:
        internal.add("path")
    return {
        field.name
        for field in dataclasses.fields(cast(Any, obj))
        if not field.name.startswith("_")
    } - internal


def _check_fields(
    method: str,
    obj: object,
    kwargs: dict[str, Any],
    *,
    allow_path: bool = False,
    exclude: Iterable[str] = (),
    extra: Iterable[str] = (),
) -> None:
    """Raise ``TypeError`` naming ``method`` and the valid fields for an unknown name.

    Call it before anything changes, so a typo leaves the mod untouched.
    """
    valid = _settable_fields(obj, allow_path=allow_path, exclude=exclude) | set(extra)
    _check_field_names(method, kwargs, valid)


def _check_field_names(method: str, kwargs: dict[str, Any], valid: Iterable[str]) -> None:
    """Raise ``TypeError`` naming ``method`` and ``valid`` for a name not in ``valid``."""
    allowed = set(valid)
    unknown = set(kwargs) - allowed
    if unknown:
        raise TypeError(
            f"{method}(): Unknown fields: {sorted(unknown)}. Valid: {sorted(allowed)}"
        )


def _create_arguments(create: Callable[..., Any], *, skip: Iterable[str] = ()) -> set[str]:
    """The keyword arguments a ``create_x`` method takes, minus ``skip`` and ``overwrite``."""
    import inspect

    excluded = set(skip) | {"self", "overwrite"}
    return {
        name
        for name, parameter in inspect.signature(create).parameters.items()
        if name not in excluded
        and parameter.kind in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY)
    }


def _set_fields(
    obj: object,
    kwargs: dict[str, Any],
    *,
    allow_path: bool = False,
    method: str = "update",
) -> None:
    from ..types import _ensure_nested

    _check_fields(method, obj, kwargs, allow_path=allow_path)
    for key, value in kwargs.items():
        if key in ("prerequisites", "mutually_exclusive") and isinstance(value, list):
            value = _ensure_nested(value)
        setattr(obj, key, value)


def _join_script_parts(parts: Sequence[str | None]) -> str:
    return "\n".join(part.strip() for part in parts if part and part.strip())


def _decision_category_definition_path(
    mod_root: Path,
    decision_path: Path,
    shadows_inherited: Callable[[Path], bool],
) -> Path:
    """Derive a category-metadata file from a decision-content file.

    The game merges every file in ``categories/``, but a mod file replaces the
    inherited file of the same name. The derived name therefore avoids names
    that would hide a game or base-mod file.
    """

    resolved = resolve_mod_output_path(mod_root, decision_path)
    decisions_root = mod_root / "common" / "decisions"
    if resolved.parent == decisions_root / "categories":
        raise ValueError(
            "Decision content path must be under common/decisions, not its categories directory"
        )
    stem = resolved.stem
    if stem.endswith("_decisions"):
        stem = stem[: -len("_decisions")]
    candidate = decisions_root / "categories" / f"{stem}_decision_categories.txt"
    while shadows_inherited(candidate):
        candidate = candidate.with_name(f"{candidate.stem}_mod.txt")
    return candidate


def _filter_validation_errors(
    errors: list[ValidationIssue],
    *,
    suppress: Iterable[str] | None = None,
    advice: bool = True,
) -> list[ValidationIssue]:
    """Apply the severity model and the caller's suppressions to findings.

    * Every finding gets the severity its code is registered with.
    * ``"info"`` findings (advice) are kept only when ``advice`` is true.
      ``Mod.validate()`` passes its own ``advice`` (default false);
      ``Mod.validate_effect()`` passes its own (default true).
    * ``suppress`` hides warnings and advice by code: ``"code"`` hides every
      finding with that code, ``"code:ID"`` only those about the object
      ``ID`` (a focus, event, idea, decision, country tag, state id, focus
      tree, ideology, dynamic modifier or bookmark). Errors cannot be
      suppressed. An entry with an unknown code raises ``ValueError``, so a
      typo or an old message-text entry does not silently hide nothing.
    """

    from ..validation import suppression_entries, validation_suppressions

    rules = validation_suppressions(suppression_entries(suppress))
    kept: list[ValidationIssue] = []
    for error in errors:
        error = _with_registered_severity(error)
        if error.severity == "info" and not advice:
            continue
        ids = rules.get(error.code)
        if ids is not None and (not ids or not ids.isdisjoint(_finding_ids(error))):
            continue
        kept.append(error)
    return kept


def _with_registered_severity(error: ValidationIssue) -> ValidationIssue:
    from ..validation import VALIDATION_SEVERITIES

    severity = VALIDATION_SEVERITIES.get(error.code)
    if severity is None or severity == error.severity:
        return error
    return dataclasses.replace(error, severity=severity)


def _finding_ids(error: ValidationIssue) -> set[str]:
    values = (
        error.focus_id,
        error.event_id,
        error.idea_id,
        error.decision_id,
        error.country_tag,
        error.state_id,
        error.focus_tree_id,
        error.ideology_id,
        error.dynamic_modifier_id,
        error.bookmark_name,
    )
    return {str(value) for value in values if value is not None and value != ""}
