"""The layered content view: game, then base mods, then the mod, read in place."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from hoi4 import Mod
from hoi4.layers import ContentLayers, FolderView


def put(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def state(ident: int, owner: str) -> str:
    return (
        f'state = {{ id = {ident} name = "STATE_{ident}" manpower = 100 state_category = rural '
        f"provinces = {{ {ident} }} history = {{ owner = {owner} add_core_of = {owner} }} }}\n"
    )


def build(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A game, one base mod and a top mod.

    The base overrides a game state, the top overrides a base state, the base
    replaces the game's ``common/ideas``, and each layer has files of its own,
    in both localisation spellings.
    """

    game, base, top = tmp_path / "game", tmp_path / "base", tmp_path / "top"
    put(game, "common/country_tags/00_countries.txt",
        'GER = "countries/Germany.txt"\nITA = "countries/Italy.txt"\n')
    put(game, "common/countries/Germany.txt", "color = { 10 20 30 }\n")
    put(game, "common/countries/Italy.txt", "color = { 40 50 60 }\n")
    put(game, "history/states/1-Berlin.txt", state(1, "GER"))
    put(game, "history/states/2-Rome.txt", state(2, "ITA"))
    put(game, "common/ideas/game_ideas.txt",
        "ideas = { country = { game_idea = { modifier = { stability_factor = 0.1 } } } }\n")
    put(game, "events/game_only.txt", "add_namespace = game\n")
    put(game, "localisation/english/game_l_english.yml",
        'l_english:\n STATE_1:0 "Berlin"\n STATE_2:0 "Rome"\n shared:0 "game"\n')
    put(game, "localization/english/spelled_l_english.yml", 'l_english:\n spelled_game:0 "z"\n')
    put(base, "descriptor.mod", 'name="base"\nreplace_path="common/ideas"\n')
    put(base, "history/states/2-Rome.txt", state(2, "GER"))
    put(base, "history/states/3-Base.txt", state(3, "ITA"))
    put(base, "common/ideas/base_ideas.txt",
        "ideas = { country = { base_idea = { modifier = { stability_factor = 0.1 } } } }\n")
    put(base, "events/base_only.txt", "add_namespace = base\n")
    put(base, "localisation/english/base_l_english.yml", 'l_english:\n shared:0 "base"\n')
    put(base, "localization/english/base_spelled_l_english.yml", 'l_english:\n spelled_base:0 "y"\n')
    put(top, "descriptor.mod", 'name="top"\n')
    put(top, "history/states/3-Base.txt", state(3, "GER"))
    put(top, "history/states/4-Top.txt", state(4, "ITA"))
    put(top, "events/top_only.txt", "add_namespace = top\n")
    return game, base, top


def test_view_precedence_replace_path_and_spellings(tmp_path):
    game, base, top = build(tmp_path)
    layers = ContentLayers(game, [base], top)

    # Only in the game, overridden by the base, overridden by the top, only in the mod.
    assert layers.source("history/states/1-Berlin.txt") == game / "history/states/1-Berlin.txt"
    assert layers.source("history/states/2-Rome.txt") == base / "history/states/2-Rome.txt"
    assert layers.source("history/states/3-Base.txt") == top / "history/states/3-Base.txt"
    assert layers.source("history/states/4-Top.txt") == top / "history/states/4-Top.txt"
    # What the mod inherits leaves out its own files; ``inherited`` keeps them.
    assert layers.file("history/states/3-Base.txt") is None
    assert layers.inherited("history/states/3-Base.txt") == base / "history/states/3-Base.txt"
    assert layers.file("history/states/4-Top.txt") is None
    assert layers.inherited("history/states/4-Top.txt") is None
    # The base replaces the game's ideas folder.
    assert layers.file("common/ideas/game_ideas.txt") is None
    assert layers.glob("common/ideas", "*.txt") == {
        Path("common/ideas/base_ideas.txt"): base / "common/ideas/base_ideas.txt"
    }
    # Both spellings are separate content folders.
    assert layers.rglob("localization") == {
        Path("localization/english/spelled_l_english.yml"):
            game / "localization/english/spelled_l_english.yml",
        Path("localization/english/base_spelled_l_english.yml"):
            base / "localization/english/base_spelled_l_english.yml",
    }
    # Game files keep their place; files new in the base come after them.
    assert list(layers.rglob("localisation", "*.yml")) == [
        Path("localisation/english/game_l_english.yml"),
        Path("localisation/english/base_l_english.yml"),
    ]
    assert list(layers.glob("history/states", "*.txt")) == [
        Path("history/states/1-Berlin.txt"),
        Path("history/states/2-Rome.txt"),
    ]
    # Only content folders are layered, and the root is never a file.
    assert layers.file("descriptor.mod") is None
    assert layers.rglob("music") == {}
    assert layers.relative(base / "events/base_only.txt") == Path("events/base_only.txt")
    assert layers.relative(tmp_path / "elsewhere.txt") is None


