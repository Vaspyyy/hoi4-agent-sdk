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

from hoi4 import Character, CharacterPortrait, Mod, effect_block, scope_block
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
    mod.update_state(1, name=name)
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
    mod.update_state(1, name='"Big"')
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
        mod.update_state(1, victory_points="1 10}}}zz_breakout=1{state={history={1")
        mod.preview()
    assert path.read_text(encoding="utf-8") == STATE

    mod = Mod(tmp_path)
    mod.update_state(1, victory_points="1 10")
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
    mod.update_state(1, **{field: value})
    mod.save()
    assert path.read_text(encoding="utf-8") != STATE
    assert getattr(Mod(tmp_path).get_state(1), field) == value


def test_a_resource_value_keeps_a_space_at_its_end(tmp_path: Path) -> None:
    source = STATE.replace("manpower = 1\n", "manpower = 1\n\tresources = { steel = 10 }\n")
    path = _write(tmp_path, "history/states/1-Test.txt", source)
    mod = Mod(tmp_path)
    mod.update_state(1, resources={"steel": "10 "})
    mod.save()
    # Written as given. (The reader turns text that is a number into a number,
    # so it reads back as 10.)
    assert 'steel = "10 "' in path.read_text(encoding="utf-8")


def test_a_state_number_spelled_another_way_is_no_change(tmp_path: Path) -> None:
    source = STATE.replace("manpower = 1\n", "manpower = 2000.0\n\tresources = { steel = 10 }\n")
    path = _write(tmp_path, "history/states/1-Test.txt", source)
    mod = Mod(tmp_path)
    mod.update_state(1, manpower=2000, resources={"steel": 10.0})
    assert mod.preview() == ""
    mod.save()
    assert path.read_text(encoding="utf-8") == source


@pytest.mark.parametrize(
    ("in_file", "given"),
    [
        ("0.5", ".5"), (".5", 0.5), ("0.5", "0.50"), ("5.", 5), ("5", "5.0"), ("1e3", 1000),
        ("1000", "1e3"), ("+5", 5), ("-0", 0), ('"0.5"', 0.5), ("0.5", "5e-1"),
    ],
)
def test_every_spelling_of_one_number_is_no_change(
    tmp_path: Path, in_file: str, given: str | float
) -> None:
    source = STATE.replace(
        "manpower = 1\n",
        f"manpower = 1\n\tlocal_supplies = {in_file}\n\tbuildings_max_level_factor = {in_file}\n"
        f"\tresources = {{ steel = {in_file} }}\n",
    )
    path = _write(tmp_path, "history/states/1-Test.txt", source)
    mod = Mod(tmp_path)
    mod.update_state(
        1, local_supplies=given, buildings_max_level_factor=given, resources={"steel": given}
    )
    assert mod.preview() == ""
    mod.save()
    assert path.read_text(encoding="utf-8") == source


@pytest.mark.parametrize(("in_file", "given"), [("1", "001"), ("001", "1"), ("1.0", "1"), ("5", "+5")])
def test_a_state_name_that_looks_like_a_number_is_text(
    tmp_path: Path, in_file: str, given: str
) -> None:
    path = _write(tmp_path, "history/states/1-Test.txt", STATE.replace('"STATE_1"', in_file))
    mod = Mod(tmp_path)
    assert mod.get_state(1).name == in_file
    mod.update_state(1, name=given)
    mod.save()
    assert f"name = {given}\n" in path.read_text(encoding="utf-8")
    assert Mod(tmp_path).get_state(1).name == given


def test_a_resource_value_with_quote_characters_is_written(tmp_path: Path) -> None:
    source = STATE.replace("manpower = 1\n", "manpower = 1\n\tresources = { steel = 10 }\n")
    path = _write(tmp_path, "history/states/1-Test.txt", source)
    mod = Mod(tmp_path)
    mod.update_state(1, resources={"steel": '"10"'})
    mod.save()
    assert 'steel = "\\"10\\""' in path.read_text(encoding="utf-8")
    assert Mod(tmp_path).get_state(1).resources == {"steel": '"10"'}


@pytest.mark.parametrize("points", ["1 .5", "1 +5", "1 1e3", "1 @capital_points", "1 5."])
def test_victory_points_take_every_number_spelling_and_script_constants(
    tmp_path: Path, points: str
) -> None:
    path = _write(tmp_path, "history/states/1-Test.txt", STATE)
    mod = Mod(tmp_path)
    mod.update_state(1, victory_points=points)
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


