import pytest

from hoi4 import Mod
from hoi4.decisions import (
    load_decision_categories_file,
    load_decisions_file,
    serialize_decision_categories_file,
    serialize_decisions_file,
)
from hoi4.patching import top_level_assignments


def test_loads_decision_category_and_decision(tmp_path):
    p = tmp_path / "decisions.txt"
    p.write_text(
        """
test_category = {
    icon = generic_decision
    allowed = { original_tag = TST }
    visible = { always = yes }
    test_decision = {
        icon = generic_decision
        cost = 25
        days_remove = 30
        fire_only_once = yes
        available = { has_war = no }
        complete_effect = { add_political_power = 50 }
        ai_will_do = { factor = 1 }
    }
}
""",
        encoding="utf-8",
    )
    categories = load_decisions_file(p)
    assert [category.id for category in categories] == ["test_category"]
    decision = categories[0].decisions[0]
    assert decision.id == "test_decision"
    assert decision.category == "test_category"
    assert decision.cost == 25
    assert decision.fire_only_once is True
    assert "add_political_power" in decision.complete_effect


def test_serialize_decision_file(tmp_path):
    mod = Mod(tmp_path)
    category = mod.create_decision_category("test_category", icon="generic_decision")
    mod.create_decision(
        "test_category", "test_decision", cost=10, complete_effect="add_stability = 0.05"
    )
    text = serialize_decisions_file([category])
    assert "test_category = {" in text
    assert "test_decision = {" in text
    assert "add_stability = 0.05" in text
    assert "icon = generic_decision" not in text

    category_text = serialize_decision_categories_file([category])
    assert "test_category = {" in category_text
    assert "icon = generic_decision" in category_text
    assert "test_decision = {" not in category_text


def test_create_decision_requires_explicit_overwrite(tmp_path):
    mod = Mod(tmp_path)
    mod.create_decision_category("test_category", icon="old_icon")
    with pytest.raises(ValueError, match="overwrite=True"):
        mod.create_decision_category("test_category", icon="new_icon")
    category = mod.create_decision_category("test_category", icon="new_icon", overwrite=True)
    assert category.icon == "new_icon"

    mod.create_decision("test_category", "test_decision", complete_effect="old_effect = yes")
    with pytest.raises(ValueError, match="overwrite=True"):
        mod.create_decision("test_category", "test_decision", complete_effect="new_effect = yes")
    decision = mod.create_decision(
        "test_category",
        "test_decision",
        complete_effect="new_effect = yes",
        overwrite=True,
    )
    assert decision.complete_effect == "new_effect = yes"
    assert [candidate.id for candidate in category.decisions] == ["test_decision"]


def test_mod_saves_and_loads_decision(tmp_path):
    mod = Mod(tmp_path)
    mod.create_decision(
        "test_category",
        "test_decision",
        cost=10,
        complete_effect="add_political_power = 25",
    )
    mod.save()
    decision_path = tmp_path / "common/decisions/mod_decisions.txt"
    category_path = (
        tmp_path
        / "common/decisions/categories/mod_decision_categories.txt"
    )
    assert decision_path.is_file()
    assert category_path.is_file()
    assert "test_decision = {" in decision_path.read_text(encoding="utf-8")
    assert "icon =" not in decision_path.read_text(encoding="utf-8")
    assert "test_category = {" in category_path.read_text(encoding="utf-8")
    mod2 = Mod(tmp_path)
    assert "test_decision" in mod2.list_decisions()
    decision = mod2.get_decision("test_decision")
    assert decision.category == "test_category"
    assert "add_political_power" in decision.complete_effect


def test_loads_category_metadata_from_categories_directory(tmp_path):
    path = tmp_path / "common/decisions/categories/test_categories.txt"
    path.parent.mkdir(parents=True)
    path.write_text(
        "test_category = { icon = generic_decision allowed = { always = yes } }\n",
        encoding="utf-8",
    )

    category = load_decision_categories_file(path)[0]

    assert category.id == "test_category"
    assert category.icon == "generic_decision"
    assert category.definition_path == path
    assert "always = yes" in category.allowed


