from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from hoi4 import MapBuilding, MapEditReport, Mod, Railway
from hoi4.map_editing import (
    append_list_tokens,
    parse_strategic_regions,
    patch_region_provinces,
    remove_list_tokens,
)
from hoi4.parser import parse_pdx
from hoi4.states import read_state, serialize_state
from hoi4.validation import validate_state

Image = pytest.importorskip("PIL.Image")

COLORS = {
    1: (10, 10, 10),
    2: (20, 20, 20),
    3: (30, 30, 30),
    4: (40, 40, 40),
    5: (50, 50, 50),
    6: (60, 60, 60),
    7: (0, 0, 200),
}
# Rows from the top; HOI4 z counts from the bottom, so row r spans z in
# [3 - r, 4 - r).
LAYOUT = (
    (1, 1, 2, 2, 4, 4, 5, 5),
    (1, 1, 2, 2, 4, 4, 5, 5),
    (3, 3, 3, 3, 6, 6, 6, 6),
    (7, 7, 7, 7, 7, 7, 7, 7),
)

STATE_1 = """# Alpha keeps this comment
state={
\tid=1
\tname="STATE_1" # inline name comment
\tmanpower=1000
\tstate_category = town
\tcustom_unknown = { keep = yes }

\thistory={
\t\towner = ABC
\t\tadd_core_of = ABC
\t\tadd_core_of = XYZ
\t\tvictory_points = { 2 10 }
\t\tvictory_points = { 3 5 }
\t\tbuildings = {
\t\t\tinfrastructure = 2
\t\t\t# naval base below
\t\t\t3 = {
\t\t\t\tnaval_base = 1
\t\t\t}
\t\t}
\t\t1939.1.1 = {
\t\t\tbuildings = { 2 = { bunker = 1 } }
\t\t}
\t}

\tprovinces={
\t\t1 2 3<SP>
\t}
\tlocal_supplies=0.0
}
""".replace("<SP>", " ")

STATE_2 = """state={
\tid=2
\tname="STATE_2"
\tmanpower=500
\tstate_category = rural
\thistory={
\t\towner = DEF
\t\tadd_core_of = DEF
\t}
\tprovinces={
\t\t4 5 6<SP>
\t}
}
""".replace("<SP>", " ")

LAND_REGION = """strategic_region={
\tid=1
\tname="STRATEGICREGION_1"
\tprovinces={
\t\t1 2 3 4 5 6<SP>
\t}
\tweather={
\t\tperiod={
\t\t\tbetween={ 0.0 30.0 }
\t\t\ttemperature={ -5.0 5.0 }
\t\t}
\t}
}
""".replace("<SP>", " ")

SEA_REGION = """strategic_region={
\tid=2
\tname="STRATEGICREGION_2"
\tprovinces={
\t\t7<SP>
\t}
\tnaval_terrain=water_shallow_sea
\tweather={
\t\tperiod={
\t\t\tbetween={ 0.0 30.0 }
\t\t\ttemperature={ 10.0 20.0 }
\t\t}
\t}
}
""".replace("<SP>", " ")

BUILDINGS = (
    "1;arms_factory;0.50;1.00;3.50;0.00;0\n"
    "1;naval_base;1.50;1.00;1.50;0.00;7\n"
    "2;arms_factory;4.50;1.00;3.50;0.00;0\n"
    "2;arms_factory;6.50;1.00;1.50;0.00;0\n"
)


def _write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_map_image(game: Path) -> None:
    image = Image.new("RGB", (len(LAYOUT[0]), len(LAYOUT)))
    pixels = image.load()
    assert pixels is not None
    for row, values in enumerate(LAYOUT):
        for column, province in enumerate(values):
            pixels[column, row] = COLORS[province]
    image.save(game / "map" / "provinces.bmp")


