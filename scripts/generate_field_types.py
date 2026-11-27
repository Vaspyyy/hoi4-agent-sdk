"""Write ``src/hoi4/fields.py``: one ``TypedDict`` per set of keyword fields.

The ``Mod`` methods that take fields by name (``update_x``, ``ensure_x``,
``update_state``, ``split_state`` and the decision helpers that pass
keywords on to ``create_decision``) type their ``**fields`` with these
dicts, so a type checker catches a wrong name or a wrong value type.

Each dict is built from the model's dataclass (or, for the pass-through
helpers, from the signature of the method they call), with the same rules
the run-time check uses (``_settable_fields`` in ``hoi4/mod/_shared.py``):

* bookkeeping fields (``_INTERNAL_FIELDS`` and names with a leading
  underscore) are left out;
* ``path`` is in only where the method moves the object with it;
* names the run-time check lets through but the method then always refuses
  (``update_focus_tree(country_tag=...)`` and the like) are left out too.

An ``ensure_x`` dict holds only the fields that work both when the object
exists (``update_x``) and when it is created (``create_x``), so a call that
type-checks never fails because of which case it meets.

Usage, from the repository root::

    PYTHONPATH=src python scripts/generate_field_types.py          # write the file
    PYTHONPATH=src python scripts/generate_field_types.py --check  # exit 1 if it is stale

``tests/test_field_types.py`` runs the check, so a dataclass and its dict
cannot drift apart.
"""

from __future__ import annotations

import argparse
import collections.abc
import dataclasses
import inspect
import sys
import textwrap
import types
import typing
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from hoi4.bookmarks import Bookmark, BookmarkCountry
from hoi4.characters import (
    AdvisorRole,
    ArmyCommanderRole,
    Character,
    CharacterInstance,
    CountryLeaderRole,
    NavyLeaderRole,
)
from hoi4.dynamic_modifiers import DynamicModifier
from hoi4.ideologies import Ideology
from hoi4.mod import Mod
from hoi4.mod._shared import _INTERNAL_FIELDS
from hoi4.types import (
    Country,
    Decision,
    DecisionCategory,
    Event,
    EventOption,
    Focus,
    FocusTree,
    Idea,
    Leader,
    OnAction,
    State,
)

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "hoi4" / "fields.py"

#: What ``path=`` takes where a method moves an object to another file.
PATH_VALUE: Any = str | Path


@dataclasses.dataclass(frozen=True)
class ModelDict:
    """A dict of the settable fields of one or more dataclasses."""

    name: str
    methods: tuple[str, ...]
    models: tuple[type, ...]
    allow_path: bool = False
    #: Accepted by the name check, but the method always refuses them.
    refused: frozenset[str] = frozenset()
    #: Run-time check uses another list than the dataclass (``None``: the dataclass).
    only: frozenset[str] | None = None
    #: Types that differ from the dataclass because the method converts the value.
    overrides: tuple[tuple[str, Any], ...] = ()
    #: Extra keywords with their types (``category_path`` and the like).
    extra: tuple[tuple[str, Any], ...] = ()
    #: ``(prefix, model, excluded)``: ``leader_<field>`` keys from a nested model.
    prefixed: tuple[str, type, frozenset[str]] | None = None
    #: Fields of the role classes are internal under another name.
    internal: frozenset[str] = frozenset()
    #: Dataclass fields that are settable although ``_INTERNAL_FIELDS`` lists them.
    no_default_internal: bool = False


@dataclasses.dataclass(frozen=True)
class EnsureDict:
    """The fields that both ``update_x`` and ``create_x`` take."""

    name: str
    methods: tuple[str, ...]
    update: str
    create: Callable[..., Any]
    skip: frozenset[str]


@dataclasses.dataclass(frozen=True)
class OptionsDict:
    """The keyword arguments of a method that another method passes on."""

    name: str
    methods: tuple[str, ...]
    function: Callable[..., Any]
    skip: frozenset[str]
    #: Keywords the passing method refuses.
    refused: frozenset[str] = frozenset()


