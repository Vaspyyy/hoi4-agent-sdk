from __future__ import annotations

from pathlib import Path
import copy

import pytest

from hoi4 import Mod
from hoi4.oob import (
    AirWing,
    Battalion,
    DivisionTemplate,
    DivisionUnit,
    EquipmentVariant,
    Fleet,
    OOBReference,
    OrderOfBattle,
    Ship,
    ShipEquipment,
    TaskForce,
    find_equipment_variants,
    find_oob_references,
    load_oob_file,
    remove_oob_reference,
    serialize_equipment_variant,
    serialize_oob,
    validate_oob,
)


OOB = """division_template = {
    name = "Infantry"
    custom_template_key = keep
    regiments = {
        infantry = { x = 0 y = 0 }
        infantry = { x = 0 y = 1 }
    }
}

units = {
    division = {
        name = "First Division"
        location = 100
        division_template = "Infantry"
        start_equipment_factor = 0.8
    }
    fleet = {
        name = "Preserved Fleet"
        naval_base = 200
        task_force = { name = "Fleet One" location = 200 }
    }
}

instant_effect = {
    add_equipment_production = {
        requested_factories = 1
    }
}
"""


def _write(path: Path, text: str = OOB) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_oob_round_trips_with_unmodeled_navy_and_production(tmp_path: Path) -> None:
    path = _write(tmp_path / "history/units/ABC_1936.txt")
    oob = load_oob_file(path, country_tag="ABC")

    assert serialize_oob(oob) == OOB
    assert oob.templates[0].battalions == [
        Battalion("infantry", 0, 0),
        Battalion("infantry", 0, 1),
    ]
    assert oob.divisions[0].location == 100


def test_division_edit_preserves_fleet_and_instant_effect(tmp_path: Path) -> None:
    path = _write(tmp_path / "history/units/ABC_1936.txt")
    oob = load_oob_file(path, country_tag="ABC")
    oob.divisions[0].location = 101
    oob.divisions[0].touched_fields.add("location")
    oob.touched_fields.add("divisions")

    rendered = serialize_oob(oob)

    assert rendered == OOB.replace("location = 100", "location = 101", 1)
    assert 'name = "Preserved Fleet"' in rendered
    assert "add_equipment_production" in rendered


def test_new_land_oob_serializes_and_validates() -> None:
    oob = OrderOfBattle(
        name="ABC_1936",
        country_tag="ABC",
        templates=[
            DivisionTemplate(
                "Infantry",
                battalions=[
                    Battalion("infantry", 0, 0),
                    Battalion("infantry", 0, 1),
                ],
                support=[Battalion("recon", 0, 0)],
            )
        ],
        divisions=[
            DivisionUnit(
                name="First Division",
                location=100,
                division_template="Infantry",
                start_experience_factor=0.2,
                start_equipment_factor=0.8,
            )
        ],
    )

    rendered = serialize_oob(oob)

    assert 'name = "Infantry"' in rendered
    assert "regiments = {" in rendered
    assert "support = {" in rendered
    assert 'division_template = "Infantry"' in rendered
    assert validate_oob(oob) == []


def test_oob_validation_rejects_duplicate_positions_and_bad_references() -> None:
    oob = OrderOfBattle(
        name="bad",
        country_tag="ABC",
        templates=[
            DivisionTemplate(
                "Infantry",
                battalions=[
                    Battalion("infantry", 0, 0),
                    Battalion("artillery", 0, 0),
                ],
            )
        ],
        divisions=[
            DivisionUnit(
                division_template="Missing",
                location=0,
                start_equipment_factor=1.5,
            )
        ],
    )

    codes = {issue.code for issue in validate_oob(oob)}

    assert {
        "duplicate_oob_grid_position",
        "unknown_oob_template",
        "invalid_oob_location",
        "invalid_oob_factor",
    } <= codes


