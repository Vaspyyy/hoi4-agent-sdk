"""No text handed to a writer may escape the place it is written to.

``} zz_breakout = 1 {`` is balanced once it sits inside a block, so the file
it lands in still parses: the block closes early, the statement escapes into
the surrounding script and a new block opens. The same works through a value
(``ok } zz_breakout = 1 { x = 1``), an unescaped quote, a ``#`` that comments
out the rest of a line, or a key that is written bare. Only the writer knows
where its block or value was meant to end, so each one has to refuse the text
or quote it.

The cases are not a hand-written list. Every text field of every model class
is tried, read from the dataclasses themselves, with every attack text in
``PAYLOADS``, in three situations: the object is created with the field, the
field is set on a new object, and the field is set on an object that was
saved and loaded again. A new field or a new model is covered without
touching this file; a new ``create_*`` method needs one recipe below.

Where text is meant to land is not written down either. Each field is first
given the harmless statement ``zz_marker = 1``. A field that holds script (an
effect, a trigger) writes it as a statement, and the blocks around it are the
field's place: an attack may put ``zz_breakout`` there or deeper, never
outside. A field that holds one value never writes a statement at all, so
for it any ``zz_breakout`` statement is an escape.

Run this file as a script to see what each case does:
``PYTHONPATH=src python tests/test_block_body_guard.py``.
"""

from __future__ import annotations

import dataclasses
import inspect
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

from hoi4 import (
    AdvisorRole,
    AirWing,
    ArmyCommanderRole,
    Battalion,
    Bookmark,
    BookmarkCountry,
    Character,
    CharacterInstance,
    CharacterPortrait,
    Country,
    CountryLeaderRole,
    Decision,
    DecisionCategory,
    DivisionTemplate,
    DivisionUnit,
    DynamicModifier,
    EquipmentVariant,
    Event,
    EventOption,
    EventText,
    Fleet,
    Focus,
    FocusTree,
    Idea,
    Ideology,
    Mod,
    NavyLeaderRole,
    OnAction,
    Ship,
    ShipEquipment,
    State,
    SubIdeology,
    TaskForce,
)
from hoi4.definitions import DEFINITION_KINDS
from hoi4.syntax import parse
from hoi4.textio import read_text

MARKER = "zz_marker = 1"
PAYLOADS = {
    "close": "} zz_breakout = 1 {",
    "value": "ok } zz_breakout = 1 { x = 1",
    "key": "a = 1 } zz_breakout = 1 { b",
    "quote": 'x" } zz_breakout = 1 { a = "',
    "comment": "zz_breakout = 1 #",
    "balanced": "a = { } zz_breakout = 1 b",
    "statement": "zz_breakout = 1",
    "braced": "{ zz_breakout = 1 }",
}
Shape = Callable[[str], Any]


def _text(payload: str) -> Any:
    return payload

_SCRIPT_SUFFIXES = {".txt", ".gfx", ".gui", ".asset"}
# Fields that hold what was read from the file, not what is written.
_SOURCE_FIELDS = {
    "raw_block", "raw_text", "raw_definition", "raw_history", "raw_character",
    "category_raw_block", "definition_raw_block", "touched", "touched_fields",
    "path", "paths", "definition_path", "history_path", "character_path",
    "source_path", "source_index", "source_occurrence", "source_location_index",
    "_source_body", "id", "tag", "kind",
}


def _shapes(annotation: object) -> list[Shape]:
    """How a payload is handed to a field or parameter of this type."""

    text = str(annotation).replace("typing.", "").replace("Optional[", "").lower()
    text = text.strip("'\"")
    if text.startswith(("dict[str", "mapping[str")):
        return [lambda payload: {"zz_key": payload}, lambda payload: {payload: 1}]
    if "list[list[str]]" in text:
        return [lambda payload: [[payload]]]
    if "list[str]" in text or "sequence[str]" in text:
        return [lambda payload: [payload]]
    if "tuple[str, ...]" in text:
        return [lambda payload: (payload,)]
    if any(container in text for container in ("list[", "dict[", "tuple[", "sequence[", "set[")):
        return []
    return [_text] if re.search(r"\bstr\b", text) else []


