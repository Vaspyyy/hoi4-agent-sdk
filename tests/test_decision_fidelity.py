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

    mod.update_decision(decision_id, cost=int(new.split()[-1]))
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
    mod.delete_decision("first_decision")
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
    mod.update_decision("edit_me", cost=4)
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

    mod.update_decision("A_edit", cost=2)
    _save(mod)

    assert first.read_text(encoding="utf-8") == before_first.replace(
        "A_edit = { cost = 1 }", "A_edit = { cost = 2 }"
    )
    assert second.read_text(encoding="utf-8") == before_second


def test_decision_in_any_file_of_a_shared_category_can_be_edited(tmp_path: Path) -> None:
    mod, first, second = _cross_file_mod(tmp_path)
    before_first = first.read_text(encoding="utf-8")
    before_second = second.read_text(encoding="utf-8")

    mod.update_decision("A_shared", cost=6)
    mod.update_decision("B_shared", cost=8)
    _save(mod)

    assert first.read_text(encoding="utf-8") == before_first.replace("cost = 5", "cost = 6")
    assert second.read_text(encoding="utf-8") == before_second.replace("cost = 7", "cost = 8")


def test_deleting_the_last_shared_decision_of_a_file_keeps_the_category_block(
    tmp_path: Path,
) -> None:
    mod, first, second = _cross_file_mod(tmp_path)
    before_second = second.read_text(encoding="utf-8")

    mod.delete_decision("A_shared")
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


def test_decision_added_to_an_existing_category_is_indented_like_its_siblings(tmp_path):
    path = tmp_path / "common/decisions/x_decisions.txt"
    path.parent.mkdir(parents=True)
    path.write_text("my_category = {\n\tfirst = {\n\t\tcost = 5\n\t}\n}\n", encoding="utf-8")
    mod = Mod(tmp_path)

    mod.create_decision("my_category", "second", cost=1, complete_effect="add_political_power = 5")
    mod.save(require_changes=True)

    assert path.read_text(encoding="utf-8") == (
        "my_category = {\n"
        "\tfirst = {\n"
        "\t\tcost = 5\n"
        "\t}\n"
        "\tsecond = {\n"
        "\t\tcost = 1\n"
        "\t\tcomplete_effect = {\n"
        "\t\t\tadd_political_power = 5\n"
        "\t\t}\n"
        "\t}\n"
        "}\n"
    )


# ── A category spread over several files is one category ──────────

SPREAD_A = """shared_cat = {
\ticon = GFX_old
\t# keep this note
\tdec_a = {
\t\tcost = 10
\t}
}
"""
SPREAD_B = """shared_cat = {
\tvisible = { always = yes }
\tdec_b = {
\t\tcost = 20
\t}
}
"""


def _spread_mod(tmp_path: Path) -> tuple[Mod, Path, Path]:
    _write(tmp_path / "descriptor.mod", 'name = "test"\n')
    first = _write(tmp_path / "common/decisions/a.txt", SPREAD_A)
    second = _write(tmp_path / "common/decisions/b.txt", SPREAD_B)
    return Mod(tmp_path), first, second


def _decision_model(mod: Mod) -> dict[str, list[tuple[str, str, int | None]]]:
    """Every category with its decisions: id, file name and cost, in model order."""

    return {
        category_id: [
            (decision.id, decision.path.name if decision.path else "", decision.cost)
            for decision in mod.get_decision_category(category_id).decisions
        ]
        for category_id in mod.list_decision_categories()
    }


def _assert_stable_after_reload(root: Path, expected: dict, **kwargs) -> None:
    reloaded = Mod(root, **kwargs)
    assert not reloaded.load_diagnostics
    assert _decision_model(reloaded) == expected
    assert reloaded.preview() == ""


def test_category_spread_over_files_holds_the_decisions_of_every_file(
    tmp_path: Path,
) -> None:
    mod, first, second = _spread_mod(tmp_path)

    category = mod.get_decision_category("shared_cat")
    assert [decision.id for decision in category.decisions] == ["dec_a", "dec_b"]
    assert category.paths == [first, second]
    assert category.path == second
    # Legacy inline presentation is read from the first file that has it.
    assert category.icon == "GFX_old"
    assert category.visible == "always = yes"
    assert mod.list_decisions() == ["dec_a", "dec_b"]


