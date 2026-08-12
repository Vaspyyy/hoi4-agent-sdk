from __future__ import annotations

from pathlib import Path

import pytest

from hoi4 import (
    Mod,
    TECHNOLOGY_CATEGORIES,
    VALIDATION_CODES,
    VALIDATION_WARNING_CODES,
)
from hoi4.effects_catalog import TECHNOLOGY_CATEGORIES as CATALOG_CATEGORIES
from hoi4.modifiers_catalog import MODIFIER_CATEGORIES
from hoi4.patching import top_level_assignments


NEW_TECHNOLOGY_CATEGORIES = {
    "air_doctrine",
    "naval_doctrine",
    "submarine_doctrine",
    "special_forces_doctrine",
    "strategic_destruction_tree",
    "battlefield_support_tree",
    "operational_integrity_tree",
    "trade_interdiction_tree",
    "convoy_defense_tree",
    "fleet_in_being_tree",
    "base_strike_main",
    "cat_mobile_warfare",
    "cat_superior_firepower",
    "cat_grand_battle_plan",
    "cat_mass_assault",
    "cat_deep_battle",
    "cat_mass_mobilization",
    "cat_base_strike",
    "cat_trade_interdiction",
    "cat_fleet_in_being",
    "cat_strategic_destruction",
    "cat_battlefield_support",
    "cat_operational_integrity",
    "cat_mountaineers_doctrine",
    "cat_marines_doctrine",
    "cat_paratroopers_doctrine",
    "cat_rangers_doctrine",
}


def _write_vanilla_countries(game_root: Path) -> None:
    tags = game_root / "common/country_tags/tags.txt"
    tags.parent.mkdir(parents=True)
    tags.write_text(
        'GER = "countries/Germany.txt"\nFRA = "countries/France.txt"\n',
        encoding="utf-8",
    )
    countries = game_root / "common/countries"
    countries.mkdir(parents=True, exist_ok=True)
    (countries / "Germany.txt").write_text("color = { 1 2 3 }\n", encoding="utf-8")
    (countries / "France.txt").write_text("color = { 4 5 6 }\n", encoding="utf-8")
    (countries / "colors.txt").write_text(
        "GER = { color = rgb { 1 2 3 } color_ui = rgb { 1 2 3 } }\n"
        "FRA = { color = rgb { 4 5 6 } color_ui = rgb { 4 5 6 } }\n",
        encoding="utf-8",
    )


