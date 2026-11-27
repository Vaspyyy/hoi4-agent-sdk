"""OOB and country checks: true alarms still fire, valid game patterns stay quiet.

Each test builds a tiny game install and mod. The game install holds what a
real mod usually inherits (states, map, unit types, equipment, OOB files);
the mod holds the country definitions and history.
"""

from __future__ import annotations

from pathlib import Path

from hoi4 import Mod
from hoi4.oob import load_oob_file, validate_oob
from hoi4.types import ValidationIssue


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _game(root: Path) -> Path:
    """A minimal game install: two states, a map, units and equipment."""

    _write(
        root / "history/states/1-Home.txt",
        "state = { id = 1 name = STATE_1 manpower = 1 state_category = rural\n"
        "    history = { owner = ABC add_core_of = ABC }\n"
        "    provinces = { 10 11 } }\n",
    )
    _write(
        root / "history/states/2-Abroad.txt",
        "state = { id = 2 name = STATE_2 manpower = 1 state_category = rural\n"
        "    history = { owner = DEF add_core_of = DEF }\n"
        "    provinces = { 20 } }\n",
    )
    _write(
        root / "map/definition.csv",
        "0;0;0;0;land;false;unknown;0\n"
        "10;1;0;0;land;true;plains;1\n"
        "11;2;0;0;land;true;plains;1\n"
        "20;3;0;0;land;true;plains;1\n"
        "30;4;0;0;sea;true;ocean;2\n"
        "40;5;0;0;land;true;plains;1\n",
    )
    _write(
        root / "common/units/infantry.txt",
        "sub_units = { infantry = { } destroyer = { } cas = { } }\n",
    )
    _write(
        root / "common/units/equipment/planes.txt",
        "equipments = {\n"
        "    small_plane_airframe = { is_archetype = yes }\n"
        "    small_plane_airframe_0 = { archetype = small_plane_airframe }\n"
        "    ship_hull_light = { is_archetype = yes }\n"
        "    ship_hull_light_1 = { archetype = ship_hull_light }\n"
        "}\n",
    )
    _write(
        root / "common/units/equipment/x_duplicates.txt",
        "duplicate_archetypes = {\n"
        "    small_plane_cas_airframe = {\n"
        "        archetype = small_plane_airframe\n"
        "        variant_name = { small_plane_cas_airframe_1 = CAS_equipment_1 }\n"
        "    }\n"
        "}\n",
    )
    return root


def _country(mod_root: Path, tag: str, history: str) -> None:
    _write(
        mod_root / "common/country_tags/00_countries.txt",
        "".join(
            f'{existing} = "countries/{existing}.txt"\n'
            for existing in sorted(
                {tag}
                | {
                    path.stem
                    for path in (mod_root / "common/countries").glob("*.txt")
                }
            )
        ),
    )
    _write(mod_root / f"common/countries/{tag}.txt", "color = { 10 20 30 }\n")
    _write(
        mod_root / f"history/countries/{tag} - Test.txt",
        "capital = 1\nset_politics = { ruling_party = neutrality }\n" + history,
    )


def _land_oob(root: Path, name: str, body: str) -> Path:
    return _write(root / f"history/units/{name}.txt", body)


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    game = _game(tmp_path / "game")
    mod_root = tmp_path / "mod"
    mod_root.mkdir()
    return game, mod_root


def _oob_findings(mod: Mod) -> list[ValidationIssue]:
    return mod._validate_country_oobs()


def _codes(findings: list[ValidationIssue]) -> dict[str, list[ValidationIssue]]:
    result: dict[str, list[ValidationIssue]] = {}
    for finding in findings:
        result.setdefault(finding.code, []).append(finding)
    return result


_TEMPLATE = (
    'division_template = { name = "Infantry" regiments = { infantry = { x = 0 y = 0 } } }\n'
)


# ── Provinces and states ──────────────────────────────────────────