def test_category_edit_removes_inline_presentation_from_every_file(tmp_path: Path) -> None:
    mod, first, second = _spread_mod(tmp_path)

    mod.update_decision_category("shared_cat", icon="GFX_new")
    _save(mod)

    assert first.read_text(encoding="utf-8") == SPREAD_A.replace("\ticon = GFX_old\n", "")
    assert second.read_text(encoding="utf-8") == SPREAD_B.replace(
        "\tvisible = { always = yes }\n", ""
    )
    definition = tmp_path / "common/decisions/categories/a_decision_categories.txt"
    assert definition.read_text(encoding="utf-8") == (
        "shared_cat = {\n\ticon = GFX_new\n\tvisible = {\n\t\talways = yes\n\t}\n}\n"
    )
    expected = {"shared_cat": [("dec_a", "a.txt", 10), ("dec_b", "b.txt", 20)]}
    _assert_stable_after_reload(tmp_path, expected)
    assert Mod(tmp_path).get_decision_category("shared_cat").icon == "GFX_new"


@pytest.mark.parametrize(("decision_id", "path_index"), [("dec_a", 0), ("dec_b", 1)])
def test_decision_edit_in_a_spread_category_changes_one_value(
    tmp_path: Path, decision_id: str, path_index: int
) -> None:
    mod, first, second = _spread_mod(tmp_path)
    sources = [SPREAD_A, SPREAD_B]

    mod.update_decision(decision_id, cost=99)
    result = mod.save(require_changes=True)

    edited = [first, second][path_index]
    assert result.written_files == [edited]
    old = "cost = 10" if path_index == 0 else "cost = 20"
    sources[path_index] = sources[path_index].replace(old, "cost = 99")
    assert first.read_text(encoding="utf-8") == sources[0]
    assert second.read_text(encoding="utf-8") == sources[1]
    assert not (tmp_path / "common/decisions/categories").exists()


def test_new_decision_in_a_spread_category_goes_to_the_last_file(tmp_path: Path) -> None:
    mod, first, second = _spread_mod(tmp_path)

    mod.create_decision("shared_cat", "dec_c", cost=30)
    assert [d.id for d in mod.get_decision_category("shared_cat").decisions] == [
        "dec_a",
        "dec_b",
        "dec_c",
    ]
    _save(mod)

    assert first.read_text(encoding="utf-8") == SPREAD_A
    assert second.read_text(encoding="utf-8") == SPREAD_B.replace(
        "\t}\n}\n", "\t}\n\tdec_c = {\n\t\tcost = 30\n\t}\n}\n"
    )
    expected = {
        "shared_cat": [("dec_a", "a.txt", 10), ("dec_b", "b.txt", 20), ("dec_c", "b.txt", 30)]
    }
    _assert_stable_after_reload(tmp_path, expected)


def test_new_decision_can_target_an_earlier_file_of_a_spread_category(
    tmp_path: Path,
) -> None:
    mod, first, second = _spread_mod(tmp_path)

    mod.create_decision("shared_cat", "dec_c", cost=30, path="common/decisions/a.txt")
    expected = {
        "shared_cat": [("dec_a", "a.txt", 10), ("dec_c", "a.txt", 30), ("dec_b", "b.txt", 20)]
    }
    assert _decision_model(mod) == expected
    _save(mod)

    assert first.read_text(encoding="utf-8") == SPREAD_A.replace(
        "\t}\n}\n", "\t}\n\tdec_c = {\n\t\tcost = 30\n\t}\n}\n"
    )
    assert second.read_text(encoding="utf-8") == SPREAD_B
    _assert_stable_after_reload(tmp_path, expected)


def test_deleting_a_decision_of_the_earlier_file_of_a_spread_category(
    tmp_path: Path,
) -> None:
    mod, first, second = _spread_mod(tmp_path)

    mod.delete_decision("dec_a")
    assert [d.id for d in mod.get_decision_category("shared_cat").decisions] == ["dec_b"]
    _save(mod)

    assert first.read_text(encoding="utf-8") == "shared_cat = {\n\ticon = GFX_old\n\t# keep this note\n}\n"
    assert second.read_text(encoding="utf-8") == SPREAD_B
    _assert_stable_after_reload(tmp_path, {"shared_cat": [("dec_b", "b.txt", 20)]})


REOPENED_A = """cat = {
\ticon = GFX_one
\td1 = { cost = 1 }
}
other = {
\tother_dec = { cost = 5 }
}
cat = {
\tallowed = { tag = SOV }
\td2 = { cost = 2 }
}
"""
REOPENED_B = """cat = {
\ticon = GFX_two
\td3 = { cost = 3 }
}
"""