def _game(tmp_path: Path, *, image: bool = True) -> Path:
    game = tmp_path / "game"
    rows = ["0;0;0;0;land;false;unknown;0"] + [
        f"{province};{r};{g};{b};{'sea' if province == 7 else 'land'};false;plains;1"
        for province, (r, g, b) in COLORS.items()
    ]
    _write(game, "map/definition.csv", "\n".join(rows) + "\n")
    if image:
        _write_map_image(game)
    _write(game, "history/states/1-Alpha.txt", STATE_1)
    _write(game, "history/states/2-Beta.txt", STATE_2)
    _write(game, "map/strategicregions/1-Land.txt", LAND_REGION)
    _write(game, "map/strategicregions/2-Sea.txt", SEA_REGION)
    _write(game, "map/supply_nodes.txt", "1 1\n1 4\n")
    _write(game, "map/railways.txt", "1 2 1 2 \n2 3 4 5 6 \n")
    _write(game, "map/buildings.txt", BUILDINGS)
    return game


def _mod(tmp_path: Path, *, image: bool = True) -> Mod:
    game = _game(tmp_path, image=image)
    mod_root = tmp_path / "mod"
    mod_root.mkdir()
    return Mod(mod_root, hoi4_install=game)


def _codes(mod: Mod) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for issue in mod.validate():
        result.setdefault(issue.code, []).append(issue.message)
    return result


MAP_CODES = {
    "province_in_multiple_states",
    "province_missing_state",
    "unknown_state_province",
    "non_land_state_province",
    "province_in_multiple_strategic_regions",
    "province_missing_strategic_region",
    "invalid_supply_node",
    "invalid_railway",
    "malformed_railway",
    "map_building_unknown_state",
    "map_building_unknown_province",
}


def test_fixture_map_is_consistent(tmp_path: Path) -> None:
    mod = _mod(tmp_path)
    assert not MAP_CODES & set(_codes(mod))


# ── States ───────────────────────────────────────────────────────────


def test_create_state_moves_province_data_and_saves_source_preserving(tmp_path: Path) -> None:
    mod = _mod(tmp_path)

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        new_id = mod.create_state([3], name="Gamma Coast", manpower=250)

    assert new_id == 3
    assert mod.get_state(1).provinces == [1, 2]
    new_state = mod.get_state(3)
    assert new_state.provinces == [3]
    assert new_state.owner == "ABC" and new_state.cores == ["ABC", "XYZ"]
    assert new_state.victory_points == "3 5"
    assert "naval_base = 1" in new_state.buildings
    assert mod.get_loc("STATE_3") == "Gamma Coast"
    assert "map file map/buildings.txt: update" in mod.preview_summary()

    mod.save()
    mod_root = tmp_path / "mod"
    alpha = (mod_root / "history/states/1-Alpha.txt").read_text(encoding="utf-8")
    # Untouched bytes survive; only the province atom and moved data change.
    assert "# Alpha keeps this comment" in alpha
    assert '\tname="STATE_1" # inline name comment' in alpha
    assert "\tcustom_unknown = { keep = yes }" in alpha
    assert "\tprovinces={\n\t\t1 2 \n\t}" in alpha
    assert "victory_points = { 2 10 }" in alpha and "{ 3 5 }" not in alpha
    assert "naval_base" not in alpha
    assert "infrastructure = 2" in alpha and "# naval base below" in alpha
    assert "1939.1.1" in alpha
    assert alpha.count("add_core_of") == 2

    gamma = (mod_root / "history/states/3-Gamma_Coast.txt").read_text(encoding="utf-8")
    parsed = read_state(Path("gamma.txt"), text=gamma)
    assert parsed.id == 3 and parsed.name == "STATE_3"
    assert parsed.provinces == [3]
    assert parsed.manpower == "250"
    assert parsed.state_category == "rural"
    assert parsed.victory_points == "3 5"

    buildings = (mod_root / "map/buildings.txt").read_text(encoding="utf-8").splitlines()
    original = BUILDINGS.splitlines()
    assert buildings[1] == "3;naval_base;1.50;1.00;1.50;0.00;7"
    assert [buildings[0], *buildings[2:]] == [original[0], *original[2:]]

    reloaded = Mod(mod_root, hoi4_install=tmp_path / "game")
    assert reloaded.get_state(3).provinces == [3]
    assert not MAP_CODES & set(_codes(reloaded))


