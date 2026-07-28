import pytest

from hoi4 import Mod
from hoi4.decisions import (
    load_decision_categories_file,
    load_decisions_file,
    serialize_decision_categories_file,
    serialize_decisions_file,
)


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
