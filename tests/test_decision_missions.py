"""Missions, targeted decisions, and the general decision fields."""

from __future__ import annotations

from pathlib import Path

import pytest

from hoi4 import Mod, VALIDATION_CODES, VALIDATION_WARNING_CODES
from hoi4.decisions import load_decisions_file, serialize_decisions_file

MISSION_SOURCE = """\
# Missions for TST
TST_missions = {
\t# Hold the line or lose stability
\tTST_hold_line = {
\t\ticon = generic_defend
\t\tdays_mission_timeout = 90 # three months
\t\tis_good = yes
\t\tselectable_mission = no
\t\tallowed = { original_tag = TST }
\t\tactivation = { has_war = yes }
\t\tavailable = { surrender_progress < 0.1 }
\t\tcancel_trigger = { has_war = no }
\t\tcancel_effect = { add_political_power = 10 }
\t\ttimeout_effect = {
\t\t\tadd_stability = -0.05 # penalty
\t\t}
\t\tcomplete_effect = { add_stability = 0.05 }
\t\tmodifier = { political_power_factor = 0.1 }
\t\tunknown_key = keep_me
\t\tcustom_block = { a = 1 }
\t\tcustom_block = { b = 2 }
\t\tai_will_do = {
\t\t\tfactor = 1
\t\t\tmodifier = { factor = 0 has_war = no }
\t\t}
\t}
\tTST_pressure = {
\t\ticon = generic_political_discourse
\t\tpriority = 5
\t\tallowed = { original_tag = TST }
\t\ttargets = { GER "ITA" }
\t\ttarget_array = global.majors
\t\ttarget_root_trigger = { is_major = yes }
\t\ttarget_trigger = { FROM = { exists = yes } }
\t\ttargets_dynamic = yes
\t\ttarget_non_existing = no
\t\tstate_target = no
\t\ton_map_mode = map_and_decisions_view
\t\twar_with_on_remove = GER
\t\tdays_re_enable = 30
\t\tdays_remove = 60
\t\tcustom_cost_trigger = { command_power > 20 }
\t\tcustom_cost_text = decision_cost_cp_20
\t\tcancel_if_not_visible = yes
\t\tremove_trigger = { has_war_with = FROM }
\t\thighlight_states = { highlight_state_targets = { state = 64 } }
\t\tcomplete_effect = { add_political_power = -20 }
\t}
}
"""


def write_source(root: Path, text: str = MISSION_SOURCE) -> Path:
    path = root / "common" / "decisions" / "TST_decisions.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_parses_mission_fields(tmp_path: Path) -> None:
    decision = load_decisions_file(write_source(tmp_path))[0].decisions[0]
    assert decision.id == "TST_hold_line"
    assert decision.days_mission_timeout == 90
    assert decision.is_good is True
    assert decision.selectable_mission is False
    assert decision.activation == "has_war = yes"
    assert decision.cancel_trigger == "has_war = no"
    assert decision.cancel_effect == "add_political_power = 10"
    assert decision.timeout_effect == "add_stability = -0.05 # penalty"
    assert decision.allowed == "original_tag = TST"
    # Only the top-level modifier is the decision modifier, not ai_will_do's.
    assert decision.modifier == "political_power_factor = 0.1"
    assert decision.cost is None
    assert decision.targets == []


def test_parses_targeted_and_general_fields(tmp_path: Path) -> None:
    decision = load_decisions_file(write_source(tmp_path))[0].decisions[1]
    assert decision.targets == ["GER", "ITA"]
    assert decision.target_array == "global.majors"
    assert decision.target_root_trigger == "is_major = yes"
    assert decision.target_trigger == "FROM = { exists = yes }"
    assert decision.targets_dynamic is True
    assert decision.target_non_existing is False
    assert decision.state_target is False
    assert decision.on_map_mode == "map_and_decisions_view"
    assert decision.war_with_on_remove == "GER"
    assert decision.days_re_enable == 30
    assert decision.custom_cost_trigger == "command_power > 20"
    assert decision.custom_cost_text == "decision_cost_cp_20"
    assert decision.cancel_if_not_visible is True
    assert decision.remove_trigger == "has_war_with = FROM"
    assert decision.highlight_states == "highlight_state_targets = { state = 64 }"
    assert decision.priority == 5
    assert decision.days_mission_timeout is None


def test_unchanged_input_is_byte_identical(tmp_path: Path) -> None:
    path = write_source(tmp_path)
    categories = load_decisions_file(path)
    assert serialize_decisions_file(categories, original=MISSION_SOURCE) == MISSION_SOURCE
    for decision in categories[0].decisions:
        decision.touched = True
    assert serialize_decisions_file(categories, original=MISSION_SOURCE) == MISSION_SOURCE

    mod = Mod(tmp_path)
    assert mod.update_decision("TST_hold_line")  # touch without changes
    mod.save()
    assert path.read_text(encoding="utf-8") == MISSION_SOURCE