def test_create_state_warns_about_dated_history_and_unavailable_map(tmp_path: Path) -> None:
    mod = _mod(tmp_path, image=False)

    with pytest.warns(RuntimeWarning) as caught:
        mod.create_state([2], name="Beta Split", owner="QQQ", cores=())

    messages = [str(item.message) for item in caught]
    assert any("dated history" in message for message in messages)
    assert any("province map is unavailable" in message for message in messages)
    state = mod.get_state(3)
    assert state.owner == "QQQ" and state.cores == []
    # Without the province map, positions stay with their old state.
    assert [entry.state_id for entry in mod.list_map_buildings()] == [1, 1, 2, 2]


def test_create_state_validates_inputs_and_is_atomic(tmp_path: Path) -> None:
    mod = _mod(tmp_path)

    with pytest.raises(ValueError, match="already exists"):
        mod.create_state([3], name="Dup", state_id=2)
    with pytest.raises(ValueError, match="sea province"):
        mod.create_state([7], name="Wet")
    with pytest.raises(KeyError):
        mod.create_state([99], name="Ghost")
    with pytest.raises(ValueError, match="empty"):
        mod.create_state([1, 2, 3], name="Everything")
    # A failure after provinces moved (bad localisation path) rolls back.
    with pytest.raises(ValueError, match="localisation"):
        mod.create_state([3], name="Rollback", localisation_file="notes.txt")

    assert mod.get_state(1).provinces == [1, 2, 3]
    assert 3 not in mod.list_states()
    assert mod.preview() == ""
    assert [entry.state_id for entry in mod.list_map_buildings()] == [1, 1, 2, 2]


def test_explicit_state_id_and_split_state(tmp_path: Path) -> None:
    mod = _mod(tmp_path)

    with pytest.raises(ValueError, match="do not belong"):
        mod.split_state(1, [4], name="Wrong")
    new_id = mod.split_state(2, [6], name="South Beta", state_id=10)

    assert new_id == 10
    assert mod.get_state(2).provinces == [4, 5]
    assert mod.get_state(10).owner == "DEF"
    assert [entry.state_id for entry in mod.list_map_buildings(state_id=10)] == [10]


def test_move_provinces_reports_and_reassigns_map_buildings(tmp_path: Path) -> None:
    mod = _mod(tmp_path)

    report = mod.move_provinces([3], 2)

    assert isinstance(report, MapEditReport)
    assert report.target_state == 2
    assert report.moved_provinces == (3,)
    assert report.source_states == (1,)
    assert report.moved_victory_points == (3,)
    assert report.moved_province_buildings == (3,)
    assert report.reassigned_map_buildings == 1
    assert report.warnings == ()
    assert mod.get_state(2).provinces == [4, 5, 6, 3]
    assert mod.get_state(2).victory_points == "3 5"

    with pytest.raises(ValueError, match="already belongs"):
        mod.move_provinces([3], 2)


def test_move_provinces_refuses_to_empty_unless_asked(tmp_path: Path) -> None:
    mod = _mod(tmp_path)

    with pytest.raises(ValueError, match="delete_empty_states"):
        mod.move_provinces([1, 2, 3], 2)
    assert mod.preview() == ""

    report = mod.move_provinces([1, 2, 3], 2, allow_empty_source=True)
    assert report.emptied_states == (1,)
    assert any("no provinces" in warning for warning in report.warnings)
    assert mod.get_state(1).provinces == []


def test_delete_empty_states_shadows_inherited_file(tmp_path: Path) -> None:
    mod = _mod(tmp_path)

    report = mod.move_provinces([1, 2, 3], 2, delete_empty_states=True)

    assert report.deleted_states == (1,)
    assert any("gap in state IDs" in warning for warning in report.warnings)
    # Every position of a fully absorbed state follows it, image or not.
    assert report.reassigned_map_buildings == 2
    assert 1 not in mod.list_states()
    assert 1 not in {entry["id"] for entry in mod.state_index()}
    with pytest.raises(KeyError):
        mod.get_state(1)
    assert "state 1: delete" in mod.preview_summary()

    mod.save()
    shadow = (tmp_path / "mod/history/states/1-Alpha.txt").read_text(encoding="utf-8")
    assert shadow.startswith("# State 1 was removed")
    reloaded = Mod(tmp_path / "mod", hoi4_install=tmp_path / "game")
    assert reloaded.get_state(2).provinces == [4, 5, 6, 1, 2, 3]
    assert not MAP_CODES & set(_codes(reloaded))


