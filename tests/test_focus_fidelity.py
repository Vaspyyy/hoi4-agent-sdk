"""Focus files are read from the syntax tree and edited one span at a time."""

from __future__ import annotations

from pathlib import Path

import pytest

from hoi4 import Mod
from hoi4.focus import load_focus_trees, serialize_focus_file
from hoi4.focus_layout import resolve_focus_positions, visual_overlap_issues
from hoi4.types import Focus, FocusTree
from hoi4.validation import validate_focus_tree

ICON_FOCUS = """\
focus_tree = {
	id = TST_tree
	country = { factor = 0 modifier = { add = 10 tag = TST } }
	# A comment that must survive.
	focus = {
		id = TST_a
		icon = { trigger = { has_war = yes } value = GFX_goal_generic_war }
		icon = GFX_goal_generic_peace
		cost = 7.2
		x = 1
		y = 0
		unknown_key = { keep = me }
	}
}
"""

MIXED = """\
@tier_cost = 5
style = { name = keep_me }
shared_focus = {
	id = SH_root
	icon = GFX_goal_generic_construct_civilian
	cost = @tier_cost
	x = 3
	y = 0
	offset = { x = 9 y = 9 trigger = { tag = TST } }
	search_filters = { FOCUS_FILTER_POLITICAL } # trailing comment
}
focus_tree = {
	focus = {
		id = TST_inner_first	# its id comes before the tree's own id
		x = 0
		y = 0
	}
	id = TST_mixed
	country = { factor = 0 modifier = { add = 10 original_tag = TST } }
	default = no
	shared_focus = SH_root
	shared_focus = JF_root
}
joint_focus = {
	id = JF_root
	x = 0 y = 0
	cost = 10
	completion_reward_joint_member = { add_political_power = 10 }
}
shared_focus = {
	id = SH_child
	prerequisite = { focus = SH_root }
	relative_position_id = SH_root
	x = 0
	y = 1
	cost = 10
}
"""


def _write(tmp_path: Path, text: str | bytes, name: str = "TST_focus.txt") -> Path:
    path = tmp_path / "common" / "national_focus" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    return path


def _changed_lines(before: str, after: str) -> list[tuple[str, str]]:
    old, new = before.split("\n"), after.split("\n")
    assert len(old) == len(new), "lines were added or removed"
    return [(a, b) for a, b in zip(old, new) if a != b]


# ── bug 1: one field edit leaves everything else alone ───────────


def test_editing_x_keeps_conditional_icon_and_fractional_cost(tmp_path: Path) -> None:
    path = _write(tmp_path, ICON_FOCUS)
    mod = Mod(tmp_path)

    focus = mod.get_focus("TST_tree", "TST_a")
    assert focus is not None
    assert focus.cost == 7.2
    assert focus.icon == "GFX_goal_generic_peace"
    assert focus.icon_blocks == ["trigger = { has_war = yes } value = GFX_goal_generic_war"]
    assert focus.icon_names() == ["GFX_goal_generic_peace", "GFX_goal_generic_war"]

    mod.update_focus("TST_tree", "TST_a", x=2)
    mod.save()

    assert _changed_lines(ICON_FOCUS, path.read_text()) == [("\t\tx = 1", "\t\tx = 2")]


def test_fractional_cost_edit_writes_only_the_cost(tmp_path: Path) -> None:
    path = _write(tmp_path, ICON_FOCUS)
    mod = Mod(tmp_path)
    mod.update_focus("TST_tree", "TST_a", cost=7.5)
    mod.save()
    assert _changed_lines(ICON_FOCUS, path.read_text()) == [("\t\tcost = 7.2", "\t\tcost = 7.5")]


def test_unchanged_spelling_is_kept(tmp_path: Path) -> None:
    text = ICON_FOCUS.replace("cost = 7.2", "cost = 7.20").replace("x = 1", "x = 01")
    path = _write(tmp_path, text)
    mod = Mod(tmp_path)
    mod.update_focus("TST_tree", "TST_a", y=3)
    mod.save()
    assert _changed_lines(text, path.read_text()) == [("\t\ty = 0", "\t\ty = 3")]


