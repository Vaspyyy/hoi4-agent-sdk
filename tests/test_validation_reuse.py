"""validate() reads the game once and shares what its checks build.

These tests count work instead of timing it, so a busy machine cannot make
them fail: a second validation must not parse an unchanged game file again,
and the data several checks need is built once per run.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

import hoi4._file_cache as file_cache
import hoi4.localisation as localisation
import hoi4.syntax as syntax
from hoi4 import Mod
from hoi4.mod._catalogs import CatalogsLayer
from hoi4.mod._oob_validation import OobValidationLayer
from hoi4.mod._script_validation import ScriptValidationLayer


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig" if relative.endswith(".yml") else "utf-8")
    return path


GAME_FILES = {
    "events/game_events.txt": (
        "add_namespace = game_ns\n"
        "country_event = { id = game_ns.1 is_triggered_only = yes\n"
        " option = { name = game_ns.1.a set_country_flag = game_flag } }\n"
    ),
    "common/scripted_effects/game_effects.txt": (
        "game_make_template = {\n"
        " division_template = { name = \"Game Template\" regiments = { infantry = { x = 0 y = 0 } } }\n"
        "}\n"
    ),
    "common/scripted_triggers/game_triggers.txt": "game_trigger = { always = yes }\n",
    "common/decisions/game_decisions.txt": (
        "game_category = { game_decision = { complete_effect = { game_make_template = yes } } }\n"
    ),
    "common/on_actions/game_on_actions.txt": (
        "on_actions = { on_startup = { effect = { country_event = game_ns.1 } } }\n"
    ),
    "history/countries/GAM - Game.txt": "capital = 1\n",
}


@pytest.fixture
def game(tmp_path: Path) -> Path:
    root = tmp_path / "game"
    for relative, text in GAME_FILES.items():
        write(root, relative, text)
    write(
        root,
        "localisation/english/game_l_english.yml",
        'l_english:\n game_ns.1.a: "Fine"\n',
    )
    return root


@pytest.fixture
def mod(tmp_path: Path, game: Path, monkeypatch: pytest.MonkeyPatch) -> Mod:
    # Files written by the test are seconds old; let the cache keep them.
    monkeypatch.setattr(file_cache, "_SETTLED_NS", 0)
    file_cache.clear_file_cache()
    root = tmp_path / "mod"
    write(root, "descriptor.mod", 'name = "test"\n')
    write(
        root,
        "events/mod_events.txt",
        "add_namespace = mod_ns\n"
        "country_event = { id = mod_ns.1 is_triggered_only = yes\n"
        " option = { name = mod_ns.1.a game_make_template = yes } }\n",
    )
    return Mod(root, hoi4_install=game)


def spy_parses(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    parsed: list[str] = []
    original = syntax.Document.__init__

    def init(self: syntax.Document, text: str) -> None:
        parsed.append(text)
        original(self, text)

    monkeypatch.setattr(syntax.Document, "__init__", init)
    return parsed


def spy_calls(monkeypatch: pytest.MonkeyPatch, owner: type, name: str, counts: Counter) -> None:
    original = getattr(owner, name)

    def counted(self, *args, **kwargs):
        counts[name] += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(owner, name, counted)


def test_a_second_validation_parses_no_unchanged_game_file(
    mod: Mod, game: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = mod.validate(stage="release", advice=True)
    parsed = spy_parses(monkeypatch)
    read_localisation: list[str] = []
    original_entries = localisation._localization_entries

    def entries(text: str) -> dict[str, str]:
        read_localisation.append(text)
        return original_entries(text)

    monkeypatch.setattr(localisation, "_localization_entries", entries)

    second = mod.validate(stage="release", advice=True)

    assert [vars(f) for f in second] == [vars(f) for f in first]
    game_texts = set(GAME_FILES.values())
    assert not [text for text in parsed if text in game_texts]
    assert not [text for text in read_localisation if "game_ns.1.a" in text]


def test_a_changed_game_file_is_read_again(
    mod: Mod, game: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod.validate(stage="release")
    changed = GAME_FILES["common/scripted_effects/game_effects.txt"].replace(
        "Game Template", "Other Template"
    )
    write(game, "common/scripted_effects/game_effects.txt", changed)
    parsed = spy_parses(monkeypatch)

    mod.validate(stage="release")

    assert changed in parsed


def test_shared_data_is_built_once_per_run(mod: Mod, monkeypatch: pytest.MonkeyPatch) -> None:
    counts: Counter = Counter()
    spy_calls(monkeypatch, OobValidationLayer, "_build_oob_check_data", counts)
    spy_calls(monkeypatch, ScriptValidationLayer, "_build_semantic_script_sources", counts)
    spy_calls(monkeypatch, CatalogsLayer, "_build_reference_corpus", counts)

    mod.validate(stage="release")
    # The token and liveness checks both read the script sources; the
    # reference, tooltip and idea checks all read the mod's references.
    assert counts == {
        "_build_oob_check_data": 1,
        "_build_semantic_script_sources": 1,
        "_build_reference_corpus": 2,  # the mod's files, and with the game's
    }
    assert mod._validation_run is None

    mod.validate(stage="release")
    assert counts["_build_semantic_script_sources"] == 2
