"""Orders of battle the game tolerates load, edit minimally and never crash validation."""

from __future__ import annotations

import copy
from pathlib import Path

from hoi4 import Mod
from hoi4.oob import DivisionUnit, Ship, ShipEquipment, serialize_oob
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


# Edits are found by comparing each item with its source block, so copies and
# plain attribute assignments are written without any ``touched_fields``.
LAND = """division_template = {
\tname = "Infantry"
\tregiments = {
\t\tinfantry = { x = 0 y = 0 }
\t}
\tpriority = 1
}
division_template = {
\tname = "Cavalry"
\tregiments = { cavalry = { x = 0 y = 0 } }
}

units = {
\tdivision = {
\t\tname = "First"
\t\tlocation = 100
\t\tdivision_template = "Infantry"
\t\tstart_experience_factor = 0.30 # veterans
\t\tfuture_field = keep
\t}
\tdivision = { division_name = { is_name_ordered = yes name_order = 2 } location = 101 division_template = "Cavalry" }
}
"""


def _land_mod(tmp_path: Path) -> tuple[Mod, Path]:
    path = _write(tmp_path / "history/units/TST_1936.txt", LAND)
    return Mod(tmp_path), path


def test_edits_on_copied_divisions_are_written(tmp_path: Path) -> None:
    mod, path = _land_mod(tmp_path)
    divisions = copy.deepcopy(mod.get_oob("TST_1936").divisions)
    divisions[0].start_experience_factor = 0.9
    divisions[1].location = 555
    assert mod.update_oob("TST_1936", divisions=divisions)
    mod.save()
    assert path.read_text(encoding="utf-8") == LAND.replace(
        "start_experience_factor = 0.30", "start_experience_factor = 0.9"
    ).replace("location = 101", "location = 555")
    reloaded = Mod(tmp_path).get_oob("TST_1936").divisions
    assert reloaded[0].start_experience_factor == 0.9
    assert reloaded[1].location == 555


def test_plain_attribute_edits_on_loaded_objects_are_written(tmp_path: Path) -> None:
    mod, path = _land_mod(tmp_path)
    oob = mod.get_oob("TST_1936")
    oob.divisions[1].name_order = 7
    oob.templates[0].priority = 2
    oob.templates[1].name = "Horse"
    assert mod.update_oob("TST_1936", templates=oob.templates, divisions=oob.divisions)
    mod.save()
    assert path.read_text(encoding="utf-8") == (
        LAND.replace("priority = 1", "priority = 2")
        .replace('name = "Cavalry"', 'name = "Horse"')
        .replace("name_order = 2", "name_order = 7")
    )


def test_a_new_field_on_a_copied_division_is_added_once(tmp_path: Path) -> None:
    mod, path = _land_mod(tmp_path)
    divisions = copy.deepcopy(mod.get_oob("TST_1936").divisions)
    divisions[1].start_equipment_factor = 0.5
    assert mod.update_oob("TST_1936", divisions=divisions)
    mod.save()
    assert path.read_text(encoding="utf-8") == LAND.replace(
        'division_template = "Cavalry" }',
        'division_template = "Cavalry" start_equipment_factor = 0.5 }',
    )


def test_reordered_divisions_keep_their_blocks_and_edits(tmp_path: Path) -> None:
    mod, path = _land_mod(tmp_path)
    divisions = copy.deepcopy(mod.get_oob("TST_1936").divisions)
    divisions.reverse()
    divisions[0].location = 555  # the second division of the file
    templates = copy.deepcopy(mod.get_oob("TST_1936").templates)
    templates.reverse()
    assert mod.update_oob("TST_1936", templates=templates, divisions=divisions)
    mod.save()
    assert path.read_text(encoding="utf-8") == LAND.replace("location = 101", "location = 555")


def test_unedited_oob_passed_back_stays_byte_identical(tmp_path: Path) -> None:
    mod, path = _land_mod(tmp_path)
    oob = copy.deepcopy(mod.get_oob("TST_1936"))
    assert mod.update_oob("TST_1936", templates=oob.templates, divisions=oob.divisions)
    mod.save()
    assert path.read_bytes() == LAND.encode("utf-8")