def _fields(
    cls: type, skip: frozenset[str] = frozenset(), also: tuple[str, ...] = ()
) -> list[tuple[str, Shape]]:
    """Every text field of ``cls`` (``also`` names text fields typed otherwise)."""

    cases: list[tuple[str, Shape]] = [(name, _text) for name in also]
    for field in dataclasses.fields(cls):
        if field.name in _SOURCE_FIELDS or field.name in skip or field.name in also:
            continue
        cases.extend((field.name, shape) for shape in _shapes(field.type))
    return cases


def _parameters(function: Callable[..., object], skip: frozenset[str]) -> list[tuple[str, Shape]]:
    cases: list[tuple[str, Shape]] = []
    for parameter in list(inspect.signature(function).parameters.values())[1:]:
        if parameter.name in skip or parameter.kind is parameter.VAR_KEYWORD:
            continue
        cases.extend((parameter.name, shape) for shape in _shapes(parameter.annotation))
    return cases


# ── fixtures ────────────────────────────────────────────────────────


def _plain_mod(root: Path) -> Mod:
    return Mod(root)


def _map_mod(root: Path) -> Mod:
    """A mod over a tiny game folder with two states, for map and state edits."""

    game = root.parent / (root.name + "_game")
    rows = ["0;0;0;0;land;false;unknown;0"] + [
        f"{province};{province * 10};0;0;land;false;plains;1" for province in range(1, 7)
    ]

    def write(relative: str, text: str) -> None:
        target = game / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    write("map/definition.csv", "\n".join(rows) + "\n")
    for state_id, provinces in ((1, "1 2 3"), (2, "4 5 6")):
        write(
            f"history/states/{state_id}-S.txt",
            f"state = {{\n\tid = {state_id}\n\tname = \"STATE_{state_id}\"\n\tmanpower = 1\n"
            "\tstate_category = rural\n\thistory = {\n\t\towner = ABC\n\t\tadd_core_of = ABC\n"
            "\t\tbuildings = {\n\t\t\tinfrastructure = 1\n\t\t}\n\t}\n"
            f"\tprovinces = {{ {provinces} }}\n}}\n",
        )
    write(
        "map/strategicregions/1-Land.txt",
        "strategic_region = {\n\tid = 1\n\tname = \"STRATEGICREGION_1\"\n"
        "\tprovinces = { 1 2 3 4 5 6 }\n\tweather = {\n\t\tperiod = {\n"
        "\t\t\tbetween = { 0.0 30.0 }\n\t\t}\n\t}\n}\n",
    )
    write("map/buildings.txt", "1;arms_factory;0.50;1.00;3.50;0.00;0\n")
    return Mod(root, hoi4_install=game)


@dataclasses.dataclass(frozen=True)
class Recipe:
    """How one kind of object is created and changed.

    ``create`` makes a plain valid object. ``update`` sets one field of it.
    ``create_with`` makes it with one field already set. ``cases`` are the
    fields to try, each with the shape its value takes (a text, a list holding
    the text, a mapping with the text as key or as value).
    """

    name: str
    cases: list[tuple[str, Shape]]
    create: Callable[[Mod], object] | None = None
    update: Callable[[Mod, str, Any], object] | None = None
    create_with: Callable[[Mod, str, Any], object] | None = None
    make_mod: Callable[[Path], Mod] = _plain_mod


def _new_focus_tree(mod: Mod) -> None:
    mod.create_focus_tree("zz_tree", "ZZZ")
    mod.add_focus("zz_tree", Focus(id="ZZ_focus", x=0, y=0))


def _new_event(mod: Mod) -> None:
    mod.create_event("zz.1", title="zz.1.t", options=[EventOption(name="zz.1.a")])


def _new_character(mod: Mod, **fields: Any) -> None:
    mod.create_character(
        "ZZZ", Character(**{"id": "ZZZ_person", "name": "Person", **fields}), recruit=False
    )


def _new_oob(mod: Mod, **parts: Any) -> None:
    land = {
        "templates": [DivisionTemplate("Line", [Battalion("infantry", 0, 0)])],
        "divisions": [DivisionUnit("Line", 1)],
    }
    mod.create_oob("ZZZ_1936", "ZZZ", assign=False, **(parts or land))


