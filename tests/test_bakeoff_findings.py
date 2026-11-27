"""Problems three models ran into while each built the same mod with 0.7.0."""

from __future__ import annotations

from pathlib import Path

import pytest

from hoi4 import BookmarkCountry, Mod, effects

DANZIG_HISTORY = """capital = 85

set_politics = {
\truling_party = fascism
\tlast_election = "1935.4.7"
\telection_frequency = 48
\telections_allowed = no
}
set_popularities = {
\tfascism = 60
\tdemocratic = 40
}

create_country_leader = {
\tname = "Albert Forster"
\tpicture = "GFX_portrait_DNZ_Albert_Forster"
\tideology = nazism
}

create_country_leader = {
\tname = "Arthur Brill"
\tideology = socialism
}
"""


def write(root: Path, path: str, text: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


@pytest.fixture
def game(tmp_path: Path) -> Path:
    game = tmp_path / "game"
    write(game, "common/country_tags/00_countries.txt", 'DNZ = "countries/Danzig.txt"\nPOL = "countries/Poland.txt"\n')
    write(game, "common/countries/Danzig.txt", 'graphical_culture = western_european_gfx\ncolor = { 78 129 151 }\n')
    write(game, "common/countries/Poland.txt", "color = { 197 92 106 }\n")
    write(game, "common/countries/colors.txt", "POL = {\n\tcolor = rgb { 197 92 106 }\n\tcolor_ui = rgb { 255 120 138 }\n}\n")
    write(game, "history/countries/DNZ - Danzig.txt", DANZIG_HISTORY)
    write(game, "history/countries/POL - Poland.txt", "capital = 10\n")
    write(
        game,
        "history/states/85-Danzig.txt",
        "state = {\n\tid = 85\n\tname = \"STATE_85\"\n\tmanpower = 366730\n\tstate_category = city\n"
        "\thistory = {\n\t\towner = POL\n\t\tadd_core_of = POL\n\t\tset_demilitarized_zone = yes\n"
        "\t\tbuildings = {\n\t\t\tinfrastructure = 3\n\t\t}\n\t}\n\tprovinces = {\n\t\t362\n\t}\n}\n",
    )
    write(game, "localisation/english/countries_l_english.yml", 'l_english:\n DNZ_fascism:0 "Free City of Danzig"\n DNZ_fascism_DEF:0 "the Free City of Danzig"\n DNZ_ADJ:0 "Danziger"\n')
    for size in ("", "medium/", "small/"):
        write(game, f"gfx/flags/{size}DNZ.tga", "x")
    return game


@pytest.fixture
def mod(tmp_path: Path, game: Path) -> Mod:
    root = tmp_path / "mod"
    root.mkdir()
    return Mod(root, hoi4_install=game)


def staged(mod: Mod) -> list[str]:
    return sorted(line[6:] for line in mod.preview().splitlines() if line.startswith("+++ b/"))


def test_demilitarized_zone_is_the_history_effect(mod: Mod) -> None:
    assert mod.get_state(85).is_demilitarized_zone is True
    mod.update_state(85, is_demilitarized_zone=False)
    mod.save()
    text = (mod.mod_root / "history/states/85-Danzig.txt").read_text(encoding="utf-8")
    assert "demilitarized" not in text
    assert "owner = POL" in text and "infrastructure = 3" in text
    assert mod.get_state(85).is_demilitarized_zone is False
    mod.update_state(85, is_demilitarized_zone=True)
    mod.save()
    text = (mod.mod_root / "history/states/85-Danzig.txt").read_text(encoding="utf-8")
    assert text.count("set_demilitarized_zone = yes") == 1
    assert "is_demilitarized_zone" not in text
    assert text.index("history = {") < text.index("set_demilitarized_zone") < text.index("provinces")


@pytest.mark.parametrize("lookups_first", [False, True])
def test_replacing_a_game_country_takes_the_places_of_its_files(mod: Mod, lookups_first: bool) -> None:
    if lookups_first:
        mod.get_state(85)
        mod.get_country("DNZ")
    country = mod.create_country("DNZ", "Free City of Danzig", overwrite_vanilla=True)
    assert country.color == (78, 129, 151)
    assert staged(mod) == [
        "common/characters/DNZ_characters.txt",
        "common/countries/Danzig.txt",
        "history/countries/DNZ - Danzig.txt",
        "localisation/english/DNZ_country_l_english.yml",
    ]
    mod.save()
    history = (mod.mod_root / "history/countries/DNZ - Danzig.txt").read_text(encoding="utf-8")
    assert "create_country_leader" not in history and "recruit_character = DNZ_leader_1" in history


def test_colour_table_is_written_only_for_a_changed_entry(mod: Mod) -> None:
    mod.update_country("DNZ", color=(1, 2, 3))
    assert "common/countries/colors.txt" not in staged(mod)
    assert "common/countries/Danzig.txt" in staged(mod)
    mod.discard()
    mod.update_country("POL", capital=11)
    assert "common/countries/colors.txt" not in staged(mod)
    mod.update_country("POL", color=(9, 9, 9))
    assert "common/countries/colors.txt" in staged(mod)


def test_old_style_leader_is_read_as_it_is_and_not_invented(mod: Mod) -> None:
    leader = mod.get_country("DNZ").leader
    assert leader is not None
    assert (leader.name, leader.ideology, leader.character_id) == ("Albert Forster", "nazism", "")
    mod.update_country("DNZ", stability=0.55)
    assert staged(mod) == ["history/countries/DNZ - Danzig.txt"]
    mod.save()
    history = (mod.mod_root / "history/countries/DNZ - Danzig.txt").read_text(encoding="utf-8")
    assert history.count("create_country_leader") == 2
    assert "recruit_character" not in history
    assert not (mod.mod_root / "localisation").exists()


def test_changing_an_old_style_leader_turns_it_into_a_character(mod: Mod) -> None:
    mod.update_country("DNZ", leader_name="Arthur Greiser")
    mod.save()
    history = (mod.mod_root / "history/countries/DNZ - Danzig.txt").read_text(encoding="utf-8")
    assert "Albert Forster" not in history and "Arthur Brill" in history
    assert history.count("recruit_character = DNZ_leader_1") == 1
    characters = (mod.mod_root / "common/characters/DNZ_characters.txt").read_text(encoding="utf-8")
    assert "Arthur Greiser" in characters and "ideology = nazism" in characters
    fresh = Mod(mod.mod_root, hoi4_install=mod.hoi4_install).get_country("DNZ").leader
    assert fresh is not None and (fresh.character_id, fresh.ideology) == ("DNZ_leader_1", "nazism")


def test_politics_change_keeps_the_game_names_of_the_country(mod: Mod) -> None:
    mod.update_country("DNZ", popularities={"fascism": 55, "democratic": 45})
    mod.save()
    assert mod.get_loc("DNZ_fascism") == "Free City of Danzig"
    assert mod.get_loc("DNZ_ADJ") == "Danziger"
    assert mod.get_loc("DNZ_democratic") == "DNZ" or mod.get_loc("DNZ_democratic")
    mod.update_country("DNZ", name="Danzig")
    assert mod.get_loc("DNZ_fascism") == "Danzig"


def test_package_accepts_the_flag_the_game_ships(mod: Mod) -> None:
    codes = {finding.code for finding in mod.validate_country_package("DNZ").findings}
    assert "missing_country_flag" not in codes
    (mod.hoi4_install / "gfx/flags/small/DNZ.tga").unlink()
    fresh = Mod(mod.mod_root, hoi4_install=mod.hoi4_install)
    assert "missing_country_flag" in {f.code for f in fresh.validate_country_package("DNZ").findings}


def test_election_date_and_frequency(mod: Mod) -> None:
    country = mod.get_country("DNZ")
    assert (country.last_election, country.election_frequency) == ("1935.4.7", 48)
    mod.update_country("DNZ", last_election="1933.5.28", election_frequency=None)
    mod.save()
    history = (mod.mod_root / "history/countries/DNZ - Danzig.txt").read_text(encoding="utf-8")
    assert 'last_election = "1933.5.28"' in history and "election_frequency" not in history
    assert "ruling_party = fascism" in history and "elections_allowed = no" in history
    new = mod.create_country("ZZZ", "Zed")
    assert new.last_election == "1936.1.1"
    mod.update_country("ZZZ", last_election="1934.2.2", election_frequency=60)
    mod.save()
    text = next((mod.mod_root / "history/countries").glob("ZZZ*.txt")).read_text(encoding="utf-8")
    assert 'last_election = "1934.2.2"' in text and "election_frequency = 60" in text


def test_bookmark_country_minor(mod: Mod) -> None:
    mod.create_bookmark("B")
    mod.add_bookmark_country("B", BookmarkCountry(tag="DNZ", minor=True))
    mod.add_bookmark_country("B", BookmarkCountry(tag="POL"))
    mod.save()
    text = next((mod.mod_root / "common/bookmarks").glob("*.txt")).read_text(encoding="utf-8")
    assert text.count("minor = yes") == 1
    fresh = Mod(mod.mod_root, hoi4_install=mod.hoi4_install)
    assert [c.minor for c in fresh.get_bookmark("B").countries] == [True, False]
    fresh.update_bookmark_country("B", "DNZ", minor=False)
    fresh.save()
    assert "minor" not in next((mod.mod_root / "common/bookmarks").glob("*.txt")).read_text(encoding="utf-8")


def test_descriptor_without_a_launcher_file(tmp_path: Path) -> None:
    from hoi4.project import write_mod_descriptors

    root = tmp_path / "standalone"
    root.mkdir()
    result = write_mod_descriptors(root, None, "Standalone", supported_version="1.20.*", tags=["Events"])
    assert result.launcher is None
    text = result.descriptor.read_text(encoding="utf-8")
    assert 'name="Standalone"' in text and "path=" not in text
    assert [p.name for p in tmp_path.iterdir() if p.suffix == ".mod"] == []


def test_dynamic_modifier_scope_is_documented_as_the_variable_scope() -> None:
    assert effects.remove_dynamic_modifier("m", scope=85) == effects.block(
        "remove_dynamic_modifier", {"modifier": "m", "scope": 85}
    )
    assert "not where the effect runs" in (effects.remove_dynamic_modifier.__doc__ or "")
    assert "85 = {" in effects.scope_block(85, effects.remove_dynamic_modifier("m"))


def test_event_text_key_shared_with_an_option_is_reported(mod: Mod) -> None:
    from hoi4 import EventOption

    mod.create_event("zz.1", title="zz.1.t", description="zz.1.d")
    for letter in "bcd":
        mod.add_event_option("zz.1", EventOption(name=f"zz.1.{letter}", effect="add_political_power = 1"))
    codes = [issue.code for issue in mod.validate() if issue.event_id == "zz.1"]
    assert codes.count("event_text_key_clash") == 1
    mod.update_event_option("zz.1", "zz.1.d", name="zz.1.e")
    assert "event_text_key_clash" not in [issue.code for issue in mod.validate()]


def test_decision_icons_are_checked_with_validate_icons(mod: Mod) -> None:
    write(
        mod.hoi4_install,
        "interface/decisions.gfx",
        'spriteTypes = {\n\tspriteType = { name = "GFX_decision_generic_industry" texturefile = "gfx/a.dds" }\n'
        '\tspriteType = { name = "GFX_decision_category_generic_political_actions" texturefile = "gfx/b.dds" }\n}\n',
    )
    fresh = Mod(mod.mod_root, hoi4_install=mod.hoi4_install)
    fresh.create_decision_category("cat", icon="generic_political_actions")
    fresh.create_decision("cat", "good", icon="generic_industry")
    fresh.create_decision("cat", "also_good", icon="GFX_decision_generic_industry")
    fresh.create_decision("cat", "bad", icon="generic_political_actions")
    found = [i for i in fresh.validate(validate_icons=True) if i.code == "unknown_decision_icon"]
    assert [i.decision_id for i in found] == ["bad"]
    assert "unknown_decision_icon" not in [i.code for i in fresh.validate()]
    fresh.update_decision_category("cat", icon="nope")
    found = [i for i in fresh.validate(validate_icons=True) if i.code == "unknown_decision_icon"]
    assert len(found) == 2


def test_advisor_small_portrait_may_be_on_the_army_channel(mod: Mod) -> None:
    from hoi4 import AdvisorRole, Character, CharacterPortrait

    mod.create_country("ZZZ", "Zed")
    mod.create_character(
        "ZZZ",
        Character(
            id="ZZZ_chief",
            country_tag="ZZZ",
            name="Chief",
            portraits=[CharacterPortrait(channel="army", large="GFX_l", small="GFX_s")],
            roles=[AdvisorRole(slot="army_chief", idea_token="ZZZ_chief")],
        ),
    )
    codes = {f.code for f in mod.validate_country_package("ZZZ").findings}
    assert "missing_character_small_portrait" not in codes


def test_portrait_crop_box(tmp_path: Path, mod: Mod) -> None:
    pil = pytest.importorskip("PIL.Image")
    source = tmp_path / "group.png"
    image = pil.new("RGB", (400, 300), (0, 0, 0))
    image.paste((255, 0, 0), (300, 0, 400, 100))
    image.save(source)
    target = mod.import_portrait_to_mod("DNZ", "red", source, output_format="tga", crop=(300, 0, 400, 100))
    mod.save()
    with pil.open(target) as result:
        assert result.size == (156, 210)
        assert result.convert("RGB").getpixel((78, 105)) == (255, 0, 0)
    with pytest.raises(ValueError, match="crop"):
        mod.import_portrait_to_mod("DNZ", "bad", source, crop=(300, 0, 500, 100))


def test_release_gate_on_action_probe_counts_the_mods_own_occurrences(mod: Mod) -> None:
    from hoi4.release_gate import _probe_on_action

    write(mod.hoi4_install, "common/on_actions/00_on_actions.txt", "on_actions = { on_startup = { effect = { log = a } } on_startup = { effect = { log = b } } }\n")
    fresh = Mod(mod.mod_root, hoi4_install=mod.hoi4_install)
    fresh.create_on_action("on_startup", effect="log = mine")
    assert _probe_on_action(fresh) == "on_startup[0].effect"
