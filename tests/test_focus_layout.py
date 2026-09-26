"""Relative anchors must describe the same static layout in every public check."""

import pytest

from hoi4 import Focus, Mod
from hoi4.focus_layout import resolve_focus_positions
from hoi4.types import FocusTree
from hoi4.validation import validate_focus_tree


def make_tree(tmp_path, focuses):
    mod = Mod(tmp_path)
    mod.create_focus_tree("layout", "GER")
    for focus in focuses:
        mod.add_focus("layout", focus)
    return mod


def chain():
    # Deliberately put children before their anchors.
    return [
        Focus(id="C", x=0, y=1, relative_position_id="B"),
        Focus(id="B", x=0, y=1, relative_position_id="A"),
        Focus(id="A", x=5, y=5),
    ]


def test_relative_chain_bounds_placement_and_round_trip(tmp_path):
    mod = make_tree(tmp_path, chain())
    assert mod.focus_tree_bounds("layout") == {
        "min_x": 5,
        "max_x": 5,
        "min_y": 5,
        "max_y": 7,
        "width": 1,
        "height": 3,
    }
    assert mod.place_continuous_focus_below_tree("layout") == "x = 50 y = 1200"
    assert mod.assert_no_visual_overlap("layout")
    assert not validate_focus_tree(mod.get_focus_tree("layout"))
    errors = mod.validate()
    assert not any(e.severity == "error" for e in errors)
    assert not any(e.code in {"visual_overlap", "unresolved_focus_position"} for e in errors)
    print(mod.preview_summary())
    print(mod.preview())
    result = mod.save(require_changes=True)
    print(result)
    print(result.written_files)
    loaded = Mod(tmp_path)
    assert [
        (f.id, f.x, f.y, f.relative_position_id) for f in loaded.get_focus_tree("layout").focuses
    ] == [("C", 0, 1, "B"), ("B", 0, 1, "A"), ("A", 5, 5, "")]
    assert loaded.focus_tree_bounds("layout") == mod.focus_tree_bounds("layout")


def test_relative_absolute_collision_and_continuous_padding(tmp_path):
    mod = make_tree(tmp_path, chain())
    mod.update_focus_tree("layout", continuous_focus_position="x = 50 y = 800")
    with pytest.raises(ValueError, match="minimum y=900"):
        mod.assert_no_visual_overlap("layout")
    mod.place_continuous_focus_below_tree("layout")
    mod.add_focus("layout", Focus(id="D", x=5, y=6))
    with pytest.raises(ValueError, match="D overlaps B at x=5, y=6"):
        mod.assert_no_visual_overlap("layout")
    assert any(
        "shares position (5, 6)" in e.message
        for e in validate_focus_tree(mod.get_focus_tree("layout"))
    )
    assert any(e.code == "visual_overlap" for e in mod.validate())


@pytest.mark.parametrize(
    "focuses, diagnostic",
    [
        (
            [Focus(id="A", relative_position_id="SHARED"), Focus(id="B", relative_position_id="A")],
            "anchor 'SHARED'",
        ),
        (
            [
                Focus(id="A", relative_position_id="B"),
                Focus(id="B", relative_position_id="A"),
                Focus(id="C", relative_position_id="B"),
            ],
            "cycle",
        ),
        ([Focus(id="A", relative_position_id="A")], "A -> A"),
    ],
)
def test_unresolved_layout_diagnostics(tmp_path, focuses, diagnostic):
    mod = make_tree(tmp_path, focuses)
    for helper in (
        mod.focus_tree_bounds,
        mod.place_continuous_focus_below_tree,
        mod.assert_no_visual_overlap,
    ):
        with pytest.raises(ValueError, match=diagnostic):
            helper("layout")
    assert mod.get_focus_tree("layout").continuous_focus_position == ""
    # Merely knowing an ID in another tree does not provide its position.
    errors = validate_focus_tree(mod.get_focus_tree("layout"), known_focus_ids={"SHARED"})
    unresolved = [e for e in errors if e.code == "unresolved_focus_position"]
    assert {e.focus_id for e in unresolved} == {f.id for f in focuses}
    assert all(diagnostic in e.message for e in unresolved)
    assert not any("shares position" in e.message for e in errors)
    errors = mod.validate()
    assert any(e.code == "unresolved_focus_position" for e in errors)
    assert not any(e.code == "visual_overlap" for e in errors)
    mod.add_focus("layout", Focus(id="X", x=9, y=9))
    mod.add_focus("layout", Focus(id="Y", x=9, y=9))
    errors = mod.validate()
    assert any(e.code == "unresolved_focus_position" for e in errors)
    overlaps = [e for e in errors if e.code == "visual_overlap"]
    assert len(overlaps) == 1
    assert "Y overlaps X" in overlaps[0].message
    assert "unresolved" not in overlaps[0].message
    assert "relative_position_id" not in overlaps[0].message


def test_absolute_and_empty_layouts(tmp_path):
    mod = make_tree(tmp_path, [])
    assert mod.focus_tree_bounds("layout") == dict.fromkeys(
        ["min_x", "max_x", "min_y", "max_y", "width", "height"], 0
    )
    assert mod.assert_no_visual_overlap("layout")
    mod.add_focus("layout", Focus(id="A", x=-3, y=-2))
    mod.add_focus("layout", Focus(id="B", x=2, y=4))
    assert mod.focus_tree_bounds("layout") == {
        "min_x": -3,
        "max_x": 2,
        "min_y": -2,
        "max_y": 4,
        "width": 6,
        "height": 7,
    }
    assert mod.place_continuous_focus_below_tree("layout", padding=200, x=-10) == "x = -10 y = 700"
    assert mod.assert_no_visual_overlap("layout")


def test_long_chain_does_not_hit_python_recursion_limit():
    focuses = [Focus(id="F0", x=-2, y=-1)] + [
        Focus(id=f"F{i}", x=1, y=1, relative_position_id=f"F{i - 1}") for i in range(1, 1500)
    ]
    layout = resolve_focus_positions(FocusTree(id="long", focuses=list(reversed(focuses))))
    assert not layout.unresolved
    assert layout.positions["F1499"] == (1497, 1498)
