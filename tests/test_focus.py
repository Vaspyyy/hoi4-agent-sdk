from pathlib import Path

from hoi4.focus import load_focus_tree, serialize_focus_tree
from hoi4.types import Focus

FIXTURES = Path(__file__).parent / "fixtures"


class TestLoadFocusTree:
    def test_loads_tree_id(self):
        tree = load_focus_tree(FIXTURES / "GER_focus.txt")
        assert tree.id == "german_focus"

    def test_loads_country_tag(self):
        tree = load_focus_tree(FIXTURES / "GER_focus.txt")
        assert tree.country_tag == "GER"

    def test_loads_all_focuses(self):
        tree = load_focus_tree(FIXTURES / "GER_focus.txt")
        assert len(tree.focuses) == 3

    def test_loads_focus_properties(self):
        tree = load_focus_tree(FIXTURES / "GER_focus.txt")
        anschluss = tree.focuses[0]
        assert anschluss.id == "GER_anschluss"
        assert anschluss.icon == "GFX_goal_anschluss"
        assert anschluss.x == 5
        assert anschluss.y == 3
        assert anschluss.cost == 10

    def test_loads_single_prerequisite(self):
        tree = load_focus_tree(FIXTURES / "GER_focus.txt")
        anschluss = tree.focuses[0]
        assert anschluss.prerequisites == [["GER_rhineland"]]

    def test_loads_multiple_prerequisite_groups(self):
        tree = load_focus_tree(FIXTURES / "GER_focus.txt")
        danzig = tree.focuses[2]
        assert danzig.id == "GER_danzig"
        assert len(danzig.prerequisites) == 2
        assert danzig.prerequisites[0] == ["GER_anschluss"]
        assert danzig.prerequisites[1] == ["GER_other_path"]

    def test_loads_mutually_exclusive(self):
        tree = load_focus_tree(FIXTURES / "GER_focus.txt")
        danzig = tree.focuses[2]
        assert danzig.mutually_exclusive == [["GER_peace"]]

    def test_loads_completion_reward(self):
        tree = load_focus_tree(FIXTURES / "GER_focus.txt")
        anschluss = tree.focuses[0]
        assert "add_political_power" in anschluss.completion_reward

    def test_focus_no_prerequisites(self):
        tree = load_focus_tree(FIXTURES / "GER_focus.txt")
        rhineland = tree.focuses[1]
        assert rhineland.id == "GER_rhineland"
        assert rhineland.prerequisites == []

    def test_ignores_commented_focus_template(self, tmp_path):
        p = tmp_path / "CAN_focus.txt"
        p.write_text(
            """
focus_tree = {
    id = canadian_focus
    country = { modifier = { tag = CAN } }

#   focus = {
#       id = CAN_template
#       completion_reward = {
#   }

    focus = {
        id = CAN_real_focus
        icon = GFX_goal_generic_consumer_goods
        x = 0
        y = 0
        relative_position_id = CAN_anchor
        search_filters = { FOCUS_FILTER_POLITICAL FOCUS_FILTER_INDUSTRY }
        bypass = { has_war = yes }
        cancel_if_invalid = yes
        continue_if_invalid = no
        available_if_capitulated = yes
        completion_reward = { add_political_power = 120 }
    }
}
""",
            encoding="utf-8",
        )
        tree = load_focus_tree(p)
        assert len(tree.focuses) == 1
        focus = tree.focuses[0]
        assert focus.id == "CAN_real_focus"
        assert focus.relative_position_id == "CAN_anchor"
        assert focus.search_filters == ["FOCUS_FILTER_POLITICAL", "FOCUS_FILTER_INDUSTRY"]
        assert focus.bypass == "has_war = yes"
        assert focus.cancel_if_invalid is True
        assert focus.continue_if_invalid is False
        assert focus.available_if_capitulated is True


class TestSerializeFocusTree:
    def test_roundtrip(self):
        tree = load_focus_tree(FIXTURES / "GER_focus.txt")
        result = serialize_focus_tree(tree)
        tree2 = load_focus_tree_from_string(result)
        assert len(tree2.focuses) == len(tree.focuses)
        for f1, f2 in zip(tree.focuses, tree2.focuses):
            assert f1.id == f2.id
            assert f1.x == f2.x
            assert f1.y == f2.y
            assert f1.prerequisites == f2.prerequisites

    def test_serialize_new_focus(self):
        from hoi4.types import FocusTree

        tree = FocusTree(id="test_tree", country_tag="TST")
        tree.focuses.append(Focus(id="TST_focus_a", x=1, y=1))
        tree.focuses.append(Focus(id="TST_focus_b", x=2, y=2, prerequisites=[["TST_focus_a"]]))

        text = serialize_focus_tree(tree)
        assert "focus_tree = {" in text
        assert "id = test_tree" in text
        assert "tag = TST" in text
        assert "id = TST_focus_a" in text
        assert "id = TST_focus_b" in text
        assert "focus = TST_focus_a" in text


def load_focus_tree_from_string(text: str):
    import tempfile
    from hoi4.focus import load_focus_tree

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(text)
        f.flush()
        return load_focus_tree(Path(f.name))
