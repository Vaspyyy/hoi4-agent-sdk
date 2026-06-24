from pathlib import Path

from hoi4.states import read_state, serialize_state, write_state, patch_state_owner
from hoi4.types import State

FIXTURES = Path(__file__).parent / "fixtures"
STATES_DIR = FIXTURES / "history" / "states"


class TestReadState:
    def test_reads_id(self):
        state = read_state(STATES_DIR / "1 - Berlin.txt")
        assert state.id == 1

    def test_reads_name(self):
        state = read_state(STATES_DIR / "1 - Berlin.txt")
        assert state.name == "STATE_1"

    def test_reads_manpower(self):
        state = read_state(STATES_DIR / "1 - Berlin.txt")
        assert state.manpower == "3200000"

    def test_reads_owner(self):
        state = read_state(STATES_DIR / "1 - Berlin.txt")
        assert state.owner == "GER"

    def test_reads_cores(self):
        state = read_state(STATES_DIR / "1 - Berlin.txt")
        assert "GER" in state.cores
        assert "FRA" in state.cores

    def test_reads_provinces(self):
        state = read_state(STATES_DIR / "1 - Berlin.txt")
        assert 101 in state.provinces
        assert 105 in state.provinces

    def test_reads_victory_points(self):
        state = read_state(STATES_DIR / "1 - Berlin.txt")
        assert "1001" in state.victory_points

    def test_reads_buildings_factor(self):
        state = read_state(STATES_DIR / "1 - Berlin.txt")
        assert state.buildings_max_level_factor == "0.5"

    def test_state_no_victory_points(self):
        state = read_state(STATES_DIR / "2 - Paris.txt")
        assert state.victory_points == ""

    def test_reads_rich_state_fields(self, tmp_path):
        p = tmp_path / "8-Luxemburg.txt"
        p.write_text(RICH_STATE_TEXT, encoding="utf-8")
        state = read_state(p)
        assert state.resources["steel"] == 8
        assert "industrial_complex = 1" in state.buildings
        assert state.local_supplies == "2.0"
        assert "resistance" in state.history


class TestSerializeState:
    def test_roundtrip_id_and_owner(self):
        original = read_state(STATES_DIR / "1 - Berlin.txt")
        text = serialize_state(original)
        restored = read_state_from_string(text)
        assert restored.id == original.id
        assert restored.owner == original.owner

    def test_serialize_new_state(self):
        state = State(id=99, name="STATE_99", owner="TST", cores=["TST"],
                      manpower="500000", provinces=[9001, 9002])
        text = serialize_state(state)
        assert "id = 99" in text
        assert "owner = TST" in text
        assert "9001" in text

    def test_preserves_unknown_rich_state_data_when_owner_changes(self, tmp_path):
        p = tmp_path / "8-Luxemburg.txt"
        p.write_text(RICH_STATE_TEXT, encoding="utf-8")
        state = read_state(p)
        state.owner = "GER"
        if "GER" not in state.cores:
            state.cores.append("GER")
        text = serialize_state(state)
        assert "owner = GER" in text
        assert "resources = {" in text
        assert "steel = 8" in text
        assert "buildings = {" in text
        assert "industrial_complex = 1" in text
        assert "local_supplies = 2.0" in text
        assert "1939.1.1 = {" in text
        assert "resistance = 10" in text


class TestPatchStateOwner:
    def test_changes_owner(self):
        original = (STATES_DIR / "1 - Berlin.txt").read_text()
        patched = patch_state_owner(original, "SOV")
        assert "owner = SOV" in patched

    def test_adds_core(self):
        original = (STATES_DIR / "2 - Paris.txt").read_text()
        patched = patch_state_owner(original, "GER")
        assert "add_core_of = GER" in patched


class TestWriteState:
    def test_writes_file(self, tmp_path):
        state = State(id=50, name="STATE_50", owner="GER", cores=["GER"],
                      manpower="1000", provinces=[500])
        path = write_state(tmp_path, state)
        assert path.exists()
        loaded = read_state(path)
        assert loaded.id == 50
        assert loaded.owner == "GER"


def read_state_from_string(text: str) -> State:
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(text)
        f.flush()
        return read_state(Path(f.name))


RICH_STATE_TEXT = """
state = {
    id = 8
    name = STATE_8
    manpower = 294748
    state_category = town
    local_supplies = 2.0
    resources = {
        steel = 8
    }
    buildings = {
        infrastructure = 3
        industrial_complex = 1
    }
    provinces = { 6583 13375 }
    history = {
        owner = LUX
        add_core_of = LUX
        victory_points = { 6583 5 }
        resistance = 10
        compliance = 20
        1939.1.1 = {
            buildings = {
                arms_factory = 1
            }
        }
    }
}
"""
