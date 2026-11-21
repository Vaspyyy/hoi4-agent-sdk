"""States are identified, read and patched through the lossless syntax tree."""

from __future__ import annotations

from pathlib import Path

from hoi4 import Mod
from hoi4.map_editing import (
    append_list_tokens,
    list_ints,
    remove_list_tokens,
    render_strategic_region,
    state_file_provinces,
)
from hoi4.map_topology import _add_explicit_adjacencies, _province_types
from hoi4.states import build_state_index, find_state_file, read_state, state_id_of


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")
    return path


COMMENTED_ID = """# id = 42 (an old comment)
state = {
\tid = 5
\tname = STATE_5
\tmanpower = 1000
\tstate_category = town
\tprovinces = { 1 2 }
\thistory = { }
\tunrelated = { id = 42 }
}
"""

REAL_42 = """state = {
\tid = 42
\tname = STATE_42
\tmanpower = 2000
\tstate_category = city
\tprovinces = { 3 }
\thistory = {
\t\towner = FRA
\t}
}
"""


# ── Bug 1: a state is identified by its own id ─────────────────────────


def test_state_id_ignores_comments_and_nested_ids() -> None:
    assert state_id_of(COMMENTED_ID) == 5
    assert state_id_of("# state = { id = 9 }\n") is None
    assert state_id_of("other = { id = 9 }\n") is None


def test_find_state_file_and_index_use_the_state_blocks_id(tmp_path: Path) -> None:
    states = tmp_path / "history" / "states"
    # The misleading file sorts first and its name even starts with 42.
    misleading = _write(states / "42 - Misleading.txt", COMMENTED_ID)
    real = _write(states / "x.txt", REAL_42)

    assert find_state_file(states, 42) == real
    assert find_state_file(states, 5) == misleading
    index = {entry["id"]: entry for entry in build_state_index(states)}
    assert sorted(index) == [5, 42]
    assert index[5]["path"] == str(misleading)
    assert index[42]["owner"] == "FRA"
    assert index[5]["owner"] is None


def test_index_owner_ignores_dated_history(tmp_path: Path) -> None:
    states = tmp_path / "history" / "states"
    _write(
        states / "1.txt",
        "state = {\n\tid = 1\n\thistory = {\n\t\t1939.1.1 = { owner = GER }\n\t\towner = POL\n\t}\n}\n",
    )
    assert build_state_index(states)[0]["owner"] == "POL"


def test_mod_loads_both_states_despite_a_misleading_comment(tmp_path: Path) -> None:
    _write(tmp_path / "history" / "states" / "a.txt", COMMENTED_ID)
    _write(tmp_path / "history" / "states" / "b.txt", REAL_42)
    mod = Mod(tmp_path)
    assert mod.list_states() == [5, 42]
    assert mod.get_state(5).manpower == "1000"
    assert mod.get_state(42).owner == "FRA"
    assert not mod.load_diagnostics


def test_read_state_takes_fields_only_from_the_right_depth(tmp_path: Path) -> None:
    text = """state = {
\tid = 3
\tname = STATE_3
\tprovinces = {
\t\t10 11 # 12 is commented out
\t}
\thistory = {
\t\towner = ITA
\t\tadd_core_of = ITA
\t\tbuildings = { infrastructure = 3 }
\t\tvictory_points = { 10 5 }
\t\t1940.1.1 = {
\t\t\towner = GER
\t\t\tadd_core_of = GER
\t\t\tbuildings = { arms_factory = 9 }
\t\t\tvictory_points = { 11 1 }
\t\t}
\t}
}
"""
    state = read_state(tmp_path / "3.txt", text=text)
    assert state.id == 3
    assert state.provinces == [10, 11]
    assert state.owner == "ITA"
    assert state.cores == ["ITA"]
    assert state.buildings == "infrastructure = 3"
    assert state.victory_points == "10 5"


# ── Bug 2: insertions are cleanly indented ─────────────────────────────


def test_owner_into_empty_one_line_history_is_clean(tmp_path: Path) -> None:
    path = _write(tmp_path / "history" / "states" / "5.txt", COMMENTED_ID)
    mod = Mod(tmp_path)
    mod.set_state_owner(5, "ABI")
    mod.save()
    assert path.read_text(encoding="utf-8") == COMMENTED_ID.replace(
        "\thistory = { }\n",
        "\thistory = {\n\t\towner = ABI\n\t\tadd_core_of = ABI\n\t}\n",
    )
    reloaded = Mod(tmp_path)
    assert reloaded.get_state(5).owner == "ABI"
    assert not reloaded.load_diagnostics


