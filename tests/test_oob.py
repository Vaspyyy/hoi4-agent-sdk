from __future__ import annotations

from pathlib import Path
import copy

from hoi4 import Mod
from hoi4.oob import (
    AirWing,
    Battalion,
    DivisionTemplate,
    DivisionUnit,
    Fleet,
    OrderOfBattle,
    Ship,
    ShipEquipment,
    TaskForce,
    load_oob_file,
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
    assert validate_oob(oob) == []


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
    oob = mod.create_oob(
        "ABC_1936",
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
        air_wings=[AirWing(10, "fighter_equipment_0", 12)],
        assign=False,
    )

    assert oob.fleets[0].task_forces[0].ships[0].equipment[0].owner == "ABC"
    assert oob.air_wings[0].owner == "ABC"