def _role_recipe(name: str, role_type: str, cls: type) -> Recipe:
    return Recipe(
        name,
        _fields(cls),
        create=lambda mod: _new_character(mod, roles=[cls()]),
        update=lambda mod, field, value: mod.update_character_role(
            "ZZZ_person", role_type, **{field: value}
        ),
        create_with=lambda mod, field, value: _new_character(
            mod, roles=[cls(**{field: value})]
        ),
    )


def _replaced(obj: Any, field: str, value: Any) -> Any:
    return dataclasses.replace(obj, **{field: value})


_SHIP = Ship("Ship", "destroyer", [ShipEquipment("ship_hull_light_1", 1, "ZZZ")])
_FORCE = TaskForce("Force", 1, [_SHIP])
_FLEET = Fleet("Fleet", 1, [_FORCE])
_TEMPLATE = DivisionTemplate("Line", [Battalion("infantry", 0, 0)])

RECIPES: list[Recipe] = [
    Recipe(
        "focus",
        _fields(Focus),
        create=_new_focus_tree,
        update=lambda mod, field, value: mod.update_focus("zz_tree", "ZZ_focus", **{field: value}),
        create_with=lambda mod, field, value: (
            mod.create_focus_tree("zz_tree", "ZZZ"),
            mod.add_focus("zz_tree", Focus(**{"id": "ZZ_focus", "x": 0, "y": 0, field: value})),
        ),
    ),
    Recipe(
        "focus tree",
        _fields(FocusTree),
        create=_new_focus_tree,
        update=lambda mod, field, value: mod.update_focus_tree("zz_tree", **{field: value}),
    ),
    Recipe(
        "event",
        _fields(Event),
        create=_new_event,
        update=lambda mod, field, value: mod.update_event("zz.1", **{field: value}),
        create_with=lambda mod, field, value: mod.create_event(
            "zz.1", **{"title": "zz.1.t", field: value}
        ),
    ),
    Recipe(
        "event option",
        _fields(EventOption),
        create=_new_event,
        update=lambda mod, field, value: mod.update_event_option("zz.1", 0, **{field: value}),
        create_with=lambda mod, field, value: mod.create_event(
            "zz.1", title="zz.1.t", options=[EventOption(**{"name": "zz.1.a", field: value})]
        ),
    ),
    Recipe(
        "event conditional text",
        [
            (kind, lambda payload, field=field: EventText(  # type: ignore[misc]
                **{"text": "zz.1.t2", "trigger": "always = yes", field: payload}
            ))
            for kind in ("conditional_titles", "conditional_descriptions")
            for field, _ in _fields(EventText)
        ],
        create=_new_event,
        update=lambda mod, field, value: mod.update_event("zz.1", **{field: [value]}),
        create_with=lambda mod, field, value: mod.create_event(
            "zz.1", title="zz.1.t", **{field: [value]}
        ),
    ),
    Recipe(
        "event option conditional name",
        [
            ("conditional_names", lambda payload, field=field: EventText(  # type: ignore[misc]
                **{"text": "zz.1.a2", "trigger": "always = yes", field: payload}
            ))
            for field, _ in _fields(EventText)
        ],
        create=_new_event,
        update=lambda mod, field, value: mod.update_event_option("zz.1", 0, **{field: [value]}),
    ),
    Recipe(
        "idea",
        _fields(Idea),
        create=lambda mod: mod.create_idea("zz_idea"),
        update=lambda mod, field, value: mod.update_idea("zz_idea", **{field: value}),
        create_with=lambda mod, field, value: mod.create_idea("zz_idea", **{field: value}),
    ),
    Recipe(
        "decision",
        _fields(Decision),
        create=lambda mod: mod.create_decision("zz_cat", "zz_decision"),
        update=lambda mod, field, value: mod.update_decision("zz_decision", **{field: value}),
        create_with=lambda mod, field, value: mod.create_decision(
            "zz_cat", "zz_decision", **{field: value}
        ),
    ),
    Recipe(
        "decision category",
        _fields(DecisionCategory),
        create=lambda mod: mod.create_decision_category("zz_cat"),
        update=lambda mod, field, value: mod.update_decision_category("zz_cat", **{field: value}),
        create_with=lambda mod, field, value: mod.create_decision_category(
            "zz_cat", **{field: value}
        ),
    ),
    Recipe(
        "ideology",
        _fields(Ideology),
        create=lambda mod: mod.create_ideology("zz_ideology"),
        update=lambda mod, field, value: mod.update_ideology("zz_ideology", **{field: value}),
        create_with=lambda mod, field, value: mod.create_ideology(
            "zz_ideology", **{field: value}
        ),
    ),
    Recipe(
        "sub-ideology",
        [("name", _text)],
        create=lambda mod: mod.create_ideology("zz_ideology"),
        update=lambda mod, field, value: mod.update_ideology(
            "zz_ideology", types=[SubIdeology(value)]
        ),
        create_with=lambda mod, field, value: mod.create_ideology(
            "zz_ideology", types=[SubIdeology(value)]
        ),
    ),
    Recipe(
        "dynamic modifier",
        _fields(DynamicModifier),
        create=lambda mod: mod.create_dynamic_modifier("zz_dynamic"),
        update=lambda mod, field, value: mod.update_dynamic_modifier(
            "zz_dynamic", **{field: value}
        ),
        create_with=lambda mod, field, value: mod.create_dynamic_modifier(
            "zz_dynamic", **{field: value}
        ),
    ),
    Recipe(
        "bookmark",
        _fields(Bookmark, frozenset({"name"})),
        create=lambda mod: mod.create_bookmark("ZZ_BOOKMARK"),
        update=lambda mod, field, value: mod.update_bookmark("ZZ_BOOKMARK", **{field: value}),
        create_with=lambda mod, field, value: mod.create_bookmark(
            "ZZ_BOOKMARK", **{field: value}
        ),
    ),
    Recipe(
        "bookmark name",
        [("name", _text)],
        create_with=lambda mod, field, value: mod.create_bookmark(value),
    ),
    Recipe(
        "bookmark country",
        _fields(BookmarkCountry),
        create=lambda mod: mod.create_bookmark("ZZ_BOOKMARK", countries=[BookmarkCountry("GER")]),
        update=lambda mod, field, value: mod.update_bookmark_country(
            "ZZ_BOOKMARK", "GER", **{field: value}
        ),
        create_with=lambda mod, field, value: mod.create_bookmark(
            "ZZ_BOOKMARK", countries=[BookmarkCountry(**{"tag": "GER", field: value})]
        ),
    ),
    Recipe(
        "country",
        [
            *_fields(Country),
            *((name, _text) for name in ("leader_name", "leader_ideology", "leader_portrait_slug")),
        ],
        create=lambda mod: mod.create_country("ZZZ", "Zzz"),
        update=lambda mod, field, value: mod.update_country("ZZZ", **{field: value}),
        create_with=lambda mod, field, value: mod.create_country(
            "ZZZ", **{"name": "Zzz", field: value}
        ),
    ),
    Recipe(
        "country name pool",
        [
            (name, lambda payload: [payload])
            for name in ("male_names", "surnames", "female_names", "callsigns")
        ],
        create_with=lambda mod, field, value: mod.set_country_name_pool(
            "ZZZ", **{"male_names": ["A"], "surnames": ["B"], field: value}
        ),
    ),
    Recipe(
        "character",
        _fields(Character),
        create=_new_character,
        update=lambda mod, field, value: mod.update_character("ZZZ_person", **{field: value}),
        create_with=lambda mod, field, value: _new_character(mod, **{field: value}),
    ),
    Recipe(
        "character portrait",
        _fields(CharacterPortrait, also=("channel",)),
        create=_new_character,
        update=lambda mod, field, value: mod.update_character(
            "ZZZ_person", portraits=[CharacterPortrait(**{"large": "GFX_zz", field: value})]
        ),
        create_with=lambda mod, field, value: _new_character(
            mod, portraits=[CharacterPortrait(**{"large": "GFX_zz", field: value})]
        ),
    ),
    Recipe(
        "character instance",
        _fields(CharacterInstance),
        create=lambda mod: _new_character(mod, instances=[CharacterInstance()]),
        update=lambda mod, field, value: mod.update_character_instance(
            "ZZZ_person", 0, **{field: value}
        ),
        create_with=lambda mod, field, value: _new_character(
            mod, instances=[CharacterInstance(**{field: value})]
        ),
    ),
    _role_recipe("advisor role", "advisor", AdvisorRole),
    _role_recipe("country leader role", "country_leader", CountryLeaderRole),
    _role_recipe("army commander role", "corps_commander", ArmyCommanderRole),
    _role_recipe("navy leader role", "navy_leader", NavyLeaderRole),
    Recipe(
        "on_action",
        _fields(OnAction),
        create=lambda mod: mod.create_on_action("on_startup", events=["zz.1"]),
        update=lambda mod, field, value: mod.update_on_action("on_startup", **{field: value}),
        create_with=lambda mod, field, value: mod.create_on_action(
            "on_startup", **{field: value}
        ),
    ),
    Recipe(
        "on_action random event",
        [("random_events", lambda payload: ["10", payload])],
        create=lambda mod: mod.create_on_action("on_startup", random_events=["5", "zz.1"]),
        update=lambda mod, field, value: mod.update_on_action("on_startup", **{field: value}),
        create_with=lambda mod, field, value: mod.create_on_action(
            "on_startup", **{field: value}
        ),
    ),
    Recipe(
        "state",
        _fields(State),
        update=lambda mod, field, value: mod.set_state_properties(1, **{field: value}),
        make_mod=_map_mod,
    ),
    Recipe(
        "new state",
        _parameters(Mod.create_state, frozenset({"localisation_file", "provinces"})),
        create_with=lambda mod, field, value: mod.create_state(
            [3], **{"name": "New", field: value}
        ),
        make_mod=_map_mod,
    ),
    Recipe(
        "new strategic region",
        _parameters(Mod.create_strategic_region, frozenset({"localisation_file", "provinces"})),
        create_with=lambda mod, field, value: mod.create_strategic_region(
            **{"name": "Region", "provinces": [3], field: value}
        ),
        make_mod=_map_mod,
    ),
    Recipe(
        "map building",
        [("building", _text)],
        create_with=lambda mod, field, value: mod.add_map_building(1, value, 1.0, 1.0, 1.0, 0.0),
        make_mod=_map_mod,
    ),
    Recipe(
        "order of battle",
        _parameters(Mod.create_oob, frozenset({"path", "kind", "country_tag"})),
        create_with=lambda mod, field, value: mod.create_oob(
            **{
                "name": "ZZZ_1936", "country_tag": "ZZZ", "assign": False,
                "templates": [_TEMPLATE], "divisions": [DivisionUnit("Line", 1)],
                field: value,
            }
        ),
    ),
    Recipe(
        "division template",
        _fields(DivisionTemplate),
        create_with=lambda mod, field, value: _new_oob(
            mod, templates=[_replaced(_TEMPLATE, field, value)], divisions=[]
        ),
    ),
    Recipe(
        "battalion",
        _fields(Battalion),
        create_with=lambda mod, field, value: _new_oob(
            mod, templates=[DivisionTemplate("Line", [Battalion(value, 0, 0)])], divisions=[]
        ),
    ),
    Recipe(
        "division",
        _fields(DivisionUnit),
        create_with=lambda mod, field, value: _new_oob(
            mod,
            templates=[
                DivisionTemplate(
                    value if field == "division_template" else "Line",
                    [Battalion("infantry", 0, 0)],
                )
            ],
            divisions=[_replaced(DivisionUnit("Line", 1), field, value)],
        ),
    ),
    Recipe(
        "fleet",
        _fields(Fleet),
        create_with=lambda mod, field, value: _new_oob(
            mod, fleets=[_replaced(_FLEET, field, value)]
        ),
    ),
    Recipe(
        "task force",
        _fields(TaskForce),
        create_with=lambda mod, field, value: _new_oob(
            mod, fleets=[Fleet("Fleet", 1, [_replaced(_FORCE, field, value)])]
        ),
    ),
    Recipe(
        "ship",
        _fields(Ship),
        create_with=lambda mod, field, value: _new_oob(
            mod,
            fleets=[Fleet("Fleet", 1, [TaskForce("Force", 1, [_replaced(_SHIP, field, value)])])],
        ),
    ),
    Recipe(
        "ship equipment",
        _fields(ShipEquipment),
        create_with=lambda mod, field, value: _new_oob(
            mod,
            fleets=[
                Fleet(
                    "Fleet", 1,
                    [TaskForce("Force", 1, [Ship("Ship", "destroyer", [
                        _replaced(ShipEquipment("ship_hull_light_1", 1, "ZZZ"), field, value)
                    ])])],
                )
            ],
        ),
    ),
    Recipe(
        "air wing",
        _fields(AirWing),
        create_with=lambda mod, field, value: _new_oob(
            mod, air_wings=[_replaced(AirWing(1, "fighter_equipment_0", 10, "ZZZ"), field, value)]
        ),
    ),
    Recipe(
        "equipment variant",
        _fields(EquipmentVariant),
        create_with=lambda mod, field, value: mod.create_equipment_variant(
            "ZZZ",
            EquipmentVariant(**{"name": "Variant", "equipment_type": "light_tank_chassis_1", field: value}),
        ),
    ),
    Recipe(
        "definition",
        [(kind, _text) for kind in DEFINITION_KINDS],
        create_with=lambda mod, field, value: mod.create_definition(field, "zz_definition", value),
    ),
    Recipe(
        "definition id",
        [(kind, _text) for kind in DEFINITION_KINDS],
        create_with=lambda mod, field, value: mod.create_definition(field, value, "x = 1"),
    ),
    Recipe(
        "ai strategy",
        [
            *_parameters(Mod.create_ai_strategy, frozenset({"path", "plan_id", "strategies"})),
            ("strategies", lambda payload: [(payload, "GER", 1)]),
            ("strategies", lambda payload: [("alliance", payload, 1)]),
        ],
        create_with=lambda mod, field, value: mod.create_ai_strategy(
            **{"plan_id": "zz_plan", "strategies": [("alliance", "GER", 1)], field: value}
        ),
    ),
    Recipe(
        "scripted localisation",
        [
            ("entries", lambda payload: [(payload, "KEY")]),
            ("entries", lambda payload: [("always = yes", payload)]),
        ],
        create_with=lambda mod, field, value: mod.create_scripted_localisation("zz_text", value),
    ),
    Recipe(
        "static modifier",
        [("modifiers", shape) for shape in _shapes("dict[str, str]")],
        create_with=lambda mod, field, value: mod.create_static_modifier("zz_static", value),
    ),
    Recipe(
        "wargoal effect",
        _parameters(Mod.create_wargoal, frozenset()),
        create_with=lambda mod, field, value: mod.create_event(
            "zz.1", title="zz.1.t",
            immediate=mod.create_wargoal(**{"target": "GER", "war_goal_type": "annex_everything", field: value}),
        ),
    ),
]
MODES = ("created with", "set on new", "set after reload")


