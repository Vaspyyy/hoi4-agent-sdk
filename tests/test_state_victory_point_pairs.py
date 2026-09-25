import pytest

from hoi4 import Mod
from hoi4.parser import parse_pdx
from hoi4.states import read_state, serialize_state
from hoi4.types import State


def point_pairs(text):
    history = parse_pdx(text).find("state").find("history")
    return [
        [node.value for node in block.children if not node.is_comment]
        for block in history.find_all("victory_points")
    ]


def test_new_state_writes_one_block_per_pair():
    state = State(id=1, provinces=[100, 200], victory_points="100 5 200 2")
    assert point_pairs(serialize_state(state)) == [["100", "5"], ["200", "2"]]
    state.victory_points = "100 5 200"
    with pytest.raises(ValueError, match="province/value pairs"):
        serialize_state(state)


def test_repeated_points_survive_history_rebuild_save_reload(tmp_path):
    path = tmp_path / "history/states/1-Test.txt"
    path.parent.mkdir(parents=True)
    path.write_text(
        "state = { id = 1 provinces = { 100 200 } history = { owner = AAA victory_points = { 100 5 } victory_points = { 200 2 } } }"
    )
    mod = Mod(tmp_path)
    state = mod.get_state(1)
    assert state.victory_points == "100 5 200 2"
    mod.set_state_properties(1, history="", owner="BBB")
    result = mod.save(require_changes=True)
    assert result.written_files == [path]
    assert point_pairs(path.read_text()) == [["100", "5"], ["200", "2"]]
    assert mod.get_state(1).victory_points == "100 5 200 2"
    assert mod.get_state(1).owner == "BBB"


def test_same_value_facade_update_repairs_flattened_block_without_history_reset(tmp_path):
    path = tmp_path / "history/states/1-Test.txt"
    path.parent.mkdir(parents=True)
    untouched = "\t1939.1.1 = { owner = CCC mystery = { nested = yes } } # later history\n"
    path.write_text(
        "state = { id = 1 provinces = { 100 200 } history = {\n\towner = AAA\n\t# keep this comment\n\tvictory_points = { 100 5 200 2 }\n"
        + untouched
        + "\tbuildings = { infrastructure = 3 }\n} }"
    )
    mod = Mod(tmp_path)
    mod.set_state_properties(1, victory_points=mod.get_state(1).victory_points)
    mod.save(require_changes=True)
    text = path.read_text()
    assert point_pairs(text) == [["100", "5"], ["200", "2"]]
    assert untouched in text and "# keep this comment" in text
    assert "buildings = { infrastructure = 3 }" in text


def test_owner_edit_preserves_valid_victory_point_comments_exactly(tmp_path):
    path = tmp_path / "state.txt"
    points = "victory_points = { 100 5 # first city\n }\n victory_points = { 200 2 }"
    path.write_text("state = { id = 1 history = { owner = AAA " + points + " } }")
    state = read_state(path)
    state.owner = "BBB"
    assert points in serialize_state(state)
    state.victory_points = "100 5 200 3"
    text = serialize_state(state)
    assert "victory_points = { 100 5 # first city\n }" in text
    assert point_pairs(text) == [["100", "5"], ["200", "3"]]
