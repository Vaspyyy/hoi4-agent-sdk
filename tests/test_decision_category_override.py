from __future__ import annotations

from pathlib import Path

from hoi4 import Mod


def test_generated_category_file_never_hides_a_game_file(tmp_path: Path) -> None:
    """A mod file replaces the game file of the same name, so a derived name must differ."""

    game = tmp_path / "game"
    vanilla = game / "common/decisions/categories/XYZ_decision_categories.txt"
    vanilla.parent.mkdir(parents=True)
    vanilla.write_text(
        "vanilla_category = {\n\ticon = generic_political_actions\n\tpriority = 5\n}\n",
        encoding="utf-8",
    )
    mod_root = tmp_path / "mod"
    mod = Mod(mod_root, hoi4_install=game)

    category = mod.create_decision_category(
        "my_category", icon="generic_research", path="common/decisions/XYZ_decisions.txt"
    )
    mod.create_decision("my_category", "my_decision", cost=10)
    mod.save(require_changes=True)

    categories_dir = mod_root / "common/decisions/categories"
    assert category.definition_path == categories_dir / "XYZ_decision_categories_mod.txt"
    assert [path.name for path in categories_dir.iterdir()] == ["XYZ_decision_categories_mod.txt"]
    assert "my_category" in (categories_dir / "XYZ_decision_categories_mod.txt").read_text(
        encoding="utf-8"
    )

    reloaded = Mod(mod_root, hoi4_install=game)
    assert not reloaded.load_diagnostics
    assert reloaded.get_decision_category("my_category").icon == "generic_research"


def test_derived_category_file_name_is_unchanged_when_nothing_is_shadowed(tmp_path: Path) -> None:
    mod = Mod(tmp_path)

    category = mod.create_decision_category("my_category", path="common/decisions/XYZ_decisions.txt")

    assert category.definition_path == (
        tmp_path / "common/decisions/categories/XYZ_decision_categories.txt"
    )
