"""Countries, country history, characters and tags read and write losslessly.

Every fixture here is synthetic. Each test pins one bug found against the real
game files: typed colours, comments read as data, a vanilla file with an
unclosed block, and depth- or comment-blind readers.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from hoi4 import Mod
from hoi4.characters import (
    AdvisorRole,
    CountryLeaderRole,
    load_characters_file,
    serialize_characters_file,
)
from hoi4.countries import (
    color_from_value,
    read_country,
    serialize_country_colors_file,
    set_color,
)
from hoi4.syntax import parse
from hoi4.tags import (
    add_country_tag,
    country_tag_entries,
    load_mod_tags,
    remove_country_tag,
)
from hoi4.validation import validate_country_history_references

warnings.filterwarnings("ignore", category=RuntimeWarning)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _game(root: Path, *, definition: str, colors: str = "", history: str = "capital = 1\n") -> Path:
    _write(root / "common/country_tags/00_countries.txt", 'ABC = "countries/Abcland.txt"\n')
    _write(root / "common/countries/Abcland.txt", definition)
    if colors:
        _write(root / "common/countries/colors.txt", colors)
    _write(root / "history/countries/ABC - Abcland.txt", history)
    return root


def _rendered(mod: Mod) -> dict[Path, str]:
    return {
        path: text
        for path, text in mod._render_dirty_files().items()
        if text is not None and (not path.exists() or path.read_text(encoding="utf-8") != text)
    }


# ── Bug 1: typed colours ────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("{ 57 113 228 }", (57, 113, 228)),
        ("{ 57  113  228 }", (57, 113, 228)),
        ("rgb { 57 113 228 }", (57, 113, 228)),
        ("RGB { 57 113 228 }", (57, 113, 228)),
        ("hsv { 0 1 1 }", (255, 0, 0)),
        ("HSV { 0.1 0.15 0.4 }", (102, 96, 87)),
        ("hsv360 { 120 100 100 }", (0, 255, 0)),
        ("{ 1 2 }", None),
        ("{ a b c }", None),
    ],
)
def test_colour_values_of_every_spelling(value: str, expected: tuple[int, int, int] | None) -> None:
    assignment = parse(f"color = {value}").root.first("color")
    assert assignment is not None
    assert color_from_value(assignment.value) == expected


def test_set_color_changes_only_the_channels_and_keeps_the_style() -> None:
    assert set_color("color = rgb { 57 113 228 }\n", "color", (10, 20, 30)) == (
        "color = rgb { 10 20 30 }\n"
    )
    assert set_color("color = { 57  113  228 } # old\n", "color", (10, 20, 30)) == (
        "color = { 10  20  30 } # old\n"
    )
    # An hsv value that changes becomes rgb, because the model is RGB.
    assert set_color("color = HSV { 0.1 0.15 0.4 }\n", "color", (10, 20, 30)) == (
        "color = rgb { 10 20 30 }\n"
    )
    # A value that already means the colour is left byte for byte, hsv included.
    unchanged = "color = HSV { 0.1 0.15 0.4 }\n"
    assert set_color(unchanged, "color", (102, 96, 87)) == unchanged
    assert set_color("a = 1\n", "color", (1, 2, 3), default_tag="rgb") == (
        "a = 1\ncolor = rgb { 1 2 3 }\n"
    )
    with pytest.raises(ValueError):
        set_color("color = { 1 2 3 }", "color", (300, 0, 0))


def test_editing_a_typed_colour_in_a_mod_country_writes_one_statement(tmp_path: Path) -> None:
    mod_root = tmp_path / "mod"
    _write(mod_root / "common/country_tags/00_countries.txt", 'ABC = "countries/ABC.txt"\n')
    definition = _write(
        mod_root / "common/countries/ABC.txt",
        "graphical_culture = western_european_gfx\ncolor = rgb { 57 113 228 } # blue\n",
    )
    _write(mod_root / "history/countries/ABC - Abcland.txt", "capital = 1\n")
    mod = Mod(mod_root)
    assert mod.get_country("ABC").color == (57, 113, 228)

    mod.update_country("ABC", color=(10, 20, 30))
    mod.save()

    text = definition.read_text(encoding="utf-8")
    assert text == "graphical_culture = western_european_gfx\ncolor = rgb { 10 20 30 } # blue\n"
    assert not parse(text).problems
    assert Mod(mod_root).get_country("ABC").color == (10, 20, 30)


def test_editing_another_field_never_touches_the_colour_line(tmp_path: Path) -> None:
    mod_root = tmp_path / "mod"
    _write(mod_root / "common/country_tags/00_countries.txt", 'ABC = "countries/ABC.txt"\n')
    original = "color = HSV { 0.1 0.15 0.4 }\ngraphical_culture = western_european_gfx\n"
    definition = _write(mod_root / "common/countries/ABC.txt", original)
    _write(mod_root / "history/countries/ABC - Abcland.txt", "capital = 1\n")
    mod = Mod(mod_root)

    mod.update_country("ABC", graphical_culture="eastern_european_gfx", capital=2)
    mod.save()

    assert definition.read_text(encoding="utf-8") == original.replace("western", "eastern")


def test_colors_table_reads_hsv_and_ui_colour_and_patches_only_the_edited_entry(
    tmp_path: Path,
) -> None:
    colors = (
        "#reload countrycolors\n\n"
        "ABC = {\n\tcolor = HSV { 0.1 0.15 0.4 }\n\tcolor_ui = rgb { 138 155 116 }\n}\n"
        "XYZ = {\n\tcolor = hsv { 0 1 1 }\n\tcolor_ui = rgb { 1 2 3 }\n}\n"
    )
    game = _game(tmp_path / "game", definition="color = { 1 2 3 }\n", colors=colors)
    country = read_country(tmp_path / "mod", "ABC", game)
    assert country.color == (102, 96, 87)
    assert country.color_ui == (138, 155, 116)

    patched = serialize_country_colors_file(colors, {"ABC": (10, 20, 30)})
    assert patched == colors.replace("HSV { 0.1 0.15 0.4 }", "rgb { 10 20 30 }").replace(
        "rgb { 138 155 116 }", "rgb { 10 20 30 }"
    )
    explicit = serialize_country_colors_file(
        colors, {"ABC": (102, 96, 87)}, ui_colors={"ABC": (9, 9, 9)}
    )
    assert explicit == colors.replace("rgb { 138 155 116 }", "rgb { 9 9 9 }")


def test_vanilla_colour_override_copies_the_table_verbatim_with_one_change(tmp_path: Path) -> None:
    colors = (
        "#reload countrycolors\n\n"
        "ABC = {\n\tcolor = rgb { 4 5 6 }\n\tcolor_ui = rgb { 7 8 9 }\n}\n"
        "XYZ = {\n\tcolor = HSV { 0 1 1 }\n}\n"
    )
    game = _game(tmp_path / "game", definition="color = { 1  2  3 }", colors=colors)
    mod_root = tmp_path / "mod"
    mod = Mod(mod_root, hoi4_install=game)
    assert mod.get_country("ABC").color == (4, 5, 6)

    mod.update_country("ABC", color=(10, 20, 30))
    rendered = _rendered(mod)

    assert set(rendered) == {
        mod_root / "common/countries/Abcland.txt",
        mod_root / "common/countries/colors.txt",
    }
    assert rendered[mod_root / "common/countries/Abcland.txt"] == "color = { 10  20  30 }"
    assert rendered[mod_root / "common/countries/colors.txt"] == colors.replace(
        "rgb { 4 5 6 }", "rgb { 10 20 30 }"
    ).replace("rgb { 7 8 9 }", "rgb { 10 20 30 }")
    mod.save()
    again = Mod(mod_root, hoi4_install=game)
    assert again.get_country("ABC").color == (10, 20, 30)
    assert again.load_diagnostics == ()


# ── Bug 2: comments are never data ──────────────────────────────


CHARACTERS = """characters = {
\tABC_rommel = {
\t\tname = ABC_rommel
\t\tportraits = {
\t\t\tarmy = { large = GFX_portrait_ABC_rommel }
\t\t\tarmy = { small = GFX_idea_ABC_rommel }
\t\t}
\t\tfield_marshal = {
\t\t\ttraits = { # desert fox comment
\t\t\t\tarmor_officer
\t\t\t}
\t\t\tskill = 4
\t\t}
\t\tadvisor = {
\t\t\tslot = army_chief
\t\t\tcost = 150.000
\t\t\ttraits = { }
\t\t\tallowed = {}
\t\t}
\t}
}
"""


def test_comment_inside_a_trait_list_is_not_a_trait(tmp_path: Path) -> None:
    path = _write(tmp_path / "common/characters/ABC.txt", CHARACTERS)
    (character,) = load_characters_file(path)
    marshal, advisor = character.roles
    assert isinstance(advisor, AdvisorRole)

    assert marshal.traits == ["armor_officer"]
    assert advisor.cost == 150
    assert advisor.traits == []


def test_rewriting_every_read_value_reproduces_the_file(tmp_path: Path) -> None:
    path = _write(tmp_path / "common/characters/ABC.txt", CHARACTERS)
    characters = load_characters_file(path)
    character = characters[0]
    character.touched_fields.update({"name", "portraits", "roles"})
    marshal, advisor = character.roles
    marshal.touched_fields.update({"traits", "skill"})
    advisor.touched_fields.update({"slot", "cost", "traits", "allowed"})

    assert serialize_characters_file(characters, CHARACTERS) == CHARACTERS


def test_one_field_edit_changes_only_that_value(tmp_path: Path) -> None:
    mod_root = tmp_path / "mod"
    path = _write(mod_root / "common/characters/ABC.txt", CHARACTERS)
    mod = Mod(mod_root)

    assert mod.update_character_role("ABC_rommel", "field_marshal", skill=5)
    assert mod.update_character("ABC_rommel", name="ABC_desert_fox")

    rendered = _rendered(mod)[path]
    assert rendered == CHARACTERS.replace("skill = 4", "skill = 5").replace(
        "name = ABC_rommel", "name = ABC_desert_fox"
    )


def test_history_idea_list_ignores_comments(tmp_path: Path) -> None:
    _write(
        tmp_path / "history/countries/ABC - Test.txt",
        "add_ideas = { # starting spirits\n\tfirst_idea # the first\n\tsecond_idea\n}\n",
    )
    assert read_country(tmp_path, "ABC").ideas == ["first_idea", "second_idea"]


# ── Bug 3: an inherited file with a block still open at the end ─


UNCLOSED_HISTORY = (
    "capital = 110 # Oslo\n"
    "set_politics = {\n\truling_party = democratic\n\tlast_election = \"1933.10.16\"\n}\n"
    "if = {\n\tlimit = { has_dlc = \"Man the Guns\" }\n\tset_naval_oob = \"ABC_naval\"\n"
)


def test_inherited_history_with_an_open_block_loads_edits_and_saves_well_formed(
    tmp_path: Path,
) -> None:
    game = _game(tmp_path / "game", definition="color = { 1 2 3 }\n", history=UNCLOSED_HISTORY)
    mod_root = tmp_path / "mod"
    mod = Mod(mod_root, hoi4_install=game)
    assert mod.get_country("ABC").capital == 110

    mod.update_country("ABC", capital=111)
    rendered = _rendered(mod)
    override = mod_root / "history/countries/ABC - Abcland.txt"
    assert set(rendered) == {override}
    assert not parse(rendered[override]).problems
    assert rendered[override] == UNCLOSED_HISTORY.replace("110", "111") + "}\n"
    assert "ABC - Abcland.txt" in mod.preview()

    mod.save()
    assert not parse(override.read_text(encoding="utf-8")).problems
    mod.discard()
    assert mod.get_country("ABC").capital == 111
    fresh = Mod(mod_root, hoi4_install=game)
    assert fresh.get_country("ABC").capital == 111
    assert fresh.load_diagnostics == ()


def test_vanilla_rename_keeps_the_inherited_history_file_name(tmp_path: Path) -> None:
    game = _game(tmp_path / "game", definition="color = { 1 2 3 }\n")
    mod_root = tmp_path / "mod"
    mod = Mod(mod_root, hoi4_install=game)

    mod.update_country("ABC", name="Renamed", capital=2)
    rendered = _rendered(mod)

    assert mod_root / "history/countries/ABC - Abcland.txt" in rendered
    assert not any("Renamed" in path.name for path in rendered if path.suffix == ".txt")


def test_malformed_country_file_in_the_mod_is_a_load_failure_and_is_never_rewritten(
    tmp_path: Path,
) -> None:
    mod_root = tmp_path / "mod"
    _write(mod_root / "common/country_tags/00_countries.txt", 'ABC = "countries/ABC.txt"\n')
    _write(mod_root / "common/countries/ABC.txt", "color = { 1 2 3 }\n")
    broken = "capital = 5\nset_politics = {\n\truling_party = democratic\n"
    history = _write(mod_root / "history/countries/ABC - Abcland.txt", broken)
    mod = Mod(mod_root)

    assert any(
        item.section == "country" and item.path == history for item in mod.load_diagnostics
    )
    assert mod.get_country("ABC").capital == 5
    mod.update_country("ABC", capital=6)
    with pytest.raises(RuntimeError, match="failed to load"):
        mod.save()
    assert history.read_text(encoding="utf-8") == broken


# ── Bug 4: depth- and comment-blind readers ─────────────────────


def test_history_reads_only_direct_statements(tmp_path: Path) -> None:
    _write(
        tmp_path / "history/countries/ABC - Test.txt",
        "# capital = 999\n"
        "if = { limit = { has_dlc = \"x\" } capital = 7 set_research_slots = 9 }\n"
        "capital = 42\n"
        "1939.1.1 = { create_country_leader = { name = \"Later Leader\" } }\n"
        "set_politics = { ruling_party = fascism # comment = nothing\n}\n",
    )
    country = read_country(tmp_path, "ABC")
    assert country.capital == 42
    assert country.research_slots is None
    assert country.ruling_party == "fascism"
    assert country.leader is not None and country.leader.name == "Later Leader"


def test_politics_edit_keeps_last_election_and_unknown_keys(tmp_path: Path) -> None:
    mod_root = tmp_path / "mod"
    _write(mod_root / "common/country_tags/00_countries.txt", 'ABC = "countries/ABC.txt"\n')
    _write(mod_root / "common/countries/ABC.txt", "color = { 1 2 3 }\n")
    original = (
        "capital = 1\n"
        "set_politics = {\n\truling_party = democratic\n\tlast_election = \"1932.11.8\"\n"
        "\telection_frequency = 48\n\telections_allowed = yes\n}\n"
    )
    history = _write(mod_root / "history/countries/ABC - Abcland.txt", original)
    mod = Mod(mod_root)

    mod.update_country("ABC", ruling_party="fascism", elections_allowed=False)
    mod.save()

    assert history.read_text(encoding="utf-8") == original.replace(
        "ruling_party = democratic", "ruling_party = fascism"
    ).replace("elections_allowed = yes", "elections_allowed = no")


def test_leader_comes_from_a_direct_or_instance_country_leader_role(tmp_path: Path) -> None:
    _write(tmp_path / "history/countries/ABC - Test.txt", "capital = 1\n")
    _write(
        tmp_path / "common/characters/ABC.txt",
        "characters = {\n"
        "\tABC_general = { name = General field_marshal = { skill = 3 } }\n"
        "\tABC_variant = {\n"
        "\t\tname = Variant\n"
        "\t\tinstance = {\n"
        "\t\t\tportraits = { civilian = { large = GFX_portrait_ABC_variant_face } }\n"
        "\t\t\tcountry_leader = { ideology = despotism # was moderate\n\t\t\t}\n"
        "\t\t}\n"
        "\t}\n"
        "}\n",
    )
    leader = read_country(tmp_path, "ABC").leader
    assert leader is not None
    assert (leader.character_id, leader.name, leader.ideology, leader.portrait_slug) == (
        "ABC_variant",
        "Variant",
        "despotism",
        "variant_face",
    )


def test_tag_files_are_read_as_script(tmp_path: Path) -> None:
    text = (
        "dynamic_tags = yes\n"
        '# IGN = "countries/Ignored.txt"\n'
        'ABC = "countries/A#b.txt" # trailing\n'
        "DEF = countries/Bare.txt DEF2 = \"x\"\n"
    )
    assert country_tag_entries(text) == [("ABC", "countries/A#b.txt"), ("DEF", "countries/Bare.txt")]
    _write(tmp_path / "common/country_tags/01_tags.txt", text)
    assert load_mod_tags(tmp_path) == ["ABC", "DEF"]


def test_tag_registration_add_and_remove_keep_other_bytes(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "common/country_tags/00_generated_tags.txt",
        '# header\nABC = "countries/ABC.txt" # keep me\nDEF = "countries/DEF.txt"',
    )
    add_country_tag(tmp_path, "GHI")
    assert path.read_text(encoding="utf-8") == (
        '# header\nABC = "countries/ABC.txt" # keep me\nDEF = "countries/DEF.txt"\n'
        'GHI = "countries/GHI.txt"\n'
    )
    add_country_tag(tmp_path, "ABC")
    assert path.read_text(encoding="utf-8").count("ABC =") == 1
    remove_country_tag(tmp_path, "DEF")
    assert path.read_text(encoding="utf-8") == (
        '# header\nABC = "countries/ABC.txt" # keep me\nGHI = "countries/GHI.txt"\n'
    )


def test_history_reference_validation_uses_the_tree(tmp_path: Path) -> None:
    _write(
        tmp_path / "history/countries/ABC - Test.txt",
        "# capital = 999\n"
        "capital = 5\n"
        "recruit_character = ABC_known # recruit_character = ABC_comment\n"
        "1939.1.1 = { recruit_character = \"ABC_missing\" }\n",
    )
    issues = validate_country_history_references(
        tmp_path, known_state_ids={4}, known_character_ids={"ABC_known"}
    )
    assert [(issue.code, issue.line) for issue in issues] == [
        ("capital_ref", 2),
        ("character_ref", 4),
    ]
    assert "ABC_missing" in issues[1].message


# ── Other fidelity rules ────────────────────────────────────────


def test_unchanged_inherited_files_are_not_copied_into_the_mod(tmp_path: Path) -> None:
    game = _game(tmp_path / "game", definition="color = { 1 2 3 }\n")
    _write(
        game / "common/characters/ABC.txt",
        "characters = { ABC_leader = { name = Leader country_leader = { ideology = liberalism } } }\n",
    )
    mod_root = tmp_path / "mod"
    mod = Mod(mod_root, hoi4_install=game)

    mod.update_country("ABC", capital=2)

    assert set(_rendered(mod)) == {mod_root / "history/countries/ABC - Abcland.txt"}


def test_vanilla_character_ids_with_non_ascii_letters_can_be_edited(tmp_path: Path) -> None:
    game = _game(tmp_path / "game", definition="color = { 1 2 3 }\n")
    _write(
        game / "common/characters/ABC.txt",
        "characters = {\n\tABC_josé_garcía = {\n\t\tname = \"José\"\n"
        "\t\tcountry_leader = {\n\t\t\tideology = liberalism\n\t\t}\n\t}\n}\n",
    )
    mod = Mod(tmp_path / "mod", hoi4_install=game)
    character = mod.get_character("ABC_josé_garcía")
    assert isinstance(character.roles[0], CountryLeaderRole)
    assert mod.update_character_role(
        "ABC_josé_garcía", "country_leader", ideology="conservatism"
    )
    with pytest.raises(ValueError):
        mod.get_character("bad id")


# ── a list or mapping gets one more entry, nothing else changes ──


COMMENTED_HISTORY = (
    "capital = 1\n"
    "add_ideas = { # starting spirits\n\tfirst_idea # the first\n\t# laws\n\tsecond_idea\n}\n"
    "set_technology = {\n\tinfantry_weapons = 1 # basics\n\ttech_support = 1\n}\n"
)


def test_adding_an_idea_and_a_technology_keeps_the_comments_in_both_blocks(tmp_path: Path) -> None:
    _write(tmp_path / "common/country_tags/00_countries.txt", 'ABC = "countries/Abcland.txt"\n')
    _write(tmp_path / "common/countries/Abcland.txt", "color = { 1 2 3 }\n")
    history = _write(tmp_path / "history/countries/ABC - Abcland.txt", COMMENTED_HISTORY)
    mod = Mod(tmp_path)
    country = mod.get_country("ABC")
    mod.update_country(
        "ABC",
        ideas=[*country.ideas, "third_idea"],
        technologies={**country.technologies, "gw_artillery": 1},
    )
    mod.save(require_changes=True)
    assert history.read_text(encoding="utf-8") == COMMENTED_HISTORY.replace(
        "\tsecond_idea\n", "\tsecond_idea\n\tthird_idea\n"
    ).replace("\ttech_support = 1\n", "\ttech_support = 1\n\tgw_artillery = 1\n")


def test_adding_a_trait_keeps_the_comments_in_the_list(tmp_path: Path) -> None:
    path = _write(tmp_path / "common/characters/ABC.txt", CHARACTERS)
    mod = Mod(tmp_path)
    assert mod.update_character_role(
        "ABC_rommel", "field_marshal", traits=["armor_officer", "trickster"]
    )
    mod.save(require_changes=True)
    assert path.read_text(encoding="utf-8") == CHARACTERS.replace(
        "\t\t\t\tarmor_officer\n", "\t\t\t\tarmor_officer\n\t\t\t\ttrickster\n"
    )
