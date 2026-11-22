"""The per-object content checks fire on real mistakes and stay quiet on valid content.

Each check has a pair of small fixtures: one with the mistake, one with the
valid shape that the game's own files use. A fake game install supplies
inherited files where a rule depends on them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hoi4 import Mod, ReadOnlyError
from hoi4.types import Event, EventOption, Focus, FocusTree, Idea, ValidationError
from hoi4.validation import (
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
        "add_namespace = first\nadd_namespace = second\nadd_namespace = dotted.news\n"
        "country_event = { id = second.1 hidden = yes }\n"
        "country_event = { id = GAME_NS.2 hidden = yes }\n"
        "country_event = { id = nowhere.3 hidden = yes }\n"
        # Like the game's MUN_slovakia.news.1: the namespace of an id is
        # everything before its last dot, so "first" does not declare
        # "first.more.5" (HOI4 wiki, Event modding).
        "country_event = { id = dotted.news.4 hidden = yes }\n"
        "country_event = { id = DOTTED.News.7 hidden = yes }\n"
        "country_event = { id = first.more.5 hidden = yes }\n"
        "country_event = { id = nowhere.news.6 hidden = yes }\n",
    )
    findings = codes(
        Mod(root, hoi4_install=game).validate(stage="build"), "undeclared_event_namespace"
    )
    assert [(f.event_id, f.severity) for f in findings] == [
        ("nowhere.3", "error"),
        ("first.more.5", "error"),
        ("nowhere.news.6", "error"),
    ]
    assert "'first.more'" in findings[1].message
    assert "'nowhere.news'" in findings[2].message


def test_a_new_event_declares_the_namespace_up_to_its_last_dot(tmp_path, game):
    mod = Mod(mod_root(tmp_path), hoi4_install=game)
    mod.create_event("my_mod.news.1", title="t", description="d")
    mod.create_event("my_mod.2", title="t", description="d")
    assert not codes(mod.validate(stage="build"), "undeclared_event_namespace")
    mod.save()
    saved = (mod.mod_root / "events" / "my_mod.news_events.txt").read_text()
    assert "add_namespace = my_mod.news\n" in saved
    assert "add_namespace = my_mod\n" in (mod.mod_root / "events" / "my_mod_events.txt").read_text()
    reloaded = Mod(mod.mod_root, hoi4_install=game)
    assert sorted(reloaded.list_events()) == ["my_mod.2", "my_mod.news.1"]
    assert not codes(reloaded.validate(stage="build"), "undeclared_event_namespace")
    reloaded.set_event_namespace("my_mod.2", "other.topic")
    with pytest.raises(ValueError, match="Invalid event namespace"):
        reloaded.set_event_namespace("my_mod.2", "other..topic")


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


def test_variant_branches_behind_their_own_allow_branch_may_share_a_position():
    conditional = tree(
        Focus(id="A", x=0, y=0, allow_branch="has_dlc = x"),
        Focus(id="B", x=0, y=0, allow_branch="NOT = { has_dlc = x }"),
        Focus(id="B2", x=0, y=1, prerequisites=[["B"]]),
        Focus(id="A2", x=0, y=1, prerequisites=[["A"]]),
    )
    assert not codes(validate_focus_tree(conditional), "visual_overlap")


def test_mutually_exclusive_branches_are_both_drawn_so_they_clash():
    """The game draws both sides of an exclusive choice; the player picks one."""

    exclusive = tree(
        Focus(id="A", x=0, y=0, mutually_exclusive=[["B"]]),
        Focus(id="B", x=2, y=0, mutually_exclusive=[["A"]]),
        Focus(id="A2", x=1, y=1, prerequisites=[["A"]]),
        Focus(id="B2", x=1, y=1, prerequisites=[["B"]]),
    )
    findings = codes(validate_focus_tree(exclusive), "visual_overlap")
    assert [f.focus_id for f in findings] == ["B2"]


def test_two_children_of_one_allow_branch_root_clash():
    """Both hang on the same condition, so they are shown together."""

    findings = validate_focus_tree(
        tree(
            Focus(id="plt_ab_root", x=4, y=0, allow_branch="always = yes"),
            Focus(id="plt_ab_x", x=4, y=1, prerequisites=[["plt_ab_root"]]),
            Focus(id="plt_ab_y", x=4, y=1, prerequisites=[["plt_ab_root"]]),
        )
    )
    assert [f.focus_id for f in codes(findings, "visual_overlap")] == ["plt_ab_y"]


def test_children_of_a_join_after_an_exclusive_choice_clash():
    """An "either" prerequisite over an exclusive pair does not hide anything."""

    findings = validate_focus_tree(
        tree(
            Focus(id="plt_me_a", x=7, y=0, mutually_exclusive=[["plt_me_b"]]),
            Focus(id="plt_me_b", x=9, y=0, mutually_exclusive=[["plt_me_a"]]),
            Focus(id="plt_join", x=8, y=1, prerequisites=[["plt_me_a", "plt_me_b"]]),
            Focus(id="plt_jx", x=8, y=2, prerequisites=[["plt_join"]]),
            Focus(id="plt_jy", x=8, y=2, prerequisites=[["plt_join"]]),
        )
    )
    assert [f.focus_id for f in codes(findings, "visual_overlap")] == ["plt_jy"]


def test_a_focus_behind_the_condition_of_another_clashes_with_it():
    """Whenever Y is drawn, so is X: both hang on ``has_dlc = x``."""

    findings = validate_focus_tree(
        tree(
            Focus(id="R", x=0, y=0, allow_branch="has_dlc = x"),
            Focus(id="A", x=2, y=0, prerequisites=[["R"]]),
            Focus(id="X", x=1, y=1, prerequisites=[["R"]]),
            Focus(id="Y", x=1, y=1, prerequisites=[["A"]]),
        )
    )
    assert [f.focus_id for f in codes(findings, "visual_overlap")] == ["Y"]


def _gate(focus_id: str, gate: str, **fields) -> Focus:
    """A focus whose ``allow_branch`` block is written as ``gate`` in its file."""

    focus = Focus(id=focus_id, **fields)
    focus.raw_block = f"id = {focus_id}\n{gate}\n"
    return focus


def test_a_focus_with_allow_branch_checks_only_its_own_condition():
    """The wiki: such a focus is "still showing up if it has disallowed parents"."""

    def overlaps(middle: Focus) -> list[str]:
        findings = validate_focus_tree(
            tree(
                Focus(id="R", x=0, y=0, allow_branch="has_dlc = x"),
                middle,
                Focus(id="Y", x=1, y=2, prerequisites=[["A"]]),
                Focus(id="Z", x=1, y=2, allow_branch="has_country_flag = z"),
            )
        )
        return [f.focus_id for f in codes(findings, "visual_overlap")]

    # Without allow_branch, A and Y are hidden with R: Y and Z are variants.
    assert overlaps(Focus(id="A", x=0, y=1, prerequisites=[["R"]])) == []
    # With one, A does not take R's condition. An empty block, a block with
    # only a comment and "always = yes" hold always, so Y is always drawn
    # and meets Z whenever Z appears.
    for gate in (
        "allow_branch = { }",
        "ALLOW_BRANCH = { # nothing yet\n }",
        "allow_branch = { always = yes }",
    ):
        middle = _gate("A", gate, x=0, y=1, prerequisites=[["R"]])
        if "always" in gate:
            middle.allow_branch = "always = yes"
        elif "#" in gate:
            middle.allow_branch = "# nothing yet"
        assert overlaps(middle) == ["Z"], gate
    # Its own condition is the only one that counts: with Z's condition, A
    # and Y appear exactly when Z does.
    same = Focus(id="A", x=0, y=1, prerequisites=[["R"]], allow_branch="has_country_flag = z")
    assert overlaps(same) == ["Z"]


def test_either_prerequisites_that_are_all_conditional_keep_a_focus_conditional():
    """Like the game's Sinkiang branch: every way in passes ``tag = SIK``."""

    findings = validate_focus_tree(
        tree(
            Focus(id="S1", x=0, y=0, allow_branch="tag = SIK"),
            Focus(id="S2", x=2, y=0, allow_branch="tag = SIK"),
            Focus(id="S3", x=1, y=1, prerequisites=[["S1", "S2"]]),
            Focus(id="X1", x=5, y=0, allow_branch="tag = XSM"),
            Focus(id="X2", x=1, y=1, prerequisites=[["X1"]]),
        )
    )
    assert not codes(findings, "visual_overlap")


