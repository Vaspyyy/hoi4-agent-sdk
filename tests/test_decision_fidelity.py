"""Decision files are read from the syntax tree and patched span by span."""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from hoi4 import Mod
from hoi4.decisions import load_decisions_file, serialize_decisions_file


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _save(mod: Mod) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mod.save()


# ── Bug 1: only the decision's own top-level keys count ──────────


def test_nested_cost_and_icon_do_not_leak_into_the_decision(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "decisions.txt",
        """cat = {
\tnested_only = {
\t\tcomplete_effect = {
\t\t\tcost = 99
\t\t\ticon = bad_icon
\t\t\tdays_remove = 3
\t\t\tfire_only_once = yes
\t\t\tavailable = { always = no }
\t\t}
\t\t# cost = 42
\t}
}
""",
    )
    decision = load_decisions_file(path)[0].decisions[0]

    assert decision.id == "nested_only"
    assert decision.cost is None
    assert decision.icon == ""
    assert decision.days_remove is None
    assert decision.fire_only_once is None
    assert decision.available == ""
    assert "cost = 99" in decision.complete_effect


def test_own_fields_win_over_nested_ones_that_come_first(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "decisions.txt",
        """cat = {
\tdecision = {
\t\tcomplete_effect = { cost = 99 icon = bad_icon }
\t\ticon = "GFX_good"
\t\tcost = 7
\t}
}
""",
    )
    decision = load_decisions_file(path)[0].decisions[0]

    assert decision.cost == 7
    assert decision.icon == "GFX_good"


def test_decision_with_only_unlisted_keys_is_still_a_decision(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "decisions.txt",
        "cat = {\n\tplain = { icon = generic ai_will_do = { factor = 1 } }\n}\n",
    )
    decisions = load_decisions_file(path)[0].decisions

    assert [decision.id for decision in decisions] == ["plain"]
    assert decisions[0].icon == "generic"


# ── Bug 2: a category reopened in the same file ───────────────────

REOPENED = """political_actions = {
\tfirst_decision = {
\t\t# keep this comment
\t\tcost = 10
\t}
}

some_other_category = {
\tother_decision = { cost = 5 }
}

political_actions = {
\tsecond_decision = {
\t\tcost = 20
\t\tunknown_key = { keep = me }
\t}
}
"""


def _reopened_mod(tmp_path: Path) -> tuple[Mod, Path]:
    _write(tmp_path / "descriptor.mod", 'name = "test"\n')
    path = _write(tmp_path / "common/decisions/SOV.txt", REOPENED)
    return Mod(tmp_path), path


def test_reopened_category_loads_all_decisions(tmp_path: Path) -> None:
    mod, _ = _reopened_mod(tmp_path)

    assert not mod.load_diagnostics
    assert mod.list_decisions() == ["first_decision", "other_decision", "second_decision"]
    category = mod.get_decision_category("political_actions")
    assert [decision.id for decision in category.decisions] == [
        "first_decision",
        "second_decision",
    ]
    assert mod.get_decision("second_decision").cost == 20


@pytest.mark.parametrize(
    ("decision_id", "old", "new"),
    [("first_decision", "cost = 10", "cost = 11"), ("second_decision", "cost = 20", "cost = 21")],
)
def test_editing_a_decision_in_a_reopened_category_changes_only_its_value(
    tmp_path: Path, decision_id: str, old: str, new: str
) -> None:
    mod, path = _reopened_mod(tmp_path)

    assert mod.update_decision(decision_id, cost=int(new.split()[-1]))
    _save(mod)

    assert path.read_text(encoding="utf-8") == REOPENED.replace(old, new)
    assert not Mod(tmp_path).load_diagnostics


def test_untouched_reopened_category_is_byte_identical(tmp_path: Path) -> None:
    mod, path = _reopened_mod(tmp_path)
    for decision_id in mod.list_decisions():
        mod.update_decision(decision_id)
    _save(mod)

    assert path.read_text(encoding="utf-8") == REOPENED


def test_reopened_category_new_decision_goes_to_last_block_and_delete_keeps_others(
    tmp_path: Path,
) -> None:
    mod, path = _reopened_mod(tmp_path)

    mod.create_decision("political_actions", "third_decision", cost=30)
    assert mod.delete_decision("first_decision")
    _save(mod)

    saved = path.read_text(encoding="utf-8")
    assert "first_decision" not in saved
    assert "# keep this comment" not in saved
    assert saved.count("political_actions = {") == 2
    assert saved.index("third_decision") > saved.index("second_decision")
    assert "unknown_key = { keep = me }" in saved
    assert "\tother_decision = { cost = 5 }\n" in saved
    reloaded = Mod(tmp_path)
    assert reloaded.list_decisions() == ["other_decision", "second_decision", "third_decision"]


def test_serializer_keeps_every_block_of_a_reopened_category(tmp_path: Path) -> None:
    path = _write(tmp_path / "decisions.txt", REOPENED)
    categories = load_decisions_file(path)

    assert serialize_decisions_file(categories, REOPENED) == REOPENED
    second = categories[0].decisions[1]
    second.cost = 25
    second.touched = True
    assert serialize_decisions_file(categories, REOPENED) == REOPENED.replace(
        "cost = 20", "cost = 25"
    )