def test_touching_legacy_combined_decision_file_migrates_category_metadata(tmp_path):
    path = tmp_path / "common/decisions/legacy_decisions.txt"
    path.parent.mkdir(parents=True)
    path.write_text(
        """legacy_category = {
    icon = generic_decision
    allowed = { always = yes }
    legacy_decision = {
        cost = 5
        complete_effect = { add_political_power = 5 }
    }
}
""",
        encoding="utf-8",
    )
    mod = Mod(tmp_path)

    assert mod.update_decision("legacy_decision", cost=10)
    mod.save()

    decision_text = path.read_text(encoding="utf-8")
    category_path = (
        tmp_path
        / "common/decisions/categories/legacy_decision_categories.txt"
    )
    category_text = category_path.read_text(encoding="utf-8")
    assert "legacy_decision = {" in decision_text
    assert "cost = 10" in decision_text
    assert "icon =" not in decision_text
    assert "allowed =" not in decision_text
    assert "legacy_category = {" in category_text
    assert "icon = generic_decision" in category_text
    assert "allowed =" in category_text


@pytest.mark.parametrize("metadata_source", ["none", "legacy", "separate"])
def test_cost_edit_keeps_nested_metadata_in_decisions(tmp_path, metadata_source):
    path = tmp_path / "common/decisions/test_decisions.txt"
    path.parent.mkdir(parents=True)
    metadata = """    icon = category_icon
    allowed = { original_tag = TST }
    visible = { always = yes }
"""
    first = """    first = {
        icon = first_icon
        cost = 5
        allowed = { has_country_flag = first_allowed }
        visible = { has_war = yes }
        complete_effect = { add_political_power = 5 }
    }
"""
    sibling = """    second = {
        # Preserve this sibling exactly.
        icon = second_icon
        cost = 10
        allowed = { has_country_flag = second_allowed }
        visible = { has_war = no }
        complete_effect = { add_stability = 0.05 }
    }
"""
    # Place legacy fields after decisions so nested fields cannot win by order.
    source = "test_category = {\n" + first + sibling
    if metadata_source == "legacy":
        source += metadata
    source += "}\n"
    path.write_text(source, encoding="utf-8")
    category_path = path.parent / "categories/test_decision_categories.txt"
    separate_source = "test_category = {\n" + metadata + "}\n"
    if metadata_source == "separate":
        category_path.parent.mkdir()
        category_path.write_text(separate_source, encoding="utf-8")

    expected = (
        ("", "", "") if metadata_source == "none"
        else ("category_icon", "original_tag = TST", "always = yes")
    )
    mod = Mod(tmp_path)
    category = mod.get_decision_category("test_category")
    sibling_before = mod.get_decision("second").raw_block
    assert (category.icon, category.allowed, category.visible) == expected
    assert mod.update_decision("first", cost=15)

    preview = mod.preview()
    assert "+        cost = 15" in preview
    assert path.read_text(encoding="utf-8") == source
    added = "\n".join(line[1:] for line in preview.splitlines()
                      if line.startswith("+") and not line.startswith("+++"))
    assert "first_icon" not in added
    assert "has_war" not in added
    assert "first_allowed" not in added
    if metadata_source == "legacy":
        assert "icon = category_icon" in added
        assert "original_tag = TST" in added
        assert "always = yes" in added

    result = mod.save(require_changes=True)
    print(result)
    print(result.written_files)
    expected_paths = {path} if metadata_source == "separate" else {path, category_path}
    assert set(result.written_files) == expected_paths
    saved = path.read_text(encoding="utf-8")
    assert first.replace("cost = 5", "cost = 15") in saved
    assert sibling in saved
    category_text = category_path.read_text(encoding="utf-8")
    if metadata_source == "separate":
        assert category_text == separate_source
    assert "first_icon" not in category_text
    assert "second_icon" not in category_text
    assert "has_war" not in category_text
    assert "has_country_flag" not in category_text
    category_span = top_level_assignments(saved)[0]
    body = saved[category_span.body_start:category_span.body_end]
    assert [span.key for span in top_level_assignments(body)] == ["first", "second"]

    reloaded = Mod(tmp_path)
    category = reloaded.get_decision_category("test_category")
    assert (category.icon, category.allowed, category.visible) == expected
    assert reloaded.get_decision("first").cost == 15
    assert reloaded.get_decision("first").icon == "first_icon"
    assert reloaded.get_decision("first").visible == "has_war = yes"
    assert reloaded.get_decision("second").cost == 10
    assert reloaded.get_decision("second").raw_block == sibling_before
    assert reloaded.preview() == ""