def test_a_focus_with_an_offset_block_may_share_its_base_position():
    moved = Focus(id="B", x=0, y=0, prerequisites=[["A"]])
    moved.raw_block = "id = B\nx = 0\ny = 0\noffset = { x = 0 y = 1 trigger = { has_idea = i } }\n"
    findings = validate_focus_tree(
        tree(Focus(id="A", x=0, y=0, allow_branch="has_idea = i"), moved)
    )
    assert not codes(findings, "visual_overlap")


def _stepping_aside(focus_id: str, offset: str = "offset = { x = 4 y = 0") -> Focus:
    focus = Focus(id=focus_id, x=5, y=0)
    focus.raw_block = (
        f"id = {focus_id}\nx = 5\ny = 0\n{offset} trigger = {{ has_idea = i }} }}\n"
    )
    return focus


def test_an_offset_block_moves_the_focuses_placed_relative_to_it():
    # Ethiopia's Italian branch: its first focus steps aside on a trigger, and
    # the focuses anchored to it move with it, away from a branch that only
    # appears then.
    for offset in ("offset = { x = 4 y = 0", "OFFSET = { X = 4"):
        findings = validate_focus_tree(
            tree(
                _stepping_aside("R", offset),
                Focus(id="C", x=0, y=1, relative_position_id="R"),
                Focus(id="D", x=5, y=1, allow_branch="has_idea = i"),
            )
        )
        assert not codes(findings, "visual_overlap")