_ROLE_INTERNAL = frozenset({"raw_block", "touched_fields", "source_occurrence", "kind"})

SPECS: tuple[ModelDict | EnsureDict | OptionsDict, ...] = (
    # ``name`` is also the method's first parameter, so Python refuses it.
    ModelDict("BookmarkFields", ("update_bookmark",), (Bookmark,), allow_path=True,
              refused=frozenset({"name"}), overrides=(("path", PATH_VALUE),)),
    ModelDict("BookmarkCountryFields", ("update_bookmark_country",), (BookmarkCountry,)),
    ModelDict("CharacterFields", ("update_character",), (Character,),
              only=frozenset({"name", "portraits"})),
    ModelDict("CharacterInstanceFields", ("update_character_instance",), (CharacterInstance,),
              only=frozenset({"allowed", "name", "portraits"})),
    ModelDict("CharacterRoleFields", ("update_character_role",),
              (CountryLeaderRole, AdvisorRole, ArmyCommanderRole, NavyLeaderRole),
              internal=_ROLE_INTERNAL, no_default_internal=True),
    ModelDict("CountryFields", ("update_country",), (Country,),
              prefixed=("leader_", Leader, frozenset({"character_id"}))),
    ModelDict("DecisionCategoryFields", ("update_decision_category",), (DecisionCategory,),
              extra=(("path", PATH_VALUE), ("category_path", PATH_VALUE))),
    ModelDict("DecisionFields", ("update_decision",), (Decision,),
              refused=frozenset({"category"}),
              overrides=(("targets", collections.abc.Sequence[str] | str),)),
    ModelDict("EventFields", ("update_event",), (Event,), allow_path=True,
              overrides=(("path", PATH_VALUE),)),
    ModelDict("EventOptionFields", ("update_event_option",), (EventOption,)),
    ModelDict("FocusTreeFields", ("update_focus_tree", "ensure_focus_tree"), (FocusTree,),
              allow_path=True, refused=frozenset({"country_tag", "kind"}),
              overrides=(("path", PATH_VALUE),)),
    ModelDict("FocusFields", ("update_focus",), (Focus,),
              overrides=(
                  ("prerequisites", list[list[str]] | list[str]),
                  ("mutually_exclusive", list[list[str]] | list[str]),
              )),
    ModelDict("IdeaFields", ("update_idea",), (Idea,), allow_path=True,
              refused=frozenset({"desc"}),
              overrides=(("path", PATH_VALUE),)),
    ModelDict("IdeologyFields", ("update_ideology",), (Ideology,), allow_path=True,
              overrides=(("path", PATH_VALUE),)),
    ModelDict("OnActionFields", ("update_on_action",), (OnAction,), allow_path=True,
              overrides=(("path", PATH_VALUE),)),
    ModelDict("StateFields", ("update_state",), (State,)),
    ModelDict("DynamicModifierFields", ("update_dynamic_modifier",), (DynamicModifier,)),
    EnsureDict("EnsureDecisionCategoryFields", ("ensure_decision_category",),
               "DecisionCategoryFields", Mod.create_decision_category,
               frozenset({"category_id"})),
    EnsureDict("EnsureDecisionFields", ("ensure_decision",), "DecisionFields",
               Mod.create_decision, frozenset({"category_id", "decision_id"})),
    EnsureDict("EnsureEventFields", ("ensure_event",), "EventFields", Mod.create_event,
               frozenset({"event_id"})),
    EnsureDict("EnsureIdeaFields", ("ensure_idea",), "IdeaFields", Mod.create_idea,
               frozenset({"idea_id"})),
    EnsureDict("EnsureOnActionFields", ("ensure_on_action",), "OnActionFields",
               Mod.create_on_action, frozenset({"action_id"})),
    OptionsDict("CreateStateOptions", ("split_state",), Mod.create_state,
                frozenset({"provinces"})),
    OptionsDict("MissionOptions", ("create_mission",), Mod.create_decision,
                frozenset(inspect.signature(Mod.create_mission).parameters),
                refused=frozenset({"cost", "custom_cost_trigger"})),
    OptionsDict("TargetedDecisionOptions", ("create_targeted_decision",), Mod.create_decision,
                frozenset(inspect.signature(Mod.create_targeted_decision).parameters)),
)


