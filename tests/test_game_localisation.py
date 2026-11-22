"""The public localisation readers see the game's own text (``hoi4_install`` only)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hoi4 import Mod
from hoi4.errors import NotFoundError


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig" if relative.endswith(".yml") else "utf-8")
    return path


@pytest.fixture
def game(tmp_path: Path) -> Path:
    root = tmp_path / "game"
    write(
        root,
        "localisation/english/countries_l_english.yml",
        'l_english:\n GER: "Germany"\n GER_ADJ: "German"\n STATE_1: "Corsica"\n',
    )
    write(
        root,
        "localisation/english/replace/fixes_l_english.yml",
        'l_english:\n GER_ADJ: "Germanic"\n',
    )
    write(root, "localisation/german/countries_l_german.yml", 'l_german:\n GER: "Deutschland"\n')
    return root


@pytest.fixture
def mod_root(tmp_path: Path) -> Path:
    root = tmp_path / "mod"
    write(root, "descriptor.mod", 'name = "test"\n')
    write(
        root,
        "localisation/english/mod_l_english.yml",
        'l_english:\n STATE_1: "Free Corsica"\n MOD_key: "Mine"\n',
    )
    return root


def test_a_key_the_mod_lacks_comes_from_the_game(game: Path, mod_root: Path) -> None:
    mod = Mod(mod_root, hoi4_install=game)
    assert mod.get_loc("GER") == "Germany"
    assert mod.get_loc("GER:0") == "Germany"
    assert mod.get_loc("GER", language="german") == "Deutschland"
    assert mod.get_loc("GER", language="l_german") == "Deutschland"
    assert mod.get_loc("GER_ADJ") == "Germanic"  # a replace folder wins
    assert mod.get_loc("STATE_1") == "Free Corsica"  # the mod's entry wins
    assert mod.get_loc("MOD_key") == "Mine"
    with pytest.raises(NotFoundError):
        mod.get_loc("STATE_1", language="german")
    with pytest.raises(NotFoundError):
        mod.get_loc("NOT_A_KEY")


def test_an_unsaved_edit_wins_over_the_game(game: Path, mod_root: Path) -> None:
    mod = Mod(mod_root, hoi4_install=game)
    mod.set_loc("GER", "Greater Germany")
    assert mod.get_loc("GER") == "Greater Germany"
    mod.delete_loc("GER")
    assert mod.get_loc("GER") == "Germany"


def test_the_game_is_indexed_on_the_first_miss_per_language(game: Path, mod_root: Path) -> None:
    mod = Mod(mod_root, hoi4_install=game)
    assert mod._game_loc_entries == {}
    assert mod.get_loc("MOD_key") == "Mine"
    assert mod._game_loc_entries == {}
    mod.get_loc("GER")
    assert set(mod._game_loc_entries) == {"english"}
    mod.get_loc("GER", language="german")
    assert set(mod._game_loc_entries) == {"english", "german"}


def test_search_and_all_include_the_game_with_the_mod_on_top(game: Path, mod_root: Path) -> None:
    mod = Mod(mod_root, hoi4_install=game)
    everything = mod.all_loc()
    assert everything["GER"] == "Germany"
    assert everything["STATE_1"] == "Free Corsica"
    assert everything["MOD_key"] == "Mine"
    assert mod.search_loc("corsica") == {"STATE_1": "Free Corsica"}
    assert mod.search_loc("germ") == {"GER": "Germany", "GER_ADJ": "Germanic"}
    assert mod.all_loc(language="german") == {"GER": "Deutschland"}


def test_saving_never_writes_the_games_entries(game: Path, mod_root: Path) -> None:
    before = {path: path.read_bytes() for path in game.rglob("*") if path.is_file()}
    mod = Mod(mod_root, hoi4_install=game)
    mod.get_loc("GER")
    mod.get_loc("GER", language="german")
    mod.all_loc()
    mod.search_loc("germ")
    assert "Germany" not in mod.preview()
    mod.set_loc("MOD_other", "Other")
    mod.set_loc("MOD_german", "Deutsch", language="german")
    mod.save()

    written = {
        path.relative_to(mod_root).as_posix(): path.read_text(encoding="utf-8-sig")
        for path in (mod_root / "localisation").rglob("*.yml")
    }
    assert set(written) == {
        "localisation/english/mod_l_english.yml",
        "localisation/german/mod_l_german.yml",
    }
    for text in written.values():
        assert "GER:" not in text
        assert "GER_ADJ" not in text
        assert "Germany" not in text and "Deutschland" not in text
    assert 'MOD_other:0 "Other"' in written["localisation/english/mod_l_english.yml"]
    assert {path: path.read_bytes() for path in game.rglob("*") if path.is_file()} == before


def test_a_hidden_game_folder_is_not_read(game: Path, mod_root: Path) -> None:
    write(mod_root, "descriptor.mod", 'name = "test"\nreplace_path = "localisation/english"\n')
    mod = Mod(mod_root, hoi4_install=game)
    with pytest.raises(NotFoundError):
        mod.get_loc("GER")
    assert mod.get_loc("STATE_1") == "Free Corsica"
    assert mod.get_loc("GER", language="german") == "Deutschland"


def test_without_an_install_only_the_mod_is_read(mod_root: Path) -> None:
    mod = Mod(mod_root)
    with pytest.raises(NotFoundError):
        mod.get_loc("GER")
    assert mod.get_loc("STATE_1") == "Free Corsica"


def test_with_base_mods_the_game_is_read_once_through_the_layers(
    game: Path, mod_root: Path, tmp_path: Path
) -> None:
    base = tmp_path / "base"
    write(base, "descriptor.mod", 'name = "base"\n')
    write(base, "localisation/english/base_l_english.yml", 'l_english:\n BASE_key: "Base"\n')
    mod = Mod(mod_root, hoi4_install=game, base_mod_paths=[base])
    assert mod.get_loc("GER") == "Germany"
    assert mod.get_loc("BASE_key") == "Base"
    assert mod._game_loc_entries == {}


def test_a_mod_file_saved_over_a_game_file_hides_it(game: Path, mod_root: Path) -> None:
    """The game's entries belong to the localisation section and reload with it."""

    mod = Mod(mod_root, hoi4_install=game)
    assert mod.get_loc("GER") == "Germany"
    # A mod file of the same name replaces the game's file as a whole.
    write(mod_root, "localisation/english/countries_l_english.yml", 'l_english:\n ITA: "Italy"\n')
    mod.discard()
    assert "localisation" not in mod._section_loaded
    with pytest.raises(NotFoundError):
        mod.get_loc("GER")
    assert mod.get_loc("ITA") == "Italy"
    assert mod.get_loc("GER", language="german") == "Deutschland"


def test_an_unrelated_save_keeps_the_game_index(game: Path, mod_root: Path) -> None:
    write(mod_root, "events/e.txt", "add_namespace = e\ncountry_event = { id = e.1 }\n")
    mod = Mod(mod_root, hoi4_install=game)
    assert mod.get_loc("GER") == "Germany"
    index = mod._game_loc_entries["english"]
    mod.update_event("e.1", title="e.1.t")
    mod.save()
    assert mod._game_loc_entries["english"] is index
    assert mod.get_loc("GER") == "Germany"
