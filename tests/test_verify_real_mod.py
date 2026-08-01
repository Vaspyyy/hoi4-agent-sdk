from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from hoi4.config import Config


def _load_verifier() -> ModuleType:
    script_path = Path(__file__).parents[1] / "scripts" / "verify_real_mod.py"
    spec = importlib.util.spec_from_file_location("verify_real_mod", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_resolve_paths = _load_verifier()._resolve_paths


def test_explicit_mod_root_uses_colocated_config_install(
    tmp_path: Path,
) -> None:
    mod_root = tmp_path / "mod"
    game_root = tmp_path / "game"
    mod_root.mkdir()
    game_root.mkdir()
    Config(mod_path=mod_root, hoi4_install=game_root).save(
        mod_root / ".hoi4.json"
    )

    resolved_mod, resolved_game = _resolve_paths(
        mod_root,
        None,
        tmp_path / "unrelated",
    )

    assert resolved_mod == mod_root
    assert resolved_game == game_root


def test_explicit_install_overrides_colocated_config(tmp_path: Path) -> None:
    mod_root = tmp_path / "mod"
    configured_game = tmp_path / "configured-game"
    explicit_game = tmp_path / "explicit-game"
    mod_root.mkdir()
    Config(mod_path=mod_root, hoi4_install=configured_game).save(
        mod_root / ".hoi4.json"
    )

    _, resolved_game = _resolve_paths(mod_root, explicit_game, tmp_path)

    assert resolved_game == explicit_game
