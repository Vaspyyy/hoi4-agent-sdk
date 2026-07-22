from __future__ import annotations

from pathlib import Path

from hoi4.dynamic_ideas import DynamicIdea, DynamicIdeaGroup, load_dynamic_ideas_file
from hoi4.dynamic_ideas import remove_dynamic_ideas_container
from hoi4.dynamic_ideas import serialize_dynamic_ideas_file
from hoi4.parser import parse_pdx


SOURCE = """# dynamic header
dynamic_country_ideas = {
    name = ABC_dynamic_ideas

    ABC_growing = {
        potential = {
            original_tag = ABC
        }
        available = { has_war = no }
        modifier = {
            political_power_gain = 0.2 # preserve me
        }
        removal_cost = -1
    }
}
"""


def test_dynamic_ideas_round_trip_without_changes(tmp_path: Path) -> None:
    path = tmp_path / "abc_dynamic_ideas.txt"
    path.write_text(SOURCE, encoding="utf-8")

    group = load_dynamic_ideas_file(path)

    assert group is not None
    assert group.name == "ABC_dynamic_ideas"
    assert group.ideas[0].potential.strip() == "original_tag = ABC"
    assert group.ideas[0].modifier == {"political_power_gain": 0.2}
    assert serialize_dynamic_ideas_file(group, original=SOURCE) == SOURCE


def test_dynamic_idea_modifier_edit_is_local(tmp_path: Path) -> None:
    path = tmp_path / "abc_dynamic_ideas.txt"
    path.write_text(SOURCE, encoding="utf-8")
    group = load_dynamic_ideas_file(path)
    assert group is not None
    group.ideas[0].modifier["political_power_gain"] = 0.3
    group.ideas[0].touched_fields.add("modifier")

    rendered = serialize_dynamic_ideas_file(group, original=SOURCE)

    assert "political_power_gain = 0.3" in rendered
    assert "# preserve me" in rendered
    assert "removal_cost = -1" in rendered
    assert "# dynamic header" in rendered
    parse_pdx(rendered)


def test_new_dynamic_group_serializes() -> None:
    group = DynamicIdeaGroup(
        name="XYZ_dynamic_ideas",
        ideas=[
            DynamicIdea(
                id="XYZ_ready",
                potential="original_tag = XYZ",
                available="has_government = democratic",
                modifier={"stability_factor": 0.1},
            )
        ],
    )

    rendered = serialize_dynamic_ideas_file(group)

    assert "dynamic_country_ideas = {" in rendered
    assert "XYZ_ready = {" in rendered
    parse_pdx(rendered)


def test_remove_dynamic_container_preserves_unrelated_source() -> None:
    original = SOURCE + "\n# unrelated footer\nother_setting = yes\n"

    rendered = remove_dynamic_ideas_container(original)

    assert "dynamic_country_ideas" not in rendered
    assert "# dynamic header" in rendered
    assert "# unrelated footer" in rendered
    assert "other_setting = yes" in rendered
    parse_pdx(rendered)
