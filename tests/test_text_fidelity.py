from __future__ import annotations

import difflib
from pathlib import Path

from hoi4.decisions import load_decisions_file, serialize_decisions_file
from hoi4.events import load_events_file, serialize_events_file
from hoi4.focus import load_focus_trees, serialize_focus_file
from hoi4.ideas import read_ideas_file, serialize_ideas_file
from hoi4.localisation import parse_localization_file, serialize_localization_file
from hoi4.on_actions import load_on_actions_file, serialize_on_actions_file
from hoi4.patching import top_level_assignments


FOCUS_FILE = """# FB-shaped shared focus file
focus_tree   = {
    id = scl_focus
    country = { factor = 0 modifier = { add = 10 original_tag = SCL } }

    focus = {
        id = SCL_start
        icon = GFX_goal_generic_construct_civ_factory # keep icon note
        x = 4
        y = 0
        cost   = 5 # 35 days
        completion_reward = {
            add_political_power = 25 # keep reward note
        }
    }
    focus = { id = SCL_second icon = GFX_goal_generic_army x = 4 y = 1 cost = 10 }
}

focus_tree = {
    id = bay_focus
    default = yes
    focus = { id = BAY_start icon = GFX_goal_generic_political_reform x = 0 y = 0 cost = 10 }
}
"""

EVENT_FILE = """# FB-shaped merged events
add_namespace   = scl # preserve namespace comment

country_event = {
    id = scl.1
    title = scl.1.t
    desc = scl.1.d
    picture   = GFX_report_event_old # preserve picture comment
    is_triggered_only = yes
    option = {
        name = scl.1.a
        # preserve option comment
        add_political_power = 10
    }
}

country_event = {
    id = bay.1
    title = bay.1.t
    option = { name = bay.1.a set_country_flag = bay_untouched }
}
"""

DECISION_FILE = """# FB-shaped merged decisions
scl_resistance = {
    SCL_sabotage_italian_supply = {
        icon = generic_sabotage
        cost   = 25 # preserve PP note
        available = { has_war = yes }
        complete_effect = { add_political_power = -25 }
    }
}

bay_resistance = {
    BAY_slovene_resistance = {
        cost = 10
        complete_effect = { set_country_flag = bay_untouched }
    }
}
"""

IDEA_FILE = """# FB-shaped nested ideas
ideas = {
    country = {
        SCL_resistance = {
            picture = GFX_idea_SCL_resistance # keep picture spelling
            custom_idea_field = keep
            modifier = {
                stability_factor = -0.10 # preserve until this block is edited
                war_support_factor = 0.20
            }
        }
        BAY_resistance = { picture = GFX_idea_BAY modifier = { stability_factor = 0.05 } }
    }
    political_advisor = {
        SCL_advisor = { picture = GFX_portrait_SCL traits = { silent_workhorse } }
    }
}
"""

ON_ACTION_FILE = """# FB-shaped shared on-actions
on_actions = {
    on_startup = {
        events = { scl.1 # preserve event note
        }
        effect = { add_stability = 0.10 }
    }
    on_daily = { effect = { set_country_flag = untouched_daily } }
}
"""

LOCALIZATION_FILE = """l_english:
 # preserve section comment
 SCL_start : 2   "The Old Beginning" # preserve translator note
 BAY_start:0 "Bavarian Beginning"
 SCL_start_desc:0 "A description."
"""


def _changed_content_lines(before: str, after: str) -> list[str]:
    return [
        line
        for line in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="")
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_all_existing_serializers_are_byte_identical_without_changes(tmp_path: Path) -> None:
    focus_path = _write(tmp_path / "focus.txt", FOCUS_FILE)
    event_path = _write(tmp_path / "events.txt", EVENT_FILE)
    decision_path = _write(tmp_path / "decisions.txt", DECISION_FILE)
    idea_path = _write(tmp_path / "ideas.txt", IDEA_FILE)
    on_action_path = _write(tmp_path / "on_actions.txt", ON_ACTION_FILE)
    loc_path = _write(tmp_path / "loc.yml", LOCALIZATION_FILE)

    focus_trees = load_focus_trees(focus_path)
    namespace, events = load_events_file(event_path)
    decisions = load_decisions_file(decision_path)
    ideas, container = read_ideas_file(idea_path)
    on_actions = load_on_actions_file(on_action_path)
    localization = parse_localization_file(loc_path)

    assert serialize_focus_file(focus_trees, FOCUS_FILE) == FOCUS_FILE
    assert serialize_events_file(namespace, events, EVENT_FILE) == EVENT_FILE
    assert serialize_decisions_file(decisions, DECISION_FILE) == DECISION_FILE
    assert serialize_ideas_file(ideas, container, IDEA_FILE) == IDEA_FILE
    assert serialize_on_actions_file(on_actions, ON_ACTION_FILE) == ON_ACTION_FILE
    assert serialize_localization_file(localization, original=LOCALIZATION_FILE) == LOCALIZATION_FILE


