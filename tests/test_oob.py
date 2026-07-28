from __future__ import annotations

from pathlib import Path

from hoi4.oob import (
    Battalion,
    DivisionTemplate,
    DivisionUnit,
    OrderOfBattle,
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
