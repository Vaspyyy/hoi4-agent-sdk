from pathlib import Path

import pytest

from hoi4 import Mod
from hoi4.decisions import load_decisions_file, serialize_decisions_file


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


def test_serialize_decision_file():
    mod = Mod("/tmp/nonexistent_decision_test_mod")
    category = mod.create_decision_category("test_category", icon="generic_decision")
    mod.create_decision("test_category", "test_decision", cost=10, complete_effect="add_stability = 0.05")
    text = serialize_decisions_file([category])
    assert "test_category = {" in text
    assert "test_decision = {" in text
    assert "add_stability = 0.05" in text


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
    mod2 = Mod(tmp_path)
    assert "test_decision" in mod2.list_decisions()
    decision = mod2.get_decision("test_decision")
    assert decision.category == "test_category"
    assert "add_political_power" in decision.complete_effect