def test_decision_defined_twice_in_a_reopened_category_is_still_a_duplicate(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "descriptor.mod", 'name = "test"\n')
    source = (
        "cat = { twice = { cost = 1 } edit_me = { cost = 2 } }\n"
        "cat = { twice = { cost = 3 } }\n"
    )
    path = _write(tmp_path / "common/decisions/dup.txt", source)
    mod = Mod(tmp_path)

    assert [(item.section, item.identifier) for item in mod.load_diagnostics] == [
        ("decision", "twice")
    ]
    assert mod.update_decision("edit_me", cost=4)
    with pytest.raises(RuntimeError, match="duplicate definitions skipped"):
        mod.save()
    assert path.read_text(encoding="utf-8") == source


# ── A category continued in another file ──────────────────────────


def _cross_file_mod(tmp_path: Path) -> tuple[Mod, Path, Path]:
    _write(tmp_path / "descriptor.mod", 'name = "test"\n')
    first = _write(
        tmp_path / "common/decisions/A.txt",
        "political_actions = {\n"
        "\t# A's part of the shared category\n"
        "\tA_shared = { cost = 5 }\n"
        "}\n"
        "A_own = {\n\tA_edit = { cost = 1 }\n}\n",
    )
    second = _write(
        tmp_path / "common/decisions/B.txt",
        "political_actions = {\n\tB_shared = { cost = 7 }\n}\n",
    )
    return Mod(tmp_path), first, second


def test_saving_a_file_keeps_its_part_of_a_category_continued_elsewhere(
    tmp_path: Path,
) -> None:
    mod, first, second = _cross_file_mod(tmp_path)
    before_first = first.read_text(encoding="utf-8")
    before_second = second.read_text(encoding="utf-8")

    assert mod.update_decision("A_edit", cost=2)
    _save(mod)

    assert first.read_text(encoding="utf-8") == before_first.replace(
        "A_edit = { cost = 1 }", "A_edit = { cost = 2 }"
    )
    assert second.read_text(encoding="utf-8") == before_second


def test_decision_in_any_file_of_a_shared_category_can_be_edited(tmp_path: Path) -> None:
    mod, first, second = _cross_file_mod(tmp_path)
    before_first = first.read_text(encoding="utf-8")
    before_second = second.read_text(encoding="utf-8")

    assert mod.update_decision("A_shared", cost=6)
    assert mod.update_decision("B_shared", cost=8)
    _save(mod)

    assert first.read_text(encoding="utf-8") == before_first.replace("cost = 5", "cost = 6")
    assert second.read_text(encoding="utf-8") == before_second.replace("cost = 7", "cost = 8")


def test_deleting_the_last_shared_decision_of_a_file_keeps_the_category_block(
    tmp_path: Path,
) -> None:
    mod, first, second = _cross_file_mod(tmp_path)
    before_second = second.read_text(encoding="utf-8")

    assert mod.delete_decision("A_shared")
    _save(mod)

    saved = first.read_text(encoding="utf-8")
    assert "A_shared" not in saved
    assert "# A's part of the shared category" in saved
    assert "A_edit = { cost = 1 }" in saved
    assert second.read_text(encoding="utf-8") == before_second
    assert Mod(tmp_path).list_decisions() == ["A_edit", "B_shared"]


# ── Saving writes only what changed ───────────────────────────────


def test_decision_edit_does_not_create_an_empty_category_definition(tmp_path: Path) -> None:
    # The category is defined by the game (or a base mod) under the same file
    # name. An empty definition written here would replace that file.
    _write(tmp_path / "descriptor.mod", 'name = "test"\n')
    path = _write(tmp_path / "common/decisions/AFG.txt", "AFG_category = {\n\tAFG_dec = { cost = 1 }\n}\n")
    mod = Mod(tmp_path)

    mod.update_decision("AFG_dec", cost=2)
    mod.create_decision("AFG_category", "AFG_new", cost=3)
    _save(mod)

    assert not (tmp_path / "common/decisions/categories").exists()
    assert "AFG_new" in path.read_text(encoding="utf-8")


def test_category_definition_edit_does_not_create_a_decisions_file(tmp_path: Path) -> None:
    _write(tmp_path / "descriptor.mod", 'name = "test"\n')
    source = (
        "first_category = {\n\ticon = generic\n\tvisible = {\n\t\t\n\t}\n}\n"
        "second_category = {\n\ticon = other\n\tallowed = {\n\t}\n}\n"
    )
    path = _write(tmp_path / "common/decisions/categories/X_decision_categories.txt", source)
    mod = Mod(tmp_path)

    # Setting fields to their current values changes nothing, empty blocks included.
    for category_id in mod.list_decision_categories():
        category = mod.get_decision_category(category_id)
        mod.update_decision_category(
            category_id, icon=category.icon, allowed=category.allowed, visible=category.visible
        )
    _save(mod)
    assert path.read_text(encoding="utf-8") == source

    mod.update_decision_category("second_category", icon="changed")
    _save(mod)
    assert path.read_text(encoding="utf-8") == source.replace("icon = other", "icon = changed")
    assert sorted(p.name for p in (tmp_path / "common/decisions").rglob("*.txt")) == [
        "X_decision_categories.txt"
    ]