def test_mod_reads_the_effective_content(tmp_path):
    game, base, top = build(tmp_path)
    mod = Mod(top, hoi4_install=game, base_mod_paths=[base])

    assert mod.get_state(1).owner == "GER"
    assert mod.get_state(2).owner == "GER"
    assert mod.get_state(2).path == base / "history/states/2-Rome.txt"
    assert mod.get_state(3).path == top / "history/states/3-Base.txt"
    assert mod.get_loc("shared") == "base"
    assert mod.content_files("history/states") == {
        "history/states/1-Berlin.txt": game / "history/states/1-Berlin.txt",
        "history/states/2-Rome.txt": base / "history/states/2-Rome.txt",
        "history/states/3-Base.txt": top / "history/states/3-Base.txt",
        "history/states/4-Top.txt": top / "history/states/4-Top.txt",
    }
    assert mod.content_files("events") == {
        "events/game_only.txt": game / "events/game_only.txt",
        "events/base_only.txt": base / "events/base_only.txt",
        "events/top_only.txt": top / "events/top_only.txt",
    }
    assert "base_idea" in mod._known_idea_ids()
    assert "game_idea" not in mod._known_idea_ids()
    sources = {entry["id"]: entry["source_layer"] for entry in mod.state_index()}
    assert sources == {1: "vanilla", 2: "base_mod", 3: "mod", 4: "mod"}


def test_view_follows_the_mod_folder_live(tmp_path):
    game, base, top = build(tmp_path)
    layers = ContentLayers(game, [base], top)
    assert layers.file("history/states/1-Berlin.txt") == game / "history/states/1-Berlin.txt"
    put(top, "history/states/1-Berlin.txt", state(1, "ITA"))
    assert layers.file("history/states/1-Berlin.txt") is None
    assert "history/states/1-Berlin.txt" not in {
        str(path) for path in layers.glob("history/states")
    }
    (top / "history/states/1-Berlin.txt").unlink()
    assert layers.file("history/states/1-Berlin.txt") == game / "history/states/1-Berlin.txt"


def test_symlink_escaping_a_layer_is_refused(tmp_path):
    game, base, top = build(tmp_path)
    outside = put(tmp_path, "outside.txt", "add_namespace = outside\n")
    (base / "events/escape.txt").symlink_to(outside)
    layers = ContentLayers(game, [base], top)
    with pytest.raises(ValueError, match="escapes"):
        layers.file("events/escape.txt")
    with pytest.raises(ValueError, match="escapes"):
        layers.rglob("events")


@pytest.mark.skipif(sys.version_info < (3, 13), reason="rglob order of Python 3.13")
def test_folder_view_lists_like_rglob(tmp_path):
    for relative in ("b/x.yml", "a/y.yml", "a/c/z.yml", "top.yml", "a/c/d/w.yml", "e/v.txt"):
        put(tmp_path, f"localisation/{relative}", "l_english:\n")
    view = FolderView(tmp_path)
    expected = [
        path.relative_to(tmp_path)
        for path in (tmp_path / "localisation").rglob("*.yml")
    ]
    assert list(view.rglob("localisation", "*.yml")) == expected
    assert list(view.glob("localisation/a")) == [Path("localisation/a/y.yml")]


def _tree_state(root: Path) -> dict[str, tuple[int, bytes]]:
    return {
        str(path.relative_to(root)): (path.stat().st_mtime_ns, path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _set_writable(root: Path, writable: bool) -> None:
    for path in [root, *root.rglob("*")]:
        mode = path.stat().st_mode
        if writable:
            path.chmod(mode | stat.S_IWUSR)
        else:
            path.chmod(mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="needs file permissions")
def test_game_and_base_mods_are_never_written(tmp_path):
    game, base, top = build(tmp_path)
    before = {root: _tree_state(root) for root in (game, base)}
    for root in (game, base):
        _set_writable(root, False)
    try:
        mod = Mod(top, hoi4_install=game, base_mod_paths=[base])
        mod.set_state_owner(2, "ITA")
        mod.set_loc("shared", "top")
        mod.validate()
        result = mod.save(require_changes=True)
        assert result.written_files
        assert all(Path(path).is_relative_to(top) for path in result.written_files)
        mod.set_state_owner(1, "ITA")
        mod.discard()
        mod.reload()
        assert mod.get_state(2).owner == "ITA"
        assert mod.get_state(1).owner == "GER"
        assert mod.get_loc("shared") == "top"
    finally:
        for root in (game, base):
            _set_writable(root, True)
    assert {root: _tree_state(root) for root in (game, base)} == before


def test_discard_with_base_mods_keeps_unchanged_sections(tmp_path):
    game, base, top = build(tmp_path)
    mod = Mod(top, hoi4_install=game, base_mod_paths=[base])
    assert mod.get_loc("shared") == "base"
    mod.list_events()
    mod.set_loc("shared", "edited")
    mod.discard()
    # Only the edited section reloads: the base mod did not change.
    assert "events" in mod._section_loaded
    assert "localisation" not in mod._section_loaded
    assert mod.get_loc("shared") == "base"

    put(base, "localisation/english/base_l_english.yml", 'l_english:\n shared:0 "changed"\n')
    mod.discard()
    # A changed base mod reloads everything.
    assert mod._section_loaded == set()
    assert mod.get_loc("shared") == "changed"

    mod.list_events()
    put(base, "events/new.txt", "add_namespace = new\n")
    mod.discard()
    assert mod._section_loaded == set()