def test_core_into_one_line_history_spreads_it_over_lines(tmp_path: Path) -> None:
    source = "state = {\n\tid = 1\n\tprovinces = { 1 }\n\thistory = { owner = POL }\n}\n"
    path = _write(tmp_path / "history" / "states" / "1.txt", source)
    mod = Mod(tmp_path)
    mod.add_state_core(1, "LIT")
    mod.save()
    assert path.read_text(encoding="utf-8") == (
        "state = {\n\tid = 1\n\tprovinces = { 1 }\n"
        "\thistory = {\n\t\towner = POL\n\t\tadd_core_of = LIT\n\t}\n}\n"
    )


def test_insertions_follow_space_indentation(tmp_path: Path) -> None:
    source = "state = {\n    id = 1\n    provinces = { 1 }\n    history = {}\n}\n"
    path = _write(tmp_path / "history" / "states" / "1.txt", source)
    mod = Mod(tmp_path)
    mod.set_state_owner(1, "POL")
    mod.save()
    assert path.read_text(encoding="utf-8") == (
        "state = {\n    id = 1\n    provinces = { 1 }\n"
        "    history = {\n        owner = POL\n        add_core_of = POL\n    }\n}\n"
    )


def test_new_core_goes_after_existing_cores_and_before_dated_history(tmp_path: Path) -> None:
    source = """state = {
\tid = 1
\tprovinces = { 1 }
\thistory = {
\t\towner = POL
\t\tadd_core_of = POL
\t\tvictory_points = { 1 5 }

\t\t1939.1.1 = {
\t\t\towner = GER
\t\t}
\t}
}
"""
    path = _write(tmp_path / "history" / "states" / "1.txt", source)
    mod = Mod(tmp_path)
    mod.add_state_core(1, "LIT")
    mod.save()
    assert path.read_text(encoding="utf-8") == source.replace(
        "\t\tadd_core_of = POL\n", "\t\tadd_core_of = POL\n\t\tadd_core_of = LIT\n"
    )


def test_owner_is_inserted_first_and_history_is_created_cleanly(tmp_path: Path) -> None:
    source = "state = {\n\tid = 1\n\tprovinces = { 1 }\n}\n"
    path = _write(tmp_path / "history" / "states" / "1.txt", source)
    mod = Mod(tmp_path)
    mod.set_state_owner(1, "POL", add_core=False)
    mod.save()
    assert path.read_text(encoding="utf-8") == (
        "state = {\n\tid = 1\n\tprovinces = { 1 }\n\thistory = {\n\t\towner = POL\n\t}\n}\n"
    )

    source = "state = {\n\tid = 2\n\tprovinces = { 2 }\n\thistory = {\n\t\tadd_core_of = POL\n\t}\n}\n"
    path = _write(tmp_path / "history" / "states" / "2.txt", source)
    mod = Mod(tmp_path)
    mod.set_state_owner(2, "POL")
    mod.save()
    assert path.read_text(encoding="utf-8") == source.replace(
        "\t\tadd_core_of = POL\n", "\t\towner = POL\n\t\tadd_core_of = POL\n"
    )


def test_resource_edit_changes_only_its_line(tmp_path: Path) -> None:
    source = """state = {
\tid = 1
\tresources = {
\t\toil = 5 # comment kept
\t\tsteel = 2
\t}
\tprovinces = { 1 }
}
"""
    path = _write(tmp_path / "history" / "states" / "1.txt", source)
    mod = Mod(tmp_path)
    mod.set_state_properties(1, resources={"oil": 5, "steel": 9})
    mod.save()
    assert path.read_text(encoding="utf-8") == source.replace("steel = 2", "steel = 9")


# ── Bug 4 and line endings: byte-exact files ───────────────────────────


def test_state_file_with_byte_order_mark_loads_and_keeps_it(tmp_path: Path) -> None:
    path = tmp_path / "history" / "states" / "7.txt"
    path.parent.mkdir(parents=True)
    source = "state = {\n\tid = 7\n\tprovinces = { 9 }\n\thistory = {\n\t\towner = GER\n\t}\n}\n"
    path.write_bytes(b"\xef\xbb\xbf" + source.encode("utf-8"))
    mod = Mod(tmp_path)
    state = mod.get_state(7)
    assert (state.id, state.owner, state.provinces) == (7, "GER", [9])
    assert not mod.load_diagnostics
    mod.set_state_owner(7, "AUS", add_core=False)
    mod.save()
    assert path.read_bytes() == b"\xef\xbb\xbf" + source.replace("GER", "AUS").encode("utf-8")