def test_inherited_states_place_divisions_and_unknown_province_still_fires(
    tmp_path: Path,
) -> None:
    game, mod_root = _setup(tmp_path)
    oob = _land_oob(
        game,
        "ABC_1936",
        _TEMPLATE
        + "units = {\n"
        '    division = { location = 10 division_template = "Infantry" }\n'
        '    division = { location = 99 division_template = "Infantry" }\n'
        "}\n",
    )
    _country(mod_root, "ABC", 'oob = "ABC_1936"\n')

    codes = _codes(_oob_findings(Mod(mod_root, hoi4_install=game)))

    unknown = codes.get("unknown_oob_province", [])
    assert [(f.severity, f.file_path, f.line) for f in unknown] == [
        ("error", str(oob), 4)
    ]
    assert "99" in unknown[0].message


def test_naval_base_and_task_force_at_sea(tmp_path: Path) -> None:
    game, mod_root = _setup(tmp_path)
    _land_oob(
        game,
        "ABC_1936_naval",
        "units = {\n"
        '    fleet = { name = "Home" naval_base = 10\n'
        '        task_force = { name = "At sea" location = 30\n'
        '            ship = { name = "One" definition = destroyer\n'
        '                equipment = { ship_hull_light_1 = { amount = 1 owner = ABC version_name = "L" } } }\n'
        "        }\n"
        '        task_force = { name = "Lost" location = 77 }\n'
        "    }\n"
        "}\n",
    )
    _country(
        mod_root,
        "ABC",
        'if = { limit = { has_dlc = "Man the Guns" } set_naval_oob = "ABC_1936_naval" }\n'
        'if = { limit = { has_dlc = "Man the Guns" }\n'
        "    set_technology = { early_ship_hull_light = 1 }\n"
        '    create_equipment_variant = { name = "L" type = ship_hull_light_1 } }\n',
    )

    unknown = _codes(_oob_findings(Mod(mod_root, hoi4_install=game))).get(
        "unknown_oob_naval_location", []
    )

    # The sea province 30 is a valid starting place; 77 does not exist.
    assert [f.message.split("'")[3] for f in unknown] == ["Lost"]
    assert unknown[0].line == 7


# ── Ownership ─────────────────────────────────────────────────────


def test_units_abroad_are_advice_and_releasables_are_not_checked(
    tmp_path: Path,
) -> None:
    game, mod_root = _setup(tmp_path)
    _land_oob(
        game,
        "ABC_1936",
        _TEMPLATE
        + 'units = { division = { location = 20 division_template = "Infantry" } }\n',
    )
    _country(mod_root, "ABC", 'oob = "ABC_1936"\n')
    # XYZ owns nothing at the start and copies ABC's file, as many
    # releasables do: the game never loads it for XYZ.
    _country(mod_root, "XYZ", 'oob = "ABC_1936"\n')

    owned = _codes(_oob_findings(Mod(mod_root, hoi4_install=game))).get(
        "oob_location_not_owned", []
    )

    assert [(f.severity, f.country_tag) for f in owned] == [("info", "ABC")]


# ── Ships and air wings ───────────────────────────────────────────


def test_ship_names_ordered_names_and_duplicates(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "ABC_naval.txt",
        "units = { fleet = { name = F naval_base = 10 task_force = { name = T location = 10\n"
        "    ship = { ordered_name = 3 definition = destroyer equipment = { ship_hull_light_1 = { amount = 1 } } }\n"
        "    ship = { definition = destroyer equipment = { ship_hull_light_1 = { amount = 1 } } }\n"
        '    ship = { name = "Twin" definition = destroyer equipment = { ship_hull_light_1 = { amount = 1 } } }\n'
        '    ship = { name = "Twin" definition = destroyer equipment = { ship_hull_light_1 = { amount = 1 } } }\n'
        "} } }\n",
    )

    codes = _codes(validate_oob(load_oob_file(path)))

    assert [f.severity for f in codes.get("oob_ship_name", [])] == ["warning"]
    assert [f.severity for f in codes.get("duplicate_oob_ship", [])] == ["warning"]