def test_removing_one_division_and_adding_another_keeps_the_rest(tmp_path: Path) -> None:
    mod, path = _land_mod(tmp_path)
    loaded = copy.deepcopy(mod.get_oob("TST_1936").divisions)
    kept = loaded[1]
    kept.location = 555
    new = DivisionUnit("Infantry", 300, name="Third")
    assert mod.update_oob("TST_1936", divisions=[kept, new])
    mod.save()
    written = path.read_text(encoding="utf-8")
    # The first division is gone, the kept one changed only its location, and
    # the new one was added after it instead of overwriting it.
    kept_line = LAND.splitlines(keepends=True)[-2].replace("location = 101", "location = 555")
    assert written.startswith(LAND[: LAND.index("\tdivision = {\n")] + kept_line)
    reloaded = Mod(tmp_path).get_oob("TST_1936").divisions
    assert [(d.name, d.name_order, d.location) for d in reloaded] == [
        ("", 2, 555),
        ("Third", None, 300),
    ]


NAVAL_AIR = """units = {
\tfleet = {
\t\tname = "Home Fleet"
\t\tnaval_base = 200
\t\ttask_force = {
\t\t\tname = "Battle Group"
\t\t\tlocation = 200
\t\t\tship = { name = "Alpha" definition = battleship equipment = { ship_hull_heavy_1 = { amount = 1 owner = TST version_name = "Old Class" } } }
\t\t\tship = {
\t\t\t\tname = "Beta"
\t\t\t\tdefinition = destroyer
\t\t\t\tequipment = {
\t\t\t\t\tship_hull_light_1 = { amount = 1 owner = TST }
\t\t\t\t}
\t\t\t}
\t\t}
\t}
}
"""

AIR = """air_wings = {
\t200 = {
\t\tfighter_equipment_0 = { owner = "TST" amount = 24 } # keep
\t\tCAS_equipment_1 = { owner = "TST" amount = 12 }
\t}
}
"""


def test_edits_on_copied_fleets_task_forces_and_ships_are_written(tmp_path: Path) -> None:
    path = _write(tmp_path / "history/units/TST_naval.txt", NAVAL_AIR)
    mod = Mod(tmp_path)
    fleets = copy.deepcopy(mod.get_oob("TST_naval").fleets)
    fleets[0].naval_base = 201
    task_force = fleets[0].task_forces[0]
    task_force.location = 202
    task_force.ships[0].equipment[0].version_name = "New Class"
    task_force.ships[1].name = "Gamma"
    task_force.ships[1].equipment[0].amount = 2
    assert mod.update_oob("TST_naval", fleets=fleets)
    mod.save()
    assert path.read_text(encoding="utf-8") == (
        NAVAL_AIR.replace("naval_base = 200", "naval_base = 201")
        .replace("location = 200", "location = 202")
        .replace('"Old Class"', '"New Class"')
        .replace('name = "Beta"', 'name = "Gamma"')
        .replace("ship_hull_light_1 = { amount = 1", "ship_hull_light_1 = { amount = 2")
    )


def test_reordered_ships_keep_their_blocks_and_edits(tmp_path: Path) -> None:
    path = _write(tmp_path / "history/units/TST_naval.txt", NAVAL_AIR)
    mod = Mod(tmp_path)
    fleets = copy.deepcopy(mod.get_oob("TST_naval").fleets)
    ships = fleets[0].task_forces[0].ships
    ships.reverse()
    ships[0].definition = "light_cruiser"  # Beta
    assert mod.update_oob("TST_naval", fleets=fleets)
    mod.save()
    assert path.read_text(encoding="utf-8") == NAVAL_AIR.replace(
        "definition = destroyer", "definition = light_cruiser"
    )


def test_edits_on_copied_air_wings_are_written(tmp_path: Path) -> None:
    path = _write(tmp_path / "history/units/TST_air.txt", AIR)
    mod = Mod(tmp_path)
    oob = mod.get_oob("TST_air")
    oob.air_wings[1].amount = 36
    assert mod.update_oob("TST_air", air_wings=oob.air_wings)
    mod.save()
    assert path.read_text(encoding="utf-8") == AIR.replace("amount = 12", "amount = 36")


