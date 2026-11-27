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
    mod.discard()
    first, second = mod.get_on_action_occurrences("on_startup")
    assert first.events == ["first.3"]
    assert second.effect == 'log = "second occurrence"'


def test_weighted_random_events_edit_keeps_weights(tmp_path):
    mod, path = make_mod(tmp_path)
    mod.update_on_action("on_daily", random_events=["20", "daily.2", "80", "0"])
    mod.save(require_changes=True)
    text = path.read_text(encoding="utf-8")
    assert "20 = daily.2" in text and "80 = 0" in text
    mod.discard()
    assert mod.get_on_action("on_daily").random_events == ["20", "daily.2", "80", "0"]


def test_hook_in_second_root_can_be_edited(tmp_path):
    mod, path = make_mod(tmp_path)
    mod.update_on_action("on_weekly", events=["weekly.2"])
    mod.save(require_changes=True)
    assert path.read_text(encoding="utf-8") == SOURCE.replace("weekly.1", "weekly.2")


REPEATED = """on_actions = {
\ton_startup = {
\t\tevents = { a.1 }
\t\tevents = { b.1 # keep me
\t\t}
\t\teffect = { add_stability = 1 # old
\t\t}
\t}
}
"""


def _repeated(tmp_path: Path) -> tuple[Mod, Path]:
    path = tmp_path / "common" / "on_actions" / "repeated.txt"
    path.parent.mkdir(parents=True)
    path.write_text(REPEATED, encoding="utf-8")
    return Mod(tmp_path), path


def test_adding_an_event_keeps_the_repeated_blocks_and_their_comments(tmp_path):
    mod, path = _repeated(tmp_path)
    mod.update_on_action("on_startup", events=["a.1", "b.1", "c.1"])
    mod.save(require_changes=True)
    assert path.read_text(encoding="utf-8") == REPEATED.replace(
        "b.1 # keep me\n", "b.1 # keep me\n\t\t\tc.1\n"
    )
    mod.discard()
    assert mod.get_on_action("on_startup").events == ["a.1", "b.1", "c.1"]


def test_removing_an_event_cuts_only_that_entry(tmp_path):
    mod, path = _repeated(tmp_path)
    mod.update_on_action("on_startup", events=["b.1"])
    mod.save(require_changes=True)
    assert path.read_text(encoding="utf-8") == REPEATED.replace("\t\tevents = { a.1 }\n", "")


def test_a_comment_only_effect_edit_is_written(tmp_path):
    mod, path = _repeated(tmp_path)
    mod.update_on_action("on_startup", effect="add_stability = 1 # new")
    mod.save(require_changes=True)
    assert path.read_text(encoding="utf-8") == REPEATED.replace("# old", "# new")


def test_a_weighted_entry_is_added_next_to_the_commented_ones(tmp_path):
    mod, path = make_mod(tmp_path)
    mod.update_on_action("on_daily", random_events=["10", "daily.1", "90", "0", "5", "daily.2"])
    mod.save(require_changes=True)
    assert path.read_text(encoding="utf-8") == SOURCE.replace(
        "\t\t\t90 = 0\n", "\t\t\t90 = 0\n\t\t\t5 = daily.2\n"
    )


def test_a_changed_weight_keeps_the_comment_on_its_line(tmp_path):
    mod, path = make_mod(tmp_path)
    mod.update_on_action("on_daily", random_events=["20", "daily.1", "80", "0"])
    mod.save(require_changes=True)
    assert path.read_text(encoding="utf-8") == SOURCE.replace(
        "10 = daily.1 # weighted", "20 = daily.1 # weighted"
    ).replace("90 = 0", "80 = 0")


def test_an_event_inserted_in_the_middle_keeps_the_repeated_blocks(tmp_path):
    mod, path = _repeated(tmp_path)
    mod.update_on_action("on_startup", events=["x.1", "a.1", "y.1", "b.1"])
    mod.save(require_changes=True)
    assert path.read_text(encoding="utf-8") == REPEATED.replace(
        "events = { a.1 }", "events = { x.1 a.1 y.1 }"
    )
    mod.discard()
    assert mod.get_on_action("on_startup").events == ["x.1", "a.1", "y.1", "b.1"]


def test_a_statement_in_random_events_is_not_taken_for_a_weight(tmp_path: Path) -> None:
    """``chance_to_fire = 25`` is not a ``weight = event`` pair: it stays one
    entry, and the pairs after it keep their weights when the list is edited."""

    source = (
        "on_actions = {\n\ton_monthly = {\n\t\trandom_events = {\n"
        "\t\t\tchance_to_fire = 25 # keep\n\t\t\t100 = 0\n\t\t\t10 = bar.1\n"
        "\t\t}\n\t}\n}\n"
    )
    path = tmp_path / "common/on_actions/zz_on_actions.txt"
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")
    mod = Mod(tmp_path)
    action = mod.get_on_action("on_monthly")
    assert action.random_events == ["chance_to_fire = 25", "100", "0", "10", "bar.1"]

    mod.update_on_action("on_monthly", random_events=[*action.random_events, "5", "bar.2"])
    mod.save()

    assert path.read_text(encoding="utf-8") == source.replace(
        "\t\t\t10 = bar.1\n", "\t\t\t10 = bar.1\n\t\t\t5 = bar.2\n"
    )
    assert Mod(tmp_path).get_on_action("on_monthly").random_events == [
        "chance_to_fire = 25", "100", "0", "10", "bar.1", "5", "bar.2",
    ]


def test_a_statement_without_spaces_is_not_paired_with_a_weight(tmp_path: Path) -> None:
    path = tmp_path / "common/on_actions/zz_on_actions.txt"
    path.parent.mkdir(parents=True)
    path.write_text(
        "on_actions = {\n\ton_monthly = {\n\t\trandom_events = {\n\t\t\t100 = 0\n\t\t}\n\t}\n}\n",
        encoding="utf-8",
    )
    mod = Mod(tmp_path)
    mod.update_on_action(
        "on_monthly", random_events=["100", "0", "chance_to_fire=25", "10", "a.1"]
    )
    mod.save()
    assert Mod(tmp_path).get_on_action("on_monthly").random_events == [
        "100", "0", "chance_to_fire = 25", "10", "a.1",
    ]