def test_plain_icon_edit_keeps_icon_blocks(tmp_path: Path) -> None:
    path = _write(tmp_path, ICON_FOCUS)
    mod = Mod(tmp_path)
    mod.update_focus("TST_tree", "TST_a", icon="GFX_goal_generic_trade")
    mod.save()
    assert _changed_lines(ICON_FOCUS, path.read_text()) == [
        ("\t\ticon = GFX_goal_generic_peace", "\t\ticon = GFX_goal_generic_trade")
    ]


def test_icon_block_edit_is_in_place(tmp_path: Path) -> None:
    path = _write(tmp_path, ICON_FOCUS)
    mod = Mod(tmp_path)
    mod.update_focus(
        "TST_tree",
        "TST_a",
        icon_blocks=["trigger = { has_war = no } value = GFX_goal_generic_war"],
    )
    mod.save()
    assert _changed_lines(ICON_FOCUS, path.read_text()) == [
        (
            "\t\ticon = { trigger = { has_war = yes } value = GFX_goal_generic_war }",
            "\t\ticon = { trigger = { has_war = no } value = GFX_goal_generic_war }",
        )
    ]


def test_new_icon_block_goes_before_the_plain_icon(tmp_path: Path) -> None:
    text = ICON_FOCUS.replace(
        "\t\ticon = { trigger = { has_war = yes } value = GFX_goal_generic_war }\n", ""
    )
    path = _write(tmp_path, text)
    mod = Mod(tmp_path)
    mod.update_focus("TST_tree", "TST_a", icon_blocks=["GFX_a = { tag = TST }"])
    mod.save()
    written = path.read_text()
    assert "\t\ticon = { GFX_a = { tag = TST } }\n\t\ticon = GFX_goal_generic_peace\n" in written
    reloaded = Mod(tmp_path).get_focus("TST_tree", "TST_a")
    assert reloaded is not None
    assert reloaded.icon_blocks == ["GFX_a = { tag = TST }"]
    assert reloaded.icon == "GFX_goal_generic_peace"


def test_focus_with_only_icon_blocks(tmp_path: Path) -> None:
    text = """\
focus_tree = {
	id = TST_tree
	focus = {
		id = TST_a
		icon = {
			GFX_focus_one = { tag = TST }
			GFX_focus_two = yes
		}
		x = 0
	}
}
"""
    path = _write(tmp_path, text)
    mod = Mod(tmp_path)
    focus = mod.get_focus("TST_tree", "TST_a")
    assert focus is not None
    assert focus.icon == "GFX_focus_one"
    assert focus.icon_names() == ["GFX_focus_one", "GFX_focus_two"]

    mod.update_focus("TST_tree", "TST_a", x=4)
    mod.save()
    assert _changed_lines(text, path.read_text()) == [("\t\tx = 0", "\t\tx = 4")]

    mod = Mod(tmp_path)
    mod.update_focus("TST_tree", "TST_a", icon="GFX_focus_three")
    mod.save()
    written = path.read_text()
    assert "GFX_focus_one = { tag = TST }" in written
    assert "\t\t}\n\t\ticon = GFX_focus_three\n" in written
    reloaded = Mod(tmp_path).get_focus("TST_tree", "TST_a")
    assert reloaded is not None and reloaded.icon == "GFX_focus_three"