def test_two_equal_air_wings_are_two_wings(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "ABC_air.txt",
        "air_wings = { 1 = {\n"
        "    fighter_equipment_0 = { owner = ABC amount = 10 }\n"
        "    fighter_equipment_0 = { owner = ABC amount = 10 }\n"
        "    ace = { modifier = x name = y surname = z callsign = w }\n"
        "} }\n",
    )

    oob = load_oob_file(path)
    assert len(oob.air_wings) == 2
    assert validate_oob(oob) == []


def test_duplicate_archetype_equipment_is_known(tmp_path: Path) -> None:
    game, mod_root = _setup(tmp_path)
    _land_oob(
        game,
        "ABC_1936_air",
        "air_wings = { 1 = {\n"
        '    small_plane_cas_airframe_0 = { owner = ABC amount = 10 version_name = "Stuka" }\n'
        '    made_up_airframe_0 = { owner = ABC amount = 10 version_name = "Stuka" }\n'
        "} }\n",
    )
    _country(
        mod_root,
        "ABC",
        'if = { limit = { has_dlc = "By Blood Alone" } set_air_oob = "ABC_1936_air"\n'
        '    create_equipment_variant = { name = "Stuka" type = small_plane_cas_airframe_0 }\n'
        '    create_equipment_variant = { name = "Stuka" type = made_up_airframe_0 } }\n',
    )

    unknown = _codes(_oob_findings(Mod(mod_root, hoi4_install=game))).get(
        "unknown_oob_equipment", []
    )

    assert [f.message.split("'")[-2] for f in unknown] == ["made_up_airframe_0"]
    assert unknown[0].severity == "error"


# ── Templates ─────────────────────────────────────────────────────


def test_templates_from_script_and_earlier_oobs(tmp_path: Path) -> None:
    game, mod_root = _setup(tmp_path)
    _land_oob(game, "ABC_1936", _TEMPLATE + "units = { }\n")
    _land_oob(
        game,
        "ABC_1939",
        "units = {\n"
        '    division = { location = 10 division_template = "Infantry" }\n'
        '    division = { location = 10 division_template = "Militia" }\n'
        '    division = { location = 10 division_template = "Nowhere" }\n'
        "}\n",
    )
    _write(
        mod_root / "common/scripted_effects/abc.txt",
        "ABC_raise_militia = { division_template = { name = \"Militia\" "
        "regiments = { infantry = { x = 0 y = 0 } } } }\n",
    )
    _country(
        mod_root,
        "ABC",
        'oob = "ABC_1936"\n1939.1.1 = { ABC_raise_militia = yes set_oob = "ABC_1939" }\n',
    )

    unknown = _codes(_oob_findings(Mod(mod_root, hoi4_install=game))).get(
        "unknown_oob_template", []
    )

    assert len(unknown) == 1
    assert "'Nowhere'" in unknown[0].message
    assert unknown[0].severity == "error"


def test_repeated_grid_position_is_a_warning() -> None:
    from hoi4.oob import Battalion, DivisionTemplate, OrderOfBattle

    oob = OrderOfBattle(
        name="ABC_1936",
        templates=[
            DivisionTemplate(
                "Cavalry",
                battalions=[Battalion("cavalry", 0, 2), Battalion("cavalry", 0, 2)],
            )
        ],
    )

    findings = _codes(validate_oob(oob)).get("duplicate_oob_grid_position", [])

    assert [f.severity for f in findings] == ["warning"]


# ── Missing files ─────────────────────────────────────────────────