def test_new_country_does_not_create_global_colors_file(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    mod.create_country("ABC", "Example", color=(10, 20, 30))

    mod.save()

    assert not (tmp_path / "common/countries/colors.txt").exists()
    assert "color = { 10 20 30 }" in (
        tmp_path / "common/countries/ABC.txt"
    ).read_text(encoding="utf-8")


def test_vanilla_color_override_seeds_complete_vanilla_table(tmp_path: Path) -> None:
    game_root = tmp_path / "game"
    mod_root = tmp_path / "mod"
    _write_vanilla_countries(game_root)
    mod = Mod(mod_root, hoi4_install=game_root)
    mod.get_country("GER")

    assert mod.update_country("GER", color=(9, 8, 7))
    mod.save()

    colors = (mod_root / "common/countries/colors.txt").read_text(encoding="utf-8")
    assert "GER = {" in colors
    assert colors.count("9 8 7") == 2
    assert "FRA = { color = rgb { 4 5 6 }" in colors


def test_vanilla_color_override_fails_without_source_table(tmp_path: Path) -> None:
    game_root = tmp_path / "game"
    mod_root = tmp_path / "mod"
    _write_vanilla_countries(game_root)
    (game_root / "common/countries/colors.txt").unlink()
    mod = Mod(mod_root, hoi4_install=game_root)
    mod.get_country("GER")
    assert mod.update_country("GER", color=(9, 8, 7))

    try:
        mod.save()
    except RuntimeError as error:
        assert "writing a partial table" in str(error)
    else:
        raise AssertionError("unsafe vanilla color override should fail closed")

    assert not (mod_root / "common/countries/colors.txt").exists()


def test_truncated_country_colors_file_is_reported(tmp_path: Path) -> None:
    game_root = tmp_path / "game"
    mod_root = tmp_path / "mod"
    _write_vanilla_countries(game_root)
    colors = mod_root / "common/countries/colors.txt"
    colors.parent.mkdir(parents=True)
    colors.write_text(
        "GER = { color = rgb { 9 8 7 } color_ui = rgb { 9 8 7 } }\n",
        encoding="utf-8",
    )

    codes = {issue.code for issue in Mod(mod_root, hoi4_install=game_root).validate()}

    assert "country_colors_shadow_vanilla" in codes


@pytest.mark.parametrize("stage", ["build", "package", "release"])
def test_malformed_country_colors_file_is_a_structured_issue(
    tmp_path: Path,
    stage: str,
) -> None:
    game_root = tmp_path / "game"
    mod_root = tmp_path / "mod"
    _write_vanilla_countries(game_root)
    colors = mod_root / "common/countries/colors.txt"
    colors.parent.mkdir(parents=True)
    colors.write_text(
        "GER = { color = rgb { 9 8 7 } color_ui = rgb { 9 8 7 }\n",
        encoding="utf-8",
    )

    mod = Mod(mod_root, hoi4_install=game_root)

    assert any(
        diagnostic.section == "country_colors" and diagnostic.path == colors
        for diagnostic in mod.load_diagnostics
    )
    assert mod.get_country("GER").color == (1, 2, 3)
    issues = mod.validate(stage=stage)
    assert any(
        issue.code == "load_failure" and issue.file_path == str(colors)
        for issue in issues
    )


def test_malformed_country_colors_file_fails_strict_loading(tmp_path: Path) -> None:
    colors = tmp_path / "common/countries/colors.txt"
    colors.parent.mkdir(parents=True)
    colors.write_text("GER = { color = rgb { 9 8 7 }\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Failed to load country_colors file"):
        Mod(tmp_path, strict_loading=True)


def test_malformed_vanilla_country_colors_is_an_exact_load_failure(tmp_path: Path) -> None:
    game_root = tmp_path / "game"
    mod_root = tmp_path / "mod"
    _write_vanilla_countries(game_root)
    colors = game_root / "common/countries/colors.txt"
    colors.write_text("GER = { color = rgb { 1 2 3 }\n", encoding="utf-8")

    mod = Mod(mod_root, hoi4_install=game_root)

    assert any(
        diagnostic.section == "country_colors" and diagnostic.path == colors
        for diagnostic in mod.load_diagnostics
    )
    assert mod.get_country("GER").color == (1, 2, 3)
    assert any(
        issue.code == "load_failure" and issue.file_path == str(colors)
        for issue in mod.validate(stage="build")
    )
    with pytest.raises(RuntimeError, match="Failed to load country_colors file"):
        Mod(mod_root, hoi4_install=game_root, strict_loading=True)


def test_reload_rebuilds_country_color_sources_and_diagnostics(tmp_path: Path) -> None:
    game_root = tmp_path / "game"
    mod_root = tmp_path / "mod"
    _write_vanilla_countries(game_root)
    colors = mod_root / "common/countries/colors.txt"
    colors.parent.mkdir(parents=True)
    colors.write_text("GER = { color = rgb { 9 8 7 }\n", encoding="utf-8")
    mod = Mod(mod_root, hoi4_install=game_root)
    assert any(diagnostic.section == "country_colors" for diagnostic in mod.load_diagnostics)

    colors.write_text(
        "GER = { color = rgb { 9 8 7 } color_ui = rgb { 9 8 7 } }\n"
        "FRA = { color = rgb { 4 5 6 } color_ui = rgb { 4 5 6 } }\n",
        encoding="utf-8",
    )
    mod.reload()

    assert not any(diagnostic.section == "country_colors" for diagnostic in mod.load_diagnostics)
    assert mod.get_country("GER").color == (9, 8, 7)
    assert not any(
        issue.code == "load_failure" and issue.file_path == str(colors)
        for issue in mod.validate(stage="build")
    )


def test_country_colors_corrupted_after_load_has_registered_error(tmp_path: Path) -> None:
    game_root = tmp_path / "game"
    mod_root = tmp_path / "mod"
    _write_vanilla_countries(game_root)
    colors = mod_root / "common/countries/colors.txt"
    colors.parent.mkdir(parents=True)
    colors.write_text(
        "GER = { color = rgb { 9 8 7 } color_ui = rgb { 9 8 7 } }\n",
        encoding="utf-8",
    )
    mod = Mod(mod_root, hoi4_install=game_root)

    colors.write_text("GER = { color = rgb { 9 8 7 }\n", encoding="utf-8")
    issues = mod.validate(stage="build")

    assert "country_colors_parse" in VALIDATION_CODES
    assert "country_colors_parse" not in VALIDATION_WARNING_CODES
    assert any(
        issue.code == "country_colors_parse" and issue.file_path == str(colors)
        for issue in issues
    )


def test_transaction_restores_country_color_source_cache(tmp_path: Path) -> None:
    game_root = tmp_path / "game"
    mod_root = tmp_path / "mod"
    _write_vanilla_countries(game_root)
    colors = mod_root / "common/countries/colors.txt"
    colors.parent.mkdir(parents=True)
    colors.write_text("GER = { color = rgb { 9 8 7 }\n", encoding="utf-8")
    mod = Mod(mod_root, hoi4_install=game_root)

    assert mod._country_color_sources[colors] is None
    with mod.transaction():
        mod._country_color_sources[colors] = (
            "GER = { color = rgb { 9 8 7 } color_ui = rgb { 9 8 7 } }\n"
        )

    assert mod._country_color_sources[colors] is None


def test_numeric_and_date_keys_are_top_level_assignments() -> None:
    spans = top_level_assignments(
        "1938.3.12 = { add_political_power = 5 }\n"
        "1 = { owner = GER }\n"
        "normal_key = yes\n"
    )

    assert [span.key for span in spans] == ["1938.3.12", "1", "normal_key"]


def test_all_reported_technology_categories_are_supported() -> None:
    assert TECHNOLOGY_CATEGORIES == CATALOG_CATEGORIES
    assert NEW_TECHNOLOGY_CATEGORIES <= set(TECHNOLOGY_CATEGORIES)
    for category in NEW_TECHNOLOGY_CATEGORIES:
        assert f"category = {category}" in Mod.effect_add_tech_bonus(
            "test_bonus",
            category=category,
        )


def test_modifier_catalog_uses_real_research_speed_modifier() -> None:
    keys = {
        key
        for _, modifiers in MODIFIER_CATEGORIES
        for _, key, _ in modifiers
    }

    assert "research_speed_factor" in keys
    assert "research_time_factor" not in keys


def test_idea_icon_validation_and_suggestion(tmp_path: Path) -> None:
    interface = tmp_path / "interface/ideas.gfx"
    interface.parent.mkdir(parents=True)
    interface.write_text(
        'spriteTypes = { spriteType = { name = "GFX_idea_industry" } }\n',
        encoding="utf-8",
    )
    mod = Mod(tmp_path)
    mod.create_idea("ABC_spirit", icon="GFX_idea_indstry")

    issues = mod.validate(validate_icons=True)

    icon_issue = next(issue for issue in issues if issue.code == "unknown_idea_icon")
    assert icon_issue.idea_id == "ABC_spirit"
    assert "GFX_idea_indstry" in icon_issue.message
    assert "Suggested close match: industry" in icon_issue.message
    assert mod.suggest_idea_icon("GFX_idea_indstry") == "industry"


def test_legacy_dynamic_country_ideas_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "common/national_ideas/legacy.txt"
    path.parent.mkdir(parents=True)
    path.write_text(
        "dynamic_country_ideas = { name = legacy broken = { } }\n",
        encoding="utf-8",
    )

    issues = Mod(tmp_path).validate()

    assert any(
        issue.code == "unsupported_dynamic_country_ideas"
        and issue.severity == "error"
        for issue in issues
    )


def test_engine_rejected_sdk_shapes_are_static_validation_errors(tmp_path: Path) -> None:
    ideas = tmp_path / "common/ideas/ABC_ideas.txt"
    ideas.parent.mkdir(parents=True)
    ideas.write_text(
        "ideas = { country = { ABC_spirit = { desc = ABC_spirit_desc } } }\n",
        encoding="utf-8",
    )
    characters = tmp_path / "common/characters/ABC_characters.txt"
    characters.parent.mkdir(parents=True)
    characters.write_text(
        "characters = { ABC_leader = { roles = { country_leader } "
        "country_leader = { ideology = liberalism } } }\n",
        encoding="utf-8",
    )
    decisions = tmp_path / "common/decisions/ABC_decisions.txt"
    decisions.parent.mkdir(parents=True)
    decisions.write_text(
        "ABC_category = { icon = generic_decision "
        "ABC_decision = { complete_effect = { add_stability = 0.1 } } }\n",
        encoding="utf-8",
    )
    focus = tmp_path / "common/national_focus/ABC_focus.txt"
    focus.parent.mkdir(parents=True)
    focus.write_text(
        "focus_tree = { id = ABC focus = { id = ABC_bad completion_reward = { "
        "set_politics = { ruling_party = democratic elections_frequency = 48 } } } }\n",
        encoding="utf-8",
    )

    issues = Mod(tmp_path).validate()
    codes = {issue.code for issue in issues if issue.severity == "error"}

    assert {
        "invalid_idea_desc_key",
        "invalid_character_roles_key",
        "invalid_decision_category_layout",
        "invalid_set_politics_field",
    } <= codes


def test_generated_country_tags_are_deterministic(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    mod.create_country("ZZZ", "Last")
    mod.create_country("AAA", "First")
    mod.create_country("MMM", "Middle")

    mod.save()

    tag_path = tmp_path / "common/country_tags/00_generated_tags.txt"
    assert tag_path.read_text(encoding="utf-8").splitlines() == [
        'AAA = "countries/AAA.txt"',
        'MMM = "countries/MMM.txt"',
        'ZZZ = "countries/ZZZ.txt"',
    ]


def test_create_idea_rejects_noncanonical_description_key(tmp_path: Path) -> None:
    mod = Mod(tmp_path)

    with pytest.raises(ValueError, match="fixed localization key 'ABC_spirit_desc'"):
        mod.create_idea("ABC_spirit", desc="SOME_OTHER_KEY")