def test_removing_one_ship_and_adding_another_keeps_the_rest(tmp_path: Path) -> None:
    path = _write(tmp_path / "history/units/TST_naval.txt", NAVAL_AIR)
    mod = Mod(tmp_path)
    fleets = copy.deepcopy(mod.get_oob("TST_naval").fleets)
    ships = fleets[0].task_forces[0].ships
    new = Ship("Delta", "submarine", [ShipEquipment("ship_hull_submarine_1", owner="TST")])
    fleets[0].task_forces[0].ships = [ships[1], new]
    assert mod.update_oob("TST_naval", fleets=fleets)
    mod.save()
    written = path.read_text(encoding="utf-8")
    alpha = NAVAL_AIR.splitlines(keepends=True)[7]
    assert alpha.lstrip().startswith('ship = { name = "Alpha"')
    beta_end = NAVAL_AIR.index("\t\t\t}\n\t\t}\n") + len("\t\t\t}\n")
    assert written.startswith(NAVAL_AIR[:beta_end].replace(alpha, ""))
    reloaded = Mod(tmp_path).get_oob("TST_naval").fleets[0].task_forces[0].ships
    assert [(s.name, s.definition) for s in reloaded] == [
        ("Beta", "destroyer"),
        ("Delta", "submarine"),
    ]


ACE = """air_wings = {
\t162 = {
\t\tlarge_plane_airframe_3 = {
\t\t\towner = "ITA"
\t\t\tamount = 12
\t\t}
\t\tname = "Angeli Mortis"
\t\tace = { # belongs to the wing above
\t\t\tmodifier = "bomber_unique"
\t\t\tname = "Carlo"
\t\t}
\t}
}
"""


def test_an_ace_block_is_not_an_air_wing(tmp_path: Path) -> None:
    path = tmp_path / "history/units/ITA_air.txt"
    path.parent.mkdir(parents=True)
    path.write_text(ACE, encoding="utf-8")
    mod = Mod(tmp_path)
    (wing,) = mod.get_oob("ITA_air").air_wings
    assert wing.equipment_type == "large_plane_airframe_3"

    wing.amount = 24
    assert mod.update_oob("ITA_air", air_wings=[wing])
    mod.save(require_changes=True)
    assert path.read_text(encoding="utf-8") == ACE.replace("amount = 12", "amount = 24")


def test_a_copy_of_a_loaded_division_is_added_and_never_replaces_the_original(
    tmp_path: Path,
) -> None:
    path = _write(tmp_path / "history/units/ABC_1936.txt", LAND)
    mod = Mod(tmp_path)
    divisions = copy.deepcopy(mod.get_oob("ABC_1936").divisions)
    extra = copy.deepcopy(divisions[0])
    extra.name = "Third"
    divisions.append(extra)
    assert mod.update_oob("ABC_1936", divisions=divisions)
    mod.save(require_changes=True)

    # The copy keeps the unknown key and the comment of the block it came from.
    assert path.read_text(encoding="utf-8") == LAND[: -len("}\n")] + (
        "\tdivision = {\n\t\tname = \"Third\"\n\t\tlocation = 100\n"
        "\t\tdivision_template = \"Infantry\"\n"
        "\t\tstart_experience_factor = 0.30 # veterans\n\t\tfuture_field = keep\n\t}\n}\n"
    )
    names = [division.name for division in Mod(tmp_path).get_oob("ABC_1936").divisions]
    assert names[0] == "First" and names[-1] == "Third" and len(names) == 3


def test_copies_of_templates_ships_and_air_wings_are_added_too(tmp_path: Path) -> None:
    path = _write(tmp_path / "history/units/ABC_1936.txt", LAND)
    air = _write(
        tmp_path / "history/units/ABC_air.txt",
        "air_wings = {\n\t100 = {\n\t\tfighter_equipment_0 = { owner = \"ABC\" amount = 10 }\n\t}\n}\n",
    )
    mod = Mod(tmp_path)
    templates = copy.deepcopy(mod.get_oob("ABC_1936").templates)
    twin = copy.deepcopy(templates[0])
    twin.name = "Guards"
    assert mod.update_oob("ABC_1936", templates=[*templates, twin])
    wings = copy.deepcopy(mod.get_oob("ABC_air").air_wings)
    more = copy.deepcopy(wings[0])
    more.amount = 20
    assert mod.update_oob("ABC_air", air_wings=[*wings, more])
    mod.save(require_changes=True)

    reloaded = Mod(tmp_path)
    assert [template.name for template in reloaded.get_oob("ABC_1936").templates] == [
        "Infantry", "Cavalry", "Guards",
    ]
    assert [wing.amount for wing in reloaded.get_oob("ABC_air").air_wings] == [10, 20]
    assert LAND.split("\nunits = {")[0].rstrip("\n") in path.read_text(encoding="utf-8")
    assert "fighter_equipment_0 = { owner = \"ABC\" amount = 10 }" in air.read_text(encoding="utf-8")