def test_missing_oob_file_matters_only_for_countries_on_the_map(
    tmp_path: Path,
) -> None:
    game, mod_root = _setup(tmp_path)
    _country(mod_root, "ABC", 'oob = "ABC_missing"\n')
    _country(mod_root, "XYZ", 'oob = "XYZ_missing"\n')

    codes = _codes(_oob_findings(Mod(mod_root, hoi4_install=game)))

    assert [(f.severity, f.country_tag) for f in codes["missing_oob_reference"]] == [
        ("error", "ABC")
    ]
    assert [
        (f.severity, f.country_tag) for f in codes["unloaded_missing_oob_reference"]
    ] == [("warning", "XYZ")]
    assert codes["missing_oob_reference"][0].file_path is not None


# ── Equipment variants ────────────────────────────────────────────


def _naval_oob(game: Path, name: str, version: str) -> None:
    _land_oob(
        game,
        name,
        "units = { fleet = { name = F naval_base = 10 task_force = { name = T location = 10\n"
        "    ship = { name = One definition = destroyer equipment = { ship_hull_light_1 = "
        f'{{ amount = 1 owner = ABC version_name = "{version}" }} }} }}\n'
        "} } }\n",
    )


def test_variant_split_over_if_and_else_covers_every_dlc_path(tmp_path: Path) -> None:
    game, mod_root = _setup(tmp_path)
    _naval_oob(game, "ABC_1936_naval", "Split Class")
    _country(
        mod_root,
        "ABC",
        'if = { limit = { has_dlc = "Man the Guns" } set_naval_oob = "ABC_1936_naval" }\n'
        "if = {\n"
        '    limit = { has_dlc = "Man the Guns" has_dlc = "Arms Against Tyranny" }\n'
        '    create_equipment_variant = { name = "Split Class" type = ship_hull_light_1 }\n'
        "    else = {\n"
        '        create_equipment_variant = { name = "Split Class" type = ship_hull_light_1 }\n'
        "    }\n"
        "}\n",
    )

    codes = _codes(_oob_findings(Mod(mod_root, hoi4_install=game)))

    assert "missing_mtg_equipment_variant" not in codes

    # Control: without the else branch one DLC path has no variant.
    history = mod_root / "history/countries/ABC - Test.txt"
    text = history.read_text(encoding="utf-8")
    start = text.index("    else = {")
    end = text.index("    }\n", start) + len("    }\n")
    history.write_text(text[:start] + text[end:], encoding="utf-8")
    codes = _codes(_oob_findings(Mod(mod_root, hoi4_install=game)))
    assert len(codes["missing_mtg_equipment_variant"]) == 1


def test_variant_on_one_branch_only_is_still_missing(tmp_path: Path) -> None:
    game, mod_root = _setup(tmp_path)
    _naval_oob(game, "ABC_1936_naval", "Partial Class")
    _country(
        mod_root,
        "ABC",
        'if = { limit = { has_dlc = "Man the Guns" } set_naval_oob = "ABC_1936_naval" }\n'
        'if = { limit = { has_dlc = "Arms Against Tyranny" }\n'
        '    create_equipment_variant = { name = "Partial Class" type = ship_hull_light_1 } }\n',
    )

    missing = _codes(_oob_findings(Mod(mod_root, hoi4_install=game))).get(
        "missing_mtg_equipment_variant", []
    )

    assert [(f.severity, f.line) for f in missing] == [("error", 2)]


def test_dated_reference_sees_variants_of_its_date(tmp_path: Path) -> None:
    game, mod_root = _setup(tmp_path)
    _naval_oob(game, "ABC_1939_naval", "Late Class")
    _country(
        mod_root,
        "ABC",
        "1939.1.1 = {\n"
        '    if = { limit = { has_dlc = "Man the Guns" } set_naval_oob = "ABC_1939_naval" }\n'
        '    create_equipment_variant = { name = "Late Class" type = ship_hull_light_1 }\n',
        # The game closes a block left open at the end of the file.
    )

    codes = _codes(_oob_findings(Mod(mod_root, hoi4_install=game)))

    assert "missing_mtg_equipment_variant" not in codes

    # Control: a 1936 reference does not see the 1939 variant.
    history = mod_root / "history/countries/ABC - Test.txt"
    history.write_text(
        history.read_text(encoding="utf-8").replace(
            '    if = { limit = { has_dlc = "Man the Guns" } set_naval_oob = "ABC_1939_naval" }\n',
            "",
        )
        + '\n}\nif = { limit = { has_dlc = "Man the Guns" } set_naval_oob = "ABC_1939_naval" }\n',
        encoding="utf-8",
    )
    codes = _codes(_oob_findings(Mod(mod_root, hoi4_install=game)))
    assert len(codes["missing_mtg_equipment_variant"]) == 1