def test_one_field_edit_preserves_comments_unknown_keys_and_repeats(tmp_path: Path) -> None:
    path = write_source(tmp_path)
    mod = Mod(tmp_path)
    assert mod.update_decision("TST_hold_line", days_mission_timeout=120)
    mod.save()
    text = path.read_text(encoding="utf-8")
    assert text == MISSION_SOURCE.replace(
        "days_mission_timeout = 90 # three months",
        "days_mission_timeout = 120 # three months",
    )
    reloaded = Mod(tmp_path)
    assert reloaded.get_decision("TST_hold_line").days_mission_timeout == 120
    assert not reloaded.load_diagnostics
    assert not [issue for issue in reloaded.validate() if issue.severity == "error"]


def test_every_new_field_round_trips_through_update(tmp_path: Path) -> None:
    path = write_source(tmp_path)
    mod = Mod(tmp_path)
    changes = {
        "activation": "has_war = yes is_subject = no",
        "timeout_effect": "add_stability = -0.1",
        "cancel_trigger": "",
        "cancel_effect": "",
        "is_good": False,
        "selectable_mission": True,
        "allowed": "tag = TST",
        "modifier": "stability_factor = 0.05",
        "highlight_states": "highlight_state_targets = { state = 1 }",
        "priority": 3,
    }
    assert mod.update_decision("TST_hold_line", **changes)
    targeted = {
        "targets": ("FRA", "ENG", "SOV"),
        "target_array": "",
        "target_trigger": "FROM = { is_major = yes }",
        "target_root_trigger": "",
        "state_target": None,
        "targets_dynamic": False,
        "target_non_existing": True,
        "on_map_mode": "decision_view_only",
        "war_with_on_remove": "",
        "days_re_enable": None,
        "custom_cost_trigger": "political_power > 50",
        "custom_cost_text": "cost text",
        "cancel_if_not_visible": False,
        "remove_trigger": "always = no",
    }
    assert mod.update_decision("TST_pressure", **targeted)
    mod.save()
    text = path.read_text(encoding="utf-8")
    assert "# Hold the line or lose stability" in text
    assert "unknown_key = keep_me" in text
    assert text.count("custom_block = {") == 2
    assert "targets = { FRA ENG SOV }" in text
    assert 'custom_cost_text = "cost text"' in text
    assert "target_array" not in text
    assert "cancel_trigger" not in text

    reloaded = Mod(tmp_path)
    mission = reloaded.get_decision("TST_hold_line")
    for key, value in changes.items():
        assert getattr(mission, key) == value, key
    pressure = reloaded.get_decision("TST_pressure")
    expected = dict(targeted, targets=["FRA", "ENG", "SOV"])
    for key, value in expected.items():
        assert getattr(pressure, key) == value, key
    # The ai_will_do modifier is untouched by the decision-level modifier edit.
    assert "modifier = { factor = 0 has_war = no }" in mission.ai_will_do


def test_unmodeled_spellings_survive_other_edits(tmp_path: Path) -> None:
    source = """\
TST_missions = {
\tTST_const = {
\t\tcost = @decision_cost
\t\tdays_mission_timeout = @mission_days
\t\tpriority = { base = 5 modifier = { add = 5 has_war = yes } }
\t\tactivation = { has_war = yes }
\t\tcomplete_effect = { add_political_power = 1 }
\t}
}
"""
    path = write_source(tmp_path, source)
    mod = Mod(tmp_path)
    decision = mod.get_decision("TST_const")
    assert decision.cost is None
    assert decision.days_mission_timeout is None
    assert decision.priority is None
    # An unparsed days_mission_timeout still makes this a mission, and an
    # unparsed cost still counts as a cost.
    codes = {issue.code for issue in mod.validate() if issue.decision_id == "TST_const"}
    assert "mission_activation_without_timeout" not in codes
    assert "mission_with_cost" in codes
    mod.update_decision("TST_const", complete_effect="add_political_power = 2")
    mod.save()
    assert path.read_text(encoding="utf-8") == source.replace(
        "add_political_power = 1", "add_political_power = 2"
    )