def _every_character() -> list[str]:
    """Every character up to U+30FF and of U+FE00 to U+FFFF, and a sample of the rest."""

    return [
        chr(code)
        for code in range(0x110000)
        if not 0xD800 <= code <= 0xDFFF
        and (code < 0x3100 or 0xFE00 <= code <= 0xFFFF or code % 97 == 0)
    ]


def test_quote_gives_one_scalar_for_every_character() -> None:
    """The writer's "does this need quotes" and the reader's "where does a word end" agree.

    A character the reader ends a word at and the writer does not quote (a
    byte-order mark was one) would split a value in two.
    """

    from hoi4.script import pdx_value
    from hoi4.syntax import Scalar, parse, quote, unquote

    def reads_back(spelled: str, text: str) -> bool:
        doc = parse(spelled)
        items = doc.root.items
        return (
            not doc.problems and not doc.comments and len(items) == 1
            and type(items[0]) is Scalar
            and (items[0].start, items[0].end) == (0, len(spelled))
            and unquote(spelled) == text
        )

    words = ["@[a]b", "@[a]", "@[", "@[a", "a@[b]c", "@[a][b]", "a\\", "\\", '\\"', "a?", "a!", "a?=b"]
    for character in _every_character():
        words += [character, "a" + character, character + "a", "a" + character + "b"]
    wrong = [text for text in words if not reads_back(quote(text), text)]
    assert wrong == []
    # ``pdx_value`` refuses line breaks and NUL; everything else has to read back.
    wrong = [
        text for text in words
        if not any(c in text for c in "\n\r\x00") and not reads_back(pdx_value(text), text)
    ]
    assert wrong == []


def test_a_byte_order_mark_inside_a_value_does_not_split_it(tmp_path: Path) -> None:
    from hoi4.events import serialize_events_file
    from hoi4.states import serialize_state
    from hoi4.types import State

    mod = Mod(tmp_path)
    assert mod.effect_add_building(1, "bunker﻿zz_loose") == (
        '1 = { add_extra_state_shared_building_slots = 1 add_building_construction = '
        '{ type = "bunker﻿zz_loose" level = 1 instant_build = yes } }'
    )
    assert serialize_events_file("zz﻿hidden", []).startswith('add_namespace = "zz﻿hidden"\n')
    text = serialize_state(State(id=1, name="A﻿zz_loose", provinces=[1]))
    assert 'name = "A﻿zz_loose"' in text
    decisions = mod.create_decision_chain(
        "zz_category", [{"id": "zz_one", "flag": "done﻿zz_loose", "clear_flags": ["old﻿yes"]}]
    )
    assert 'set_country_flag = "done﻿zz_loose"' in decisions[0].complete_effect
    assert 'clr_country_flag = "old﻿yes"' in decisions[0].complete_effect


