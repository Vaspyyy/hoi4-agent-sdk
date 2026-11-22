"""The typed ``**fields`` of ``Mod`` (``hoi4.fields``) match the models and the run-time check."""

from __future__ import annotations

import ast
import importlib.util
import re
import subprocess
import sys
import typing
from collections.abc import Callable
from pathlib import Path

import pytest

import hoi4.fields
from hoi4 import (
    AdvisorRole,
    ArmyCommanderRole,
    BookmarkCountry,
    Character,
    CharacterInstance,
    CountryLeaderRole,
    EventOption,
    Focus,
    Mod,
    NavyLeaderRole,
)

ROOT = Path(__file__).resolve().parents[1]


def _generator():
    spec = importlib.util.spec_from_file_location(
        "generate_field_types", ROOT / "scripts" / "generate_field_types.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # its dataclasses look themselves up here
    spec.loader.exec_module(module)
    return module


GENERATOR = _generator()
SPECS = {spec.name: spec for spec in GENERATOR.SPECS}


def test_fields_module_is_what_the_generator_writes_from_the_models() -> None:
    """A field added to a model, or a changed type, makes this fail until the script runs."""

    assert (ROOT / "src/hoi4/fields.py").read_text(encoding="utf-8") == GENERATOR.render(), (
        "src/hoi4/fields.py is stale: run "
        "PYTHONPATH=src python scripts/generate_field_types.py"
    )


def test_every_dict_has_the_types_of_its_source() -> None:
    built = GENERATOR.build()
    text = GENERATOR._Formatter()  # typing.Sequence and collections.abc.Sequence read the same
    for name in hoi4.fields.__all__:
        hints = typing.get_type_hints(getattr(hoi4.fields, name))
        assert {key: text(hint) for key, hint in hints.items()} == {
            key: text(hint) for key, hint in built[name].items()
        }, name


def test_every_method_named_in_a_spec_uses_its_dict() -> None:
    import inspect

    for spec in GENERATOR.SPECS:
        for method in spec.methods:
            parameters = inspect.signature(getattr(Mod, method)).parameters.values()
            kwargs = [p for p in parameters if p.kind is p.VAR_KEYWORD]
            assert len(kwargs) == 1, method
            assert kwargs[0].annotation == f"Unpack[{spec.name}]", method


def test_no_other_public_mod_method_takes_untyped_keywords() -> None:
    import inspect

    typed = {method for spec in GENERATOR.SPECS for method in spec.methods}
    for name in dir(Mod):
        if name.startswith("_") or name in typed:
            continue
        member = getattr(Mod, name)
        if not callable(member) or isinstance(member, type):
            continue
        for parameter in inspect.signature(member).parameters.values():
            assert parameter.kind is not parameter.VAR_KEYWORD, f"Mod.{name} takes **{parameter.name}"


# ── The dicts hold what the run-time check accepts ──────────────────


@pytest.fixture
def mod(tmp_path: Path) -> Mod:
    state = tmp_path / "history/states/1-S.txt"
    state.parent.mkdir(parents=True)
    state.write_text(
        "state = { id = 1 name = STATE_1 history = { owner = ABC } provinces = { 1 } }\n",
        encoding="utf-8",
    )
    mod = Mod(tmp_path)
    mod.create_country("ABC", "Abcland")
    mod.create_bookmark("zz_bm", countries=[BookmarkCountry(tag="ABC")])
    mod.create_character(
        "ABC",
        Character(
            id="ABC_hero",
            roles=[
                CountryLeaderRole(ideology="liberalism"),
                AdvisorRole(slot="political_advisor", idea_token="ABC_hero_advisor"),
                ArmyCommanderRole(kind="field_marshal"),
                NavyLeaderRole(),
            ],
            instances=[CharacterInstance()],
        ),
    )
    mod.create_decision_category("zz_cat")
    mod.create_decision("zz_cat", "zz_dec")
    mod.create_dynamic_modifier("zz_dm")
    mod.create_event("zz.1", options=[EventOption(name="zz.1.a")])
    mod.create_focus_tree("zz_tree", "ABC")
    mod.add_focus("zz_tree", Focus(id="ABC_f"))
    mod.create_idea("zz_idea")
    mod.create_ideology("zz_ideo")
    mod.create_on_action("on_startup", effect="add_political_power = 1")
    return mod


#: One call per method with an unknown field, on an object that exists.
UPDATES: dict[str, Callable[..., object]] = {
    "update_bookmark": lambda mod, **f: mod.update_bookmark("zz_bm", **f),
    "update_bookmark_country": lambda mod, **f: mod.update_bookmark_country("zz_bm", "ABC", **f),
    "update_character": lambda mod, **f: mod.update_character("ABC_hero", **f),
    "update_character_instance": lambda mod, **f: mod.update_character_instance(
        "ABC_hero", 0, **f
    ),
    "update_country": lambda mod, **f: mod.update_country("ABC", **f),
    "update_decision_category": lambda mod, **f: mod.update_decision_category("zz_cat", **f),
    "update_decision": lambda mod, **f: mod.update_decision("zz_dec", **f),
    "update_event": lambda mod, **f: mod.update_event("zz.1", **f),
    "update_event_option": lambda mod, **f: mod.update_event_option("zz.1", 0, **f),
    "update_focus_tree": lambda mod, **f: mod.update_focus_tree("zz_tree", **f),
    "ensure_focus_tree": lambda mod, **f: mod.ensure_focus_tree("zz_tree", "ABC", **f),
    "update_focus": lambda mod, **f: mod.update_focus("zz_tree", "ABC_f", **f),
    "update_idea": lambda mod, **f: mod.update_idea("zz_idea", **f),
    "update_ideology": lambda mod, **f: mod.update_ideology("zz_ideo", **f),
    "update_on_action": lambda mod, **f: mod.update_on_action("on_startup", **f),
    "update_state": lambda mod, **f: mod.update_state(1, **f),
    "update_dynamic_modifier": lambda mod, **f: mod.update_dynamic_modifier("zz_dm", **f),
}

#: ``ensure_x`` on an object that exists and on one that does not.
ENSURES: dict[str, tuple[Callable[..., object], Callable[..., object]]] = {
    "ensure_decision_category": (
        lambda mod, **f: mod.ensure_decision_category("zz_cat", **f),
        lambda mod, **f: mod.ensure_decision_category("zz_new", **f),
    ),
    "ensure_decision": (
        lambda mod, **f: mod.ensure_decision("zz_cat", "zz_dec", **f),
        lambda mod, **f: mod.ensure_decision("zz_cat", "zz_new", **f),
    ),
    "ensure_event": (
        lambda mod, **f: mod.ensure_event("zz.1", **f),
        lambda mod, **f: mod.ensure_event("zz.2", **f),
    ),
    "ensure_idea": (
        lambda mod, **f: mod.ensure_idea("zz_idea", **f),
        lambda mod, **f: mod.ensure_idea("zz_new", **f),
    ),
    "ensure_on_action": (
        lambda mod, **f: mod.ensure_on_action("on_startup", **f),
        lambda mod, **f: mod.ensure_on_action("on_new_hook", **f),
    ),
}


def _valid(call: Callable[..., object], mod: Mod) -> set[str]:
    with pytest.raises(TypeError) as caught:
        call(mod, zz_bogus=1)
    found = re.search(r"Valid: (\[.*\])", str(caught.value))
    assert found, str(caught.value)
    return set(ast.literal_eval(found.group(1)))


def _spec_of(method: str):
    return next(spec for spec in GENERATOR.SPECS if method in spec.methods)


@pytest.mark.parametrize("method", sorted(UPDATES))
def test_update_dict_holds_what_the_method_accepts(mod: Mod, method: str) -> None:
    spec = _spec_of(method)
    keys = set(getattr(hoi4.fields, spec.name).__annotations__)
    assert keys == _valid(UPDATES[method], mod) - spec.refused


@pytest.mark.parametrize("method", sorted(UPDATES))
def test_names_left_out_as_refused_are_refused(mod: Mod, method: str) -> None:
    before = mod.preview()
    for name in sorted(_spec_of(method).refused):
        with pytest.raises((TypeError, ValueError)):
            UPDATES[method](mod, **{name: "zz"})
    assert mod.preview() == before


def test_role_dict_holds_the_fields_of_every_role_type(mod: Mod) -> None:
    valid: set[str] = set()
    for role_type in ("country_leader", "advisor", "field_marshal", "navy_leader"):
        valid |= _valid(
            lambda mod, **f: mod.update_character_role("ABC_hero", role_type, **f), mod
        )
    assert set(hoi4.fields.CharacterRoleFields.__annotations__) == valid


@pytest.mark.parametrize("method", sorted(ENSURES))
def test_ensure_dict_holds_what_works_whether_the_object_exists_or_not(
    mod: Mod, method: str
) -> None:
    existing, new = ENSURES[method]
    spec = _spec_of(method)
    update = SPECS[spec.update]
    expected = (_valid(existing, mod) - update.refused) & _valid(new, mod)
    assert set(getattr(hoi4.fields, spec.name).__annotations__) == expected


# ── A type checker catches wrong calls ──────────────────────────────

DEMO = '''\
from hoi4 import Mod

mod = Mod("demo")
# Three wrong calls.
mod.update_event("demo.1", titel="demo.1.t")
mod.update_state(1, manpower=[1000])
mod.update_focus("demo_tree", "DEM_focus", touched=True)
# Three right calls.
mod.update_event("demo.1", title="demo.1.t", path="events/demo.txt")
mod.update_state(1, manpower=1000, add_cores=["DEM"])
mod.update_focus("demo_tree", "DEM_focus", prerequisites=["DEM_start"], cost=5)
'''


def test_mypy_reports_the_three_wrong_calls_and_none_of_the_right_ones(tmp_path: Path) -> None:
    pytest.importorskip("mypy")
    demo = tmp_path / "demo.py"
    demo.write_text(DEMO, encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable, "-m", "mypy", "--no-site-packages", "--python-version", "3.11",
            "--cache-dir", str(tmp_path / "cache"), "--config-file", str(ROOT / "pyproject.toml"),
            str(demo),
        ],
        capture_output=True,
        text=True,
        env={"MYPYPATH": str(ROOT / "src"), "PATH": ""},
        cwd=tmp_path,
        timeout=300,
    )
    errors = [line for line in result.stdout.splitlines() if ": error:" in line]
    assert [int(line.split(":")[1]) for line in errors] == [5, 6, 7], result.stdout
    assert 'Unexpected keyword argument "titel"' in errors[0]
    assert '"manpower"' in errors[1] and "list[int]" in errors[1]
    assert 'Unexpected keyword argument "touched"' in errors[2]


@pytest.mark.parametrize(
    ("method", "target", "own"),
    [
        ("split_state", "create_state", {"provinces"}),
        ("create_mission", "create_decision", {"category_id", "decision_id"}),
        ("create_targeted_decision", "create_decision", {"category_id", "decision_id"}),
    ],
)
def test_pass_through_dict_holds_every_keyword_of_the_called_method(
    method: str, target: str, own: set[str]
) -> None:
    import inspect

    spec = _spec_of(method)
    passing = {  # positional-only names (split_state's state_id) stay free for **fields
        name
        for name, parameter in inspect.signature(getattr(Mod, method)).parameters.items()
        if parameter.kind in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY)
    }
    called = set(inspect.signature(getattr(Mod, target)).parameters) - {"self"}
    expected = called - own - passing - spec.refused
    assert set(getattr(hoi4.fields, spec.name).__annotations__) == expected