def test_prerequisite_group_edit_is_in_place(tmp_path: Path) -> None:
    text = """\
focus_tree = {
	id = TST_tree
	focus = { id = TST_a x = 0 }
	focus = { id = TST_b x = 1 }
	focus = {
		id = TST_c
		prerequisite = { focus = TST_a } # first group
		prerequisite = {
			focus = TST_b
		}
		x = 2
	}
}
"""
    path = _write(tmp_path, text)
    mod = Mod(tmp_path)
    mod.update_focus("TST_tree", "TST_c", prerequisites=[["TST_a"], ["TST_b", "TST_a"]])
    mod.save()
    written = path.read_text()
    assert "prerequisite = { focus = TST_a } # first group" in written
    assert "\t\tprerequisite = {\n\t\t\tfocus = TST_b\n\t\t\tfocus = TST_a\n\t\t}\n" in written
    reloaded = Mod(tmp_path).get_focus("TST_tree", "TST_c")
    assert reloaded is not None
    assert reloaded.prerequisites == [["TST_a"], ["TST_b", "TST_a"]]


def test_reading_ignores_nested_and_commented_keys(tmp_path: Path) -> None:
    text = """\
focus_tree = {
	id = TST_tree
	focus = {
		# id = TST_commented
		id = TST_a
		offset = { x = 9 y = 9 trigger = { always = no } }
		x = 2
		y = 3
		completion_reward = { will_lead_to_war_with = GER icon = GFX_nested cost = 99 }
		cost = 4
	}
}
"""
    _write(tmp_path, text)
    focus = Mod(tmp_path).get_focus("TST_tree", "TST_a")
    assert focus is not None
    assert (focus.x, focus.y, focus.cost) == (2, 3, 4)
    assert focus.will_lead_to_war_with == ""
    assert focus.icon == "GFX_goal_generic_construct_civilian"


def test_crlf_and_bom_survive_an_edit(tmp_path: Path) -> None:
    raw = b"\xef\xbb\xbf" + ICON_FOCUS.replace("\n", "\r\n").encode("utf-8")
    path = _write(tmp_path, raw)
    mod = Mod(tmp_path)
    mod.update_focus("TST_tree", "TST_a", x=2)
    mod.save()
    assert path.read_bytes() == raw.replace(b"\t\tx = 1\r\n", b"\t\tx = 2\r\n")


# ── bug 2: the tree's own id ─────────────────────────────────────


def test_tree_id_is_the_trees_own_top_level_id(tmp_path: Path) -> None:
    path = _write(tmp_path, MIXED)
    mod = Mod(tmp_path)
    assert "TST_mixed" in mod.list_focus_trees()
    assert "TST_inner_first" not in mod.list_focus_trees()
    tree = mod.get_focus_tree("TST_mixed")
    assert tree.country_tag == "TST"
    assert tree.default is False
    assert tree.shared_focuses == ["SH_root", "JF_root"]

    mod.update_focus("TST_mixed", "TST_inner_first", x=5)
    mod.save()
    assert _changed_lines(MIXED, path.read_text()) == [
        (
            "\t\tx = 0",
            "\t\tx = 5",
        )
    ]


# ── bug 3: shared_focus and joint_focus blocks ───────────────────


def test_top_level_shared_and_joint_focuses_are_loaded(tmp_path: Path) -> None:
    _write(tmp_path, MIXED)
    mod = Mod(tmp_path)
    assert sorted(mod.list_focus_trees()) == ["TST_focus", "TST_mixed"]

    container = mod.get_focus_tree("TST_focus")
    assert container.kind == "file"
    assert [(f.id, f.kind) for f in container.focuses] == [
        ("SH_root", "shared_focus"),
        ("JF_root", "joint_focus"),
        ("SH_child", "shared_focus"),
    ]
    root = container.focuses[0]
    assert root.cost == 5  # resolved from @tier_cost
    assert (root.x, root.y) == (3, 0)  # not the nested offset
    assert root.search_filters == ["FOCUS_FILTER_POLITICAL"]
    assert container.focuses[2].prerequisites == [["SH_root"]]
    assert mod.get_focus_tree("TST_mixed").kind == "focus_tree"


