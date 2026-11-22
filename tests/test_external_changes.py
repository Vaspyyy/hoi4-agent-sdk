"""A change behind the SDK's back is never lost, and a ``touch`` never blocks a save.

One case per kind of content the SDK writes. The files are old (their time
proves nothing about the moment they were read), so every case depends on
what the readers noted, not on a digest taken because a file was fresh.
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
        lambda mod: mod.update_country("MOD", capital=2, ruling_party="democratic"),
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


@pytest.mark.parametrize("read_first", [True, False], ids=["read", "not read"])
@pytest.mark.parametrize("case", sorted(CASES))
def test_a_touch_never_blocks_a_save(old_mod: Path, case: str, read_first: bool) -> None:
    relative, read, edit = CASES[case]
    mod = Mod(old_mod)
    if read_first:
        read(mod)
    os.utime(old_mod / relative, None)

    edit(mod)
    mod.save()


@pytest.mark.parametrize("same_time", [False, True], ids=["new time", "old time set back"])
@pytest.mark.parametrize("read_first", [True, False], ids=["read", "not read"])
@pytest.mark.parametrize("case", sorted(CASES))
def test_a_change_behind_the_sdk_is_refused_or_kept(
    old_mod: Path, case: str, read_first: bool, same_time: bool
) -> None:
    """Never lost: ``save()`` refuses, or what it writes is built on the changed file."""

    relative, read, edit = CASES[case]
    path = old_mod / relative
    marker = _marker(relative)
    mod = Mod(old_mod)
    if read_first:
        read(mod)
    before = path.stat()
    path.write_text(path.read_text(encoding="utf-8") + marker, encoding="utf-8")
    if same_time:
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))

    edit(mod)
    try:
        mod.save()
    except ExternalModificationError:
        pass
    assert marker.strip() in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "case",
    ["event", "localisation", "idea", "focus", "decision", "on_action", "character", "oob"],
)
def test_a_change_after_the_section_loaded_is_refused(old_mod: Path, case: str) -> None:
    """The model in memory was built from the older text: the save has to stop."""

    relative, read, edit = CASES[case]
    path = old_mod / relative
    mod = Mod(old_mod)
    read(mod)
    path.write_text(path.read_text(encoding="utf-8") + _marker(relative), encoding="utf-8")

    edit(mod)
    with pytest.raises(ExternalModificationError, match=Path(relative).name):
        mod.save()