def test_delete_empty_mod_only_state_removes_file(tmp_path: Path) -> None:
    mod = _mod(tmp_path)
    new_id = mod.create_state([3], name="Temp")
    mod.save()
    created = tmp_path / "mod/history/states/3-Temp.txt"
    assert created.is_file()

    mod = Mod(tmp_path / "mod", hoi4_install=tmp_path / "game")
    report = mod.move_provinces([3], 1, delete_empty_states=True)
    assert report.deleted_states == (new_id,)
    assert report.warnings == ()  # highest ID: no gap
    mod.save()
    assert not created.exists()


def test_transaction_rolls_back_map_edits(tmp_path: Path) -> None:
    mod = _mod(tmp_path)

    with mod.transaction():
        mod.create_state([3], name="Preview Only")
        mod.add_supply_node(2)
        mod.move_provinces([1, 2], 2, delete_empty_states=True, allow_empty_source=True)
        assert "map/supply_nodes.txt" in mod.preview()

    assert mod.preview() == ""
    assert mod.get_state(1).provinces == [1, 2, 3]
    assert mod.list_supply_nodes() == [1, 4]
    assert not (tmp_path / "mod/map").exists()


# ── Strategic regions ────────────────────────────────────────────────


def test_strategic_region_listing_and_moves(tmp_path: Path) -> None:
    mod = _mod(tmp_path)

    regions = mod.list_strategic_regions()
    assert [(region.id, region.provinces) for region in regions] == [
        (1, (1, 2, 3, 4, 5, 6)),
        (2, (7,)),
    ]
    assert mod.get_strategic_region(2).path == "map/strategicregions/2-Sea.txt"
    with pytest.raises(KeyError):
        mod.get_strategic_region(5)
    with pytest.raises(ValueError, match="empty"):
        mod.move_provinces_to_strategic_region([7], 1)

    updated = mod.move_provinces_to_strategic_region([6], 2)

    assert updated.provinces == (7, 6)
    assert mod.get_strategic_region(1).provinces == (1, 2, 3, 4, 5)
    mod.save()
    land = (tmp_path / "mod/map/strategicregions/1-Land.txt").read_text(encoding="utf-8")
    assert land == LAND_REGION.replace("1 2 3 4 5 6 ", "1 2 3 4 5 ")
    sea = (tmp_path / "mod/map/strategicregions/2-Sea.txt").read_text(encoding="utf-8")
    assert sea == SEA_REGION.replace("\t\t7 \n", "\t\t7 6 \n")


def test_create_strategic_region_copies_weather(tmp_path: Path) -> None:
    mod = _mod(tmp_path)

    region_id = mod.create_strategic_region("Eastern Hills", [4, 5])

    assert region_id == 3
    region = mod.get_strategic_region(3)
    assert region.provinces == (4, 5) and region.has_weather
    assert region.name == "STRATEGICREGION_3"
    assert mod.get_loc("STRATEGICREGION_3") == "Eastern Hills"
    assert mod.get_strategic_region(1).provinces == (1, 2, 3, 6)
    mod.save()
    text = (tmp_path / "mod/map/strategicregions/3-Eastern_Hills.txt").read_text(encoding="utf-8")
    assert "temperature={ -5.0 5.0 }" in text
    assert parse_pdx(text).children[0].key == "strategic_region"
    reloaded = Mod(tmp_path / "mod", hoi4_install=tmp_path / "game")
    assert not MAP_CODES & set(_codes(reloaded))


def test_create_strategic_region_with_explicit_weather(tmp_path: Path) -> None:
    mod = _mod(tmp_path)

    with pytest.raises(ValueError, match="already exists"):
        mod.create_strategic_region("Dup", [4], region_id=1)
    mod.create_strategic_region(
        "Custom",
        [4],
        region_id=9,
        weather="period = { between = { 0.0 30.0 } }",
    )
    assert mod.get_strategic_region(9).has_weather