def test_dirty_mixed_file_is_byte_identical(tmp_path: Path) -> None:
    path = _write(tmp_path, MIXED)
    mod = Mod(tmp_path)
    for tree_id in mod.list_focus_trees():
        tree = mod.get_focus_tree(tree_id)
        for focus in tree.focuses:
            mod.update_focus(tree_id, focus.id, x=focus.x + 1)
            mod.update_focus(tree_id, focus.id, x=focus.x - 1)
            mod.update_focus(tree_id, focus.id, cost=focus.cost)
        mod.update_focus_tree(tree_id)
    mod.save()
    assert path.read_bytes() == MIXED.encode("utf-8")


def test_editing_a_shared_focus_changes_only_its_value(tmp_path: Path) -> None:
    path = _write(tmp_path, MIXED)
    mod = Mod(tmp_path)
    mod.update_focus("TST_focus", "SH_root", x=4)
    mod.save()
    assert _changed_lines(MIXED, path.read_text()) == [("\tx = 3", "\tx = 4")]
    assert "cost = @tier_cost" in path.read_text()


def test_joint_focus_edit_on_shared_line(tmp_path: Path) -> None:
    path = _write(tmp_path, MIXED)
    mod = Mod(tmp_path)
    mod.update_focus("TST_focus", "JF_root", y=2)
    mod.save()
    assert _changed_lines(MIXED, path.read_text()) == [("\tx = 0 y = 0", "\tx = 0 y = 2")]


def test_add_remove_and_rekind_top_level_focuses(tmp_path: Path) -> None:
    path = _write(tmp_path, MIXED)
    mod = Mod(tmp_path)
    assert mod.remove_focus("TST_focus", "SH_child")
    mod.add_focus("TST_focus", Focus(id="SH_new", kind="shared_focus", x=1, y=2))
    mod.update_focus("TST_focus", "JF_root", kind="shared_focus")
    mod.save()
    written = path.read_text()
    assert "SH_child" not in written
    assert "joint_focus = {" not in written
    assert "shared_focus = {\n\tid = JF_root\n" in written
    assert written.endswith(
        "shared_focus = {\n\tid = SH_new\n\ticon = GFX_goal_generic_construct_civilian\n"
        "\tx = 1\n\ty = 2\n\tcost = 10\n}\n"
    )
    assert written.startswith("@tier_cost = 5\nstyle = { name = keep_me }\n")

    reloaded = Mod(tmp_path).get_focus_tree("TST_focus")
    assert [(f.id, f.kind) for f in reloaded.focuses] == [
        ("SH_root", "shared_focus"),
        ("JF_root", "shared_focus"),
        ("SH_new", "shared_focus"),
    ]


def test_shared_focus_references_are_edited_in_place(tmp_path: Path) -> None:
    path = _write(tmp_path, MIXED)
    mod = Mod(tmp_path)
    mod.update_focus_tree("TST_mixed", shared_focuses=["SH_root", "SH_other", "JF_root"])
    mod.save()
    assert path.read_text() == MIXED.replace(
        "\tshared_focus = JF_root\n", "\tshared_focus = SH_other\n\tshared_focus = JF_root\n"
    )
    tree = Mod(tmp_path).get_focus_tree("TST_mixed")
    assert tree.shared_focuses == ["SH_root", "SH_other", "JF_root"]


def test_deleting_the_container_removes_only_top_level_focus_blocks(tmp_path: Path) -> None:
    path = _write(tmp_path, MIXED)
    mod = Mod(tmp_path)
    assert mod.delete_focus_tree("TST_focus")
    mod.save()
    written = path.read_text()
    assert "SH_root = " not in written and "id = SH_root" not in written
    assert "id = JF_root" not in written
    assert "style = { name = keep_me }" in written
    assert "focus_tree = {" in written and "shared_focus = SH_root" in written


