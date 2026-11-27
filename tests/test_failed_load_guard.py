from __future__ import annotations

from pathlib import Path

import pytest

from hoi4 import Mod
from hoi4.events import serialize_events_file
from hoi4.mod import _persistence as persistence
from hoi4.syntax import ParseError

BROKEN_IDEAS = "ideas = {\n\tcountry = {\n\t\tkept_spirit = { modifier = { stability_factor = 0.1 } }\n"


def test_save_refuses_to_replace_a_mod_file_that_failed_to_load(tmp_path: Path) -> None:
    """A file the SDK could not read was never modeled, so it must not be rewritten."""

    path = tmp_path / "common/ideas/broken.txt"
    path.parent.mkdir(parents=True)
    path.write_text(BROKEN_IDEAS, encoding="utf-8")
    mod = Mod(tmp_path)
    assert [diagnostic.path for diagnostic in mod.load_diagnostics] == [path]
    assert "kept_spirit" not in mod.list_ideas()

    mod.create_idea("new_spirit", modifier={"stability_factor": 0.2}, path="common/ideas/broken.txt")

    with pytest.raises(RuntimeError, match=r"Refusing to rewrite .*failed to load"):
        mod.preview()
    with pytest.raises(RuntimeError, match=r"Refusing to rewrite .*failed to load"):
        mod.save()
    assert path.read_text(encoding="utf-8") == BROKEN_IDEAS


def test_content_in_other_files_still_saves_next_to_a_failed_file(tmp_path: Path) -> None:
    path = tmp_path / "common/ideas/broken.txt"
    path.parent.mkdir(parents=True)
    path.write_text(BROKEN_IDEAS, encoding="utf-8")
    mod = Mod(tmp_path)

    mod.create_idea("new_spirit", modifier={"stability_factor": 0.2}, path="common/ideas/fine.txt")
    mod.save(require_changes=True)

    assert path.read_text(encoding="utf-8") == BROKEN_IDEAS
    assert "new_spirit" in (tmp_path / "common/ideas/fine.txt").read_text(encoding="utf-8")


def test_a_broken_state_file_never_gets_a_second_copy_from_the_game(tmp_path: Path) -> None:
    """The mod's own state file is unreadable, so the game's state must not be written next to it."""

    game = tmp_path / "game"
    vanilla = game / "history/states/7-Holland.txt"
    vanilla.parent.mkdir(parents=True)
    vanilla.write_text(
        "state = {\n\tid = 7\n\tname = STATE_7\n\tprovinces = { 1 2 }\n"
        "\thistory = {\n\t\towner = HOL\n\t}\n}\n",
        encoding="utf-8",
    )
    root = tmp_path / "mod"
    broken = root / "history/states/7-Mine.txt"
    broken.parent.mkdir(parents=True)
    source = "state = {\n\tid = 7\n\tname = STATE_7\n\tprovinces = { 1 2 }\n\thistory = {\n\t\towner = HOL\n}\n"
    broken.write_text(source, encoding="utf-8")

    mod = Mod(root, hoi4_install=game)
    assert [diagnostic.path for diagnostic in mod.load_diagnostics] == [broken]
    mod.set_state_owner(7, "GER")
    with pytest.raises(RuntimeError, match=r"Refusing to create .*7-Holland\.txt.*7-Mine\.txt"):
        mod.save()

    assert sorted(path.name for path in broken.parent.iterdir()) == ["7-Mine.txt"]
    assert broken.read_text(encoding="utf-8") == source


def test_preview_shows_the_repaired_override_that_save_writes(tmp_path: Path) -> None:
    """A game file with a missing brace is copied into the mod well formed, in preview too."""

    game = tmp_path / "game"
    inherited = game / "common/ideas/open.txt"
    inherited.parent.mkdir(parents=True)
    inherited.write_text(
        "ideas = {\n\tcountry = {\n\t\topen_spirit = {\n\t\t\tremoval_cost = 1\n\t\t}\n\t}\n",
        encoding="utf-8",
    )
    root = tmp_path / "mod"
    root.mkdir()
    mod = Mod(root, hoi4_install=game)
    mod.load_inherited_content("common/ideas/open.txt")
    assert mod.load_diagnostics == ()
    mod.update_idea("open_spirit", removal_cost=5)

    preview = mod.preview()
    mod.save(require_changes=True)
    written = (root / "common/ideas/open.txt").read_text(encoding="utf-8")
    assert written == inherited.read_text(encoding="utf-8").replace(
        "removal_cost = 1", "removal_cost = 5"
    ) + "}\n"
    added = [line[1:] for line in preview.splitlines() if line.startswith("+") and not line.startswith("+++")]
    assert added == written.splitlines()


def test_malformed_script_is_refused_and_nothing_is_written(tmp_path: Path) -> None:
    """Malformed script handed to the SDK is refused; the file on disk stays as it was."""

    path = tmp_path / "events/t.txt"
    path.parent.mkdir(parents=True)
    source = "add_namespace = t\ncountry_event = {\n\tid = t.1\n\toption = { name = t.1.a }\n}\n"
    path.write_text(source, encoding="utf-8")
    mod = Mod(tmp_path)
    mod.update_event("t.1", immediate="add_stability = {")

    with pytest.raises(ParseError, match=r"Unclosed block .*'immediate'"):
        mod.preview()
    with pytest.raises(ParseError, match=r"Unclosed block .*'immediate'"):
        mod.save()
    assert path.read_text(encoding="utf-8") == source


def test_save_never_writes_a_file_with_a_new_syntax_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The last check before the disk: a patcher bug cannot leave a file more broken."""

    path = tmp_path / "events/t.txt"
    path.parent.mkdir(parents=True)
    source = "add_namespace = t\ncountry_event = {\n\tid = t.1\n\toption = { name = t.1.a }\n}\n"
    path.write_text(source, encoding="utf-8")
    mod = Mod(tmp_path)
    mod.update_event("t.1", picture="GFX_report_event_generic")

    def broken_patcher(*args: object, **kwargs: object) -> str:
        return serialize_events_file(*args, **kwargs) + "stray = {\n"  # type: ignore[arg-type]

    monkeypatch.setattr(persistence, "serialize_events_file", broken_patcher)

    with pytest.raises(RuntimeError, match=r"Refusing to write .*syntax problem"):
        mod.preview()
    with pytest.raises(RuntimeError, match=r"Refusing to write .*syntax problem"):
        mod.save()
    assert path.read_text(encoding="utf-8") == source
