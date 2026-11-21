"""On-actions are read from the syntax tree and written back span by span."""

from pathlib import Path

from hoi4 import Mod
from hoi4.on_actions import load_on_actions_file, serialize_on_actions_file

SOURCE = """# header comment
on_actions = {
\t# startup hooks
\ton_startup = {
\t\tunknown_flag = yes
\t\teffect = {
\t\t\tevery_country = {
\t\t\t\t# nested lists are not the hook's lists
\t\t\t\trandom_events = { 100 = nested.1 }
\t\t\t}
\t\t}
\t\tevents = { first.1 } # trailing comment
\t\teffect = { add_stability = 0.1 }
\t\tevents = { first.2 }
\t}
\ton_daily = {
\t\trandom_events = {
\t\t\t10 = daily.1 # weighted
\t\t\t90 = 0
\t\t}
\t}
\ton_startup = { effect = { log = "second occurrence" } }
}
on_actions = {
\ton_weekly = { events = { weekly.1 } }
}
"""


def make_mod(tmp_path: Path) -> tuple[Mod, Path]:
    path = tmp_path / "common" / "on_actions" / "hooks.txt"
    path.parent.mkdir(parents=True)
    path.write_text(SOURCE, encoding="utf-8")
    return Mod(tmp_path), path


def test_repeated_blocks_are_joined_and_nested_lists_ignored(tmp_path):
    mod, path = make_mod(tmp_path)
    first, second = mod.get_on_action_occurrences("on_startup")
    assert first.events == ["first.1", "first.2"]
    assert first.random_events == []
    assert "every_country" in first.effect
    assert "add_stability = 0.1" in first.effect
    assert second.effect == 'log = "second occurrence"'
    assert mod.get_on_action("on_daily").random_events == ["10", "daily.1", "90", "0"]
    # Hooks in a second ``on_actions`` block are loaded too.
    assert mod.get_on_action("on_weekly").events == ["weekly.1"]
    assert [action.id for action in load_on_actions_file(path)] == [
        "on_startup", "on_daily", "on_startup", "on_weekly",
    ]


def test_unchanged_round_trip_is_byte_identical(tmp_path):
    mod, path = make_mod(tmp_path)
    for action_id in mod.list_on_actions():
        for index, action in enumerate(mod.get_on_action_occurrences(action_id)):
            mod.update_on_action(
                action_id,
                occurrence=index,
                effect=action.effect,
                events=list(action.events),
                random_events=list(action.random_events),
            )
    mod.save()
    assert path.read_text(encoding="utf-8") == SOURCE
    actions = load_on_actions_file(path)
    assert serialize_on_actions_file(actions, SOURCE) == SOURCE


def test_editing_one_hook_leaves_everything_else_alone(tmp_path):
    mod, path = make_mod(tmp_path)
    mod.update_on_action("on_daily", effect="add_political_power = 1")
    mod.save(require_changes=True)
    assert path.read_text(encoding="utf-8") == SOURCE.replace(
        "\t\t\t90 = 0\n\t\t}\n",
        "\t\t\t90 = 0\n\t\t}\n\t\teffect = {\n\t\t\tadd_political_power = 1\n\t\t}\n",
    )


def test_editing_events_writes_the_first_block_and_drops_repeats(tmp_path):
    mod, path = make_mod(tmp_path)
    mod.update_on_action("on_startup", occurrence=0, events=["first.3"])
    mod.save(require_changes=True)
    text = path.read_text(encoding="utf-8")
    assert text == SOURCE.replace("events = { first.1 }", "events = { first.3 }").replace(
        "\t\tevents = { first.2 }\n", ""
    )
    mod.reload()
    first, second = mod.get_on_action_occurrences("on_startup")
    assert first.events == ["first.3"]
    assert second.effect == 'log = "second occurrence"'


def test_weighted_random_events_edit_keeps_weights(tmp_path):
    mod, path = make_mod(tmp_path)
    mod.update_on_action("on_daily", random_events=["20", "daily.2", "80", "0"])
    mod.save(require_changes=True)
    text = path.read_text(encoding="utf-8")
    assert "20 = daily.2" in text and "80 = 0" in text
    mod.reload()
    assert mod.get_on_action("on_daily").random_events == ["20", "daily.2", "80", "0"]


def test_hook_in_second_root_can_be_edited(tmp_path):
    mod, path = make_mod(tmp_path)
    mod.update_on_action("on_weekly", events=["weekly.2"])
    mod.save(require_changes=True)
    assert path.read_text(encoding="utf-8") == SOURCE.replace("weekly.1", "weekly.2")