# ── Supply nodes, railways, building positions ───────────────────────


def test_supply_nodes_edit_single_lines(tmp_path: Path) -> None:
    mod = _mod(tmp_path)

    assert mod.list_supply_nodes() == [1, 4]
    with pytest.raises(ValueError, match="already"):
        mod.add_supply_node(1)
    with pytest.raises(ValueError, match="not land"):
        mod.add_supply_node(7)
    with pytest.raises(KeyError):
        mod.remove_supply_node(3)
    mod.add_supply_node(6)
    mod.remove_supply_node(1)
    mod.save()

    assert (tmp_path / "mod/map/supply_nodes.txt").read_text(encoding="utf-8") == "1 4\n1 6\n"


def test_railway_editing_checks_adjacency_and_keeps_other_lines(tmp_path: Path) -> None:
    mod = _mod(tmp_path)

    assert mod.list_railways() == [
        Railway(level=1, provinces=(1, 2), index=0),
        Railway(level=2, provinces=(4, 5, 6), index=1),
    ]
    with pytest.raises(ValueError, match="not adjacent"):
        mod.add_railway(1, [1, 6])
    with pytest.raises(ValueError, match="level"):
        mod.add_railway(9, [1, 3])
    added = mod.add_railway(3, [1, 3, 6])
    assert added == Railway(level=3, provinces=(1, 3, 6), index=2)
    assert mod.set_railway_level([6, 5, 4], 4) == 1
    assert mod.remove_railway(mod.list_railways()[0]) == 1
    with pytest.raises(KeyError):
        mod.remove_railway([2, 3])
    mod.save()

    assert (tmp_path / "mod/map/railways.txt").read_text(encoding="utf-8") == (
        "4 3 4 5 6 \n3 3 1 3 6\n"
    )


def test_railway_adjacency_check_skips_without_map_image(tmp_path: Path) -> None:
    mod = _mod(tmp_path, image=False)

    assert mod.add_railway(1, [1, 6]).provinces == (1, 6)


def test_map_buildings_add_list_remove(tmp_path: Path) -> None:
    mod = _mod(tmp_path)

    assert [entry.building for entry in mod.list_map_buildings(state_id=1)] == [
        "arms_factory",
        "naval_base",
    ]
    with pytest.raises(KeyError):
        mod.add_map_building(42, "arms_factory", 1, 1, 1, 0)
    with pytest.raises(ValueError, match="not a sea"):
        mod.add_map_building(1, "naval_base", 1, 1, 1, 0, adjacent_sea_province=3)
    with pytest.raises(ValueError, match="not a land"):
        mod.add_map_building(1, "floating_harbor", 1, 1, 1, 0, adjacent_sea_province=7)
    with mod.transaction():
        mod.add_map_building(1, "floating_harbor", 1, 1, 1, 0, adjacent_sea_province=3)
    added = mod.add_map_building(2, "bunker", 5.25, 1.0, 2.5, 0.75)
    assert isinstance(added, MapBuilding)
    assert added.index == 4 and added.z == 2.5
    with pytest.raises(ValueError):
        mod.remove_map_buildings()
    assert mod.remove_map_buildings(state_id=2, building="arms_factory") == 2
    with pytest.raises(KeyError, match="stale"):
        mod.remove_map_buildings(entries=[added])
    mod.save()

    assert (tmp_path / "mod/map/buildings.txt").read_text(encoding="utf-8") == (
        "1;arms_factory;0.50;1.00;3.50;0.00;0\n"
        "1;naval_base;1.50;1.00;1.50;0.00;7\n"
        "2;bunker;5.25;1.00;2.50;0.75;0\n"
    )


