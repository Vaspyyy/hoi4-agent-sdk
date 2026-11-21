"""A value that is not one clean word is written in quotes, never refused.

Writers put a value after ``key =``. A value with a space, a brace or a ``#``
would end early and spill into the script around it, so it is written as one
quoted string (``hoi4.syntax.as_scalar``). Values that the files already hold
that way must keep working: the game ships a character whose portrait name
contains a space.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hoi4 import Character, CharacterPortrait, Mod
from hoi4.syntax import ParseError

STATE = (
    'state = {\n\tid = 1\n\tname = "STATE_1"\n\tmanpower = 1\n\tstate_category = town\n'
    "\thistory = {\n\t\towner = FRA\n\t}\n\tprovinces = { 1 }\n}\n"
)
CHARACTERS = (
    "characters = {\n\tSIA_person = {\n\t\tname = \"Person\"\n\t\tportraits = {\n"
    "\t\t\tcivilian = {\n\t\t\t\tlarge = GFX_portrait_SIA_person\n"
    '\t\t\t\tsmall="GFX_portrait_SIA_sri person_small"\n\t\t\t}\n\t\t}\n\t}\n}\n'
)


def _write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_a_portrait_name_with_a_space_survives_a_no_op_update(tmp_path: Path) -> None:
    path = _write(tmp_path, "common/characters/SIA.txt", CHARACTERS)
    mod = Mod(tmp_path)
    character = mod.get_character("SIA_person")
    assert character.portraits[0].small == "GFX_portrait_SIA_sri person_small"

    mod.update_character(character.id, portraits=character.portraits)

    # The update adds the character's missing name to the localisation; the
    # script file itself has nothing to change.
    assert "common/characters/SIA.txt" not in mod.preview()
    mod.save()
    assert path.read_text(encoding="utf-8") == CHARACTERS


def test_a_new_portrait_path_with_spaces_is_quoted(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    mod.create_character(
        "ZZZ",
        Character(
            id="ZZZ_person",
            name="Person",
            portraits=[CharacterPortrait(large="gfx/leaders/ZZZ/My Portrait.dds")],
        ),
        recruit=False,
    )
    mod.save()

    reloaded = Mod(tmp_path)
    assert reloaded.get_character("ZZZ_person").portraits[0].large == "gfx/leaders/ZZZ/My Portrait.dds"
    reloaded.update_character(
        "ZZZ_person", portraits=[CharacterPortrait(large="gfx/leaders/ZZZ/Other Portrait.dds")]
    )
    assert '+\t\t\t\tlarge = "gfx/leaders/ZZZ/Other Portrait.dds"' in reloaded.preview()


@pytest.mark.parametrize("name", ["New York", 'The "Big" Apple', "Plain"])
def test_a_state_name_may_hold_spaces_and_quotes(tmp_path: Path, name: str) -> None:
    _write(tmp_path, "history/states/1-Test.txt", STATE)
    mod = Mod(tmp_path)
    mod.set_state_properties(1, name=name)
    mod.save()
    assert Mod(tmp_path).get_state(1).name == name


def test_an_idea_icon_with_a_space_stays_quoted_on_the_next_edit(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    mod.create_idea("zz_idea", icon="my icon")
    mod.save()

    reloaded = Mod(tmp_path)
    reloaded.update_idea("zz_idea", modifier={"political_power_gain": 0.1})
    reloaded.save()

    text = (tmp_path / "common/ideas/mod_ideas.txt").read_text(encoding="utf-8")
    assert 'picture = "my icon"' in text
    assert Mod(tmp_path).get_idea("zz_idea").modifier == {"political_power_gain": 0.1}


def test_a_value_with_a_comment_sign_does_not_swallow_the_rest_of_the_line(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    mod.create_country("ZZZ", "Zzz")
    mod.save()
    history = next((tmp_path / "history/countries").glob("*.txt"))
    history.write_text(
        history.read_text(encoding="utf-8").replace(
            " ruling_party = democratic\n", ' ruling_party = democratic last_election = "1936.1.1"\n'
        ).replace(' last_election = "1936.1.1"\n elections', " elections"),
        encoding="utf-8",
    )

    reloaded = Mod(tmp_path)
    reloaded.update_country("ZZZ", ruling_party="fascism # oops")
    reloaded.save()

    assert 'ruling_party = "fascism # oops" last_election = "1936.1.1"' in history.read_text(
        encoding="utf-8"
    )


def test_a_key_has_to_be_one_word(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    with pytest.raises(ParseError, match="one word as a key"):
        mod.create_country("ZZZ", "Zzz", popularities={"zz_breakout = 1 #": 100})
        mod.preview()


# ── plain model text reads back exactly as it was given ──────────────


def test_an_icon_with_quote_characters_survives_an_unrelated_edit(tmp_path: Path) -> None:
    """The writer quotes model text itself; it never guesses that text which
    starts and ends with a quote character is already script."""

    mod = Mod(tmp_path)
    mod.create_idea("zz_idea", icon='"foo"')
    mod.save()

    reloaded = Mod(tmp_path)
    assert reloaded.get_idea("zz_idea").icon == '"foo"'
    reloaded.update_idea("zz_idea", modifier={"political_power_gain": 0.1})
    reloaded.save()

    assert Mod(tmp_path).get_idea("zz_idea").icon == '"foo"'


@pytest.mark.parametrize("source_name", ['"STATE_1"', "STATE_1"])
def test_a_state_name_with_quote_characters_reads_back(tmp_path: Path, source_name: str) -> None:
    _write(tmp_path, "history/states/1-Test.txt", STATE.replace('"STATE_1"', source_name))
    mod = Mod(tmp_path)
    mod.set_state_properties(1, name='"Big"')
    mod.save()
    assert Mod(tmp_path).get_state(1).name == '"Big"'


def test_spaces_at_the_ends_of_a_value_are_kept(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    mod.create_character(
        "ZZZ",
        Character(id="ZZZ_person", name="Person", portraits=[CharacterPortrait(large="GFX_foo ")]),
        recruit=False,
    )
    mod.save()
    assert Mod(tmp_path).get_character("ZZZ_person").portraits[0].large == "GFX_foo "


def test_a_new_state_writes_a_resource_value_once(tmp_path: Path) -> None:
    from hoi4.states import read_state, serialize_state
    from hoi4.types import State

    text = serialize_state(
        State(
            id=99, name="Test", manpower=100, state_category="town", owner="GER",
            provinces=[1], resources={"steel": "10 foo", "oil": 4},
        )
    )
    assert 'steel = "10 foo"' in text
    assert read_state(tmp_path / "99-Test.txt", text=text).resources == {"steel": "10 foo", "oil": 4}


def test_victory_points_have_to_be_numbers(tmp_path: Path) -> None:
    path = _write(tmp_path, "history/states/1-Test.txt", STATE)
    mod = Mod(tmp_path)
    with pytest.raises(ValueError, match="have to be numbers"):
        mod.set_state_properties(1, victory_points="1 10}}}zz_breakout=1{state={history={1")
        mod.preview()
    assert path.read_text(encoding="utf-8") == STATE

    mod = Mod(tmp_path)
    mod.set_state_properties(1, victory_points="1 10")
    mod.save()
    assert Mod(tmp_path).get_state(1).victory_points == "1 10"


def test_a_party_set_on_a_history_without_politics_stays_one_value(tmp_path: Path) -> None:
    _write(tmp_path, "common/country_tags/00_countries.txt", 'ZZZ = "countries/Zzz.txt"\n')
    _write(tmp_path, "common/countries/Zzz.txt", "color = { 1 2 3 }\n")
    history = _write(tmp_path, "history/countries/ZZZ - Zzz.txt", "capital = 1\n")
    mod = Mod(tmp_path)
    mod.update_country("ZZZ", ruling_party="democratic zz_breakout = 1")
    try:
        mod.save()
    except ValueError:
        return  # refused is fine too
    from hoi4.syntax import parse

    names = [item.name for item, _ in parse(history.read_text(encoding="utf-8")).root.walk()]
    assert "zz_breakout" not in names


def _names(text: str) -> list[str]:
    from hoi4.syntax import parse

    doc = parse(text)
    assert not doc.problems
    return [item.name for item, _ in doc.root.walk()]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", '"STATE_1"'),
        ("name", "STATE_1 "),
        ("name", " STATE_1"),
        ("state_category", '"town"'),
        ("state_category", "town "),
    ],
)
def test_a_state_edit_that_only_adds_quotes_or_spaces_is_written(
    tmp_path: Path, field: str, value: str
) -> None:
    path = _write(tmp_path, "history/states/1-Test.txt", STATE)
    mod = Mod(tmp_path)
    mod.set_state_properties(1, **{field: value})
    mod.save()
    assert path.read_text(encoding="utf-8") != STATE
    assert getattr(Mod(tmp_path).get_state(1), field) == value


def test_a_state_number_spelled_another_way_is_no_change(tmp_path: Path) -> None:
    source = STATE.replace("manpower = 1\n", "manpower = 2000.0\n\tresources = { steel = 10 }\n")
    path = _write(tmp_path, "history/states/1-Test.txt", source)
    mod = Mod(tmp_path)
    mod.set_state_properties(1, manpower=2000, resources={"steel": 10.0})
    assert mod.preview() == ""
    mod.save()
    assert path.read_text(encoding="utf-8") == source


def test_a_resource_value_with_quote_characters_is_written(tmp_path: Path) -> None:
    source = STATE.replace("manpower = 1\n", "manpower = 1\n\tresources = { steel = 10 }\n")
    path = _write(tmp_path, "history/states/1-Test.txt", source)
    mod = Mod(tmp_path)
    mod.set_state_properties(1, resources={"steel": '"10"'})
    mod.save()
    assert 'steel = "\\"10\\""' in path.read_text(encoding="utf-8")
    assert Mod(tmp_path).get_state(1).resources == {"steel": '"10"'}


@pytest.mark.parametrize("points", ["1 .5", "1 +5", "1 1e3", "1 @capital_points", "1 5."])
def test_victory_points_take_every_number_spelling_and_script_constants(
    tmp_path: Path, points: str
) -> None:
    path = _write(tmp_path, "history/states/1-Test.txt", STATE)
    mod = Mod(tmp_path)
    mod.set_state_properties(1, victory_points=points)
    mod.save()
    province, value = points.split()
    assert f"victory_points = {{ {province} {value} }}" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("ideology", ["despotism zz_breakout = 1", '"despotism"', "despotism "])
def test_a_leader_ideology_is_written_as_the_one_value_it_is(
    tmp_path: Path, ideology: str
) -> None:
    mod = Mod(tmp_path)
    mod.create_country("ZZZ", "Zedland", color=(1, 2, 3))
    mod.save()

    # The country serializer on its own, as ``write_character_file`` uses it.
    from hoi4.countries import serialize_country_files
    from hoi4.syntax import quote

    country = Mod(tmp_path).get_country("ZZZ")
    country.leader.ideology = ideology
    country.touched_fields = {"leader_ideology"}
    files = serialize_country_files(tmp_path, country)
    text = next(text for path, text in files.items() if "characters" in path.parts)
    assert f"ideology = {quote(ideology, force=True)}" in text
    assert "zz_breakout" not in _names(text)

    mod = Mod(tmp_path)
    mod.update_country("ZZZ", leader_ideology=ideology)
    mod.save()

    characters = next((tmp_path / "common" / "characters").glob("*.txt"))
    assert "zz_breakout" not in _names(characters.read_text(encoding="utf-8"))
    assert Mod(tmp_path).get_country("ZZZ").leader.ideology == ideology


def test_an_event_namespace_that_is_not_one_word_stays_one_value() -> None:
    from hoi4.events import serialize_events_file

    namespace = "zz } zz_breakout = 1 #"
    new_file = serialize_events_file(namespace, [])
    assert "zz_breakout" not in _names(new_file)
    assert _names(new_file) == ["add_namespace"]

    patched = serialize_events_file(namespace, [], "add_namespace = old\n")
    assert _names(patched) == ["add_namespace"]
    assert serialize_events_file(None, [], "add_namespace = old\n").strip() == ""
    # A plain namespace is written as before, and writing it again changes nothing.
    assert serialize_events_file("zz", []).startswith("add_namespace = zz\n")
    assert serialize_events_file("zz", [], "add_namespace = zz # mine\n") == (
        "add_namespace = zz # mine\n"
    )


def test_effect_builders_and_decision_chains_quote_their_names(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    assert _names(mod.effect_add_building(1, "bunker zz_breakout = 1")).count("zz_breakout") == 0
    assert "type = bunker level" in mod.effect_add_building(1, "bunker")
    bonus = mod.effect_add_tech_bonus("my bonus } zz_breakout = 1 {", category="industry")
    assert "zz_breakout" not in _names(bonus)
    assert mod.effect_add_tech_bonus("zz_bonus") == (
        "add_tech_bonus = { name = zz_bonus bonus = 0.5 uses = 1 category = industry }"
    )

    decisions = mod.create_decision_chain(
        "zz_category",
        [
            {"id": "zz_one", "flag": "done zz_breakout = 1", "clear_flags": ["old zz_breakout = 1"]},
            {"id": "zz_two"},
        ],
    )
    for decision in decisions:
        for body in (decision.visible, decision.available, decision.complete_effect):
            assert "zz_breakout" not in _names(body)
    assert 'set_country_flag = "done zz_breakout = 1"' in decisions[0].complete_effect
    assert "set_country_flag = zz_two_done" in decisions[1].complete_effect


def test_a_new_mapping_key_has_to_be_one_word() -> None:
    from hoi4.structured_patching import patch_scalar_mapping

    assert patch_scalar_mapping("a = 1\n", {"a": 1, "b": "two words"}) == 'a = 1\nb = "two words"\n'
    with pytest.raises(ParseError, match="one word as a key"):
        patch_scalar_mapping("a = 1\n", {"a": 1, "b } zz_breakout = 1 { c": 2})
