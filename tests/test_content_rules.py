"""The per-object content checks fire on real mistakes and stay quiet on valid content.

Each check has a pair of small fixtures: one with the mistake, one with the
valid shape that the game's own files use. A fake game install supplies
inherited files where a rule depends on them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hoi4 import Mod
from hoi4.types import Event, EventOption, Focus, FocusTree, Idea, ValidationError
from hoi4.validation import (
    _script_warnings,
    validate_event,
    validate_focus_tree,
    validate_idea,
)


def write(root: Path, relative: str, text: str, *, bom: bool = False) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(("﻿" if bom else "") + text, encoding="utf-8")
    return path


def codes(findings: list[ValidationError], code: str) -> list[ValidationError]:
    return [finding for finding in findings if finding.code == code]


@pytest.fixture
def game(tmp_path: Path) -> Path:
    root = tmp_path / "game"
    write(
        root,
        "localisation/english/game_l_english.yml",
        "l_english:\n"
        ' GAME_focus: "The "Quoted" Focus"\n'
        ' GAME_focus_desc:0 "Text"\n'
        ' BSK_fascism: "Greater Bashkiria"\n'
        ' BSK_fascism_ADJ: "Bashkir"\n'
        ' BSK_fascism_DEF: "the Realm"\n',
        bom=True,
    )
    write(root, "events/game_events.txt", "add_namespace = game_ns\n")
    write(
        root,
        "common/country_tag_aliases/aliases.txt",
        "SOU = { original_tag = SOV }\n",
    )
    return root


def mod_root(tmp_path: Path) -> Path:
    root = tmp_path / "mod"
    write(root, "descriptor.mod", 'name = "test"\n')
    return root


# ── Focus localisation ──────────────────────────────────────────────


def focus_file(focuses: str) -> str:
    return f"focus_tree = {{\n id = test_tree\n {focuses}\n}}\n"


def test_focus_without_localisation_anywhere_is_reported(tmp_path, game):
    root = mod_root(tmp_path)
    write(root, "common/national_focus/t.txt", focus_file("focus = { id = TST_nowhere x = 0 y = 0 }"))
    findings = codes(Mod(root, hoi4_install=game).validate(stage="build"), "missing_localization")
    assert {(f.focus_id, f.severity) for f in findings} == {("TST_nowhere", "warning")}
    assert len(findings) == 2  # name and description


def test_focus_localised_by_the_game_or_through_text_is_quiet(tmp_path, game):
    root = mod_root(tmp_path)
    write(
        root,
        "common/national_focus/t.txt",
        focus_file(
            "focus = { id = GAME_focus x = 0 y = 0 }\n"
            "focus = { id = TST_alias TEXT = GAME_focus x = 1 y = 0 }"
        ),
    )
    findings = Mod(root, hoi4_install=game).validate(stage="build")
    assert not codes(findings, "missing_localization")


def test_unsaved_deletion_of_a_key_counts_as_missing(tmp_path):
    root = mod_root(tmp_path)
    write(root, "common/national_focus/t.txt", focus_file("focus = { id = TST_f x = 0 y = 0 }"))
    write(
        root,
        "localisation/english/t_l_english.yml",
        'l_english:\n TST_f: "Name"\n TST_f_desc: "The "Desc""\n',
        bom=True,
    )
    mod = Mod(root)
    assert not codes(mod.validate(stage="build"), "missing_localization")
    mod.delete_loc("TST_f")
    assert [f.message for f in codes(mod.validate(stage="build"), "missing_localization")]


# ── Events ──────────────────────────────────────────────────────────


def test_visible_event_without_option_is_an_error():
    event = Event(id="t.1", title="t.1.t", description="t.1.d")
    findings = validate_event(event, namespaces=["t"])
    assert [(f.code, f.severity) for f in findings if f.severity != "info"] == [
        ("missing_event_options", "error")
    ]


def test_hidden_event_needs_no_title_description_or_option():
    event = Event(id="t.1", raw_block="id = t.1\nHidden = yes\nimmediate = { }")
    assert validate_event(event, namespaces=["t"]) == []


def test_visible_event_without_title_warns_unless_a_conditional_title_exists():
    plain = Event(id="t.1", description="d", options=[EventOption(name="a", effect="x = 1")])
    assert [f.code for f in validate_event(plain)] == ["missing_event_title"]
    conditional = Event(
        id="t.1",
        description="d",
        options=[EventOption(name="a", effect="x = 1")],
        raw_block="title = { text = t.1.t trigger = { always = yes } }",
    )
    from hoi4.types import EventText

    conditional.conditional_titles = [EventText(text="t.1.t", trigger="always = yes")]
    assert validate_event(conditional) == []


def test_option_that_only_closes_the_event_is_advice():
    event = Event(id="t.1", title="t", description="d", options=[EventOption(name="t.1.a")])
    assert [(f.code, f.severity) for f in validate_event(event)] == [
        ("event_option_no_effect", "info")
    ]


def test_undeclared_namespace_is_an_error_and_any_declaration_counts(tmp_path, game):
    root = mod_root(tmp_path)
    write(
        root,
        "events/t.txt",
        "add_namespace = first\nadd_namespace = second\n"
        "country_event = { id = second.1 hidden = yes }\n"
        "country_event = { id = GAME_NS.2 hidden = yes }\n"
        "country_event = { id = nowhere.3 hidden = yes }\n",
    )
    findings = codes(
        Mod(root, hoi4_install=game).validate(stage="build"), "undeclared_event_namespace"
    )
    assert [(f.event_id, f.severity) for f in findings] == [("nowhere.3", "error")]


def test_war_declaration_in_immediate_is_advice_read_from_the_tree():
    event = Event(
        id="t.1",
        title="t",
        description="d",
        immediate="# declare_war_on = { }\nif = { limit = { always = yes } DECLARE_WAR_ON = { target = FRA type = annex_everything } }",
        options=[EventOption(name="a", effect="x = 1")],
    )
    assert [(f.code, f.severity) for f in validate_event(event)] == [
        ("war_declaration_in_immediate", "info")
    ]
    commented = Event(
        id="t.1",
        title="t",
        description="d",
        immediate="# declare_war_on = { }",
        options=[EventOption(name="a", effect="x = 1")],
    )
    assert validate_event(commented) == []


# ── Ideas ───────────────────────────────────────────────────────────


def idea_file(category: str) -> str:
    return f"ideas = {{ {category} = {{ TST_idea = {{ modifier = {{ stability_factor = 0.1 }} }} }} }}\n"


def test_misspelled_idea_category_is_an_error(tmp_path):
    root = mod_root(tmp_path)
    write(root, "common/ideas/t.txt", idea_file("contry"))
    findings = codes(Mod(root).validate(stage="build"), "unknown_idea_category")
    assert [(f.idea_id, f.severity) for f in findings] == [("TST_idea", "error")]


@pytest.mark.parametrize("category", ["country", "hidden_ideas", "tank_manufacturer", "my_slot"])
def test_declared_idea_category_or_slot_is_quiet(tmp_path, category):
    root = mod_root(tmp_path)
    write(root, "common/ideas/t.txt", idea_file(category))
    write(
        root,
        "common/idea_tags/t.txt",
        "idea_categories = { my_category = { slot = my_slot } }\n",
    )
    assert not codes(Mod(root).validate(stage="build"), "unknown_idea_category")


def test_idea_without_effect_and_prefixed_picture_are_advice():
    idea = Idea(id="flavour", raw_block="picture = GFX_idea_generic_x\nallowed = { always = no }")
    assert sorted((f.code, f.severity) for f in validate_idea(idea)) == [
        ("idea_without_effect", "info"),
        ("invalid_idea_picture_prefix", "info"),
    ]


def test_country_history_can_assign_a_hidden_idea(tmp_path):
    root = mod_root(tmp_path)
    write(root, "common/ideas/t.txt", idea_file("hidden_ideas"))
    mod = Mod(root)
    mod.create_country("TST", "Test", ideas=["TST_idea"])
    findings = mod.validate(stage="build", advice=True)
    assert not [f for f in findings if f.idea_id == "TST_idea"]


# ── Focus trees ─────────────────────────────────────────────────────


def tree(*focuses: Focus) -> FocusTree:
    return FocusTree(id="t", focuses=list(focuses))


def test_two_visible_focuses_on_one_position_warn():
    findings = validate_focus_tree(tree(Focus(id="A", x=1, y=1), Focus(id="B", x=1, y=1)))
    assert [(f.code, f.focus_id) for f in findings] == [("visual_overlap", "B")]


def test_exclusive_or_conditional_branches_may_share_a_position():
    exclusive = tree(
        Focus(id="A", x=0, y=0, mutually_exclusive=[["B"]]),
        Focus(id="B", x=2, y=0, mutually_exclusive=[["A"]]),
        Focus(id="A2", x=1, y=1, prerequisites=[["A"]]),
        Focus(id="B2", x=1, y=1, prerequisites=[["B"]]),
    )
    assert not codes(validate_focus_tree(exclusive), "visual_overlap")
    conditional = tree(
        Focus(id="A", x=0, y=0, allow_branch="has_dlc = x"),
        Focus(id="B", x=0, y=0, allow_branch="NOT = { has_dlc = x }"),
        Focus(id="B2", x=0, y=1, prerequisites=[["B"]]),
        Focus(id="A2", x=0, y=1, prerequisites=[["A"]]),
    )
    assert not codes(validate_focus_tree(conditional), "visual_overlap")


def test_a_conditional_focus_still_clashes_with_an_always_shown_one():
    findings = validate_focus_tree(
        tree(Focus(id="A", x=0, y=0, allow_branch="has_dlc = x"), Focus(id="B", x=0, y=0))
    )
    assert codes(findings, "visual_overlap")


def test_search_filter_needs_a_sprite(tmp_path):
    focus = Focus(id="A", search_filters=["FOCUS_FILTER_INDUSTRIAL", "FOCUS_FILTER_INDUSTRY"])
    findings = codes(validate_focus_tree(tree(focus)), "unknown_focus_search_filter")
    assert [f.message.split("'")[3] for f in findings] == ["FOCUS_FILTER_INDUSTRIAL"]

    root = mod_root(tmp_path)
    write(
        root,
        "common/national_focus/t.txt",
        focus_file(
            "focus = { id = A x = 0 y = 0 search_filters = { FOCUS_FILTER_MINE FOCUS_FILTER_TYPO } }"
        ),
    )
    write(
        root,
        "interface/filters.gfx",
        'spriteTypes = { spriteType = { name = "GFX_FOCUS_FILTER_MINE" texturefile = "x.dds" } }\n',
    )
    findings = codes(Mod(root).validate(stage="build"), "unknown_focus_search_filter")
    assert ["FOCUS_FILTER_TYPO" in f.message for f in findings] == [True]


# ── Script hints ────────────────────────────────────────────────────


def test_core_effect_at_country_scope_warns_but_not_inside_a_state_scope():
    assert codes(_script_warnings("add_core_of = ROOT"), "country_scope_core_effect")
    assert codes(
        _script_warnings("if = { limit = { always = yes }\n add_core_of = ROOT\n}"),
        "country_scope_core_effect",
    )
    for valid in (
        "every_owned_state = {\n\tadd_core_of = ROOT\n}",
        "random_list = { 50 = { 64 = { add_core_of = ROOT } } }",
        "64 = { add_core_of = ROOT }",
    ):
        assert not codes(_script_warnings(valid), "country_scope_core_effect"), valid
    assert not codes(
        _script_warnings("add_core_of = ROOT", root_scope="STATE"), "country_scope_core_effect"
    )
    assert codes(_script_warnings("GER = { add_core_of = 64 }"), "country_scope_core_effect")


def test_tag_alias_is_a_known_scope(tmp_path, game):
    root = mod_root(tmp_path)
    write(root, "common/national_focus/t.txt", focus_file(
        "focus = { id = GAME_focus x = 0 y = 0 completion_reward = { SOU = { add_stability = 0.1 } ZZZ = { add_stability = 0.1 } } }"
    ))
    findings = codes(Mod(root, hoi4_install=game).validate(stage="build"), "unknown_country_scope")
    assert ["'ZZZ'" in f.message for f in findings] == [True]


# ── Strict localisation ─────────────────────────────────────────────


def test_strict_localisation_reads_names_the_way_the_game_does(tmp_path, game):
    root = mod_root(tmp_path)
    mod = Mod(root, hoi4_install=game)
    mod.create_country("BSK", "Bashkiria")
    mod.create_country("NNN", "Nowhere")
    mod.create_idea("TST_spirit", category="country", modifier={"stability_factor": 0.1})
    mod.set_loc("TST_spirit", "Spirit")
    mod.create_event("t.1", title="t.1.t", options=[EventOption(name="t.1.a", effect="x = 1")])
    # Drop the names create_country wrote, so only the game's keys remain.
    for key in [key for key in mod.all_loc() if key.startswith(("BSK", "NNN"))]:
        mod.delete_loc(key)
    findings = mod.validate(stage="build", strict_localization=True, advice=True)
    missing = {(f.message.split("'")[3], f.severity) for f in findings if "localization" in f.code}
    assert ("BSK", "warning") not in missing  # BSK_fascism counts
    assert ("NNN", "warning") in missing
    assert ("NNN_ADJ", "warning") in missing
    assert ("NNN_DEF", "info") in missing  # the game falls back to the name
    assert ("TST_spirit_desc", "info") in missing
    assert ("t.1.t", "warning") in missing
    assert ("t.1.a", "warning") in missing


def test_plain_text_and_bracket_commands_need_no_key(tmp_path):
    root = mod_root(tmp_path)
    mod = Mod(root)
    mod.create_event(
        "t.1",
        title="[TST_dynamic_title]",
        description="Plain text the game shows as written.",
        options=[EventOption(name="t.1.missing_key", effect="x = 1")],
    )
    findings = codes(
        mod.validate(stage="build", strict_localization=True), "missing_localization"
    )
    assert [f.message.split("'")[3] for f in findings] == ["t.1.missing_key"]


# ── Severity model and suppression ──────────────────────────────────


def test_advice_is_hidden_by_default(tmp_path):
    root = mod_root(tmp_path)
    mod = Mod(root)
    mod.create_event("t.1", options=[EventOption(name="t.1.a")])
    assert not codes(mod.validate(stage="build"), "event_option_no_effect")
    assert codes(mod.validate(stage="build", advice=True), "event_option_no_effect")


def test_suppression_by_code_and_by_object(tmp_path):
    root = mod_root(tmp_path)
    write(
        root,
        "common/national_focus/t.txt",
        focus_file("focus = { id = TST_a x = 0 y = 0 }\nfocus = { id = TST_b x = 1 y = 0 }"),
    )
    mod = Mod(root)

    def missing(**kwargs) -> set[str | None]:
        return {
            f.focus_id
            for f in mod.validate(stage="build", **kwargs)
            if f.code == "missing_localization"
        }

    assert missing() == {"TST_a", "TST_b"}
    assert missing(suppress=["missing_localization:TST_a"]) == {"TST_b"}
    assert missing(suppress=["missing_localization"]) == set()
    assert missing(suppress_warnings=["missing_localization:TST_b"]) == {"TST_a"}
    with pytest.raises(ValueError, match="not a validation code"):
        mod.validate(suppress=["No localization found"])
    with pytest.raises(ValueError, match="error code"):
        mod.validate(suppress=["missing_event_options"])


def test_every_returned_finding_has_its_registered_severity(tmp_path):
    from hoi4.validation import VALIDATION_SEVERITIES

    root = mod_root(tmp_path)
    write(root, "common/ideas/t.txt", idea_file("contry"))
    mod = Mod(root)
    mod.create_idea("SCL_advisor", category="political_advisor", modifier={"x": 1})
    mod.create_event(
        "t.1", options=[EventOption(name="t.1.a", effect="add_ideas = SCL_advisor")]
    )
    findings = mod.validate(stage="build", advice=True, strict_localization=True)
    assert findings
    assert all(f.code and VALIDATION_SEVERITIES[f.code] == f.severity for f in findings)