def test_create_mission_renders_and_reloads(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    mod.create_mission(
        "TST_missions",
        "TST_new_mission",
        days_mission_timeout=60,
        activation="has_war = yes",
        available="surrender_progress < 0.1",
        complete_effect="add_stability = 0.05",
        timeout_effect="add_stability = -0.05",
        is_good=True,
        selectable_mission=False,
        icon="generic_defend",
        loc_name="Hold the Line",
        loc_desc="Survive.",
    )
    mod.save()
    text = (tmp_path / "common/decisions/mod_decisions.txt").read_text(encoding="utf-8")
    assert (
        "\tTST_new_mission = {\n"
        "\t\ticon = generic_defend\n"
        "\t\tdays_mission_timeout = 60\n"
        "\t\tis_good = yes\n"
        "\t\tselectable_mission = no\n"
        "\t\tavailable = {\n\t\t\tsurrender_progress < 0.1\n\t\t}\n"
        "\t\tactivation = {\n\t\t\thas_war = yes\n\t\t}\n"
        "\t\tcomplete_effect = {\n\t\t\tadd_stability = 0.05\n\t\t}\n"
        "\t\ttimeout_effect = {\n\t\t\tadd_stability = -0.05\n\t\t}\n"
        "\t}"
    ) in text
    reloaded = Mod(tmp_path)
    mission = reloaded.get_decision("TST_new_mission")
    assert mission.days_mission_timeout == 60
    assert mission.activation == "has_war = yes"
    assert mission.timeout_effect == "add_stability = -0.05"
    assert reloaded.get_loc("TST_new_mission") == "Hold the Line"
    assert not [
        issue for issue in reloaded.validate() if issue.code.startswith("mission_")
    ]


def test_create_mission_rejects_costs_and_bad_timeouts(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    with pytest.raises(ValueError, match="cost"):
        mod.create_mission("cat", "m", days_mission_timeout=30, cost=10)
    with pytest.raises(ValueError, match="positive"):
        mod.create_mission("cat", "m", days_mission_timeout=0)
    with pytest.raises(TypeError):
        mod.create_mission("cat", "m", days_mission_timeout=True)
    assert "m" not in mod.list_decisions()


def test_create_targeted_decision(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    with pytest.raises(ValueError, match="target"):
        mod.create_targeted_decision("TST_diplomacy", "TST_nothing")
    with pytest.raises(ValueError, match="Invalid decision target"):
        mod.create_targeted_decision("TST_diplomacy", "TST_bad", targets=["GER ITA", "{"])
    mod.create_targeted_decision(
        "TST_diplomacy",
        "TST_improve",
        targets="GER ITA",
        target_trigger="FROM = { is_major = yes }",
        targets_dynamic=True,
        cost=25,
        complete_effect="FROM = { add_opinion_modifier = { target = ROOT modifier = trade } }",
    )
    mod.create_targeted_decision(
        "TST_diplomacy",
        "TST_claim_state",
        state_target=True,
        target_trigger="FROM = { is_core_of = ROOT }",
        on_map_mode="map_only",
    )
    mod.save()
    text = (tmp_path / "common/decisions/mod_decisions.txt").read_text(encoding="utf-8")
    assert "\t\tcost = 25\n" in text
    assert "\t\ttargets = { GER ITA }\n" in text
    assert "\t\ttargets_dynamic = yes\n" in text
    assert "\t\tstate_target = yes\n" in text
    assert "\t\ton_map_mode = map_only\n" in text
    reloaded = Mod(tmp_path)
    assert reloaded.get_decision("TST_improve").targets == ["GER", "ITA"]
    assert reloaded.get_decision("TST_claim_state").state_target is True
    assert reloaded.get_decision("TST_claim_state").target_trigger == "FROM = { is_core_of = ROOT }"


def test_mission_validation(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    mod.create_decision("cat", "paid_mission", days_mission_timeout=30, cost=10)
    mod.create_decision(
        "cat", "custom_paid_mission", days_mission_timeout=30, custom_cost_trigger="always = yes"
    )
    mod.create_decision("cat", "free_mission", days_mission_timeout=30, cost=0)
    mod.create_decision("cat", "orphan_activation", activation="has_war = yes")
    mod.create_decision("cat", "plain", cost=50, targets=["GER"])
    issues = [issue for issue in mod.validate() if issue.code.startswith("mission_")]
    by_decision = {(issue.decision_id, issue.code, issue.severity) for issue in issues}
    assert by_decision == {
        ("paid_mission", "mission_with_cost", "warning"),
        ("custom_paid_mission", "mission_with_cost", "warning"),
        ("orphan_activation", "mission_activation_without_timeout", "error"),
    }
    assert "mission_with_cost" in VALIDATION_WARNING_CODES
    assert "mission_activation_without_timeout" in VALIDATION_CODES
    assert "mission_activation_without_timeout" not in VALIDATION_WARNING_CODES


def test_mission_only_block_is_recognized_as_decision(tmp_path: Path) -> None:
    write_source(
        tmp_path,
        "TST_missions = {\n\tTST_bare = {\n\t\tdays_mission_timeout = 10\n"
        "\t\ttimeout_effect = { add_stability = -0.01 }\n\t}\n}\n",
    )
    mod = Mod(tmp_path)
    assert mod.get_decision("TST_bare").timeout_effect == "add_stability = -0.01"


def test_mission_effects_feed_liveness(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    mod.create_event("tst.1", title="tst.1.t", description="tst.1.d", is_triggered_only=True)
    mod.create_mission(
        "cat",
        "TST_timer",
        days_mission_timeout=30,
        timeout_effect="country_event = { id = tst.1 }\nset_country_flag = tst_timeout",
        activation="has_country_flag = tst_timeout",
    )
    report = mod.analyze_content_liveness()
    assert "tst.1" in report.fired_events
    assert "tst_timeout" not in report.flags_set_only
    assert "tst_timeout" not in report.flags_read_only


def test_update_decision_rejects_unknown_fields(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    mod.create_decision("cat", "d")
    with pytest.raises(TypeError, match="Unknown fields"):
        mod.update_decision("d", not_a_field=1)