def test_an_offset_block_does_not_excuse_two_focuses_that_are_always_drawn():
    # While the trigger of the offset does not hold, both sit on one spot.
    findings = validate_focus_tree(
        tree(
            _stepping_aside("R"),
            Focus(id="C", x=0, y=1, relative_position_id="R"),
            Focus(id="D", x=5, y=1),
        )
    )
    assert [f.focus_id for f in codes(findings, "visual_overlap")] == ["D"]


def test_two_branches_behind_the_same_condition_are_shown_together():
    def pair(first: str, second: str) -> list[str]:
        findings = validate_focus_tree(
            tree(
                Focus(id="G1", x=0, y=0, allow_branch=first),
                Focus(id="G2", x=4, y=0, allow_branch=second),
                Focus(id="A", x=2, y=1, prerequisites=[["G1"]]),
                Focus(id="B", x=2, y=1, prerequisites=[["G2"]]),
            )
        )
        return [f.focus_id for f in codes(findings, "visual_overlap")]

    # Two gate focuses, one condition: when it holds, both branches appear.
    assert pair("has_country_flag = f", "has_country_flag  =  f") == ["B"]
    assert pair('has_dlc = "X"', "HAS_DLC = X # the same") == ["B"]
    assert pair('has_dlc = "X"', 'has_dlc = "x"') == []
    assert pair("always = yes", "always = yes") == ["B"]
    assert pair("has_country_flag = f", "NOT = { has_country_flag = f }") == []


