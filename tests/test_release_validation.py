from __future__ import annotations

from pathlib import Path

import pytest

from hoi4 import EventOption, Focus, Mod


def _write_documentation(game_root: Path) -> None:
    documentation = game_root / "documentation"
    documentation.mkdir(parents=True)
    (documentation / "effects_documentation.md").write_text(
        (
            "## army_experience\n\n"
            "* Supported Scopes: COUNTRY\n\n"
            "## add_core_of\n\n"
            "* Supported Scopes: STATE\n\n"
            "## country_event\n\n## set_country_flag\n\n## zero_effect\n"
        ),
        encoding="utf-8",
    )
    (documentation / "triggers_documentation.md").write_text(
        "## has_country_flag\n\n* Supported Scopes: COUNTRY\n",
        encoding="utf-8",
    )
    (documentation / "modifiers_documentation.md").write_text(
        "## stability_factor\n",
        encoding="utf-8",
    )
    script = game_root / "common" / "scripted_effects" / "uses.txt"
    script.parent.mkdir(parents=True)
    script.write_text(
        (
            "sample = {\n"
            + "army_experience = 1\n" * 7
            + "add_core_of = ABC\n"
            + "has_country_flag = sample_flag\n"
            + "}\n"
        ),
        encoding="utf-8",
    )


def test_installed_vocabulary_warns_with_frequency_and_allowlist(
    tmp_path: Path,
) -> None:
    mod_root = tmp_path / "mod"
    game_root = tmp_path / "game"
    _write_documentation(game_root)
    mod = Mod(mod_root, hoi4_install=game_root)

    findings = mod.validate_effect("add_army_experience = 25")

    issue = next(item for item in findings if item.code == "unknown_effect_token")
    assert "0 installed-game uses" in issue.message
    assert "army_experience" in issue.message
    assert "7 installed-game uses" in issue.message
    vocabulary = mod.game_script_vocabulary()
    assert vocabulary.effects["army_experience"].supported_scopes == ("COUNTRY",)
    zero = mod.validate_effect("zero_effect = yes")
    assert any(item.code == "unseen_effect_token" for item in zero)
    assert not mod.validate_effect(
        "add_army_experience = 25",
        script_token_allowlist=["effect:add_army_experience"],
    )


def test_mod_defined_scripted_effect_is_part_of_vocabulary_extension(
    tmp_path: Path,
) -> None:
    mod_root = tmp_path / "mod"
    game_root = tmp_path / "game"
    _write_documentation(game_root)
    scripted = mod_root / "common" / "scripted_effects" / "custom.txt"
    scripted.parent.mkdir(parents=True)
    scripted.write_text("my_custom_effect = { army_experience = 1 }\n", encoding="utf-8")
    mod = Mod(mod_root, hoi4_install=game_root)

    assert not any(
        item.code == "unknown_effect_token"
        for item in mod.validate_effect("my_custom_effect = yes")
    )


def test_installed_vocabulary_warns_for_explicit_wrong_scopes(
    tmp_path: Path,
) -> None:
    mod_root = tmp_path / "mod"
    game_root = tmp_path / "game"
    _write_documentation(game_root)
    mod = Mod(mod_root, hoi4_install=game_root)

    state_effect = mod.validate_effect(
        "123 = { army_experience = 25 }",
        scope=None,
    )
    effect_issue = next(
        item for item in state_effect if item.code == "unsupported_effect_scope"
    )
    assert "STATE scope" in effect_issue.message
    assert "COUNTRY" in effect_issue.message

    nested_trigger = mod.validate_effect(
        "123 = { if = { limit = { has_country_flag = sample_flag } } }",
        scope=None,
    )
    trigger_issue = next(
        item for item in nested_trigger if item.code == "unsupported_trigger_scope"
    )
    assert "STATE scope" in trigger_issue.message
    assert "COUNTRY" in trigger_issue.message

    assert not any(
        item.code.startswith("unsupported_")
        for item in mod.validate_effect("army_experience = 25")
    )
    assert not any(
        item.code.startswith("unsupported_")
        for item in mod.validate_effect(
            "123 = { add_core_of = ABC }",
            scope=None,
        )
    )


def test_validation_stages_defer_package_checks(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    mod.create_country("ABC", "Example", capital=1)

    build_codes = {item.code for item in mod.validate(stage="build")}
    package_codes = {item.code for item in mod.validate(stage="package")}

    assert "missing_country_flag" not in build_codes
    assert "missing_country_flag" in package_codes
    with pytest.raises(ValueError, match="Unknown validation stage"):
        mod.validate(stage="finished")  # type: ignore[arg-type]


def test_content_liveness_reports_dead_content_and_asymmetric_flags(
    tmp_path: Path,
) -> None:
    mod = Mod(tmp_path)
    mod.create_focus_tree("ABC_tree", "ABC")
    mod.add_focus(
        "ABC_tree",
        Focus(
            id="ABC_a",
            prerequisites=[["ABC_b"]],
            completion_reward="set_country_flag = ABC_written",
        ),
    )
    mod.add_focus(
        "ABC_tree",
        Focus(
            id="ABC_b",
            prerequisites=[["ABC_a"]],
            available="has_country_flag = ABC_read",
        ),
    )
    mod.create_event(
        "abc.1",
        is_triggered_only=True,
        options=[EventOption(name="abc.1.a")],
    )
    mod.create_idea("ABC_unused_idea")
    mod.set_loc("ABC_unused_key", "Unused")

    report = mod.analyze_content_liveness()

    assert report.unreachable_focuses == ("ABC_a", "ABC_b")
    assert report.unfired_events == ("abc.1",)
    assert report.ungranted_ideas == ("ABC_unused_idea",)
    assert report.flags_set_only == ("ABC_written",)
    assert report.flags_read_only == ("ABC_read",)
    assert "ABC_unused_key" in report.unused_localization
    assert {
        "unreachable_focus",
        "unfired_event",
        "ungranted_idea",
        "flag_set_never_read",
        "flag_read_never_set",
        "unused_localization",
    } <= {item.code for item in report.findings}
    release_codes = {item.code for item in mod.validate(stage="release")}
    assert "unfired_event" in release_codes