def _model_keys(spec: ModelDict) -> dict[str, Any]:
    keys: dict[str, Any] = {}
    for model in spec.models:
        hints = typing.get_type_hints(model)
        internal = set(spec.internal)
        if not spec.no_default_internal:
            internal |= _INTERNAL_FIELDS
            if not spec.allow_path:
                internal.add("path")
        for field in dataclasses.fields(model):
            name = field.name
            if name.startswith("_") or name in internal or name in spec.refused:
                continue
            if spec.only is not None and name not in spec.only:
                continue
            hint = hints[name]
            if name in keys and keys[name] != hint:
                hint = keys[name] | hint
            keys[name] = hint
    for name, hint in spec.extra:
        keys[name] = hint
    if spec.prefixed is not None:
        prefix, model, excluded = spec.prefixed
        hints = typing.get_type_hints(model)
        for field in dataclasses.fields(model):
            if field.name in excluded or field.name in _INTERNAL_FIELDS:
                continue
            keys[prefix + field.name] = hints[field.name]
    for name, hint in spec.overrides:
        if name not in keys:
            raise SystemExit(f"{spec.name}: override for unknown key {name!r}")
        keys[name] = hint
    return keys


def _function_keys(
    function: Callable[..., Any], skip: Iterable[str], *, keep_overwrite: bool = False
) -> dict[str, Any]:
    hints = typing.get_type_hints(function)
    excluded = set(skip) | {"self"} | (set() if keep_overwrite else {"overwrite"})
    return {
        name: hints[name]
        for name, parameter in inspect.signature(function).parameters.items()
        if name not in excluded
        and parameter.kind in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY)
    }


def build() -> dict[str, dict[str, Any]]:
    """Every dict's keys and types, in ``SPECS`` order."""

    built: dict[str, dict[str, Any]] = {}
    for spec in SPECS:
        if isinstance(spec, ModelDict):
            built[spec.name] = _model_keys(spec)
        elif isinstance(spec, EnsureDict):
            created = _function_keys(spec.create, spec.skip)
            built[spec.name] = {
                name: hint for name, hint in built[spec.update].items() if name in created
            }
        else:
            keys = _function_keys(spec.function, spec.skip, keep_overwrite=True)
            built[spec.name] = {
                name: hint for name, hint in keys.items() if name not in spec.refused
            }
    return built


class _Formatter:
    """Turns a resolved type into source text and notes what it needs to import."""

    def __init__(self) -> None:
        self.imports: dict[str, set[str]] = {}

    def _need(self, module: str, name: str) -> str:
        self.imports.setdefault(module, set()).add(name)
        return name

    def __call__(self, hint: Any) -> str:
        if hint is type(None) or hint is None:
            return "None"
        origin = typing.get_origin(hint)
        args = typing.get_args(hint)
        if origin in (typing.Union, types.UnionType):
            parts = [self(arg) for arg in args if arg is not type(None)]
            if type(None) in args:
                parts.append("None")
            return " | ".join(parts)
        if origin is typing.Literal:
            self._need("typing", "Literal")
            return f"Literal[{', '.join(repr(arg) for arg in args)}]"
        if origin is not None:
            if origin in (list, dict, set, tuple, frozenset):
                name = origin.__name__
            elif getattr(origin, "__module__", "") == "collections.abc":
                name = self._need("collections.abc", origin.__name__)
            else:
                raise SystemExit(f"Cannot write the type {hint!r}")
            inner = ", ".join("..." if arg is Ellipsis else self(arg) for arg in args)
            return f"{name}[{inner}]"
        if isinstance(hint, type):
            if hint.__module__ == "builtins":
                return hint.__name__
            module = hint.__module__
            if module.startswith("pathlib"):
                module = "pathlib"
            return self._need(module, hint.__name__)
        raise SystemExit(f"Cannot write the type {hint!r}")