def test_script_builders_keep_names_and_effects_inside_their_block(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    # What they always built is built as before.
    assert effect_block("set_politics", {"ruling_party": "fascism", "elections_allowed": False}) == (
        "set_politics = { ruling_party = fascism elections_allowed = no }"
    )
    assert scope_block("GER", "add_political_power = 1", "add_stability = 0.1") == (
        "GER = { add_political_power = 1 add_stability = 0.1 }"
    )
    assert scope_block(42, "add_manpower = 1") == "42 = { add_manpower = 1 }"

    with pytest.raises(ParseError, match="one word as a key"):
        effect_block("foo", {"a = 1 } zz_breakout = 1 foo2 = { b": 1})
    with pytest.raises(ParseError, match="one word as a key"):
        effect_block("foo = { } zz_breakout = 1 foo2", {"a": 1})
    with pytest.raises(ParseError, match="one word as a key"):
        scope_block("GER = { } zz_breakout = 1 GER", "add_political_power = 1")
    with pytest.raises(ParseError):
        scope_block("GER", "} zz_breakout = 1 GER = {")
    with pytest.raises(ParseError):
        mod.effect_start_civil_war("fascism", effects=["} zz_breakout = 1 x = {"])
    with pytest.raises(ParseError):
        mod.effect_division_template("Infantry", "infantry = { x = 0 y = 0 } } zz_breakout = 1 x = {")

    # A comment in an effect swallows neither the closing brace nor the next effect.
    commented = scope_block("GER", "add_political_power = 1 # a gift", "add_stability = 0.1")
    assert commented == "GER = { add_political_power = 1 # a gift\nadd_stability = 0.1 }"
    assert _names(commented) == ["GER", "add_political_power", "add_stability"]
    commented = scope_block("GER", "add_political_power = 1 # a gift")
    assert commented == "GER = { add_political_power = 1 # a gift\n}"
    assert _names(commented) == ["GER", "add_political_power"]
    # A comment that does not end the text changes nothing about the layout.
    assert scope_block("GER", "# note\nadd_political_power = 1") == (
        "GER = { # note\nadd_political_power = 1 }"
    )
    assert scope_block("GER", 'set_country_flag = "a # b"', "add_stability = 0.1") == (
        'GER = { set_country_flag = "a # b" add_stability = 0.1 }'
    )
    assert scope_block(" GER ", "add_stability = 0.1") == "GER = { add_stability = 0.1 }"
    assert scope_block("GER") == "GER = {  }"
    nested = scope_block(
        "GER",
        "add_political_power = 1 # gift",
        "if = {\n\tlimit = { always = yes }\n\tadd_war_support = 0.1\n}\nadd_stability = 0.1",
    )
    assert _names(nested)[:3] == ["GER", "add_political_power", "if"]
    from hoi4.syntax import parse

    (outer,) = parse(nested).root.items
    assert [item.name for item in outer.block.assignments()] == [
        "add_political_power", "if", "add_stability",
    ]

    # A division template keeps comments, in the middle and at the end.
    template = mod.effect_division_template(
        "Test",
        regiments="infantry = { x = 0 y = 0 } # first\nartillery = { x = 1 y = 0 }",
    )
    assert template == (
        'division_template = { name = "Test" regiments = { infantry = { x = 0 y = 0 } # first\n'
        "artillery = { x = 1 y = 0 } } }"
    )
    assert mod.effect_division_template("Test", "# frontline\ninfantry = { x = 0 y = 0 }") == (
        'division_template = { name = "Test" regiments = { # frontline\ninfantry = { x = 0 y = 0 } } }'
    )
    assert _names(template) == [
        "division_template", "name", "regiments", "infantry", "x", "y", "artillery", "x", "y",
    ]
    template = mod.effect_division_template(
        "Test",
        regiments="infantry = { x = 0 y = 0 } # only",
        support="engineer = { x = 0 y = 0 } # last",
        division_names_group="ZZ_INF",
    )
    assert _names(template) == [
        "division_template", "name", "regiments", "infantry", "x", "y",
        "support", "engineer", "x", "y", "division_names_group",
    ]
    assert mod.effect_division_template("Test", "infantry = { x = 0 y = 0 }") == (
        'division_template = { name = "Test" regiments = { infantry = { x = 0 y = 0 } } }'
    )


def test_scope_block_reads_each_effect_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from hoi4 import script, syntax

    effects = ["add_stability = 0.1 # why", "add_war_support = 0.1"] * 300
    read: list[int] = []
    real_parse = syntax.parse

    def counting_parse(text: str) -> syntax.Document:
        read.append(len(text))
        return real_parse(text)

    monkeypatch.setattr(syntax, "parse", counting_parse)
    monkeypatch.setattr(script, "parse", counting_parse)
    result = script.scope_block("GER", *effects)
    monkeypatch.undo()

    # One read per effect (and one for the scope): the cost does not grow
    # with the square of the number of effects.
    assert len(read) <= len(effects) + 1
    assert max(read) < 40
    (outer,) = real_parse(result).root.items
    assert len(list(outer.block.assignments())) == 600


def test_pdx_value_spells_any_object_as_one_scalar() -> None:
    from decimal import Decimal

    from hoi4.script import pdx_value

    assert pdx_value(Path("gfx/my folder/x.dds")) == '"gfx/my folder/x.dds"'
    assert pdx_value(Decimal("0.5")) == "0.5"
    assert pdx_value(3) == "3"
    assert pdx_value(0.25) == "0.25"
    assert pdx_value(True) == "yes"


def test_a_number_beyond_what_can_be_compared_is_not_a_crash() -> None:
    from hoi4.patching import plain_values_equivalent

    assert plain_values_equivalent("1e10000000000000000000", "0") is False
    assert plain_values_equivalent("1e3", "1000") is True


def test_a_very_small_state_number_keeps_its_digits() -> None:
    from hoi4.states import _scalar_text

    assert _scalar_text(1e-7) == "0.0000001"
    assert _scalar_text(1.5e-10) == "0.00000000015"
    assert _scalar_text(1e20) == "100000000000000000000.0"
    assert _scalar_text(0.5) == "0.5"
    assert _scalar_text(2000) == "2000"


def test_an_empty_namespace_removes_the_line_like_none_does() -> None:
    from hoi4.events import serialize_events_file

    assert serialize_events_file("", [], "add_namespace = old\n").strip() == ""
    assert serialize_events_file("", []).strip() == ""