def _cases() -> Iterator[tuple[str, Recipe, str, str, Shape, str]]:
    for recipe in RECIPES:
        for index, (field, shape) in enumerate(recipe.cases):
            for mode in MODES:
                if mode == "created with" and recipe.create_with is None:
                    continue
                if mode != "created with" and recipe.update is None:
                    continue
                if mode == "set after reload" and recipe.create is None:
                    continue
                for payload in PAYLOADS:
                    label = f"{recipe.name}: {field} #{index} {payload} ({mode})"
                    yield label, recipe, mode, field, shape, payload


CASES = {
    label: (recipe, mode, field, shape, payload)
    for label, recipe, mode, field, shape, payload in _cases()
}


def _not_applicable(error: BaseException) -> bool:
    """A recipe asked for a field this call does not take: nothing was tested."""

    text = str(error)
    return isinstance(error, TypeError) and (
        "unexpected keyword argument" in text
        or "got multiple values" in text
        # A field the API refuses to change at all (a character's country).
        or "cannot update" in text
    )


def _apply(root: Path, recipe: Recipe, mode: str, field: str, value: Any) -> tuple[str, str]:
    """Make the edit and save. Return ``(outcome, detail)`` without judging the files."""

    mod = recipe.make_mod(root)
    try:
        if mode == "created with":
            assert recipe.create_with is not None
            recipe.create_with(mod, field, value)
        else:
            if recipe.create is not None:
                recipe.create(mod)
                if mode == "set after reload":
                    mod.save()
                    mod = type(mod)(root, hoi4_install=mod.hoi4_install)
            assert recipe.update is not None
            recipe.update(mod, field, value)
        mod.preview()
        mod.save()
    except (ValueError, RuntimeError) as error:
        # ParseError is a ValueError; the last check before saving raises
        # RuntimeError. Either way nothing was written.
        return "refused", f"{type(error).__name__}: {str(error)[:90]}"
    except Exception as error:  # noqa: BLE001 - reported by the test
        if _not_applicable(error):
            return "skipped", str(error)[:90]
        return "crashed", f"{type(error).__name__}: {str(error)[:160]}"
    return "written", ""


