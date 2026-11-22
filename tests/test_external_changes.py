"""A change behind the SDK's back is never lost, and a ``touch`` never blocks a save.

One case per kind of content the SDK writes. The files are old (their time
proves nothing about the moment they were read), so every case depends on
what the readers noted, not on a digest taken because a file was fresh.

The change made behind the SDK is a real one: another value of the same
size, where possible the very value the SDK's own edit sets. The rule under
test: the first read of a file is what the SDK knows of it, until ``save()``,
``discard()`` or ``reload()``.

* changed before that first read: the file is taken as it is, the save works
  and keeps the rest of the change;
* changed after it, before or after the SDK's own edit: ``save()`` refuses
  and leaves the file alone.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from hoi4 import ExternalModificationError, Mod

FILES = {
    "descriptor.mod": 'name = "t"\n',
    "common/country_tags/00_generated_tags.txt": 'MOD = "countries/Modland.txt"\n',
    "common/countries/Modland.txt": (
        "graphical_culture = western_european_gfx\ncolor = { 10 20 30 }\n"
    ),
    "common/countries/colors.txt": (
        "MOD = {\n\tcolor = rgb { 10 20 30 }\n\tcolor_ui = rgb { 10 20 30 }\n}\n"
    ),
    "history/countries/MOD - Modland.txt": (
        "capital = 2\nset_politics = { ruling_party = neutrality }\n"
    ),
    "history/states/2-Modstate.txt": (
        'state = {\n\tid = 2\n\tname = "STATE_2"\n\tmanpower = 100\n'
        "\tstate_category = town\n\thistory = { owner = MOD }\n\tprovinces = { 20 }\n}\n"
    ),
    "map/supply_nodes.txt": "1 10\n",
    "common/scripted_triggers/st.txt": "my_trig = { always = yes }\n",
    "common/technologies/my_tech.txt": (
        "technologies = {\n\tmy_tech = { research_cost = 1 }\n}\n"
    ),
    "events/mod_events.txt": (
        "add_namespace = mod_ev\ncountry_event = {\n\tid = mod_ev.1\n\ttitle = mod_ev.1.t\n"
        "\tdesc = mod_ev.1.d\n\toption = { name = mod_ev.1.a }\n}\n"
    ),
    "localisation/english/mod_l_english.yml": '﻿l_english:\n MOD_key: "Mine"\n',
    "common/ideas/mod.txt": (
        "ideas = {\n\tcountry = {\n\t\tmod_spirit = { modifier = { stability_factor = 0.1 } }\n\t}\n}\n"
    ),
    "common/national_focus/mod.txt": (
        "focus_tree = {\n\tid = mod_tree\n\tcountry = { factor = 0 }\n"
        "\tfocus = { id = MOD_a x = 0 y = 0 cost = 1 }\n}\n"
    ),
    "common/decisions/mod.txt": "mod_cat = {\n\tmod_dec = { cost = 1 }\n}\n",
    "common/decisions/categories/mod.txt": "mod_cat = { }\n",
    "common/on_actions/mod.txt": "on_actions = {\n\ton_startup = { effect = { log = x } }\n}\n",
    "common/characters/MOD.txt": "characters = {\n\tMOD_guy = { name = MOD_guy }\n}\n",
    "history/units/MOD_1936.txt": (
        'division_template = {\n\tname = "Inf"\n\tregiments = { infantry = { x = 0 y = 0 } }\n}\n'
        "units = { }\n"
    ),
}

Step = Callable[[Mod], object]

# name: (the file changed behind the SDK's back, a read of it, an edit that writes it)
CASES: dict[str, tuple[str, Step, Step]] = {
    "tags": (
        "common/country_tags/00_generated_tags.txt",
        lambda mod: mod.get_country("MOD"),
        lambda mod: mod.create_country("XYZ", name="Xyzland", capital=2),
    ),
    "colors": (
        "common/countries/colors.txt",
        lambda mod: mod.get_country("MOD"),
        lambda mod: mod.update_country("MOD", color=(1, 2, 3)),
    ),
    "country file": (
        "common/countries/Modland.txt",
        lambda mod: mod.get_country("MOD"),
        lambda mod: mod.update_country("MOD", color=(1, 2, 3)),
    ),
    "country history": (
        "history/countries/MOD - Modland.txt",
        lambda mod: mod.get_country("MOD"),
        lambda mod: mod.update_country("MOD", capital=2, ruling_party="fascism"),
    ),
    "state": (
        "history/states/2-Modstate.txt",
        lambda mod: mod.list_states(),
        lambda mod: mod.set_state_properties(2, manpower=5),
    ),
    "supply nodes": (
        "map/supply_nodes.txt",
        lambda mod: mod.list_supply_nodes(),
        lambda mod: mod.add_supply_node(20),
    ),
    "scripted trigger": (
        "common/scripted_triggers/st.txt",
        lambda mod: mod.list_definitions("scripted_trigger"),
        lambda mod: mod.update_definition("scripted_trigger", "my_trig", "always = no"),
    ),
    "technology": (
        "common/technologies/my_tech.txt",
        lambda mod: mod.list_definitions("technology"),
        lambda mod: mod.update_definition("technology", "my_tech", "research_cost = 2"),
    ),
    "event": (
        "events/mod_events.txt",
        lambda mod: mod.get_event("mod_ev.1"),
        lambda mod: mod.update_event("mod_ev.1", title="x"),
    ),
    "localisation": (
        "localisation/english/mod_l_english.yml",
        lambda mod: mod.get_loc("MOD_key"),
        lambda mod: mod.set_loc("MOD_key", "Yours"),
    ),
    "idea": (
        "common/ideas/mod.txt",
        lambda mod: mod.get_idea("mod_spirit"),
        lambda mod: mod.update_idea("mod_spirit", icon="x"),
    ),
    "focus": (
        "common/national_focus/mod.txt",
        lambda mod: mod.get_focus_tree("mod_tree"),
        lambda mod: mod.update_focus("mod_tree", "MOD_a", x=3),
    ),
    "decision": (
        "common/decisions/mod.txt",
        lambda mod: mod.get_decision("mod_dec"),
        lambda mod: mod.update_decision("mod_dec", cost=2),
    ),
    "on_action": (
        "common/on_actions/mod.txt",
        lambda mod: mod.list_on_actions(),
        lambda mod: mod.update_on_action("on_startup", effect="log = y"),
    ),
    "character": (
        "common/characters/MOD.txt",
        lambda mod: mod.get_character("MOD_guy"),
        lambda mod: mod.update_character("MOD_guy", name="Other"),
    ),
    "oob": (
        "history/units/MOD_1936.txt",
        lambda mod: mod.get_oob("MOD_1936"),
        lambda mod: mod.update_oob("MOD_1936", divisions=[]),
    ),
}


# What changes behind the SDK's back: (text in the file, what replaces it).
CHANGES: dict[str, tuple[str, str]] = {
    "tags": ('MOD = "countries/Modland.txt"\n', 'MOD = "countries/Modland.txt"\nEXT = "countries/Modland.txt"\n'),
    "colors": ("10 20 30", "40 50 60"),
    "country file": ("10 20 30", "40 50 60"),
    "country history": ("neutrality", "democratic"),
    "state": ("manpower = 100", "manpower = 777"),
    "supply nodes": ("1 10", "1 30"),
    "scripted trigger": ("always = yes", "has_war = no"),
    "technology": ("research_cost = 1", "research_cost = 9"),
    "event": ("title = mod_ev.1.t", "title = mod_ev.1.z"),
    "localisation": ('"Mine"', '"Ours"'),
    "idea": ("stability_factor = 0.1", "stability_factor = 0.9"),
    "focus": ("cost = 1", "cost = 9"),
    "decision": ("cost = 1", "cost = 9"),
    "on_action": ("log = x", "log = z"),
    "character": ("name = MOD_guy", "name = MOD_gal"),
    "oob": ('"Inf"', '"Art"'),
}


@pytest.fixture
def old_mod(tmp_path: Path) -> Path:
    old = time.time() - 600
    for relative, text in FILES.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        os.utime(path, (old, old))
    return tmp_path


def _marker(relative: str) -> str:
    if relative.endswith(".yml"):
        return ' EXT_key: "external"\n'
    if "supply" in relative:
        return "1 99\n"
    return "# external change\n"


def _change(path: Path, case: str, *, same_time: bool, marker: str = "") -> str:
    """Change ``path`` as another program would; returns the text it holds then."""

    old, new = CHANGES[case]
    before = path.stat()
    text = path.read_text(encoding="utf-8")
    assert old in text
    changed = text.replace(old, new) + marker
    path.write_text(changed, encoding="utf-8")
    if same_time:
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    return changed


TIMES = pytest.mark.parametrize("same_time", [False, True], ids=["new time", "old time set back"])
READ_FIRST = pytest.mark.parametrize("read_first", [True, False], ids=["read", "not read"])
EVERY_CASE = pytest.mark.parametrize("case", sorted(CASES))


@pytest.mark.parametrize("when", ["before the read", "after the read", "after the edit"])
@READ_FIRST
@EVERY_CASE
def test_a_touch_never_blocks_a_save(old_mod: Path, case: str, read_first: bool, when: str) -> None:
    relative, read, edit = CASES[case]
    mod = Mod(old_mod)
    if when == "before the read":
        os.utime(old_mod / relative, None)
    if read_first:
        read(mod)
    if when == "after the read":
        os.utime(old_mod / relative, None)

    edit(mod)
    if when == "after the edit":
        os.utime(old_mod / relative, None)
    mod.save()


@TIMES
@READ_FIRST
@EVERY_CASE
def test_a_change_before_the_first_read_is_built_on(
    old_mod: Path, case: str, read_first: bool, same_time: bool
) -> None:
    """Nothing was read yet: the file is taken as it is, as a fresh ``Mod`` would."""

    relative, read, edit = CASES[case]
    path = old_mod / relative
    marker = _marker(relative)
    mod = Mod(old_mod)
    _change(path, case, same_time=same_time, marker=marker)
    if read_first:
        read(mod)

    edit(mod)
    mod.save()
    assert marker.strip() in path.read_text(encoding="utf-8")


@TIMES
@EVERY_CASE
def test_a_change_after_the_first_read_is_refused(old_mod: Path, case: str, same_time: bool) -> None:
    """What is in memory was built from the older text: the save has to stop."""

    relative, read, edit = CASES[case]
    path = old_mod / relative
    mod = Mod(old_mod)
    read(mod)
    changed = _change(path, case, same_time=same_time)

    edit(mod)
    with pytest.raises(ExternalModificationError, match=Path(relative).name):
        mod.save()
    assert path.read_text(encoding="utf-8") == changed


@TIMES
@READ_FIRST
@EVERY_CASE
def test_a_change_after_the_edit_is_refused(
    old_mod: Path, case: str, read_first: bool, same_time: bool
) -> None:
    """The edit is waiting to be saved: a change of its file since then is not overwritten."""

    relative, read, edit = CASES[case]
    path = old_mod / relative
    mod = Mod(old_mod)
    if read_first:
        read(mod)
    edit(mod)
    changed = _change(path, case, same_time=same_time)

    with pytest.raises(ExternalModificationError, match=Path(relative).name):
        mod.save()
    assert path.read_text(encoding="utf-8") == changed


# ── Edits that replace a file they did not read ─────────────────────────────

NAMES = "MOD = {\n\tmale = { names = { Old } }\n\tsurnames = { Name }\n}\n"


def _import_trigger(mod: Mod, source: Path) -> None:
    mod.import_script_file(source, "common/scripted_triggers/st.txt", overwrite=True)


def _name_pool(mod: Mod, source: Path) -> None:
    mod.set_country_name_pool("MOD", male_names=["New"], surnames=["Name"])


def _color_table(mod: Mod, source: Path) -> None:
    mod.rebuild_country_color_table()


# name: (the file that is replaced, text another program writes there, the edit)
REPLACING: dict[str, tuple[str, tuple[str, str], Callable[[Mod, Path], None]]] = {
    "import_script_file": ("common/scripted_triggers/st.txt", ("always = yes", "has_war = no"), _import_trigger),
    "set_country_name_pool": ("common/names/00_generated_names.txt", ("Old", "Odd"), _name_pool),
    "rebuild_country_color_table": ("common/countries/colors.txt", ("10 20 30", "40 50 60"), _color_table),
}


@pytest.fixture
def replacing_mod(old_mod: Path) -> tuple[Path, Path]:
    names = old_mod / "common/names/00_generated_names.txt"
    names.parent.mkdir(parents=True)
    names.write_text(NAMES, encoding="utf-8")
    # A full colour table is only rebuilt for a mod that replaces every tag.
    descriptor = old_mod / "descriptor.mod"
    descriptor.write_text('name = "t"\nreplace_path = "common/country_tags"\n', encoding="utf-8")
    old = time.time() - 600
    for path in (names, descriptor):
        os.utime(path, (old, old))
    source = old_mod.parent / f"{old_mod.name}_source.txt"
    source.write_text("my_trig = { always = no }\n", encoding="utf-8")
    return old_mod, source


@pytest.mark.parametrize("name", sorted(REPLACING))
def test_an_edit_that_replaces_a_file_saves_after_a_touch(
    replacing_mod: tuple[Path, Path], name: str
) -> None:
    root, source = replacing_mod
    relative, _, edit = REPLACING[name]
    mod = Mod(root)
    edit(mod, source)
    os.utime(root / relative, None)

    mod.save()


@TIMES
@pytest.mark.parametrize("name", sorted(REPLACING))
def test_an_edit_that_replaces_a_file_refuses_a_change_since_the_edit(
    replacing_mod: tuple[Path, Path], name: str, same_time: bool
) -> None:
    root, source = replacing_mod
    relative, (old, new), edit = REPLACING[name]
    path = root / relative
    mod = Mod(root)
    edit(mod, source)
    before = path.stat()
    changed = path.read_text(encoding="utf-8").replace(old, new)
    path.write_text(changed, encoding="utf-8")
    if same_time:
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))

    with pytest.raises(ExternalModificationError, match=Path(relative).name):
        mod.save()
    assert path.read_text(encoding="utf-8") == changed