def test_container_kind_rules(tmp_path: Path) -> None:
    _write(tmp_path, MIXED)
    mod = Mod(tmp_path)
    with pytest.raises(ValueError, match="not a focus_tree"):
        mod.update_focus_tree("TST_focus", default=True)
    with pytest.raises(ValueError, match="not allowed"):
        mod.add_focus("TST_mixed", Focus(id="TST_x", kind="shared_focus"))
    with pytest.raises(ValueError, match="not allowed"):
        mod.update_focus("TST_focus", "SH_root", kind="national_focus")
    with pytest.raises(ValueError, match="kind"):
        mod.update_focus_tree("TST_mixed", kind="file")


def test_upsert_keeps_the_block_kind(tmp_path: Path) -> None:
    _write(tmp_path, MIXED)
    mod = Mod(tmp_path)
    mod.upsert_focus("TST_focus", Focus(id="SH_root", x=7))
    focus = mod.get_focus("TST_focus", "SH_root")
    assert focus is not None and focus.kind == "shared_focus" and focus.x == 7


def test_inherited_file_with_stray_brace_round_trips() -> None:
    text = MIXED + "}\n"
    trees = load_focus_trees(Path("broken.txt"), text=text)
    for tree in trees:
        tree.touched = True
        for focus in tree.focuses:
            focus.touched = True
    assert serialize_focus_file(trees, text) == text


# ── validation and layout ────────────────────────────────────────


def test_container_has_no_shared_canvas() -> None:
    container = FocusTree(
        id="shared",
        kind="file",
        focuses=[
            Focus(id="A", kind="shared_focus", x=0, y=0),
            Focus(id="B", kind="shared_focus", x=0, y=0),
            Focus(id="C", kind="shared_focus", relative_position_id="TREE_anchor"),
        ],
    )
    layout = resolve_focus_positions(container)
    assert visual_overlap_issues(container, layout) == []
    messages = [e.message for e in validate_focus_tree(container, known_focus_ids={"TREE_anchor"})]
    assert not any("shares position" in m or "unresolved" in m for m in messages)
    messages = [e.message for e in validate_focus_tree(container)]
    assert any("unresolved" in m for m in messages)


def test_focus_kind_inside_a_tree_is_validated() -> None:
    tree = FocusTree(id="t", focuses=[Focus(id="A", kind="joint_focus")])
    assert any("kind 'joint_focus'" in e.message for e in validate_focus_tree(tree))


def test_state_scope_validation_reads_the_tree() -> None:
    tree = FocusTree(
        id="t",
        focuses=[
            Focus(
                id="A",
                completion_reward=(
                    "random_list = { 50 = { add_political_power = 1 } }\n"
                    "# 777 = { add_core_of = ROOT }\n"
                    "if = { limit = { always = yes } 999 = { add_core_of = ROOT } }"
                ),
            )
        ],
    )
    unknown = [
        e.state_id
        for e in validate_focus_tree(tree, known_state_ids={1})
        if "unknown state ID" in e.message
    ]
    assert unknown == [999]


def test_continuous_position_check_reads_the_tree() -> None:
    tree = FocusTree(
        id="t",
        focuses=[Focus(id="A", x=0, y=5)],
        continuous_focus_position="# y = 9999\nx = 50 y = 100",
    )
    issues = visual_overlap_issues(tree, resolve_focus_positions(tree))
    assert any("y=100" in issue for issue in issues)


def test_insert_into_one_line_focus_keeps_comment_lines_apart(tmp_path: Path) -> None:
    text = "focus_tree = {\n\tid = T\n\tfocus = { id = A icon = { GFX_x = yes } x = 0 }\n}\n"
    path = _write(tmp_path, text)
    mod = Mod(tmp_path)
    mod.update_focus("T", "A", icon_blocks=["GFX_x = yes", "# chosen at war\nGFX_y = { has_war = yes }"])
    mod.save()
    focus = Mod(tmp_path).get_focus("T", "A")
    assert focus is not None
    assert focus.x == 0
    assert focus.icon_names()[-2:] == ["GFX_x", "GFX_y"]
    assert "focus = { id = A icon = { GFX_x = yes } icon = {" in path.read_text()
