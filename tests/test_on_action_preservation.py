from pathlib import Path

import pytest

from hoi4 import Mod
from hoi4.on_actions import load_on_actions_file, serialize_on_actions_file
from hoi4.parser import find_assignment_block


SOURCE = """# Weighted hooks retain their source details.
on_actions = {
    on_daily = {
        events = { example.2 }
        random_events   = { # keep the weighted lottery
            10 = example.1 # first outcome
            90 = 0 # no event
            10 = example.3 # repeated weights are intentional
        }
        effect = { add_stability = 0.10 }
    }
    on_weekly = { effect = { add_political_power = 1 } }
}
"""
RELATIVE = Path("common/on_actions/weighted.txt")


def write_source(root: Path) -> Path:
    path = root / RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SOURCE, encoding="utf-8")
    return path


@pytest.mark.parametrize("touched", [False, True])
def test_weighted_on_action_unchanged_round_trip(tmp_path: Path, touched: bool) -> None:
    path = write_source(tmp_path)
    actions = load_on_actions_file(path)
    actions[0].touched = touched

    assert serialize_on_actions_file(actions, SOURCE) == SOURCE


@pytest.mark.parametrize("inherited", [False, True])
def test_weighted_on_action_effect_edit_preview_save_reload(
    tmp_path: Path, inherited: bool,
) -> None:
    root = tmp_path / "mod"
    root.mkdir()
    base = tmp_path / "base"
    source = write_source(base if inherited else root)
    mod = Mod(root, base_mod_paths=[base] if inherited else [])
    if inherited:
        mod.load_inherited_content(RELATIVE)
    original_list = list(mod.get_on_action("on_daily").random_events)
    assert mod.preview() == ""
    assert mod.update_on_action("on_daily", effect="add_stability = 0.20")

    preview = mod.preview()
    assert "add_stability = 0.20" in preview
    assert "10 example.1 90 0" not in preview
    assert source.read_text(encoding="utf-8") == SOURCE
    result = mod.save(require_changes=True)
    print(result)
    print(result.written_files)
    assert result.written_files == [root / RELATIVE]
    rendered = (root / RELATIVE).read_text(encoding="utf-8")
    # Inherited hooks may be reindented; all weighted source lines must survive.
    weighted = find_assignment_block(rendered, "random_events")[0]
    original_weighted = find_assignment_block(SOURCE, "random_events")[0]
    assert [line.strip() for line in weighted.splitlines()] == [
        line.strip() for line in original_weighted.splitlines()
    ]
    if inherited:
        assert source.read_text(encoding="utf-8") == SOURCE
    else:
        assert rendered == SOURCE.replace("add_stability = 0.10", "add_stability = 0.20")

    mod.reload()
    assert mod.get_on_action("on_daily").effect == "add_stability = 0.20"
    assert mod.get_on_action("on_daily").random_events == original_list
    assert mod.get_on_action("on_weekly").effect == "add_political_power = 1"
    assert mod.preview() == ""
    # A second edit must preserve the weighted block after reloading, too.
    mod.update_on_action("on_daily", events=["example.4"], effect="")
    result = mod.save(require_changes=True)
    print(result)
    assert result.written_files == [root / RELATIVE]
    assert find_assignment_block(
        (root / RELATIVE).read_text(encoding="utf-8"), "random_events"
    )[0] == weighted
    mod.reload()
    assert mod.get_on_action("on_daily").events == ["example.4"]
    assert mod.get_on_action("on_daily").effect == ""


@pytest.mark.parametrize("random_events", [["example.4", "example.5"], []])
def test_explicit_on_action_list_edits_still_apply(
    tmp_path: Path, random_events: list[str],
) -> None:
    path = write_source(tmp_path)
    mod = Mod(tmp_path)
    mod.update_on_action("on_daily", events=[], random_events=random_events)
    result = mod.save(require_changes=True)
    print(result)
    assert result.written_files == [path]
    mod.reload()
    action = mod.get_on_action("on_daily")
    assert action.events == []
    assert action.random_events == random_events
    assert action.effect == "add_stability = 0.10"
    assert "first outcome" not in path.read_text(encoding="utf-8")