def _landings(root: Path, name: str) -> tuple[set[tuple[str, tuple[str, ...]]], str]:
    """Where statements called ``name`` sit: ``(file, names of the blocks around)``.

    Also returns the first file that no longer parses, or ``""``.
    """

    found: set[tuple[str, tuple[str, ...]]] = set()
    broken = ""
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _SCRIPT_SUFFIXES:
            continue
        document = parse(read_text(path))
        relative = path.relative_to(root).as_posix()
        if document.problems and not broken:
            broken = f"{relative} no longer parses: {document.problems[0].message}"
        for item, parents in document.root.walk():
            if item.name == name:
                found.add((relative, tuple(parent.name for parent in parents)))
    return found, broken


_PLACES: dict[tuple[str, str, str, int], set[tuple[str, tuple[str, ...]]]] = {}


def _place(recipe: Recipe, mode: str, field: str, shape: Shape) -> set[tuple[str, tuple[str, ...]]]:
    """Where this field writes a harmless statement, if it writes statements at all."""

    key = (recipe.name, mode, field, id(shape))
    if key not in _PLACES:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "mod"
            outcome, _ = _apply(root, recipe, mode, field, shape(MARKER))
            _PLACES[key] = _landings(root, "zz_marker")[0] if outcome == "written" else set()
    return _PLACES[key]


