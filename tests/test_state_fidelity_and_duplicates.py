from __future__ import annotations

from pathlib import Path

import pytest

from hoi4 import Mod
from hoi4.patching import top_level_assignments
from hoi4.states import read_state, serialize_state
from hoi4.types import State


STATE_WITH_HISTORY = """state = {
\tid = 52
\tname = "STATE_52"
\tmanpower = 10
\tstate_category = city
\thistory = {
\t\towner = GER
\t\tadd_core_of = GER
\t\tvictory_points = { 100 5 } # first city
\t\tvictory_points = { 200 3 } # second city
\t\tbuildings = {
\t\t\tinfrastructure = 4
\t\t\tarms_factory = 2
\t\t}
\t\t1939.1.1 = {
\t\t\tbuildings = { air_base = 10 }
\t\t}
\t}
\tprovinces = { 100 200 }
}
"""


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_existing_state_noop_round_trip_is_byte_identical(tmp_path: Path) -> None:
    path = _write(tmp_path / "52-Test.txt", STATE_WITH_HISTORY)
    state = read_state(path)

    assert serialize_state(state) == STATE_WITH_HISTORY
    assert "arms_factory = 2" in state.buildings
    assert "air_base = 10" not in state.buildings


def test_manpower_edit_changes_only_manpower_and_preserves_history(tmp_path: Path) -> None:
    state_path = _write(
        tmp_path / "history" / "states" / "52-Test.txt", STATE_WITH_HISTORY
    )
    mod = Mod(tmp_path)

    mod.set_state_properties(52, manpower="11")
    assert mod.preview().count("victory_points") == 0
    mod.save(require_changes=True)

    expected = STATE_WITH_HISTORY.replace("\tmanpower = 10", "\tmanpower = 11")
    assert state_path.read_text(encoding="utf-8") == expected


def test_existing_buildings_edit_stays_in_history_and_is_minimal(tmp_path: Path) -> None:
    path = _write(tmp_path / "52-Test.txt", STATE_WITH_HISTORY)
    state = read_state(path)
    state.buildings = state.buildings.replace("arms_factory = 2", "arms_factory = 3")

    rendered = serialize_state(state)

    assert rendered == STATE_WITH_HISTORY.replace("arms_factory = 2", "arms_factory = 3")


def test_owner_edit_preserves_repeated_victory_points_and_dated_history(
    tmp_path: Path,
) -> None:
    path = _write(tmp_path / "52-Test.txt", STATE_WITH_HISTORY)
    state = read_state(path)
    state.owner = "FRA"

    rendered = serialize_state(state)

    assert rendered == STATE_WITH_HISTORY.replace("\t\towner = GER", "\t\towner = FRA")


def test_new_state_writes_buildings_inside_history(tmp_path: Path) -> None:
    state = State(
        id=7,
        manpower="100",
        owner="TST",
        cores=["TST"],
        buildings="infrastructure = 2",
        provinces=[7],
    )
    text = serialize_state(state)
    state_span = next(span for span in top_level_assignments(text) if span.key == "state")
    assert state_span.body_start is not None and state_span.body_end is not None
    state_body = text[state_span.body_start : state_span.body_end]

    assert not any(span.key == "buildings" for span in top_level_assignments(state_body))
    history_span = next(
        span for span in top_level_assignments(state_body) if span.key == "history"
    )
    assert history_span.body_start is not None and history_span.body_end is not None
    history_body = state_body[history_span.body_start : history_span.body_end]
    assert any(span.key == "buildings" for span in top_level_assignments(history_body))


def test_validation_rejects_state_level_buildings(tmp_path: Path) -> None:
    _write(
        tmp_path / "history" / "states" / "1-Invalid.txt",
        "state = { id = 1 manpower = 1 state_category = town "
        "buildings = { infrastructure = 1 } provinces = { 1 } "
        "history = { owner = TST add_core_of = TST } }",
    )
    mod = Mod(tmp_path)

    errors = mod.validate()

    error = next(error for error in errors if error.code == "state_buildings_outside_history")
    assert error.state_id == 1
    assert error.file_path and error.file_path.endswith("1-Invalid.txt")


def _make_duplicate(root: Path, section: str) -> tuple[str, Path, Path]:
    if section == "country_tag":
        first = _write(
            root / "common" / "country_tags" / "a.txt",
            'TST = "countries/First.txt"\n',
        )
        second = _write(
            root / "common" / "country_tags" / "b.txt",
            'TST = "countries/Second.txt"\n',
        )
        _write(root / "common" / "countries" / "First.txt", "color = { 1 2 3 }\n")
        _write(root / "common" / "countries" / "Second.txt", "color = { 4 5 6 }\n")
        return "TST", first, second
    if section == "state":
        body = (
            "state = { id = 1 manpower = 1 state_category = town "
            "provinces = { 1 } history = { owner = TST } }\n"
        )
        first = _write(root / "history" / "states" / "1 - A.txt", body)
        second = _write(root / "history" / "states" / "1 - B.txt", body)
        return "1", first, second
    if section == "focus_tree":
        body = "focus_tree = { id = duplicate_tree default = no }\n"
        first = _write(root / "common" / "national_focus" / "a.txt", body)
        second = _write(root / "common" / "national_focus" / "b.txt", body)
        return "duplicate_tree", first, second
    if section == "event":
        body = (
            "add_namespace = duplicate\n"
            "country_event = { id = duplicate.1 title = duplicate.1.t "
            "desc = duplicate.1.d option = { name = duplicate.1.a } }\n"
        )
        first = _write(root / "events" / "a.txt", body)
        second = _write(root / "events" / "b.txt", body)
        return "duplicate.1", first, second
    if section == "decision":
        first = _write(
            root / "common" / "decisions" / "a.txt",
            "category_a = { duplicate_decision = { cost = 1 } }\n",
        )
        second = _write(
            root / "common" / "decisions" / "b.txt",
            "category_b = { duplicate_decision = { cost = 2 } }\n",
        )
        return "duplicate_decision", first, second
    if section == "idea":
        body = (
            "ideas = { country = { duplicate_idea = { picture = GFX_idea_generic "
            "modifier = { stability_factor = 0.1 } } } }\n"
        )
        first = _write(root / "common" / "ideas" / "a.txt", body)
        second = _write(root / "common" / "ideas" / "b.txt", body)
        return "duplicate_idea", first, second
    raise AssertionError(f"Unhandled duplicate section: {section}")


