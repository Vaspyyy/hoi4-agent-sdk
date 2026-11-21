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
