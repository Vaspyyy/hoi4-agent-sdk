"""Config discovery resolves paths relative to the discovered file."""

import json

import pytest

from hoi4 import Mod
from hoi4.config import find_config


@pytest.mark.parametrize("explicit_start", [False, True])
def test_relative_paths_use_parent_config_directory(tmp_path, monkeypatch, explicit_start):
    project = tmp_path / "project"
    nested = project / "scripts" / "tasks"
    nested.mkdir(parents=True)
    mod_path = project / "mod"
    mod_path.mkdir()
    game_path = tmp_path / "game"
    game_path.mkdir()
    (project / ".hoi4.json").write_text(
        json.dumps({"mod_path": "mod", "hoi4_install": "../game"}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path if explicit_start else nested)
    start = nested if explicit_start else None

    config = find_config(start)
    assert config is not None
    assert config.mod_path == mod_path
    assert config.hoi4_install == game_path
    mod = Mod.from_config(start)
    assert mod.mod_root == mod_path
    assert mod.hoi4_install == game_path


def test_absolute_paths_remain_absolute_when_config_found_in_parent(tmp_path):
    project = tmp_path / "project"
    nested = project / "scripts"
    nested.mkdir(parents=True)
    mod_path = tmp_path / "external_mod"
    game_path = tmp_path / "external_game"
    (project / ".hoi4.json").write_text(
        json.dumps({"mod_path": str(mod_path), "hoi4_install": str(game_path)}),
        encoding="utf-8",
    )

    config = find_config(nested)
    assert config is not None
    assert config.mod_path == mod_path
    assert config.hoi4_install == game_path
