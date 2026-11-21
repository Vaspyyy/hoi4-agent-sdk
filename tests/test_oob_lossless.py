"""Orders of battle the game tolerates load, edit minimally and never crash validation."""

from __future__ import annotations

import copy
from pathlib import Path

from hoi4 import Mod
from hoi4.oob import serialize_oob
from hoi4.syntax import parse


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# Like the game's FRA_1936.txt: the units block is never closed.
UNCLOSED = """division_template = {
\tname = "Infantry"
\tregiments = {
\t\tinfantry = { x = 0 y = 0 }
\t}
}

units = {
\tdivision = {
\t\tname = "First"
\t\tlocation = 100
\t\tdivision_template = "Infantry"
\t}
\tdivision = {
\t\tname = "Second"
\t\tlocation = 101
\t\tdivision_template = "Infantry"
\t}
"""


def _game(tmp_path: Path) -> Path:
    game = tmp_path / "game"
    _write(game / "common/country_tags/test.txt", 'ABC = "countries/ABC.txt"\n')
    _write(game / "common/countries/ABC.txt", "color = { 1 2 3 }\n")
    _write(game / "history/countries/ABC.txt", 'capital = 1\noob = "ABC_1936"\n')
    _write(game / "history/units/ABC_1936.txt", UNCLOSED)
    return game


def test_unclosed_inherited_oob_loads(tmp_path: Path) -> None:
    mod = Mod(tmp_path / "mod", hoi4_install=_game(tmp_path))
    oob = mod.get_oob("ABC_1936")
    assert [division.name for division in oob.divisions] == ["First", "Second"]
    assert [template.name for template in oob.templates] == ["Infantry"]


def test_validation_does_not_raise_after_editing_the_country(tmp_path: Path) -> None:
    mod = Mod(tmp_path / "mod", hoi4_install=_game(tmp_path))
    mod.update_country("ABC", color=(10, 20, 30))
    for stage in ("build", "release"):
        result = mod.validate(stage=stage)
        assert "oob_load_error" not in {issue.code for issue in result}


def test_editing_an_unclosed_inherited_oob_writes_a_well_formed_override(tmp_path: Path) -> None:
    game = _game(tmp_path)
    mod = Mod(tmp_path / "mod", hoi4_install=game)
    divisions = copy.deepcopy(mod.get_oob("ABC_1936").divisions)
    divisions[1].location = 202
    divisions[1].touched_fields.add("location")
    assert mod.update_oob("ABC_1936", divisions=divisions)
    # A new override previews as a whole new file, already repaired.
    preview = mod.preview()
    assert "+\t\tlocation = 202" in preview and preview.endswith("+}\n")
    mod.save()

    written = (tmp_path / "mod/history/units/ABC_1936.txt").read_text(encoding="utf-8")
    assert written == UNCLOSED.replace("location = 101", "location = 202") + "}\n"
    assert not parse(written).problems
    assert (game / "history/units/ABC_1936.txt").read_text(encoding="utf-8") == UNCLOSED
    reloaded = Mod(tmp_path / "mod", hoi4_install=game)
    assert not reloaded.load_diagnostics
    assert reloaded.get_oob("ABC_1936").divisions[1].location == 202


def test_adding_a_division_to_an_unclosed_oob_lands_inside_units(tmp_path: Path) -> None:
    mod = Mod(tmp_path / "mod", hoi4_install=_game(tmp_path))
    divisions = copy.deepcopy(mod.get_oob("ABC_1936").divisions)
    extra = copy.deepcopy(divisions[0])
    extra.name, extra.location, extra.source_index, extra.raw_block = "Third", 103, -1, ""
    assert mod.update_oob("ABC_1936", divisions=[*divisions, extra])
    mod.save()
    reloaded = Mod(tmp_path / "mod", hoi4_install=tmp_path / "game")
    assert [d.name for d in reloaded.get_oob("ABC_1936").divisions] == ["First", "Second", "Third"]
    assert not reloaded.load_diagnostics


def test_unclosed_oob_in_the_mod_is_a_load_failure(tmp_path: Path) -> None:
    _write(tmp_path / "history/units/ABC_1936.txt", UNCLOSED)
    mod = Mod(tmp_path)
    assert [diagnostic.section for diagnostic in mod.load_diagnostics] == ["oob"]


def test_dirty_oob_without_changes_renders_byte_identical(tmp_path: Path) -> None:
    source = UNCLOSED + "}\n# trailing comment\nair_wings = {\n\t100 = {\n\t\tfighter_equipment_0 = { owner = \"ABC\" amount = 10 }\n\t}\n}\n"
    _write(tmp_path / "history/units/ABC_1936.txt", source)
    oob = Mod(tmp_path).get_oob("ABC_1936")
    oob.touched_fields.update({"templates", "divisions", "fleets", "air_wings"})
    assert serialize_oob(oob) == source


def test_renaming_an_air_wings_equipment_replaces_only_its_key(tmp_path: Path) -> None:
    source = (
        "air_wings = {\n\t100 = {\n"
        "\t\t\"fighter_equipment_0\" = { owner = \"ABC\" amount = 10 } # keep\n"
        "\t}\n}\n"
    )
    path = _write(tmp_path / "history/units/ABC_air.txt", source)
    mod = Mod(tmp_path)
    wings = copy.deepcopy(mod.get_oob("ABC_air").air_wings)
    wings[0].equipment_type = "fighter_equipment_1"
    assert mod.update_oob("ABC_air", air_wings=wings)
    mod.save()
    assert path.read_text(encoding="utf-8") == source.replace(
        '"fighter_equipment_0"', "fighter_equipment_1"
    )


SHIPS = """units = {
\tfleet = {
\t\tname = "Fleet"
\t\tnaval_base = 200
\t\ttask_force = {
\t\t\tname = "Subs"
\t\t\tlocation = 200
\t\t\tship = { ordered_name = 401 definition = submarine equipment = { ship_hull_submarine_1 = { amount = 1 owner = ABC } } }\t\t\t
\t\t\tship = { name = "Named" definition = submarine equipment = { ship_hull_submarine_1 = { amount = 1 owner = ABC } } }
\t\t}
\t}
}
"""


def test_passing_ships_back_unchanged_keeps_the_file(tmp_path: Path) -> None:
    path = _write(tmp_path / "history/units/ABC_naval.txt", SHIPS)
    mod = Mod(tmp_path)
    oob = mod.get_oob("ABC_naval")
    assert mod.update_oob("ABC_naval", fleets=oob.fleets)
    mod.save()
    # A ship named by ``ordered_name`` must not gain ``name = ""``.
    assert path.read_text(encoding="utf-8") == SHIPS


def test_new_field_in_a_one_line_ship_stays_on_that_line(tmp_path: Path) -> None:
    path = _write(tmp_path / "history/units/ABC_naval.txt", SHIPS)
    mod = Mod(tmp_path)
    fleets = copy.deepcopy(mod.get_oob("ABC_naval").fleets)
    fleets[0].task_forces[0].ships[1].pride_of_the_fleet = True
    assert mod.update_oob("ABC_naval", fleets=fleets)
    mod.save()
    assert path.read_text(encoding="utf-8") == SHIPS.replace(
        "owner = ABC } } }\n\t\t}", "owner = ABC } } pride_of_the_fleet = yes }\n\t\t}"
    )