def test_naval_and_air_oob_load_and_land_edit_remains_byte_preserving(
    tmp_path: Path,
) -> None:
    source = OOB.replace(
        "task_force = { name = \"Fleet One\" location = 200 }",
        """task_force = {
            name = "Fleet One"
            location = 200
            ship = {
                name = "SMS Test"
                definition = light_cruiser
                future_ship_field = keep
                equipment = {
                    ship_hull_cruiser_1 = {
                        amount = 1
                        owner = ABC
                        version_name = "Test Class"
                    }
                }
            }
        }""",
    ) + """air_wings = {
    10 = {
        fighter_equipment_0 = {
            owner = ABC
            amount = 24
            future_air_field = keep
        }
    }
}
"""
    path = _write(tmp_path / "history/units/ABC_1936.txt", source)
    oob = load_oob_file(path, country_tag="ABC")

    assert oob.fleets[0].task_forces[0].ships[0].name == "SMS Test"
    assert oob.air_wings[0].amount == 24
    oob.divisions[0].location = 101
    oob.divisions[0].touched_fields.add("location")
    oob.touched_fields.add("divisions")

    rendered = serialize_oob(oob)

    assert rendered == source.replace("location = 100", "location = 101", 1)
    assert "future_ship_field = keep" in rendered
    assert "future_air_field = keep" in rendered
    assert "add_equipment_production" in rendered


def test_new_naval_and_air_oob_serializes_and_validates() -> None:
    oob = OrderOfBattle(
        name="ABC_1936",
        country_tag="ABC",
        fleets=[
            Fleet(
                name="Test Fleet",
                naval_base=100,
                task_forces=[
                    TaskForce(
                        name="Test Squadron",
                        location=100,
                        ships=[
                            Ship(
                                name="SMS Test",
                                definition="light_cruiser",
                                equipment=[
                                    ShipEquipment(
                                        "ship_hull_cruiser_1",
                                        owner="ABC",
                                        version_name="Test Class",
                                    )
                                ],
                            )
                        ],
                    )
                ],
            )
        ],
        air_wings=[
            AirWing(
                location=10,
                equipment_type="fighter_equipment_0",
                amount=24,
                owner="ABC",
            )
        ],
    )

    rendered = serialize_oob(oob)

    assert "fleet = {" in rendered
    assert "task_force = {" in rendered
    assert "ship = {" in rendered
    assert "ship_hull_cruiser_1 = {" in rendered
    assert "air_wings = {" in rendered
    assert "fighter_equipment_0 = {" in rendered
    findings = validate_oob(oob)
    assert [finding.code for finding in findings] == ["mixed_oob_kinds"]
    assert findings[0].severity == "warning"


def test_mod_update_oob_patches_navy_without_clobbering_unknown_content(
    tmp_path: Path,
) -> None:
    source = OOB.replace(
        "task_force = { name = \"Fleet One\" location = 200 }",
        """task_force = {
            name = "Fleet One"
            location = 200
            future_force_field = keep
            ship = {
                name = "SMS Old"
                definition = destroyer
                equipment = {
                    destroyer_1 = { amount = 1 owner = ABC future_equipment = keep }
                }
            }
        }""",
    )
    _write(tmp_path / "history/units/ABC_1936.txt", source)
    mod = Mod(tmp_path)
    fleets = copy.deepcopy(mod.get_oob("ABC_1936").fleets)
    fleets[0].task_forces[0].ships[0].name = "SMS New"

    assert mod.update_oob("ABC_1936", fleets=fleets)
    mod.save()
    rendered = (tmp_path / "history/units/ABC_1936.txt").read_text(encoding="utf-8")

    assert 'name = "SMS New"' in rendered
    assert "future_force_field = keep" in rendered
    assert "future_equipment = keep" in rendered
    assert "add_equipment_production" in rendered