def test_a_branch_whose_conditions_the_other_needs_too_is_shown_with_it():
    def overlaps(*focuses: Focus) -> list[str]:
        return [f.focus_id for f in codes(validate_focus_tree(tree(*focuses)), "visual_overlap")]

    # B is hidden with A, C writes A's condition again: both need only X.
    assert overlaps(
        Focus(id="A", x=0, y=0, allow_branch='has_dlc = "X"'),
        Focus(id="B", x=0, y=1, prerequisites=[["A"]]),
        Focus(id="C", x=0, y=1, prerequisites=[["A"]], allow_branch='HAS_DLC="X" # again'),
    ) == ["C"]
    # A focus is hidden as soon as one of its prerequisite focuses is, also
    # inside one "either" block: C needs a and b, D only a. Whenever C is
    # drawn, so is D.
    assert overlaps(
        Focus(id="G1", x=0, y=0, allow_branch="has_country_flag = a"),
        Focus(id="G2", x=4, y=0, allow_branch="has_country_flag = b"),
        Focus(id="C", x=2, y=1, prerequisites=[["G1", "G2"]]),
        Focus(id="D", x=2, y=1, prerequisites=[["G1"]]),
    ) == ["D"]
    # The same with two prerequisite blocks.
    assert overlaps(
        Focus(id="G1", x=0, y=0, allow_branch="has_country_flag = a"),
        Focus(id="G2", x=4, y=0, allow_branch="has_country_flag = b"),
        Focus(id="C", x=2, y=1, prerequisites=[["G1"], ["G2"]]),
        Focus(id="D", x=2, y=1, prerequisites=[["G2"]]),
    ) == ["D"]
    # Each of the two needs a condition the other does not need.
    assert overlaps(
        Focus(id="G1", x=0, y=0, allow_branch="has_country_flag = a"),
        Focus(id="G2", x=4, y=0, allow_branch="has_country_flag = b"),
        Focus(id="C", x=2, y=1, prerequisites=[["G1"]]),
        Focus(id="D", x=2, y=1, prerequisites=[["G2"]]),
    ) == []


def _focus_file(*focuses: str) -> str:
    body = "\n".join(f"\tfocus = {{ {focus} cost = 5 }}" for focus in focuses)
    return f"focus_tree = {{\n\tid = test_tree\n\tcountry = {{ factor = 0 }}\n{body}\n}}\n"


def test_allow_branch_cleared_through_the_api_counts_before_save(tmp_path):
    """Before save() and after it, the same answer."""

    root = mod_root(tmp_path)
    write(
        root,
        "common/national_focus/t.txt",
        _focus_file(
            'id = A x = 0 y = 0 allow_branch = { has_dlc = "X" }',
            'id = B x = 0 y = 1 prerequisite = { focus = A } allow_branch = { has_dlc = "Y" }',
            'id = D x = 0 y = 1 allow_branch = { has_dlc = "Z" }',
            # An empty block is kept by save(): E checks only itself.
            "id = E x = 5 y = 1 prerequisite = { focus = A } allow_branch = { }",
            'id = F x = 5 y = 1 allow_branch = { has_dlc = "Z" }',
        ),
    )

    def overlaps(mod: Mod) -> list[str]:
        return [f.focus_id for f in codes(mod.validate(stage="build"), "visual_overlap")]

    mod = Mod(root)
    assert overlaps(mod) == ["F"]
    # Without its own allow_branch, B is hidden with A: B hangs on X, D on Z.
    mod.update_focus("test_tree", "B", allow_branch="")
    mod.update_focus("test_tree", "E", cost=7)
    assert overlaps(mod) == ["F"]
    mod.save()
    saved = (root / "common/national_focus/t.txt").read_text()
    assert 'has_dlc = "Y"' not in saved and "allow_branch = { }" in saved
    assert overlaps(Mod(root)) == ["F"]


@pytest.mark.parametrize("where", ["mod", "base"])
def test_a_focus_below_a_shared_focus_of_another_file_is_hidden_with_it(tmp_path, where):
    """The parent's ``allow_branch`` counts wherever the parent is defined."""

    root = mod_root(tmp_path)
    base = tmp_path / "base"
    shared_root = root if where == "mod" else base

    def shared(root_gate: str) -> None:
        write(
            shared_root,
            "common/national_focus/s.txt",
            f"shared_focus = {{ id = S_root x = 0 y = 0 cost = 1 {root_gate} }}\n"
            "shared_focus = { id = S_child x = 0 y = 1 cost = 1"
            " prerequisite = { focus = S_root } }\n",
        )

    def tree(parent: str, other_gate: str) -> None:
        write(
            root,
            "common/national_focus/t.txt",
            "focus_tree = {\n\tid = t_tree\n\tcountry = { factor = 0 }\n"
            "\tshared_focus = S_root\n"
            f"\tfocus = {{ id = F x = 5 y = 5 cost = 1 prerequisite = {{ focus = {parent} }} }}\n"
            f"\tfocus = {{ id = H x = 5 y = 5 cost = 1 {other_gate} }}\n"
            "}\n",
        )

    def overlaps() -> list[str]:
        mod = Mod(root, base_mod_paths=[base] if where == "base" else [])
        found = [f.focus_id for f in codes(mod.validate(stage="build"), "visual_overlap")]
        if found:
            with pytest.raises(ValueError, match="overlaps"):
                mod.assert_no_visual_overlap("t_tree")
        else:
            assert mod.assert_no_visual_overlap("t_tree")
        return found

    gate_x, gate_y = 'allow_branch = { has_dlc = "X" }', 'allow_branch = { has_dlc = "Y" }'
    shared(gate_x)
    # F is hidden with S_root (X), H hangs on Y: two variants.
    for parent in ("S_root", "S_child"):
        tree(parent, gate_y)
        assert overlaps() == [], parent
        # H behind the same condition as S_root appears together with F.
        tree(parent, gate_x)
        assert overlaps() == ["H"], parent
    # A shared parent that is always drawn hides nothing.
    shared("")
    tree("S_root", gate_y)
    assert overlaps() == ["H"]