def test_focus_scalar_edit_changes_only_its_value_bytes(tmp_path: Path) -> None:
    path = _write(tmp_path / "focus.txt", FOCUS_FILE)
    trees = load_focus_trees(path)
    trees[0].focuses[0].cost = 7
    trees[0].focuses[0].touched = True
    trees[0].touched = True

    rendered = serialize_focus_file(trees, FOCUS_FILE)
    assert rendered == FOCUS_FILE.replace("cost   = 5", "cost   = 7", 1)
    assert len(_changed_content_lines(FOCUS_FILE, rendered)) == 2


def test_event_scalar_edit_changes_only_its_value_bytes(tmp_path: Path) -> None:
    path = _write(tmp_path / "events.txt", EVENT_FILE)
    namespace, events = load_events_file(path)
    events[0].picture = "GFX_report_event_new"
    events[0].touched = True

    rendered = serialize_events_file(namespace, events, EVENT_FILE)
    assert rendered == EVENT_FILE.replace("GFX_report_event_old", "GFX_report_event_new", 1)
    assert len(_changed_content_lines(EVENT_FILE, rendered)) == 2


def test_decision_scalar_edit_changes_only_its_value_bytes(tmp_path: Path) -> None:
    path = _write(tmp_path / "decisions.txt", DECISION_FILE)
    categories = load_decisions_file(path)
    categories[0].decisions[0].cost = 26
    categories[0].decisions[0].touched = True

    rendered = serialize_decisions_file(categories, DECISION_FILE)
    assert rendered == DECISION_FILE.replace("cost   = 25", "cost   = 26", 1)
    assert len(_changed_content_lines(DECISION_FILE, rendered)) == 2


def test_idea_scalar_edit_does_not_rewrite_modifier_block(tmp_path: Path) -> None:
    path = _write(tmp_path / "ideas.txt", IDEA_FILE)
    ideas, container = read_ideas_file(path)
    ideas[0].icon = "SCL_resistance_new"
    ideas[0].touched = True

    rendered = serialize_ideas_file(ideas, container, IDEA_FILE)
    assert rendered == IDEA_FILE.replace(
        "GFX_idea_SCL_resistance", "SCL_resistance_new", 1
    )
    assert len(_changed_content_lines(IDEA_FILE, rendered)) == 2


def test_idea_block_edit_is_bounded_to_the_modifier_body(tmp_path: Path) -> None:
    path = _write(tmp_path / "ideas.txt", IDEA_FILE)
    ideas, container = read_ideas_file(path)
    ideas[0].modifier["war_support_factor"] = 0.25
    ideas[0].touched = True

    rendered = serialize_ideas_file(ideas, container, IDEA_FILE)
    assert "war_support_factor = 0.25" in rendered
    assert "BAY_resistance = { picture = GFX_idea_BAY" in rendered
    assert "SCL_advisor = { picture = GFX_portrait_SCL" in rendered
    assert len(_changed_content_lines(IDEA_FILE, rendered)) <= 7


def test_on_action_block_edit_does_not_rewrite_other_actions(tmp_path: Path) -> None:
    path = _write(tmp_path / "on_actions.txt", ON_ACTION_FILE)
    actions = load_on_actions_file(path)
    actions[0].effect = "add_stability = 0.20"
    actions[0].touched = True

    rendered = serialize_on_actions_file(actions, ON_ACTION_FILE)
    assert rendered == ON_ACTION_FILE.replace("add_stability = 0.10", "add_stability = 0.20", 1)
    assert len(_changed_content_lines(ON_ACTION_FILE, rendered)) == 2


def test_repeated_on_action_blocks_round_trip_and_patch_by_source_occurrence(
    tmp_path: Path,
) -> None:
    source = """on_actions = {
    on_startup = { effect = { first = yes } }
    on_empty = {

    }
    on_daily = { effect = { untouched = yes } }
    on_startup = { effect = { second = yes } }
}
"""
    path = _write(tmp_path / "on_actions.txt", source)
    actions = load_on_actions_file(path)

    assert serialize_on_actions_file(actions, source) == source
    actions[3].effect = "second = changed"
    actions[3].touched = True

    assert serialize_on_actions_file(actions, source) == source.replace(
        "second = yes", "second = changed"
    )


def test_localization_edit_changes_only_the_quoted_value(tmp_path: Path) -> None:
    path = _write(tmp_path / "loc.yml", LOCALIZATION_FILE)
    entries = parse_localization_file(path)
    entries["SCL_start"] = "A New Beginning"

    rendered = serialize_localization_file(entries, original=LOCALIZATION_FILE)
    assert rendered == LOCALIZATION_FILE.replace("The Old Beginning", "A New Beginning", 1)
    assert len(_changed_content_lines(LOCALIZATION_FILE, rendered)) == 2


def test_top_level_assignments_preserve_duplicate_quoted_key_spans() -> None:
    text = '''# quoted country keys used by bookmarks
"GER" = { name = "First" nested = { "GER" = ignored } }
# duplicate is deliberate
"GER"   = { name = "Second" }
'''
    spans = top_level_assignments(text)

    assert [span.key for span in spans] == ["GER", "GER"]
    assert [text[span.start] for span in spans] == ['"', '"']
    assert [text[span.body_start : span.body_end].strip() for span in spans] == [
        'name = "First" nested = { "GER" = ignored }',
        'name = "Second"',
    ]
