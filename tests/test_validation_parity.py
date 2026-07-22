from __future__ import annotations

from pathlib import Path

from hoi4 import Mod, VALIDATION_CODES, VALIDATION_WARNING_CODES


def test_validate_reports_missing_country_tag_definition_target(tmp_path: Path) -> None:
    tags = tmp_path / "common" / "country_tags" / "tags.txt"
    countries = tmp_path / "common" / "countries"
    tags.parent.mkdir(parents=True)
    countries.mkdir(parents=True)
    tags.write_text(
        '# tag definitions\nABC = "countries/missing.txt"\nDEF = "countries/DEF.txt" # valid\n',
        encoding="utf-8",
    )
    (countries / "DEF.txt").write_text(
        "graphical_culture = western_european_gfx\n", encoding="utf-8"
    )

    issues = [issue for issue in Mod(tmp_path).validate() if issue.code == "tag_definition"]

    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert issues[0].country_tag == "ABC"
    assert issues[0].file_path == str(tags)
    assert issues[0].line == 2
    assert issues[0].message == ("Tag ABC references countries/missing.txt but file does not exist")


def test_validate_country_history_refs_use_mod_and_vanilla_catalogs(
    tmp_path: Path,
) -> None:
    mod_root = tmp_path / "mod"
    game_root = tmp_path / "game"
    history = mod_root / "history" / "countries" / "ABC - Test.txt"
    mod_state = mod_root / "history" / "states" / "1-Mod.txt"
    vanilla_state = game_root / "history" / "states" / "2-Vanilla.txt"
    mod_characters = mod_root / "common" / "characters" / "mod.txt"
    vanilla_characters = game_root / "common" / "characters" / "vanilla.txt"
    for path in (history, mod_state, vanilla_state, mod_characters, vanilla_characters):
        path.parent.mkdir(parents=True, exist_ok=True)

    history.write_text(
        """# capital = 999 and recruit_character = commented_out
capital = 1
capital = 2
capital = 999
recruit_character = mod_character
recruit_character = vanilla_character
recruit_character = missing_character
""",
        encoding="utf-8",
    )
    mod_state.write_text("state = { id = 1 history = { } }\n", encoding="utf-8")
    vanilla_state.write_text("state = { id = 2 history = { } }\n", encoding="utf-8")
    mod_characters.write_text(
        "characters = { mod_character = { name = MOD_CHARACTER_NAME } }\n",
        encoding="utf-8",
    )
    vanilla_characters.write_text(
        "characters = { vanilla_character = { name = VANILLA_CHARACTER_NAME } }\n",
        encoding="utf-8",
    )

    issues = Mod(mod_root, hoi4_install=game_root).validate()
    capitals = [issue for issue in issues if issue.code == "capital_ref"]
    characters = [issue for issue in issues if issue.code == "character_ref"]

    assert [(issue.state_id, issue.line, issue.severity) for issue in capitals] == [
        (999, 4, "error")
    ]
    assert [(issue.message, issue.line, issue.severity) for issue in characters] == [
        ("Character missing_character is not defined", 7, "warning")
    ]


def test_validate_reports_one_focus_prerequisite_cycle_per_tree(tmp_path: Path) -> None:
    path = tmp_path / "common" / "national_focus" / "cycle.txt"
    path.parent.mkdir(parents=True)
    path.write_text(
        """focus_tree = {
    id = cycle_tree
    focus = { id = A x = 0 y = 0 prerequisite = { focus = B } }
    focus = { id = B x = 1 y = 0 prerequisite = { focus = C } }
    focus = { id = C x = 2 y = 0 prerequisite = { focus = A } }
    focus = { id = D x = 3 y = 0 prerequisite = { focus = D } }
}
""",
        encoding="utf-8",
    )

    cycles = [issue for issue in Mod(tmp_path).validate() if issue.code == "focus_cycle"]

    assert len(cycles) == 1
    assert cycles[0].severity == "error"
    assert cycles[0].focus_id == "A"
    assert cycles[0].file_path == str(path)
    assert cycles[0].message == "Focus prerequisite cycle detected involving A"


def test_parity_validation_codes_have_stable_severities() -> None:
    assert {"tag_definition", "capital_ref", "character_ref", "focus_cycle"} <= set(
        VALIDATION_CODES
    )
    assert "character_ref" in VALIDATION_WARNING_CODES
    assert {"tag_definition", "capital_ref", "focus_cycle"}.isdisjoint(VALIDATION_WARNING_CODES)