def _docstring(spec: ModelDict | EnsureDict | OptionsDict) -> list[str]:
    methods = " and ".join(f"``Mod.{method}()``" for method in spec.methods)
    if isinstance(spec, ModelDict):
        text = f"The fields {methods} can set."
    elif isinstance(spec, EnsureDict):
        update = next(s for s in SPECS if s.name == spec.update).methods[0]
        text = (
            f"The fields {methods} takes: the ones both ``Mod.{update}()`` and "
            f"``Mod.{spec.create.__name__}()`` accept, so the call works whether "
            "the object exists or not."
        )
    else:
        text = (
            f"The keywords {methods} passes on to ``Mod.{spec.function.__name__}()``."
        )
    lines = textwrap.wrap(text, width=84)
    if len(lines) == 1:
        return [f'    """{lines[0]}"""']
    return ['    """' + lines[0], *(f"    {line}" for line in lines[1:]), '    """']


def render() -> str:
    """The text of ``fields.py``."""

    built = build()
    formatter = _Formatter()
    classes = []
    for spec in SPECS:
        lines = [f"class {spec.name}(TypedDict, total=False):", *_docstring(spec), ""]
        for name, hint in built[spec.name].items():
            lines.append(f"    {name}: {formatter(hint)}")
        classes.append("\n".join(lines))
    import_lines = []
    for module in sorted(formatter.imports, key=lambda m: (m.startswith("hoi4"), m)):
        names = ", ".join(sorted(formatter.imports[module]))
        relative = "." + module.removeprefix("hoi4.") if module.startswith("hoi4.") else module
        import_lines.append(f"from {relative} import {names}")
    typing_line = "from typing import TypedDict"
    if "typing" in formatter.imports:
        import_lines = [line for line in import_lines if not line.startswith("from typing ")]
        typing_line = (
            f"from typing import {', '.join(sorted(formatter.imports['typing'] | {'TypedDict'}))}"
        )
    stdlib = [line for line in import_lines if not line.startswith("from .")]
    local = [line for line in import_lines if line.startswith("from .")]
    names = "\n".join(f'    "{spec.name}",' for spec in sorted(SPECS, key=lambda s: s.name))
    header = '''"""Typed keyword fields of the ``Mod`` methods that take fields by name.

A type checker (mypy, pyright, an editor) uses these to catch a wrong field
name or a wrong value type in calls such as ``mod.update_event(id, titel=...)``.
Callers without a type checker still get a ``TypeError`` listing the valid
names at run time.

To build the fields first and pass them later, annotate the dict::

    from hoi4.fields import EventFields

    changes: EventFields = {"title": "my_event.t", "picture": "GFX_report_event_generic"}
    mod.update_event("my_event.1", **changes)

Generated by ``scripts/generate_field_types.py`` from the model dataclasses.
Do not edit by hand; run the script again after changing a model.
"""

from __future__ import annotations

'''
    body = "\n".join(stdlib) + "\n" + typing_line + "\n\n" + "\n".join(local)
    return (
        header
        + body
        + "\n\n__all__ = [\n"
        + names
        + "\n]\n\n\n"
        + "\n\n\n".join(classes)
        + "\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if fields.py is stale")
    options = parser.parse_args(argv)
    text = render()
    if options.check:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != text:
            print(f"{TARGET.relative_to(ROOT)} is stale; run {Path(__file__).name} again")
            return 1
        print(f"{TARGET.relative_to(ROOT)} is up to date")
        return 0
    TARGET.write_text(text, encoding="utf-8")
    print(f"wrote {TARGET.relative_to(ROOT)} ({len(SPECS)} TypedDicts)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