def test_line_files_keep_crlf_comments_and_unterminated_lines(tmp_path: Path) -> None:
    mod = _mod(tmp_path)
    original = "# header\r\n1 1\r\n1 4"
    _write(tmp_path / "mod", "map/supply_nodes.txt", "")
    (tmp_path / "mod/map/supply_nodes.txt").write_bytes(original.encode("utf-8"))
    mod = Mod(tmp_path / "mod", hoi4_install=tmp_path / "game")

    mod.add_supply_node(2)
    mod.save()

    assert (tmp_path / "mod/map/supply_nodes.txt").read_bytes() == (
        b"# header\r\n1 1\r\n1 4\r\n1 2\r\n"
    )


# ── Validation ───────────────────────────────────────────────────────


def test_validation_reports_map_reference_problems(tmp_path: Path) -> None:
    mod = _mod(tmp_path)
    mod_root = tmp_path / "mod"
    _write(mod_root, "history/states/1-Alpha.txt", STATE_1.replace("1 2 3 ", "1 2 "))
    _write(
        mod_root,
        "history/states/3-Dup.txt",
        "state = { id = 3 name = STATE_3 history = { owner = ABC } provinces = { 4 } }\n",
    )
    _write(mod_root, "map/strategicregions/2-Sea.txt", SEA_REGION.replace("7 ", "7 1 "))
    _write(mod_root, "map/supply_nodes.txt", "1 1\n1 7\n")
    _write(mod_root, "map/railways.txt", "1 2 1 6\n1 3 1 2\n")
    _write(
        mod_root,
        "map/buildings.txt",
        BUILDINGS + "9;arms_factory;1;1;1;0;0\n1;naval_base;1;1;1;0;2\n",
    )
    mod = Mod(mod_root, hoi4_install=tmp_path / "game")

    codes = _codes(mod)

    assert any("Province 4" in message for message in codes["province_in_multiple_states"])
    assert any(": 3" in message for message in codes["province_missing_state"])
    assert codes["province_in_multiple_strategic_regions"]
    assert codes["invalid_supply_node"]
    assert any("not adjacent" in message for message in codes["invalid_railway"])
    assert codes["malformed_railway"]
    assert codes["map_building_unknown_state"]
    # Land province 2 is known but cannot be a naval base's adjacent sea.
    assert any(message.endswith("province 2") for message in codes["map_building_unknown_province"])
    severities = {
        issue.code: issue.severity
        for issue in mod.validate()
        if issue.code in MAP_CODES
    }
    assert severities["province_missing_state"] == "warning"
    assert severities["province_in_multiple_states"] == "error"


def test_validation_detects_missing_strategic_region(tmp_path: Path) -> None:
    mod = _mod(tmp_path)
    _write(
        tmp_path / "mod",
        "map/strategicregions/1-Land.txt",
        LAND_REGION.replace("1 2 3 4 5 6 ", "1 2 3 4 5 "),
    )
    mod = Mod(tmp_path / "mod", hoi4_install=tmp_path / "game")

    assert "province_missing_strategic_region" in _codes(mod)


# ── Serializer regression checks ─────────────────────────────────────


def test_state_serializer_unchanged_input_is_byte_identical() -> None:
    state = read_state(Path("1-Alpha.txt"), text=STATE_1)
    assert serialize_state(state) == STATE_1


def test_state_province_edit_keeps_comments_unknown_keys_and_repeats() -> None:
    state = read_state(Path("1-Alpha.txt"), text=STATE_1)
    state.provinces = [1, 3, 8]

    rendered = serialize_state(state)

    assert rendered == STATE_1.replace("\t\t1 2 3 \n", "\t\t1 3 8 \n")
    reparsed = read_state(Path("1-Alpha.txt"), text=rendered)
    assert reparsed.provinces == [1, 3, 8]
    assert reparsed.cores == ["ABC", "XYZ"]
    assert parse_pdx(rendered).children
    assert not [
        issue for issue in validate_state(reparsed, {"ABC", "XYZ"}) if issue.severity == "error"
    ]


def test_state_province_reorder_falls_back_to_rendered_block() -> None:
    state = read_state(Path("1-Alpha.txt"), text=STATE_1)
    state.provinces = [3, 1]

    rendered = serialize_state(state)

    assert read_state(Path("x.txt"), text=rendered).provinces == [3, 1]
    assert "# Alpha keeps this comment" in rendered


