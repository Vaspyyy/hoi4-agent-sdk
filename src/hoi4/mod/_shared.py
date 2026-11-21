"""Helpers and constants shared by several ``Mod`` layers."""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Sequence, cast

from ..paths import (
    resolve_mod_output_path,
)
from ..types import (
    ValidationError,
)


class ExternalModificationError(RuntimeError):
    """A source file changed on disk after this ``Mod`` instance loaded it."""


_IDEA_EFFECT_RE = re.compile(r"\b(?:add_ideas|remove_ideas)\s*=\s*([A-Za-z0-9_.:-]+)")


_HAS_IDEA_RE = re.compile(r"\bhas_idea\s*=\s*([A-Za-z0-9_.:-]+)")


_EVENT_REF_RE = re.compile(
    r"\b(?:country_event|state_event|news_event|unit_leader_event|operative_leader_event)"
    r"\s*=\s*\{[^{}]*\bid\s*=\s*([A-Za-z0-9_.:-]+)"
)


_EQUIPMENT_STOCKPILE_RE = re.compile(r"\badd_equipment_to_stockpile\s*=\s*\{([^{}]*)\}")


_TECH_BLOCK_RE = re.compile(r"\bset_technology\s*=\s*\{([^{}]*)\}")


_LOAD_FOCUS_TREE_RE = re.compile(r"\bload_focus_tree\s*=\s*\{[^{}]*\btree\s*=\s*([A-Za-z0-9_.:-]+)")


def _set_fields(obj: object, kwargs: dict[str, Any], *, allow_path: bool = False) -> None:
    from ..types import _ensure_nested

    internal = {
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
    }
    if not allow_path:
        internal.add("path")
    if not dataclasses.is_dataclass(obj):
        raise TypeError(f"Expected dataclass instance, got {type(obj).__name__}")
    valid = {f.name for f in dataclasses.fields(cast(Any, obj))} - internal
    unknown = set(kwargs) - valid
    if unknown:
        raise TypeError(f"Unknown fields: {sorted(unknown)}. Valid: {sorted(valid)}")
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
    errors: list[ValidationError],
    suppress_warnings: list[str] | tuple[str, ...] | set[str] | None = None,
) -> list[ValidationError]:
    if not suppress_warnings:
        return errors
    suppress = set(suppress_warnings)
    return [
        error
        for error in errors
        if not (
            error.severity == "warning"
            and (error.code in suppress or any(token in error.message for token in suppress))
        )
    ]
