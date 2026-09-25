import pytest
from hoi4 import Mod
from hoi4.states import read_state, serialize_state


def test_scripted_trigger_transaction_preview_and_save(tmp_path):
    m = Mod(tmp_path)
    with m.transaction():
        m.create_scripted_trigger("example", "always = yes")
        assert "always = yes" in m.preview()
    assert not m.preview()
    m.create_scripted_trigger("example", "always = yes")
    m.create_scripted_trigger("sibling", "always = no")
    with pytest.raises(ValueError):
        m.create_scripted_trigger("example", "always = no")
    result = m.save(require_changes=True)
    assert len(result.written_files) == 1
    m.create_scripted_trigger("example", "always = no", overwrite=True)
    m.save(require_changes=True)
    text = (tmp_path / "common/scripted_triggers/00_generated_triggers.txt").read_text()
    assert "sibling" in text and "always = yes" not in text


def test_scripted_trigger_rejects_escape_and_malformed(tmp_path):
    m = Mod(tmp_path)
    with pytest.raises(ValueError):
        m.create_scripted_trigger("bad id", "always = yes")
    with pytest.raises(ValueError):
        m.create_scripted_trigger("okay", "always = yes", path="../escape.txt")
    with pytest.raises(Exception):
        m.create_scripted_trigger("okay", "AND = {")
    assert not m.preview()


def test_impassable_state_round_trip_preserves_unknown(tmp_path):
    p = tmp_path / "state.txt"
    p.write_text(
        "state = { id = 1 name = STATE_1 manpower = 0 state_category = wasteland provinces = { 1 } mystery = yes history = { owner = GER } }"
    )
    s = read_state(p)
    s.impassable = True
    text = serialize_state(s)
    assert "impassable = yes" in text and "mystery = yes" in text
    p.write_text(text)
    s = read_state(p)
    assert s.impassable
    s.impassable = False
    assert "impassable = yes" not in serialize_state(s)


def test_state_history_reset_keeps_modeled_fields_and_ignores_vp_comments(tmp_path):
    p = tmp_path / "state.txt"
    p.write_text(
        "state = { id = 1 name = STATE_1 manpower = 100 provinces = { 1 } history = { owner = GER add_core_of = GER victory_points = { 1 5 # City\n } buildings = { infrastructure = 2 } 1939.1.1 = { owner = FRA } } }"
    )
    s = read_state(p)
    assert s.victory_points == "1 5"
    s.history = ""
    text = serialize_state(s)
    assert "owner = GER" in text and "add_core_of = GER" in text and "infrastructure = 2" in text
    assert "1939.1.1" not in text
