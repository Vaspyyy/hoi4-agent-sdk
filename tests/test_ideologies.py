from __future__ import annotations

from pathlib import Path

from hoi4.ideologies import Ideology, SubIdeology, load_ideologies_file
from hoi4.ideologies import serialize_ideologies_file
from hoi4.parser import parse_pdx


SOURCE = """# header survives
ideologies = {
    custom = {
        types = {
            custom_main = { }
            custom_hidden = { can_be_randomly_selected = no }
        }
        color = { 12 34 56 } # keep this comment
        rules = {
            can_send_volunteers = yes
        }
        modifiers = {
            drift_defence_factor = 0.1
            hidden_modifier = {
                political_power_factor = 0.2
            }
        }
        ai_democratic = yes
        unknown_future_key = { untouched = yes }
    }
}
"""


def test_loads_structured_ideology_fields(tmp_path: Path) -> None:
    path = tmp_path / "ideologies.txt"
    path.write_text(SOURCE, encoding="utf-8")

    ideology = load_ideologies_file(path)[0]

    assert ideology.id == "custom"
    assert ideology.color == (12, 34, 56)
    assert ideology.types == [
        SubIdeology("custom_main", True),
        SubIdeology("custom_hidden", False),
    ]
    assert ideology.rules == {"can_send_volunteers": "yes"}
    assert ideology.modifiers == {"drift_defence_factor": "0.1"}
    assert ideology.hidden_modifiers == {"political_power_factor": "0.2"}
    assert ideology.ai_behavior == "democratic"


def test_noop_serialization_is_byte_identical(tmp_path: Path) -> None:
    path = tmp_path / "ideologies.txt"
    path.write_text(SOURCE, encoding="utf-8")

    ideologies = load_ideologies_file(path)

    assert serialize_ideologies_file(ideologies, original=SOURCE) == SOURCE


def test_one_field_edit_preserves_unknown_content_and_comments(tmp_path: Path) -> None:
    path = tmp_path / "ideologies.txt"
    path.write_text(SOURCE, encoding="utf-8")
    ideologies = load_ideologies_file(path)
    ideologies[0].color = (90, 80, 70)
    ideologies[0].touched_fields.add("color")

    rendered = serialize_ideologies_file(ideologies, original=SOURCE)

    assert "# header survives" in rendered
    assert "# keep this comment" in rendered
    assert "unknown_future_key = { untouched = yes }" in rendered
    assert "90 80 70" in rendered
    parse_pdx(rendered)


def test_new_ideology_serializes_valid_script() -> None:
    ideology = Ideology(
        id="new_way",
        color=(1, 2, 3),
        types=[SubIdeology("new_way_main")],
        rules={"can_create_factions": "yes"},
        can_collaborate=True,
    )

    rendered = serialize_ideologies_file([ideology])

    assert "new_way = {" in rendered
    assert "can_collaborate = yes" in rendered
    parse_pdx(rendered)


def test_none_numeric_fields_are_omitted_instead_of_serialized_as_none() -> None:
    ideology = Ideology(
        id="minimal",
        ai_ideology_wanted_units_factor=None,
        ai_give_core_state_control_threshold=None,
        war_impact_on_world_tension=None,
        faction_impact_on_world_tension=None,
    )

    rendered = serialize_ideologies_file([ideology])

    assert "None" not in rendered
    assert "ai_ideology_wanted_units_factor" not in rendered
    assert "ai_give_core_state_control_threshold" not in rendered
    assert "war_impact_on_world_tension" not in rendered
    assert "faction_impact_on_world_tension" not in rendered
    parse_pdx(rendered)


def test_none_numeric_field_removes_existing_assignment_without_rewriting_source(
    tmp_path: Path,
) -> None:
    source = """ideologies = {
    custom = {
        war_impact_on_world_tension = 0.25 # remove only this
        future_setting = yes
    }
}
"""
    path = tmp_path / "ideologies.txt"
    path.write_text(source, encoding="utf-8")
    ideologies = load_ideologies_file(path)
    ideologies[0].war_impact_on_world_tension = None
    ideologies[0].touched_fields.add("war_impact_on_world_tension")

    rendered = serialize_ideologies_file(ideologies, original=source)

    assert "war_impact_on_world_tension" not in rendered
    assert "future_setting = yes" in rendered
    parse_pdx(rendered)
