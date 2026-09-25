import pytest

from hoi4 import Mod
import hoi4.mod as mod_module


def write_state(directory, filename, state_id, owner="AAA"):
    path = directory / "history/states" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"state = {{ id = {state_id} name = STATE_{state_id} provinces = {{ {state_id} }} history = {{ owner = {owner} }} }}"
    )
    return path


def test_inherited_paths_are_scanned_once_and_resolve_content_not_filename(tmp_path, monkeypatch):
    game, target = tmp_path / "game", tmp_path / "mod"
    one = write_state(game, "2 - StaleFilename.txt", 1)
    two = write_state(game, "1-NoSpace.txt", 2)
    mod = Mod(target, hoi4_install=game)
    actual = mod_module.build_state_index
    calls = []

    def counting_index(directory):
        calls.append(directory)
        return actual(directory)

    monkeypatch.setattr(mod_module, "build_state_index", counting_index)
    assert mod.get_state(2).id == 2
    assert mod.get_state(2).path == two
    assert mod.get_state(1).id == 1
    assert mod.get_state(1).path == one
    for _ in range(3):
        with pytest.raises(KeyError):
            mod.get_state(999)
    assert calls == [game / "history/states"]
    assert not (target / "history/states").exists()


def test_writable_state_wins_and_reload_refreshes_inherited_cache(tmp_path, monkeypatch):
    game, target = tmp_path / "game", tmp_path / "mod"
    write_state(game, "1-Lower.txt", 1, "AAA")
    write_state(game, "2-Lower.txt", 2, "AAA")
    upper = write_state(target, "99 - MisleadingFilename.txt", 1, "BBB")
    mod = Mod(target, hoi4_install=game)
    assert mod.get_state(1).owner == "BBB"
    assert mod.get_state(1).path == upper
    assert mod.get_state(2).owner == "AAA"
    write_state(game, "3-New.txt", 3, "CCC")
    with pytest.raises(KeyError):
        mod.get_state(3)
    mod.reload()
    assert mod.get_state(3).owner == "CCC"
    assert mod.get_state(1).owner == "BBB"
    write_state(game, "2-Lower.txt", 2, "DDD")
    mod.discard()
    assert mod.get_state(2).owner == "DDD"