@pytest.mark.parametrize(
    ("section", "code"),
    [
        ("country_tag", "duplicate_country_tag"),
        ("state", "duplicate_state_id"),
        ("focus_tree", "duplicate_focus_tree_id"),
        ("event", "duplicate_event_id"),
        ("decision", "duplicate_decision_id"),
        ("idea", "duplicate_idea_id"),
    ],
)
def test_duplicate_ids_retain_both_sources_and_validate(
    tmp_path: Path,
    section: str,
    code: str,
) -> None:
    identifier, first, second = _make_duplicate(tmp_path, section)

    mod = Mod(tmp_path)

    diagnostic = next(item for item in mod.load_diagnostics if item.section == section)
    assert diagnostic.identifier == identifier
    assert diagnostic.related_path == first
    assert diagnostic.path == second
    error = next(item for item in mod.validate() if item.code == code)
    assert error.related_file_path == str(first)
    assert error.file_path == str(second)

    if section == "country_tag":
        assert mod.get_country("TST").definition_path == (
            tmp_path / "common" / "countries" / "First.txt"
        ).resolve()
    elif section == "state":
        assert mod.get_state(1).source_path == first
        assert mod.state_index(include_vanilla=False)[0]["path"] == str(first)
    elif section == "focus_tree":
        assert mod.get_focus_tree(identifier).path == first.resolve()
    elif section == "event":
        assert mod.get_event(identifier).path == first
    elif section == "decision":
        assert mod.get_decision(identifier).path == first
    elif section == "idea":
        assert mod.get_idea(identifier).path == first


@pytest.mark.parametrize(
    "section",
    ["country_tag", "state", "focus_tree", "event", "decision", "idea"],
)
def test_strict_loading_rejects_duplicate_ids(tmp_path: Path, section: str) -> None:
    _make_duplicate(tmp_path, section)

    with pytest.raises(RuntimeError, match="Duplicate .* ID"):
        Mod(tmp_path, strict_loading=True)


def test_repeated_decision_category_with_unique_decisions_is_not_a_duplicate(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path / "common" / "decisions" / "a.txt",
        "shared_category = { first_decision = { cost = 1 } }\n",
    )
    _write(
        tmp_path / "common" / "decisions" / "b.txt",
        "shared_category = { second_decision = { cost = 2 } }\n",
    )

    mod = Mod(tmp_path, strict_loading=True)

    assert mod.list_decisions() == ["first_decision", "second_decision"]
    assert not mod.load_diagnostics
    with pytest.raises(RuntimeError, match="extended across multiple files"):
        mod.update_decision("first_decision", cost=3)

    assert mod.update_decision("second_decision", cost=4)
    preview = mod.preview()
    assert "b.txt" in preview
    assert "a.txt" not in preview


def test_overwrite_decision_category_replaces_in_place_and_drops_old_index(
    tmp_path: Path,
) -> None:
    source = _write(
        tmp_path / "common" / "decisions" / "custom.txt",
        "shared_category = {\n"
        "  old_decision = { cost = 1 available = { always = yes } }\n"
        "}\n",
    )
    mod = Mod(tmp_path)

    category = mod.create_decision_category(
        "shared_category",
        icon="GFX_decision_new",
        overwrite=True,
    )

    assert category.path == source
    assert "old_decision" not in mod.list_decisions()
    preview = mod.preview()
    assert "custom.txt" in preview
    assert "old_decision" in preview
    assert "GFX_decision_new" in preview

    mod.save(require_changes=True)
    reloaded = Mod(tmp_path)
    assert reloaded.list_decisions() == []
    assert reloaded.get_decision_category("shared_category").icon == "GFX_decision_new"


def test_overwrite_decision_category_refuses_cross_file_extension(tmp_path: Path) -> None:
    _write(
        tmp_path / "common" / "decisions" / "a.txt",
        "shared_category = { first_decision = { cost = 1 } }\n",
    )
    _write(
        tmp_path / "common" / "decisions" / "b.txt",
        "shared_category = { second_decision = { cost = 2 } }\n",
    )
    mod = Mod(tmp_path)

    with pytest.raises(RuntimeError, match="extended across multiple files"):
        mod.create_decision_category("shared_category", overwrite=True)

    assert mod.preview() == ""