def _reopened_spread_mod(tmp_path: Path) -> tuple[Mod, Path, Path]:
    _write(tmp_path / "descriptor.mod", 'name = "test"\n')
    first = _write(tmp_path / "common/decisions/a.txt", REOPENED_A)
    second = _write(tmp_path / "common/decisions/b.txt", REOPENED_B)
    return Mod(tmp_path), first, second


def test_category_reopened_in_one_file_and_continued_in_another(tmp_path: Path) -> None:
    mod, first, second = _reopened_spread_mod(tmp_path)

    category = mod.get_decision_category("cat")
    assert [d.id for d in category.decisions] == ["d1", "d2", "d3"]
    assert category.paths == [first, second]
    assert category.icon == "GFX_one"

    mod.update_decision("d2", cost=22)
    _save(mod)
    assert first.read_text(encoding="utf-8") == REOPENED_A.replace("cost = 2 ", "cost = 22 ")
    assert second.read_text(encoding="utf-8") == REOPENED_B

    mod = Mod(tmp_path)
    mod.update_decision_category("cat", icon="GFX_new")
    _save(mod)
    assert first.read_text(encoding="utf-8") == (
        REOPENED_A.replace("cost = 2 ", "cost = 22 ")
        .replace("\ticon = GFX_one\n", "")
        .replace("\tallowed = { tag = SOV }\n", "")
    )
    assert second.read_text(encoding="utf-8") == REOPENED_B.replace("\ticon = GFX_two\n", "")
    expected = {
        "cat": [("d1", "a.txt", 1), ("d2", "a.txt", 22), ("d3", "b.txt", 3)],
        "other": [("other_dec", "a.txt", 5)],
    }
    _assert_stable_after_reload(tmp_path, expected)
    reloaded = Mod(tmp_path).get_decision_category("cat")
    assert (reloaded.icon, reloaded.allowed) == ("GFX_new", "tag = SOV")


def test_mod_files_extending_a_game_category(tmp_path: Path) -> None:
    game = tmp_path / "game"
    game_category = _write(
        game / "common/decisions/categories/00_decision_categories.txt",
        "shared_cat = {\n\ticon = GFX_game\n}\n",
    )
    game_decisions = _write(
        game / "common/decisions/shared.txt",
        "shared_cat = {\n\tgame_dec = { cost = 1 }\n}\n",
    )
    root = tmp_path / "mod"
    _write(root / "descriptor.mod", 'name = "test"\n')
    first = _write(root / "common/decisions/mod_a.txt", "shared_cat = {\n\tdec_a = { cost = 10 }\n}\n")
    second = _write(root / "common/decisions/mod_b.txt", "shared_cat = {\n\tdec_b = { cost = 20 }\n}\n")
    game_bytes = (game_category.read_bytes(), game_decisions.read_bytes())
    mod = Mod(root, hoi4_install=game)

    category = mod.get_decision_category("shared_cat")
    assert [d.id for d in category.decisions] == ["dec_a", "dec_b"]
    assert category.paths == [first, second]

    mod.create_decision("shared_cat", "dec_c", cost=30)
    mod.delete_decision("dec_a")
    _save(mod)

    assert first.read_text(encoding="utf-8") == "shared_cat = {\n}\n"
    assert second.read_text(encoding="utf-8") == (
        "shared_cat = {\n\tdec_b = { cost = 20 }\n\tdec_c = {\n\t\tcost = 30\n\t}\n}\n"
    )
    assert (game_category.read_bytes(), game_decisions.read_bytes()) == game_bytes
    assert not (root / "common/decisions/categories").exists()
    _assert_stable_after_reload(
        root,
        {"shared_cat": [("dec_b", "mod_b.txt", 20), ("dec_c", "mod_b.txt", 30)]},
        hoi4_install=game,
    )


# ── Strings are quoted when they need it ──────────────────────────


def test_decision_string_edits_are_quoted_when_needed(tmp_path: Path) -> None:
    mod, first, _ = _spread_mod(tmp_path)

    mod.update_decision(
        "dec_a", icon="a b", custom_cost_text="cost text", on_map_mode="map_only"
    )
    _save(mod)

    saved = first.read_text(encoding="utf-8")
    assert '\t\ticon = "a b"\n' in saved
    assert '\t\tcustom_cost_text = "cost text"\n' in saved
    assert "\t\ton_map_mode = map_only\n" in saved
    decision = Mod(tmp_path).get_decision("dec_a")
    assert (decision.icon, decision.custom_cost_text) == ("a b", "cost text")


