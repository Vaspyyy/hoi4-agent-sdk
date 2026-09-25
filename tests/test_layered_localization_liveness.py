"""Liveness audits authored localization, while fallback remains resolvable."""

from pathlib import Path

from hoi4 import EventOption, Mod


def write_loc(root: Path, name: str, **entries: str) -> None:
    path = root / "localisation" / "english" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "l_english:\n" + "".join(f' {key}:0 "{value}"\n' for key, value in entries.items())
    )


def test_layered_liveness_distinguishes_authorship_and_fallback(tmp_path: Path) -> None:
    game, base, top = [tmp_path / name for name in ("game", "base", "top")]
    write_loc(game, "game.yml", game_unused="Game only", inherited_title="Title")
    write_loc(
        base,
        "base.yml",
        base_unused="Base only",
        inherited_desc="Description",
        inherited_option="Continue",
        overridden="Base",
    )
    write_loc(base, "shared.yml", shadowed_unused="Replaced file")
    # An actual top-mod file shadows this inherited file.
    write_loc(top, "shared.yml", top_unused="Top only")
    mod = Mod(top, hoi4_install=game, base_mod_paths=[base])
    mod.set_loc("pending_unused", "New pending entry")
    mod.set_loc("overridden", "Pending override")
    mod.create_event(
        "test.1",
        title="inherited_title",
        description="inherited_desc",
        is_triggered_only=True,
        options=[EventOption(name="inherited_option", effect="add_political_power = 1")],
    )
    report = mod.analyze_content_liveness()
    assert set(report.unused_localization) == {"top_unused", "pending_unused", "overridden"}
    assert mod.get_loc("game_unused") == "Game only"
    assert mod.get_loc("base_unused") == "Base only"
    assert {"inherited_title", "inherited_desc", "inherited_option"} <= set(
        report.used_localization
    )
    # Strict lookup still resolves inherited text; no localization dictionary pruning.
    assert mod._has_loc("inherited_title")
    assert mod._has_loc("inherited_desc")
    assert mod._has_loc("inherited_option")
    assert not [
        issue
        for issue in mod.validate(stage="build", strict_localization=True)
        if issue.code == "missing_localization"
    ]
    mod.delete_loc("top_unused")
    assert "top_unused" not in mod.analyze_content_liveness().unused_localization


def test_single_mod_unused_localization_remains_reported(tmp_path: Path) -> None:
    write_loc(tmp_path, "own.yml", unused_disk="Unused")
    mod = Mod(tmp_path)
    mod.set_loc("unused_pending", "Unused pending")
    assert set(mod.analyze_content_liveness().unused_localization) == {
        "unused_disk",
        "unused_pending",
    }
    mod.delete_loc("unused_disk")
    assert mod.analyze_content_liveness().unused_localization == ("unused_pending",)
