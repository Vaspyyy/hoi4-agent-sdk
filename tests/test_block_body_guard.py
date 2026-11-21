"""Script text with a stray brace is refused by every writer.

``} zz_breakout = 1 {`` is balanced once it sits inside a block, so the file
it lands in still parses: the block closes early, the statement escapes into
the surrounding script and a new block opens. Only the writer that wraps the
text knows where the block was meant to end, so each one checks it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from hoi4 import EventOption, EventText, Focus, Mod

PAYLOAD = "} zz_breakout = 1 {"

EVENTS = (
    "add_namespace = t\ncountry_event = {\n\tid = t.1\n\ttitle = t.1.t\n"
    "\toption = { name = t.1.a }\n}\n"
)
FOCUS_TREE = (
    "focus_tree = {\n\tid = zz_tree\n\tcountry = { factor = 1 }\n"
    "\tfocus = {\n\t\tid = F1\n\t\ticon = GFX_goal_generic\n\t\tx = 0\n\t\ty = 0\n\t\tcost = 1\n\t}\n}\n"
)
DECISIONS = "cat_a = {\n\ticon = icon_a\n\tdec_a = {\n\t\tcost = 10\n\t}\n}\n"
STATE = (
    "state = {\n\tid = 1\n\tname = \"STATE_1\"\n\tmanpower = 1\n\tstate_category = town\n"
    "\thistory = {\n\t\towner = FRA\n\t}\n\tprovinces = { 1 }\n}\n"
)
ON_ACTIONS = "on_actions = {\n\ton_startup = {\n\t\tevents = {\n\t\t\tt.1\n\t\t}\n\t}\n}\n"

SOURCES = {
    "events/t.txt": EVENTS,
    "common/national_focus/zz.txt": FOCUS_TREE,
    "common/decisions/zz.txt": DECISIONS,
    "history/states/1-Test.txt": STATE,
    "common/on_actions/zz_on_actions.txt": ON_ACTIONS,
}

Attempt = Callable[[Mod], object]

ATTEMPTS: dict[str, Attempt] = {
    # A new block in an existing object.
    "event trigger": lambda mod: mod.update_event("t.1", trigger=PAYLOAD),
    "event immediate": lambda mod: mod.update_event("t.1", immediate=PAYLOAD),
    "event mean_time_to_happen": lambda mod: mod.update_event("t.1", mean_time_to_happen=PAYLOAD),
    "event conditional title": lambda mod: mod.update_event(
        "t.1", conditional_titles=[EventText("t.1.t2", PAYLOAD)]
    ),
    "event option effect": lambda mod: mod.update_event_option("t.1", 0, effect=PAYLOAD),
    "focus icon block": lambda mod: mod.update_focus("zz_tree", "F1", icon_blocks=[PAYLOAD]),
    "focus completion_reward": lambda mod: mod.update_focus(
        "zz_tree", "F1", completion_reward=PAYLOAD
    ),
    "decision available": lambda mod: mod.update_decision("dec_a", available=PAYLOAD),
    "decision category allowed": lambda mod: mod.update_decision_category(
        "cat_a", allowed=PAYLOAD
    ),
    "state buildings": lambda mod: mod.set_state_properties(1, buildings=PAYLOAD),
    "on_action effect": lambda mod: mod.update_on_action("on_startup", effect=PAYLOAD),
    "on_action events": lambda mod: mod.update_on_action("on_startup", events=[PAYLOAD]),
    "on_action random_events": lambda mod: mod.update_on_action(
        "on_startup", random_events=["10", PAYLOAD]
    ),
    # A new object.
    "new event trigger": lambda mod: mod.create_event("t.2", title="t", trigger=PAYLOAD),
    "new event option": lambda mod: mod.create_event(
        "t.2", title="t", options=[EventOption(name="t.2.a", effect=PAYLOAD)]
    ),
    "new event conditional description": lambda mod: mod.create_event(
        "t.2", title="t", conditional_descriptions=[EventText("t.2.d", PAYLOAD)]
    ),
    "new focus available": lambda mod: mod.add_focus(
        "zz_tree", Focus(id="F2", x=1, y=0, available=PAYLOAD)
    ),
    "new focus icon block": lambda mod: mod.add_focus(
        "zz_tree", Focus(id="F2", x=1, y=0, icon_blocks=[PAYLOAD])
    ),
    "new decision complete_effect": lambda mod: mod.create_decision(
        "cat_a", "dec_b", complete_effect=PAYLOAD
    ),
    "new mission activation": lambda mod: mod.create_mission(
        "cat_a", "mission_b", days_mission_timeout=30, activation=PAYLOAD
    ),
    "new decision category visible": lambda mod: mod.create_decision_category(
        "cat_b", visible=PAYLOAD
    ),
    "new dynamic modifier enable": lambda mod: mod.create_dynamic_modifier(
        "zz_dynamic", enable=PAYLOAD
    ),
    "new on_action effect": lambda mod: mod.create_on_action("on_monthly", effect=PAYLOAD),
    "new on_action events": lambda mod: mod.create_on_action("on_monthly", events=[PAYLOAD]),
    "new ai strategy allowed": lambda mod: mod.create_ai_strategy(
        "zz_plan", [("alliance", "GER", 10)], allowed=PAYLOAD
    ),
    "new scripted localisation trigger": lambda mod: mod.create_scripted_localisation(
        "zz_text", [(PAYLOAD, "KEY")]
    ),
    "new scripted effect": lambda mod: mod.create_scripted_effect("zz_effect", PAYLOAD),
}


@pytest.mark.parametrize("name", sorted(ATTEMPTS))
def test_script_text_with_a_stray_brace_is_refused(tmp_path: Path, name: str) -> None:
    for relative, text in SOURCES.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    mod = Mod(tmp_path)

    # ParseError is a ValueError; some writers refuse with a plain one.
    with pytest.raises(ValueError):
        ATTEMPTS[name](mod)
        mod.preview()

    for relative, text in SOURCES.items():
        assert (tmp_path / relative).read_text(encoding="utf-8") == text