def test_region_patch_keeps_comments_and_is_noop_without_changes() -> None:
    text = (
        "strategic_region = {\n id = 4\n provinces = { 1 2 # two\n 3 }\n other = { 2 }\n}\n"
    )
    assert patch_region_provinces(text, 4) == text
    patched = patch_region_provinces(text, 4, remove=[2], add=[9])
    assert patched == (
        "strategic_region = {\n id = 4\n provinces = { 1 # two\n 3 9 }\n other = { 2 }\n}\n"
    )
    (region,) = parse_strategic_regions(patched, "x.txt")
    assert region.provinces == (1, 3, 9)


@pytest.mark.parametrize(
    ("body", "remove", "expected"),
    [
        (" 1 2 3 ", [2], " 1 3 "),
        (" 1 2 3 ", [3], " 1 2 "),
        (" 1 2 3 ", [1, 2, 3], " "),
        ("\n\t\t1 2 3\n\t", [3], "\n\t\t1 2\n\t"),
        ("\n\t\t1 2 3\n\t", [2, 3], "\n\t\t1\n\t"),
        (" 12 2 # 2 stays\n", [2], " 12 # 2 stays\n"),
    ],
)
def test_remove_list_tokens(body: str, remove: list[int], expected: str) -> None:
    assert remove_list_tokens(body, remove) == expected


def test_append_list_tokens() -> None:
    assert append_list_tokens(" 1 2 ", [3, 4]) == " 1 2 3 4 "
    assert append_list_tokens(" ", [5]) == " 5 "
    assert append_list_tokens("\n\t", [5]) == " 5\n\t"


def test_replace_path_hides_inherited_states_and_regions(tmp_path: Path) -> None:
    _game(tmp_path)
    mod_root = tmp_path / "mod"
    _write(
        mod_root,
        "descriptor.mod",
        'name = "Conversion"\nreplace_path = "history/states"\n'
        'replace_path = "map/strategicregions"\n',
    )
    _write(
        mod_root,
        "history/states/1-West.txt",
        "state = { id = 1 name = STATE_1 history = { owner = ABC } provinces = { 1 2 3 4 } }\n",
    )
    _write(
        mod_root,
        "history/states/2-East.txt",
        "state = { id = 2 name = STATE_2 history = { owner = DEF } provinces = { 5 6 } }\n",
    )
    _write(
        mod_root,
        "map/strategicregions/1-All.txt",
        "strategic_region = { id = 1 name = X provinces = { 1 2 3 4 5 6 7 } }\n",
    )
    mod = Mod(mod_root, hoi4_install=tmp_path / "game")

    assert [region.path for region in mod.list_strategic_regions()] == [
        "map/strategicregions/1-All.txt"
    ]
    assert not MAP_CODES & set(_codes(mod))
    assert mod.create_state([4], name="Middle") == 3


def test_validation_accepts_vanilla_lake_floating_harbor_and_empty_region_shapes() -> None:
    from hoi4.map_editing import StrategicRegion, parse_map_buildings, validate_map_consistency

    buildings = parse_map_buildings(
        "1;floating_harbor;1.0;1.0;1.0;0.0;1\n"   # floating harbors name coastal land
        "1;naval_base_spawn;1.0;1.0;1.0;0.0;3\n"  # naval bases name the sea
        "1;floating_harbor;1.0;1.0;1.0;0.0;3\n"   # a sea province is wrong here
    )
    issues = validate_map_consistency(
        province_types={1: "land", 2: "lake", 3: "sea"},
        state_provinces={1: [1, 2]},
        state_paths={1: "history/states/1-A.txt"},
        regions=[
            StrategicRegion(1, "R1", (1, 2, 3), "map/strategicregions/1.txt"),
            StrategicRegion(2, "R2", (), "map/strategicregions/2.txt"),
        ],
        supply_nodes=None,
        railways=None,
        buildings=buildings,
    )
    by_code = {(issue.code, issue.severity, issue.line) for issue in issues}
    assert by_code == {
        ("empty_strategic_region", "warning", None),
        ("map_building_unknown_province", "error", 3),
    }
