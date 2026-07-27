from __future__ import annotations

from pathlib import Path

from hoi4.dynamic_modifiers import (
    DynamicModifier,
    load_dynamic_modifiers_file,
    serialize_dynamic_modifiers_file,
)
from hoi4.parser import parse_pdx


SOURCE = """# dynamic header
ABC_growth = {
    icon = GFX_idea_unknown
    enable = {
        original_tag = ABC
    }
    remove_trigger = { has_war = yes }
    attacker_modifier = no
    political_power_gain = 0.2 # preserve me
    removal_cost = -1
}
"""


def test_dynamic_modifiers_round_trip_without_changes(tmp_path: Path) -> None:
    path = tmp_path / "abc_dynamic_modifiers.txt"
    path.write_text(SOURCE, encoding="utf-8")

    modifiers = load_dynamic_modifiers_file(path)

    assert len(modifiers) == 1
    modifier = modifiers[0]
    assert modifier.id == "ABC_growth"
    assert modifier.enable.strip() == "original_tag = ABC"
    assert modifier.remove_trigger.strip() == "has_war = yes"
    assert modifier.attacker_modifier is False
    assert modifier.modifier["political_power_gain"] == 0.2
    assert serialize_dynamic_modifiers_file(modifiers, original=SOURCE) == SOURCE


def test_dynamic_modifier_edit_is_local(tmp_path: Path) -> None:
    path = tmp_path / "abc_dynamic_modifiers.txt"
    path.write_text(SOURCE, encoding="utf-8")
    modifiers = load_dynamic_modifiers_file(path)
    modifier = modifiers[0]
    modifier.modifier["political_power_gain"] = 0.3
    modifier.touched_fields.add("modifier")

    rendered = serialize_dynamic_modifiers_file(modifiers, original=SOURCE)

    assert "political_power_gain = 0.3" in rendered
    assert "# preserve me" in rendered
    assert "removal_cost = -1" in rendered
    assert "# dynamic header" in rendered
    parse_pdx(rendered)


def test_new_dynamic_modifiers_serialize() -> None:
    modifier = DynamicModifier(
        id="XYZ_ready",
        icon="GFX_idea_unknown",
        enable="original_tag = XYZ",
        remove_trigger="has_war = yes",
        modifier={"stability_factor": 0.1},
    )

    rendered = serialize_dynamic_modifiers_file([modifier])

    assert "XYZ_ready = {" in rendered
    assert "enable = {" in rendered
    assert "stability_factor = 0.1" in rendered
    assert "dynamic_country_ideas" not in rendered
    parse_pdx(rendered)


def test_delete_dynamic_modifier_preserves_unrelated_source(tmp_path: Path) -> None:
    original = SOURCE + "\n# unrelated footer\nother_setting = yes\n"
    path = tmp_path / "abc_dynamic_modifiers.txt"
    path.write_text(original, encoding="utf-8")

    rendered = serialize_dynamic_modifiers_file([], original=original)

    assert "ABC_growth" not in rendered
    assert "# dynamic header" in rendered
    assert "# unrelated footer" in rendered
    assert "other_setting = yes" in rendered
    parse_pdx(rendered)