def test_chassis_check_ignores_paths_without_the_dlc_and_knows_special_projects(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path / "common/technologies/test.txt",
        "technologies = { basic_hull = { enable_equipments = { ship_hull_light_1 } } }\n",
    )
    _write(
        tmp_path / "common/special_projects/projects/naval.txt",
        "sp_cruiser_sub = { project_output = { enable_equipments = {\n"
        '    limit = { has_dlc = "Man the Guns" } ship_hull_cruiser_submarine } } }\n',
    )
    _write(
        tmp_path / "common/technologies/subs.txt",
        "technologies = { cruiser_subs = { enable_equipments = { ship_hull_cruiser_submarine } } }\n",
    )
    mod = Mod(tmp_path)
    country = mod.create_country("ABC", "Test Country")
    country.raw_history = (
        'if = { limit = { has_dlc = "Man the Guns" } set_technology = { basic_hull = 1 } }\n'
        # Exists on every path, but the hull only matters with Man the Guns.
        'create_equipment_variant = { name = "Everywhere" type = ship_hull_light_1 }\n'
        "complete_special_project = { project = sp:sp_cruiser_sub }\n"
        'create_equipment_variant = { name = "Surcouf" type = ship_hull_cruiser_submarine }\n'
    )
    assert mod._validate_equipment_variant_unlocks(country) == []

    country.raw_history = (
        'create_equipment_variant = { name = "Too Early" type = ship_hull_light_1 }\n'
    )
    findings = mod._validate_equipment_variant_unlocks(country)
    assert [f.code for f in findings] == ["equipment_variant_chassis_not_unlocked"]


# ── Countries ─────────────────────────────────────────────────────


def test_country_named_only_per_ideology_has_a_name(tmp_path: Path) -> None:
    game, mod_root = _setup(tmp_path)
    _write(
        game / "localisation/english/countries_l_english.yml",
        '﻿l_english:\n ABC_neutrality: "Abcland"\n ABC_neutrality_ADJ: "Abc"\n',
    )
    _country(mod_root, "ABC", "")
    _country(mod_root, "XYZ", "")
    # Civil-war tags take the name of the country that spawns them.
    _write(
        mod_root / "common/country_tags/zz_dynamic_countries.txt",
        'dynamic_tags = yes\nD01 = "countries/D01.txt"\n',
    )
    _write(mod_root / "common/countries/D01.txt", "color = { 1 2 3 }\n")

    mod = Mod(mod_root, hoi4_install=game)
    assert "D01" in mod._countries
    names = _codes(mod._validate_countries(set())).get("missing_country_name", [])

    assert [(f.country_tag, f.severity) for f in names] == [("XYZ", "warning")]


def test_every_country_finding_has_a_code() -> None:
    from hoi4.types import Country
    from hoi4.validation import validate_country

    country = Country(
        tag="bad!",
        name="",
        ruling_party="anarchism",
        popularities={"democratic": 10},
        color=(300, 0, 0),
    )

    findings = validate_country(country)

    assert findings
    assert all(finding.code for finding in findings)
    assert {finding.code for finding in findings} >= {
        "invalid_country_tag",
        "missing_country_name",
        "country_popularity_sum",
        "invalid_ruling_party",
        "invalid_country_color",
    }