def test_create_oob_defaults_naval_and_air_equipment_owner(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    naval = mod.create_oob(
        "ABC_naval",
        "ABC",
        fleets=[
            Fleet(
                "Fleet",
                100,
                [TaskForce(
                    "Force",
                    100,
                    [Ship(
                        "Ship",
                        "destroyer",
                        [ShipEquipment("destroyer_1")],
                    )],
                )],
            )
        ],
        assign=False,
    )
    air = mod.create_oob(
        "ABC_air",
        "ABC",
        air_wings=[AirWing(10, "fighter_equipment_0", 12)],
        assign=False,
    )

    assert naval.fleets[0].task_forces[0].ships[0].equipment[0].owner == "ABC"
    assert air.air_wings[0].owner == "ABC"


def test_create_oob_rejects_mixed_or_mismatched_new_content(
    tmp_path: Path,
) -> None:
    mod = Mod(tmp_path)
    fleet = Fleet("Fleet", 100)

    with pytest.raises(ValueError, match="cannot mix"):
        mod.create_oob(
            "ABC_mixed",
            "ABC",
            fleets=[fleet],
            air_wings=[AirWing(10, "fighter_equipment_0", 12)],
            assign=False,
        )
    with pytest.raises(ValueError, match="does not match"):
        mod.create_oob(
            "ABC_wrong_kind",
            "ABC",
            kind="land",
            fleets=[fleet],
            assign=False,
        )


def test_dlc_conditioned_oob_references_and_variants_parse_if_else() -> None:
    history = '''IF = {
    limit = { has_dlc = "Man the Guns" }
    set_naval_oob = "ABC_naval_mtg"
    create_equipment_variant = {
        name = "Test Class"
        type = ship_hull_light_1
    }
    ELSE = {
        set_naval_oob = "ABC_naval_legacy"
        create_equipment_variant = {
            name = "Legacy Class"
            type = destroyer_1
        }
    }
}
'''

    assert find_oob_references(history) == (
        OOBReference("ABC_naval_mtg", "naval", ("Man the Guns",), ()),
        OOBReference("ABC_naval_legacy", "naval", (), ("Man the Guns",)),
    )
    variants = find_equipment_variants(history)
    assert [
        (item.name, item.required_dlc, item.excluded_dlc)
        for item in variants
    ] == [
        ("Test Class", ("Man the Guns",), ()),
        ("Legacy Class", (), ("Man the Guns",)),
    ]


def test_dated_oob_references_preserve_their_date() -> None:
    history = '''set_oob = "ABC_1936"
1939.1.1 = {
    set_oob = "ABC_1939"
}
'''

    references = find_oob_references(history)

    assert references == (
        OOBReference("ABC_1936", "land"),
        OOBReference("ABC_1939", "land", date="1939.1.1"),
    )
    updated, removed = remove_oob_reference(
        history,
        OOBReference("ABC_1936", "land"),
    )
    assert removed
    assert 'set_oob = "ABC_1939"' in updated
    assert 'set_oob = "ABC_1936"' not in updated


def test_assign_country_oob_allows_same_conditions_on_different_date(
    tmp_path: Path,
) -> None:
    mod = Mod(tmp_path)
    country = mod.create_country("ABC", "Test Country")
    country.raw_history += '\n1939.1.1 = { set_oob = "ABC_1939" }\n'

    mod.assign_country_oob("ABC", "ABC_1936")

    assert {reference.name for reference in mod._country_oob_references(country)} == {
        "ABC_1936",
        "ABC_1939",
    }


def test_modular_variant_requires_chassis_technology(tmp_path: Path) -> None:
    _write(
        tmp_path / "common/technologies/test.txt",
        """technologies = {
    early_ship_hull_light = {
        enable_equipments = { ship_hull_light_1 }
    }
}
""",
    )
    _write(
        tmp_path / "common/units/equipment/test.txt",
        "ship_hull_light_1 = { }\n",
    )
    variant = EquipmentVariant(
        "Test Class",
        "ship_hull_light_1",
        allow_without_tech=True,
    )
    mod = Mod(tmp_path)
    country = mod.create_country("ABC", "Test Country")

    with pytest.raises(ValueError, match="allow_without_tech"):
        mod.create_equipment_variant("ABC", variant)

    country.raw_history += "\n" + serialize_equipment_variant(variant) + "\n"
    issues = mod.validate(stage="build")
    assert "equipment_variant_chassis_not_unlocked" in {
        issue.code for issue in issues
    }

    country.technologies["early_ship_hull_light"] = 1
    assert not mod._validate_equipment_variant_unlocks(country)


@pytest.mark.parametrize(
    ("history", "expected"),
    [
        (
            """if = {
    limit = { has_dlc = "Man the Guns" }
    create_equipment_variant = {
        name = "Out of Order Class"
        type = ship_hull_light_1
    }
}
if = {
    limit = { has_dlc = "Man the Guns" }
    set_technology = { early_ship_hull_light = 1 }
}
""",
            1,
        ),
        (
            """if = {
    limit = { has_dlc = "Man the Guns" }
    set_technology = { early_ship_hull_light = 1 }
}
if = {
    limit = { has_dlc = "Man the Guns" }
    create_equipment_variant = {
        name = "Valid Class"
        type = ship_hull_light_1
    }
}
""",
            0,
        ),
        (
            """if = {
    limit = { NOT = { has_dlc = "Man the Guns" } }
    set_technology = { early_ship_hull_light = 1 }
}
if = {
    limit = { has_dlc = "Man the Guns" }
    create_equipment_variant = {
        name = "Wrong Branch Class"
        type = ship_hull_light_1
    }
}
""",
            1,
        ),
        (
            """if = {
    limit = { has_dlc = "Man the Guns" }
    set_technology = { early_ship_hull_light = 1 }
}
if = {
    limit = { NOT = { has_dlc = "Man the Guns" } }
    set_technology = { early_ship_hull_light = 1 }
}
create_equipment_variant = {
    name = "Every Path Class"
    type = ship_hull_light_1
}
""",
            0,
        ),
        (
            """if = {
    limit = { has_dlc = "Man the Guns" }
    set_technology = { early_ship_hull_light = 1 }
}
if = {
    limit = {
        OR = {
            has_dlc = "Man the Guns"
            has_dlc = "By Blood Alone"
        }
    }
    create_equipment_variant = {
        name = "Partially Unlocked OR Class"
        type = ship_hull_light_1
    }
}
""",
            1,
        ),
        (
            """if = {
    limit = { has_country_flag = permit_variant }
    set_technology = { early_ship_hull_light = 1 }
    create_equipment_variant = {
        name = "Same Guard Class"
        type = ship_hull_light_1
    }
}
""",
            0,
        ),
        (
            """if = {
    limit = { has_country_flag = choose_branch }
    set_technology = { early_ship_hull_light = 1 }
    else = {
        set_technology = { early_ship_hull_light = 1 }
    }
}
create_equipment_variant = {
    name = "Exhaustively Unlocked Class"
    type = ship_hull_light_1
}
""",
            0,
        ),
        (
            """if = {
    limit = { has_country_flag = mutable_guard }
    set_technology = { early_ship_hull_light = 1 }
}
if = {
    limit = { has_country_flag = mutable_guard }
    create_equipment_variant = {
        name = "Separately Guarded Class"
        type = ship_hull_light_1
    }
}
""",
            1,
        ),
        (
            """if = {
    limit = {
        OR = {
            has_dlc = "Man the Guns"
            has_country_flag = permit_technology
        }
    }
    set_technology = { early_ship_hull_light = 1 }
}
create_equipment_variant = {
    name = "Unconditionally Exposed Class"
    type = ship_hull_light_1
}
""",
            1,
        ),
        (
            """if = {
    limit = { has_dlc = "Man the Guns" }
    set_technology = { early_ship_hull_light = 1 }
}
if = {
    limit = {
        AND = {
            has_dlc = "Man the Guns"
            has_country_flag = permit_variant
        }
    }
    create_equipment_variant = {
        name = "Narrow Mixed Predicate Class"
        type = ship_hull_light_1
    }
}
""",
            0,
        ),
        (
            """if = {
    limit = { NOT = { has_dlc = "Man the Guns" } }
    set_technology = { early_ship_hull_light = 1 }
}
if = {
    limit = {
        NOT = {
            OR = {
                has_dlc = "Man the Guns"
                has_country_flag = deny_variant
            }
        }
    }
    create_equipment_variant = {
        name = "Legacy Mixed OR Class"
        type = ship_hull_light_1
    }
}
""",
            0,
        ),
        (
            """if = {
    limit = { NOT = { has_dlc = "Man the Guns" } }
    set_technology = { early_ship_hull_light = 1 }
}
if = {
    limit = {
        NOT = {
            AND = {
                has_dlc = "Man the Guns"
                has_country_flag = deny_variant
            }
        }
    }
    create_equipment_variant = {
        name = "Broad Mixed Negation Class"
        type = ship_hull_light_1
    }
}
""",
            1,
        ),
        (
            """if = {
    limit = {
        OR = {
            has_dlc = "Man the Guns"
            has_dlc = "By Blood Alone"
        }
    }
    set_technology = { early_ship_hull_light = 1 }
}
if = {
    limit = { has_dlc = "By Blood Alone" }
    create_equipment_variant = {
        name = "Covered BBA Class"
        type = ship_hull_light_1
    }
}
""",
            0,
        ),
        (
            """if = {
    limit = { has_dlc = "Man the Guns" }
    set_technology = { early_ship_hull_light = 1 }
}
if = {
    limit = {
        OR = {
            has_dlc = "Man the Guns"
            has_country_flag = permit_variant
        }
    }
    create_equipment_variant = {
        name = "Mixed Predicate Class"
        type = ship_hull_light_1
    }
}
""",
            1,
        ),
        (
            """if = {
    limit = {
        NOT = {
            OR = {
                has_dlc = "Man the Guns"
                has_dlc = "By Blood Alone"
            }
        }
    }
    set_technology = { early_ship_hull_light = 1 }
}
if = {
    limit = {
        NOT = { has_dlc = "Man the Guns" }
        NOT = { has_dlc = "By Blood Alone" }
    }
    create_equipment_variant = {
        name = "Covered Legacy Class"
        type = ship_hull_light_1
    }
}
""",
            0,
        ),
    ],
)
def test_modular_variant_validation_respects_source_order_and_dlc_paths(
    tmp_path: Path,
    history: str,
    expected: int,
) -> None:
    _write(
        tmp_path / "common/technologies/test.txt",
        """technologies = {
    early_ship_hull_light = {
        enable_equipments = { ship_hull_light_1 }
    }
}
""",
    )
    mod = Mod(tmp_path)
    country = mod.create_country("ABC", "Test Country")
    country.raw_history = history
    country.history_path = tmp_path / "history/countries/ABC - Test Country.txt"

    issues = mod._validate_equipment_variant_unlocks(country)

    assert len(issues) == expected
    if issues:
        issue = issues[0]
        expected_offset = history.index("create_equipment_variant")
        assert issue.line == history.count("\n", 0, expected_offset) + 1
        assert issue.column is not None


def test_pending_country_technologies_serialize_before_new_variant(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "common/countries/ABC.txt", "color = { 1 2 3 }\n")
    _write(
        tmp_path / "common/country_tags/00_tags.txt",
        'ABC = "countries/ABC.txt"\n',
    )
    history_path = tmp_path / "history/countries/ABC - Test.txt"
    _write(history_path, "capital = 1\n")
    _write(
        tmp_path / "common/technologies/test.txt",
        """technologies = {
    early_ship_hull_light = {
        enable_equipments = { ship_hull_light_1 }
    }
}
""",
    )
    _write(
        tmp_path / "common/units/equipment/test.txt",
        "ship_hull_light_1 = { }\n",
    )
    mod = Mod(tmp_path, strict_loading=True)
    mod.update_country("ABC", technologies={"early_ship_hull_light": 1})
    mod.create_equipment_variant(
        "ABC",
        EquipmentVariant("Queued Model Class", "ship_hull_light_1"),
    )

    assert not mod._validate_equipment_variant_unlocks(mod.get_country("ABC"))
    preview = mod.preview()
    assert preview.index("set_technology") < preview.index(
        "create_equipment_variant"
    )
    mod.save(require_changes=True)

    history = history_path.read_text(encoding="utf-8")
    assert history.index("set_technology") < history.index(
        "create_equipment_variant"
    )
    reloaded = Mod(tmp_path, strict_loading=True)
    assert not reloaded._validate_equipment_variant_unlocks(
        reloaded.get_country("ABC")
    )


def test_pending_country_technologies_move_before_existing_variant(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "common/countries/ABC.txt", "color = { 1 2 3 }\n")
    _write(
        tmp_path / "common/country_tags/00_tags.txt",
        'ABC = "countries/ABC.txt"\n',
    )
    history_path = tmp_path / "history/countries/ABC - Test.txt"
    _write(
        history_path,
        """capital = 1
create_equipment_variant = {
    name = "Existing Class"
    type = ship_hull_light_1
}
set_technology = { other_technology = 1 } # technology block marker
""",
    )
    _write(
        tmp_path / "common/technologies/test.txt",
        """technologies = {
    early_ship_hull_light = {
        enable_equipments = { ship_hull_light_1 }
    }
}
""",
    )
    _write(
        tmp_path / "common/units/equipment/test.txt",
        "ship_hull_light_1 = { }\n",
    )
    mod = Mod(tmp_path, strict_loading=True)

    assert mod._validate_equipment_variant_unlocks(mod.get_country("ABC"))
    mod.update_country("ABC", technologies={"early_ship_hull_light": 1})

    country = mod.get_country("ABC")
    assert not mod._validate_equipment_variant_unlocks(country)
    preview = mod.preview()
    assert preview.index("set_technology") < preview.index(
        "create_equipment_variant"
    )
    mod.save(require_changes=True)

    history = history_path.read_text(encoding="utf-8")
    assert history.index("set_technology") < history.index(
        "create_equipment_variant"
    )
    assert "} # technology block marker" in history
    reloaded = Mod(tmp_path, strict_loading=True)
    assert not reloaded._validate_equipment_variant_unlocks(
        reloaded.get_country("ABC")
    )


def test_mod_authors_separate_dlc_aware_naval_and_air_oobs(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    mod.create_country("ABC", "Test Country")
    variant = EquipmentVariant(
        name="Test Class",
        equipment_type="ship_hull_light_1",
        modules={"fixed_ship_engine_slot": "light_ship_engine_1"},
        required_dlc=("Man the Guns",),
    )
    mod.create_equipment_variant("ABC", variant)
    mod.create_oob(
        "ABC_naval_mtg",
        "ABC",
        kind="naval",
        required_dlc=("Man the Guns",),
        fleets=[
            Fleet(
                "Fleet",
                100,
                [
                    TaskForce(
                        "Force",
                        100,
                        [
                            Ship(
                                "Ship",
                                "destroyer",
                                [
                                    ShipEquipment(
                                        "ship_hull_light_1",
                                        version_name="Test Class",
                                    )
                                ],
                            )
                        ],
                    )
                ],
            )
        ],
    )
    mod.create_oob(
        "ABC_naval_legacy",
        "ABC",
        kind="naval",
        excluded_dlc=("Man the Guns",),
        fleets=[
            Fleet(
                "Legacy Fleet",
                100,
                [
                    TaskForce(
                        "Legacy Force",
                        100,
                        [
                            Ship(
                                "Legacy Ship",
                                "destroyer",
                                [ShipEquipment("destroyer_1")],
                            )
                        ],
                    )
                ],
            )
        ],
    )
    mod.create_oob(
        "ABC_air",
        "ABC",
        kind="air",
        required_dlc=("By Blood Alone",),
        air_wings=[AirWing(1, "small_plane_airframe_0", 12)],
    )
    mod.save()

    history_path = next((tmp_path / "history/countries").glob("ABC*.txt"))
    history = history_path.read_text(encoding="utf-8")
    references = find_oob_references(history)

    assert OOBReference(
        "ABC_naval_mtg", "naval", ("Man the Guns",), ()
    ) in references
    assert OOBReference(
        "ABC_naval_legacy", "naval", (), ("Man the Guns",)
    ) in references
    assert OOBReference(
        "ABC_air", "air", ("By Blood Alone",), ()
    ) in references
    assert find_equipment_variants(history) == (variant,)
    assert mod.unassign_country_oob(
        "ABC",
        "ABC_air",
        kind="air",
        required_dlc=("By Blood Alone",),
    )
    assert not mod.unassign_country_oob(
        "ABC",
        "ABC_air",
        kind="air",
        required_dlc=("By Blood Alone",),
    )


def _write_minimal_naval_context(root: Path) -> None:
    _write(
        root / "common/units/equipment/naval.txt",
        (
            "ship_hull_light_1 = { }\n"
            "destroyer_1 = { }\n"
            "small_plane_airframe_0 = { }\n"
        ),
    )
    _write(
        root / "common/units/naval.txt",
        "sub_units = { destroyer = { } }\n",
    )
    _write(
        root / "history/states/1-Test.txt",
        "state = { id = 1 provinces = { 100 } history = { owner = ABC } }\n",
    )
    _write(
        root / "map/definition.csv",
        "100;1;2;3;land;false;plains;1\n",
    )


def test_mtg_naval_oob_requires_resolvable_country_variant(tmp_path: Path) -> None:
    _write_minimal_naval_context(tmp_path)
    mod = Mod(tmp_path)
    mod.create_country("ABC", "Test Country", capital=1)
    mod.create_equipment_variant(
        "ABC",
        EquipmentVariant(
            "Test Class",
            "ship_hull_light_1",
            modules={"fixed_ship_engine_slot": "light_ship_engine_1"},
            required_dlc=("Man the Guns",),
        ),
    )
    oob = mod.create_oob(
        "ABC_naval_mtg",
        "ABC",
        kind="naval",
        required_dlc=("Man the Guns",),
        fleets=[
            Fleet(
                "Fleet",
                100,
                [
                    TaskForce(
                        "Force",
                        100,
                        [
                            Ship(
                                "Ship",
                                "destroyer",
                                [
                                    ShipEquipment(
                                        "ship_hull_light_1",
                                        version_name="Test Class",
                                    )
                                ],
                            )
                        ],
                    )
                ],
            )
        ],
    )

    assert mod._validate_oob_context(oob, "ABC") == []


def test_naval_validation_catches_engine_skipped_ship_shapes(
    tmp_path: Path,
) -> None:
    _write_minimal_naval_context(tmp_path)
    mod = Mod(tmp_path)
    mod.create_country("ABC", "Test Country", capital=1)
    legacy = mod.create_oob(
        "ABC_bad_legacy",
        "ABC",
        kind="naval",
        assign=False,
        fleets=[
            Fleet(
                "Fleet",
                100,
                [
                    TaskForce(
                        "Force",
                        100,
                        [Ship("Ship", "destroyer", [ShipEquipment("destroyer_1")])],
                    )
                ],
            )
        ],
    )
    hull = mod.create_oob(
        "ABC_bad_mtg",
        "ABC",
        kind="naval",
        required_dlc=("Man the Guns",),
        assign=False,
        fleets=[
            Fleet(
                "Fleet 2",
                100,
                [
                    TaskForce(
                        "Force 2",
                        100,
                        [
                            Ship(
                                "Ship 2",
                                "destroyer",
                                [ShipEquipment("ship_hull_light_1")],
                            )
                        ],
                    )
                ],
            )
        ],
    )

    assert "legacy_naval_oob_without_dlc_fallback" in {
        issue.code for issue in mod._validate_oob_context(legacy, "ABC")
    }
    assert "missing_mtg_ship_variant_name" in {
        issue.code for issue in mod._validate_oob_context(hull, "ABC")
    }


def test_bba_air_oob_requires_resolvable_country_variant(tmp_path: Path) -> None:
    _write_minimal_naval_context(tmp_path)
    mod = Mod(tmp_path)
    mod.create_country("ABC", "Test Country", capital=1)
    mod.create_equipment_variant(
        "ABC",
        EquipmentVariant(
            "Test Fighter",
            "small_plane_airframe_0",
            modules={"engine_type_slot": "engine_1_1x"},
            required_dlc=("By Blood Alone",),
        ),
    )
    valid = mod.create_oob(
        "ABC_air_bba",
        "ABC",
        kind="air",
        required_dlc=("By Blood Alone",),
        assign=False,
        air_wings=[
            AirWing(
                1,
                "small_plane_airframe_0",
                12,
                version_name="Test Fighter",
            )
        ],
    )
    invalid = mod.create_oob(
        "ABC_air_bad",
        "ABC",
        kind="air",
        assign=False,
        air_wings=[AirWing(1, "small_plane_airframe_0", 12)],
    )

    assert mod._validate_oob_context(valid, "ABC") == []
    invalid_codes = {
        issue.code for issue in mod._validate_oob_context(invalid, "ABC")
    }
    assert {
        "ungated_bba_air_oob",
        "missing_bba_air_variant_name",
    } <= invalid_codes