def test_new_decision_and_category_quote_strings_when_needed(tmp_path: Path) -> None:
    _write(tmp_path / "descriptor.mod", 'name = "test"\n')
    mod = Mod(tmp_path)

    mod.create_decision_category("new_cat", icon="GFX new")
    mod.create_decision(
        "new_cat",
        "new_dec",
        icon="GFX dec",
        war_with_on_remove="FROM",
        target_array="my array",
        cost=5,
    )
    _save(mod)

    assert (tmp_path / "common/decisions/mod_decisions.txt").read_text(encoding="utf-8") == (
        "new_cat = {\n"
        "\tnew_dec = {\n"
        '\t\ticon = "GFX dec"\n'
        "\t\tcost = 5\n"
        '\t\ttarget_array = "my array"\n'
        "\t\twar_with_on_remove = FROM\n"
        "\t}\n"
        "\n"
        "}\n"
    )
    assert (
        tmp_path / "common/decisions/categories/mod_decision_categories.txt"
    ).read_text(encoding="utf-8") == 'new_cat = {\n\ticon = "GFX new"\n}\n'
    reloaded = Mod(tmp_path)
    assert reloaded.get_decision_category("new_cat").icon == "GFX new"
    assert reloaded.get_decision("new_dec").icon == "GFX dec"
    assert reloaded.preview() == ""


def test_category_icon_edit_is_quoted_and_quoted_source_round_trips(tmp_path: Path) -> None:
    _write(tmp_path / "descriptor.mod", 'name = "test"\n')
    source = 'cat = {\n\ticon = "GFX_quoted"\n}\n'
    path = _write(tmp_path / "common/decisions/categories/x_decision_categories.txt", source)
    mod = Mod(tmp_path)

    assert mod.get_decision_category("cat").icon == "GFX_quoted"
    mod.update_decision_category("cat", icon="GFX_quoted")
    _save(mod)
    assert path.read_text(encoding="utf-8") == source

    mod = Mod(tmp_path)
    mod.update_decision_category("cat", icon="GFX two words")
    _save(mod)
    assert path.read_text(encoding="utf-8") == 'cat = {\n\ticon = "GFX two words"\n}\n'
    assert Mod(tmp_path).get_decision_category("cat").icon == "GFX two words"


def test_a_category_edit_writes_no_definition_for_its_untouched_neighbours(tmp_path: Path) -> None:
    path = tmp_path / "common/decisions/a.txt"
    path.parent.mkdir(parents=True)
    source = (
        "my_cat = {\n\tdec_a = { cost = 10 }\n}\n"
        "other_cat = {\n\ticon = GFX_other\n\tdec_b = { cost = 20 }\n}\n"
    )
    path.write_text(source, encoding="utf-8")
    mod = Mod(tmp_path)
    mod.update_decision_category("my_cat", icon="GFX_new")
    mod.save(require_changes=True)

    definitions = tmp_path / "common/decisions/categories/a_decision_categories.txt"
    assert definitions.read_text(encoding="utf-8") == "my_cat = {\n\ticon = GFX_new\n}\n"
    assert path.read_text(encoding="utf-8") == source


@pytest.mark.parametrize("edit", ["update", "delete", "create"])
def test_a_decision_edit_does_not_write_a_definition_for_its_category(
    tmp_path: Path, edit: str
) -> None:
    """Two legacy categories in one file. Editing ``cat_b`` itself moves its
    presentation to the categories file; a decision edit in ``cat_a`` in the
    same session must not put ``cat_a`` there too."""

    decisions = _write(
        tmp_path / "common/decisions/01_first.txt",
        "cat_a = {\n\ticon = icon_a\n\tdec_a = {\n\t\tcost = 10\n\t}\n"
        "\tdec_a2 = {\n\t\tcost = 11\n\t}\n}\n"
        "cat_b = {\n\ticon = icon_b\n\tdec_b = {\n\t\tcost = 20\n\t}\n}\n",
    )
    mod = Mod(tmp_path)
    if edit == "update":
        mod.update_decision("dec_a", cost=99)
    elif edit == "delete":
        mod.delete_decision("dec_a2")
    else:
        mod.create_decision("cat_a", "dec_a3", cost=5)
    mod.update_decision_category("cat_b", icon="icon_b_new")
    _save(mod)

    definitions = tmp_path / "common/decisions/categories/01_first_decision_categories.txt"
    assert definitions.read_text(encoding="utf-8") == "cat_b = {\n\ticon = icon_b_new\n}\n"
    written = decisions.read_text(encoding="utf-8")
    assert "cat_a = {\n\ticon = icon_a\n" in written
    assert "icon_b" not in written
