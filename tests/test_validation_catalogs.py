"""validate() sees what the game inherits and what the API changed before save().

Each test is a false alarm or a missed finding a reviewer reproduced: a
catalog that ignored advisor idea tokens, focus ids or country files of the
game, or a cached scan of the mod's files that went stale after an unsaved
edit.
"""

from __future__ import annotations

from pathlib import Path

from hoi4 import Mod
from hoi4.types import ValidationError


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def codes(findings: list[ValidationError], code: str) -> list[ValidationError]:
    return [finding for finding in findings if finding.code == code]


def mod_root(tmp_path: Path) -> Path:
    root = tmp_path / "mod"
    write(root, "descriptor.mod", 'name = "test"\n')
    return root


# ── Advisor idea tokens ─────────────────────────────────────────────


def test_country_history_may_assign_an_advisor_idea_token_of_the_game(tmp_path):
    game = tmp_path / "game"
    write(
        game,
        "common/characters/TST.txt",
        "characters = {\n"
        " TST_chief = {\n"
        "  name = TST_chief\n"
        "  advisor = { slot = political_advisor idea_token = TST_chief_tok }\n"
        " }\n"
        "}\n",
    )
    root = mod_root(tmp_path)
    write(root, "common/country_tags/00_countries.txt", 'TST = "countries/Test.txt"\n')
    write(root, "common/countries/Test.txt", "color = { 1 2 3 }\n")
    history = write(
        root,
        "history/countries/TST - Test.txt",
        "capital = 1\nadd_ideas = {\n TST_chief_tok\n TST_typo_idea\n}\n",
    )
    findings = codes(Mod(root, hoi4_install=game).validate(stage="build"), "unknown_assigned_idea")
    assert [(f.idea_id, f.file_path, f.line) for f in findings] == [
        ("TST_typo_idea", str(history.resolve()), 4)
    ]


# ── Country tag files ───────────────────────────────────────────────


def test_a_tag_may_point_to_a_country_file_the_game_inherits(tmp_path):
    game = tmp_path / "game"
    write(game, "common/countries/Germany.txt", "color = { 1 2 3 }\n")
    root = mod_root(tmp_path)
    write(
        root,
        "common/country_tags/00_countries.txt",
        'GER = "countries/Germany.txt"\nXXA = "countries/Nowhere.txt"\n',
    )
    findings = codes(Mod(root, hoi4_install=game).validate(stage="build"), "tag_definition")
    assert [(f.country_tag, f.line) for f in findings] == [("XXA", 2)]


def test_replace_path_hides_an_inherited_country_file(tmp_path):
    game = tmp_path / "game"
    write(game, "common/countries/Germany.txt", "color = { 1 2 3 }\n")
    root = mod_root(tmp_path)
    write(root, "descriptor.mod", 'name = "test"\nreplace_path = "common/countries"\n')
    write(root, "common/country_tags/00_countries.txt", 'GER = "countries/Germany.txt"\n')
    findings = codes(Mod(root, hoi4_install=game).validate(stage="build"), "tag_definition")
    assert [f.country_tag for f in findings] == ["GER"]