def test_allow_branch_is_read_in_any_case(tmp_path):
    root = mod_root(tmp_path)
    write(
        root,
        "common/national_focus/t.txt",
        _focus_file(
            'id = A x = 0 y = 0 ALLOW_BRANCH = { has_dlc = "X" }',
            'id = B x = 0 y = 0 Allow_Branch = { has_dlc = "Y" }',
            'id = C x = 3 y = 0 ALLOW_BRANCH = { has_dlc = "X" }',
            "id = D x = 3 y = 0 allow_branch = { HAS_DLC = X }",
            "id = E x = 6 y = 0 allow_branch = { always = YES }",
            'id = F x = 6 y = 0 allow_branch = { has_dlc = "X" }',
        ),
    )
    findings = codes(Mod(root).validate(stage="build"), "visual_overlap")
    assert [f.focus_id for f in findings] == ["D", "F"]


def test_an_offset_block_that_moves_nothing_excuses_nothing():
    findings = validate_focus_tree(
        tree(
            _stepping_aside("R", "offset = { x = 0 y = 0"),
            Focus(id="C", x=0, y=1, relative_position_id="R"),
            Focus(id="D", x=5, y=1, allow_branch="has_idea = i"),
        )
    )
    assert [f.focus_id for f in codes(findings, "visual_overlap")] == ["D"]


def test_two_focuses_moved_by_the_same_offset_block_still_overlap():
    findings = validate_focus_tree(
        tree(
            _stepping_aside("R"),
            Focus(id="C", x=0, y=1, relative_position_id="R"),
            Focus(id="D", x=0, y=1, relative_position_id="R"),
        )
    )
    assert [f.focus_id for f in codes(findings, "visual_overlap")] == ["D"]


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


def test_core_effect_at_country_scope_warns_but_not_inside_a_state_scope(tmp_path):
    mod = Mod(mod_root(tmp_path))

    def core(script: str, **kwargs: str) -> list:
        return codes(mod.validate_effect(script, **kwargs), "country_scope_core_effect")

    assert core("add_core_of = ROOT")
    assert core("if = { limit = { always = yes }\n add_core_of = ROOT\n}")
    for valid in (
        "every_owned_state = {\n\tadd_core_of = ROOT\n}",
        "random_list = { 50 = { 64 = { add_core_of = ROOT } } }",
        "64 = { add_core_of = ROOT }",
    ):
        assert not core(valid), valid
    assert not core("add_core_of = ROOT", scope="STATE")
    assert core("GER = { add_core_of = 64 }")


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
        try:
            mod.delete_loc(key)
        except ReadOnlyError:
            pass  # the game's own key stays
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


def test_validate_effect_shows_advice_unless_turned_off(tmp_path):
    mod = Mod(mod_root(tmp_path))
    script = "every_country = { add_to_faction = ROOT }"
    shown = codes(mod.validate_effect(script), "faction_scope_footgun")
    assert [finding.severity for finding in shown] == ["info"]
    assert not codes(mod.validate_effect(script, advice=False), "faction_scope_footgun")


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
    assert missing(suppress=["missing_localization:TST_b"]) == {"TST_a"}
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
