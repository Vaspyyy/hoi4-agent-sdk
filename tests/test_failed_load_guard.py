from __future__ import annotations

from pathlib import Path

import pytest

from hoi4 import Mod

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

    with pytest.raises(RuntimeError, match="Refusing to rewrite .*failed to load"):
        mod.preview()
    with pytest.raises(RuntimeError, match="Refusing to rewrite .*failed to load"):
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
