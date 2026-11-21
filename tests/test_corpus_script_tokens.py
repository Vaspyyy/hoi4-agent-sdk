"""Opt-in check of the script vocabulary and scope walker against a real game install.

Skipped unless ``HOI4_INSTALL_DIR`` points at a Hearts of Iron IV install. The
install is only read, never written. Run it with::

    HOI4_INSTALL_DIR="$HOME/.local/share/Steam/steamapps/common/Hearts of Iron IV" \\
        python -m pytest -m corpus tests/test_corpus_script_tokens.py -p no:cacheprovider -q

The game's own script is valid by definition, so every effect, trigger,
modifier and weight block of its events, national focuses, decisions, ideas,
on-actions, scripted effects and triggers, characters, dynamic modifiers and
bookmarks (about 118,000 blocks) must walk without an unknown name and
without a scope finding. The only exceptions are the real mistakes in the
game's files listed in ``KNOWN_GAME_MISTAKES``; a game update that fixes one
simply stops reporting it.

The first run reads the whole install to build the vocabulary cache (about
30 seconds on a busy machine, measured 2026-11); the walk itself takes about
20 seconds.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from hoi4._script_corpus import iter_script_blocks
from hoi4.script_scopes import walk_script
from hoi4.script_vocabulary import (
    GameScriptVocabulary,
    ScriptSource,
    check_statement,
    load_game_script_vocabulary,
    validate_script_sources,
)

pytestmark = pytest.mark.corpus

INSTALL = os.environ.get("HOI4_INSTALL_DIR")

#: Real mistakes in the game's files (game version 1.20.0, checked 2026-11-21):
#: (file, line, code, name).
KNOWN_GAME_MISTAKES = {
    # ``random_core_state = { limit = { ... } }`` closes before the effect, so
    # the state effect runs in the country scope of ROOT.
    ("events/SEA_Japan.txt", 6734, "unsupported_effect_scope", "add_building_construction"),
    ("events/SEA_Japan.txt", 6826, "unsupported_effect_scope", "add_building_construction"),
    ("events/SEA_Japan.txt", 6917, "unsupported_effect_scope", "add_building_construction"),
    ("events/SEA_Japan.txt", 7005, "unsupported_effect_scope", "add_building_construction"),
    ("events/WUW_Germany.txt", 5631, "unsupported_effect_scope", "add_building_construction"),
    ("events/WUW_Germany.txt", 5723, "unsupported_effect_scope", "add_building_construction"),
    ("events/WUW_Germany.txt", 5814, "unsupported_effect_scope", "add_building_construction"),
    # ``ITA = { every_core_state = { PREV = { add_claim_by = ROOT } } }``:
    # PREV is ITA, a country; a claim is added to a state.
    ("common/national_focus/switzerland.txt", 5428, "unsupported_effect_scope", "add_claim_by"),
    ("common/national_focus/switzerland.txt", 5450, "unsupported_effect_scope", "add_claim_by"),
    ("common/national_focus/switzerland.txt", 5526, "unsupported_effect_scope", "add_claim_by"),
    # State-targeted decisions: FROM is the target state, which has no
    # country flags (the owner is one line further down).
    ("common/decisions/AUS.txt", 1028, "unsupported_effect_scope", "set_country_flag"),
    ("common/decisions/AUS.txt", 1381, "unsupported_effect_scope", "clr_country_flag"),
    ("common/decisions/AUS.txt", 1391, "unsupported_effect_scope", "set_country_flag"),
    ("common/decisions/AUS.txt", 1403, "unsupported_effect_scope", "clr_country_flag"),
    # Inside the target state of a state-targeted decision: a focus is the
    # country's, so this should be ROOT = { has_completed_focus = ... }.
    ("common/decisions/CHI_decisions.txt", 12191, "unsupported_trigger_scope", "has_completed_focus"),
    # THIS is a state in an any_state limit; states have no civilian factory count.
    ("events/BFTB_Turkey.txt", 4880, "unsupported_trigger_scope", "num_of_civilian_factories"),
    # An option's ``name = ...`` pasted into an ``immediate`` block.
    ("events/TTBE_Romania.txt", 3311, "unknown_effect_token", "name"),
}


@pytest.fixture(scope="module")
def install() -> Path:
    if not INSTALL:
        pytest.skip("set HOI4_INSTALL_DIR to a Hearts of Iron IV install")
    root = Path(INSTALL)
    if not (root / "documentation" / "effects_documentation.md").is_file():
        pytest.skip(f"{root} is not a Hearts of Iron IV install with documentation")
    return root


@pytest.fixture(scope="module")
def vocabulary(install: Path) -> GameScriptVocabulary:
    return load_game_script_vocabulary(install)


def test_every_block_of_the_game_walks_cleanly(install: Path, vocabulary: GameScriptVocabulary) -> None:
    findings = set()
    blocks = 0
    for path, block in iter_script_blocks(install):
        blocks += 1
        source = ScriptSource(
            "",
            block.kind,
            block.owner_kind,
            block.owner_id,
            path.relative_to(install).as_posix(),
            block.scope,
            block.from_scope,
        )
        statements = walk_script(
            block.block,
            block.kind,
            scope=block.scope,
            from_scope=block.from_scope,
            is_command=lambda kind, name: name in vocabulary.folded_names(kind),
            characters=vocabulary.characters,
            arguments=block.arguments,
        )
        for statement in statements:
            finding = check_statement(statement, source, statement.line, vocabulary, {}, ())
            if finding is not None and finding.severity != "info":
                findings.add((source.file_path, statement.line, finding.code, statement.name))
    assert blocks > 50_000
    unexpected = sorted(findings - KNOWN_GAME_MISTAKES)
    assert unexpected == [], "\n".join(map(str, unexpected[:50]))


def test_true_alarms_survive_against_the_real_vocabulary(vocabulary: GameScriptVocabulary) -> None:
    def codes(text: str, kind: str, scope: str | None = "COUNTRY") -> list[str]:
        source = ScriptSource(text, kind, "focus", "probe", scope=scope)  # type: ignore[arg-type]
        return [
            item.code
            for item in validate_script_sources([source], vocabulary, custom_tokens={})
            if item.severity != "info"
        ]

    assert codes("add_politcal_power = 50", "effect") == ["unknown_effect_token"]
    assert codes("if = { limit = { has_contry_flag = x } add_stability = 0.1 }", "effect") == [
        "unknown_trigger_token"
    ]
    assert codes("stabilty_factor = 0.1", "modifier") == ["unknown_modifier_token"]
    assert codes("add_building_construction = { type = arms_factory level = 1 }", "effect") == [
        "unsupported_effect_scope"
    ]
    # And what the old checker got wrong stays quiet.
    assert codes("production_speed_rail_way_factor = 0.1\neconomy_cost_factor = -0.1", "modifier") == []
    assert codes(
        "IF = { limit = { original_TAG = GER } ELSE_IF = { } } random_list = { 12.5 = { } }",
        "effect",
    ) == []


def test_documented_modifier_families_are_expanded(vocabulary: GameScriptVocabulary) -> None:
    patterns = {template.pattern for template in vocabulary.modifier_templates}
    assert "production_speed_<Building>_factor" in patterns
    assert "<IdeaGroup>_cost_factor" in patterns
    assert vocabulary.lookup("modifier", "production_speed_industrial_complex_factor") is not None
    assert vocabulary.lookup("modifier", "fascism_acceptance") is not None
    assert vocabulary.lookup("modifier", "country_resource_oil") is not None
    assert vocabulary.family_members["Building"]["rail_way"] == "common/buildings"