def test_crlf_state_file_keeps_crlf_after_an_edit(tmp_path: Path) -> None:
    path = tmp_path / "history" / "states" / "1.txt"
    path.parent.mkdir(parents=True)
    source = "state = {\r\n\tid = 1\r\n\tprovinces = { 1 }\r\n\thistory = { }\r\n}\r\n"
    path.write_bytes(source.encode("utf-8"))
    mod = Mod(tmp_path)
    mod.set_state_owner(1, "POL", add_core=False)
    mod.save()
    assert path.read_bytes() == (
        b"state = {\r\n\tid = 1\r\n\tprovinces = { 1 }\r\n"
        b"\thistory = {\r\n\t\towner = POL\r\n\t}\r\n}\r\n"
    )


def test_crlf_line_files_stay_crlf(tmp_path: Path) -> None:
    supply = tmp_path / "map" / "supply_nodes.txt"
    supply.parent.mkdir(parents=True)
    supply.write_bytes(b"1 10\r\n1 11\r\n")
    railways = tmp_path / "map" / "railways.txt"
    railways.write_bytes(b"1 2 10 11\r\n2 2 11 12\r\n")
    mod = Mod(tmp_path)
    mod.add_supply_node(12)
    mod.set_railway_level([11, 12], 3)
    mod.save()
    assert supply.read_bytes() == b"1 10\r\n1 11\r\n1 12\r\n"
    assert railways.read_bytes() == b"1 2 10 11\r\n3 2 11 12\r\n"


def test_csv_readers_do_not_drop_bytes(tmp_path: Path) -> None:
    definition = tmp_path / "definition.csv"
    # A Windows-1252 byte in a name column used to be silently dropped.
    definition.write_bytes(b"0;0;0;0;land;false;unknown;0\r\n1;1;2;3;land;false;plains;1\r\n2;4;5;6;sea;false;ocean;0\r\n")
    assert _province_types(definition) == {0: "land", 1: "land", 2: "sea"}
    adjacencies = tmp_path / "adjacencies.csv"
    adjacencies.write_bytes(b"From;To;Type;Through;start_x;start_y;stop_x;stop_y;adjacency_rule_name;Comment\r\n1;3;sea;2;-1;-1;-1;-1;;Stra\xdfe\r\n")
    graph: dict[int, set[int]] = {}
    _add_explicit_adjacencies(graph, adjacencies, {1, 3})
    assert graph == {1: {3}, 3: {1}}


# ── Malformed state files ──────────────────────────────────────────────


def test_unclosed_inherited_state_loads_and_its_override_is_well_formed(tmp_path: Path) -> None:
    game = tmp_path / "game"
    source = "state = {\n\tid = 1\n\tprovinces = { 1 }\n\thistory = {\n\t\towner = POL\n\t}\n"
    _write(game / "history" / "states" / "1-Test.txt", source)
    mod = Mod(tmp_path / "mod", hoi4_install=game)
    assert mod.get_state(1).owner == "POL"
    mod.set_state_owner(1, "GER", add_core=False)
    mod.save()
    written = (tmp_path / "mod" / "history" / "states" / "1-Test.txt").read_text(encoding="utf-8")
    assert written == source.replace("POL", "GER") + "}\n"
    reloaded = Mod(tmp_path / "mod", hoi4_install=game)
    assert reloaded.get_state(1).owner == "GER"
    assert not reloaded.load_diagnostics


def test_malformed_state_in_the_mod_is_a_load_failure(tmp_path: Path) -> None:
    _write(tmp_path / "history" / "states" / "1.txt", "state = {\n\tid = 1\n\tprovinces = { 1 }\n")
    mod = Mod(tmp_path)
    assert [diagnostic.section for diagnostic in mod.load_diagnostics] == ["state"]
    assert 1 not in mod.list_states()


# ── Map readers use the tree ───────────────────────────────────────────


def test_state_file_provinces_reads_only_the_state_blocks_list(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "1.txt",
        "# provinces = { 99 }\nstate = {\n\tid = 1\n\tother = { provinces = { 98 } }\n"
        "\tprovinces = {\n\t\t1 2 # 3\n\t}\n}\n",
    )
    assert state_file_provinces(path) == (1, 2)


def test_list_token_helpers_ignore_comments_and_nested_values() -> None:
    body = " 1 2 # 3 is gone\n 4 { 5 } x = 6 "
    assert list_ints(body) == [1, 2, 4]
    assert remove_list_tokens(body, [3, 5, 6]) == body
    assert remove_list_tokens(body, [2]) == " 1 # 3 is gone\n 4 { 5 } x = 6 "
    assert append_list_tokens(" 1 2 # 3\n", [7]) == " 1 2 7 # 3\n"


def test_render_strategic_region_recognises_a_weather_assignment() -> None:
    rendered = render_strategic_region(1, "REGION_1", [1], "weather = {\n\tperiod = { }\n}")
    assert rendered.count("weather = {") == 1
    wrapped = render_strategic_region(1, "REGION_1", [1], "period = { }")
    assert "\tweather = {\n\t\tperiod = { }\n\t}\n" in wrapped