def run_case(
    root: Path, recipe: Recipe, mode: str, field: str, shape: Shape, payload: str
) -> tuple[str, str]:
    """Return ``(outcome, detail)``: refused, written, skipped, crashed or escaped."""

    outcome, detail = _apply(root, recipe, mode, field, shape(PAYLOADS[payload]))
    if outcome in {"skipped", "crashed"}:
        return outcome, detail
    landings, broken = _landings(root, "zz_breakout")
    if broken:
        return "escaped", broken
    place = _place(recipe, mode, field, shape) if landings else set()
    for file, parents in sorted(landings):
        inside = any(
            file == place_file and parents[: len(place_parents)] == place_parents
            for place_file, place_parents in place
        )
        if not inside:
            return "escaped", f"{file}: zz_breakout inside {' > '.join(parents) or 'the file'}"
    return outcome, detail


@pytest.mark.filterwarnings("ignore::RuntimeWarning", "ignore::DeprecationWarning")
@pytest.mark.parametrize("label", sorted(CASES))
def test_text_never_escapes_where_it_is_written(tmp_path: Path, label: str) -> None:
    recipe, mode, field, shape, payload = CASES[label]
    outcome, detail = run_case(tmp_path / "mod", recipe, mode, field, shape, payload)
    if outcome == "skipped":
        pytest.skip(detail)
    assert outcome in {"refused", "written"}, f"{outcome}: {detail}"


def test_the_probe_covers_every_writer() -> None:
    """Guard the guard: the case list must stay broad."""

    names = {recipe.name for recipe in RECIPES}
    assert len(CASES) > 3000
    assert {"focus", "event", "idea", "decision", "state", "country", "character"} <= names


if __name__ == "__main__":
    counts: dict[str, int] = {}
    for label in sorted(CASES):
        recipe, mode, field, shape, payload = CASES[label]
        with tempfile.TemporaryDirectory() as folder:
            outcome, detail = run_case(Path(folder) / "mod", recipe, mode, field, shape, payload)
        counts[outcome] = counts.get(outcome, 0) + 1
        if outcome in {"escaped", "crashed"} or "-v" in sys.argv:
            print(f"{outcome:8} {label}  {detail}")
    print(counts)
