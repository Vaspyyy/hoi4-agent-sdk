"""Helpers and constants shared by several ``Mod`` layers."""

from __future__ import annotations

import dataclasses
import enum
import types
import typing
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any, TypeVar, cast
from collections.abc import Sequence

from ..errors import Hoi4Error
from ..paths import (
    resolve_mod_output_path,
)
from ..types import (
    ValidationIssue,
)


_Parsed = TypeVar("_Parsed")


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
        "raw_text",
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
    _check_model_lists(method, type(obj), kwargs)


def _check_model_lists(
    method: str, model: type, values: dict[str, Any], *, none_is_unset: bool = False
) -> None:
    """Check the values given for fields of ``model`` before anything is set.

    Two things are refused with a ``TypeError`` that names ``method``:
    ``None`` for a field that cannot be empty (its type does not allow
    ``None``), and a list of models that holds something else, at any depth
    (an ``EventOption`` in ``options`` whose ``conditional_names`` holds a
    string). Both used to be accepted and then made ``preview()`` and
    ``save()`` raise.

    Each checked list is put back into ``values`` as a list, so a generator
    is read once and its items are kept for the caller. ``none_is_unset``
    is for methods whose own keywords default to ``None`` ("not given").
    """
    lists = _model_list_fields(model)
    can_be_none = _nullable_fields(model)
    for name, value in list(values.items()):
        if value is None:
            if not none_is_unset and can_be_none is not None and name not in can_be_none:
                raise TypeError(
                    f"{method}(): {name} cannot be None. Pass an empty value "
                    "(an empty list, dict or string) to clear it."
                )
            continue
        if name in lists:
            values[name] = _require_items(method, name, value, lists[name])


def _check_model(method: str, obj: object, label: str) -> None:
    """Raise ``TypeError`` when ``obj`` is not a model or a list in it holds another class.

    For a model the caller built and hands over whole (a ``Character``, an
    ``EventOption``, a ``Focus``): its lists of models are checked at every
    depth, like the ``**fields`` of an update. The caller's object is left
    as it is; only a list given as a one-shot iterator is replaced by a real
    list, because reading it uses it up.
    """
    if not dataclasses.is_dataclass(obj) or isinstance(obj, type):
        raise TypeError(f"{method}(): {label} must be a model object, got {type(obj).__name__}")
    _check_lists_inside(method, label, obj)


def _check_lists_inside(method: str, label: str, obj: object) -> None:
    for name, classes in _model_list_fields(type(obj)).items():
        value = getattr(obj, name)
        checked = _require_items(method, f"{label}.{name}", value, classes)
        if not isinstance(value, (list, tuple)):
            setattr(obj, name, checked)


_NULLABLE_FIELDS: dict[type, frozenset[str] | None] = {}


def _nullable_fields(model: type) -> frozenset[str] | None:
    """The fields of ``model`` whose type allows ``None``; ``None`` when the types cannot be read."""
    if model in _NULLABLE_FIELDS:
        return _NULLABLE_FIELDS[model]
    found: frozenset[str] | None
    try:
        hints = typing.get_type_hints(model)
    except Exception:  # a hint that only resolves for the type checker
        found = None
    else:
        found = frozenset(
            name
            for name, hint in hints.items()
            if hint is Any
            or hint is object
            or hint is type(None)
            or (
                typing.get_origin(hint) in (typing.Union, types.UnionType)
                and type(None) in typing.get_args(hint)
            )
        )
    _NULLABLE_FIELDS[model] = found
    return found


_MODEL_LIST_FIELDS: dict[type, dict[str, tuple[type, ...]]] = {}


def _model_list_fields(model: type) -> dict[str, tuple[type, ...]]:
    """The fields of ``model`` that hold a list of other models, with the classes allowed in it.

    ``Ideology.types`` is a ``list[SubIdeology]``: a string in that list would
    only fail later, inside ``preview()`` or ``save()``.
    """
    known = _MODEL_LIST_FIELDS.get(model)
    if known is not None:
        return known
    found: dict[str, tuple[type, ...]] = {}
    _MODEL_LIST_FIELDS[model] = found
    try:
        hints = typing.get_type_hints(model)
    except Exception:  # a hint that only resolves for the type checker
        return found
    for name, hint in hints.items():
        if typing.get_origin(hint) is not list:
            continue
        arguments = typing.get_args(hint)
        if len(arguments) != 1:
            continue
        item = arguments[0]
        members = (
            typing.get_args(item)
            if typing.get_origin(item) in (typing.Union, types.UnionType)
            else (item,)
        )
        if members and all(
            isinstance(member, type) and dataclasses.is_dataclass(member) for member in members
        ):
            found[name] = tuple(members)
    return found


def _require_items(
    method: str, name: str, values: object, classes: tuple[type, ...]
) -> list[Any]:
    """Return ``values`` as a list, or raise ``TypeError`` unless it holds ``classes`` objects.

    Use the returned list, not ``values``: a generator can be read only once.
    """
    wanted = " or ".join(cls.__name__ for cls in classes)
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise TypeError(
            f"{method}(): {name} must be a list of {wanted} objects, got {type(values).__name__}"
        )
    items = list(values)
    for index, item in enumerate(items):
        if not isinstance(item, classes):
            raise TypeError(
                f"{method}(): {name} must hold {wanted} objects, got {type(item).__name__}"
            )
        _check_lists_inside(method, f"{name}[{index}]", item)
    return items


def _typed_dict_keys(options: type) -> frozenset[str]:
    """Every key of a ``TypedDict`` class (the keywords a method passes on)."""
    return frozenset(options.__required_keys__ | options.__optional_keys__)  # type: ignore[attr-defined]


def _check_field_names(
    method: str, kwargs: Mapping[Any, Any], valid: Iterable[str], *, where: str = ""
) -> None:
    """Raise ``TypeError`` naming ``method`` and ``valid`` for a name not in ``valid``.

    ``where`` says which part of the call holds the name (``" in step 2"``).
    """
    allowed = set(valid)
    unknown = set(kwargs) - allowed
    if unknown:
        # A mapping the caller built can have keys that are not strings.
        names = sorted(unknown, key=str)
        raise TypeError(
            f"{method}(): Unknown fields{where}: {names}. Valid: {sorted(allowed)}"
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


def _keep_recent(
    previous: dict[str, _Parsed], current: dict[str, _Parsed], *, floor: int = 64
) -> dict[str, _Parsed]:
    """A cache keyed by exact text, after one run: this run's entries and the newest older ones.

    An edit that is undone (a transaction that rolls back) finds its text
    again, and a long session of edits does not keep every text it ever saw:
    the result holds at most three times the entries of this run, and at
    least ``floor``.
    """

    kept = {key: value for key, value in previous.items() if key not in current}
    kept.update(current)
    limit = max(floor, 3 * len(current))
    for key in list(kept)[: max(0, len(kept) - limit)]:
        del kept[key]
    return kept


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
