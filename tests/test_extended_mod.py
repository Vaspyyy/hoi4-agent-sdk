from __future__ import annotations

from pathlib import Path

import pytest

from hoi4 import Mod, SubIdeology
from hoi4.bookmarks import BookmarkCountry
from hoi4.progress import OperationCancelled


IDEOLOGY_SOURCE = """ideologies = {
    custom = {
        color = { 1 2 3 } # color comment
        types = { custom_main = { } }
        rules = { can_send_volunteers = yes }
        future_setting = { keep = yes }
    }
}
"""

DYNAMIC_SOURCE = """dynamic_country_ideas = {
    name = ABC_dynamic
    ABC_growth = {
        potential = { original_tag = ABC }
        modifier = { political_power_gain = 0.1 # modifier comment
        }
        future_setting = yes
    }
}
"""

BOOKMARK_SOURCE = '''bookmarks = {
    bookmark = {
        name = "TEST_START"
        desc = "TEST_START_DESC"
        date = 1936.1.1.12
        default_country = "ABC"
        "ABC" = {
            history = ABC_DESC
            ideology = democratic
            required_dlc = { "DLC One" "DLC Two" }
        }
        "ABC" = {
            available = { has_dlc = "Test DLC" }
            history = ABC_DLC_DESC
            ideology = neutrality # variant comment
        }
        "---" = { history = "OTHER_COUNTRIES_DESC" }
        effect = {
            randomize_weather = 22345 # obligatory bookmark seed
        }
        future_setting = yes
    }
}
'''


def _write_extended_mod(root: Path) -> None:
    (root / "common/ideologies").mkdir(parents=True)
    (root / "common/national_ideas").mkdir(parents=True)
    (root / "common/bookmarks").mkdir(parents=True)
    (root / "common/ideologies/custom.txt").write_text(IDEOLOGY_SOURCE, encoding="utf-8")
    (root / "common/national_ideas/dynamic.txt").write_text(
        DYNAMIC_SOURCE, encoding="utf-8"
    )
    (root / "common/bookmarks/start.txt").write_text(BOOKMARK_SOURCE, encoding="utf-8")


def test_extended_domains_load_update_preview_save_and_preserve_source(tmp_path: Path) -> None:
    _write_extended_mod(tmp_path)
    mod = Mod(tmp_path)

    assert mod.list_ideologies() == ["custom"]
    assert mod.list_dynamic_ideas() == ["ABC_growth"]
    assert mod.list_bookmarks() == ["TEST_START"]
    assert mod.get_bookmark("TEST_START").countries[0].required_dlc == [
        "DLC One",
        "DLC Two",
    ]

    assert mod.update_ideology("custom", color=(9, 8, 7))
    assert mod.update_dynamic_idea("ABC_growth", modifier={"political_power_gain": 0.2})
    assert mod.update_bookmark_country(
        "TEST_START", "ABC", occurrence=1, ideology="fascism"
    )
    assert mod.update_bookmark_country(
        "TEST_START", "ABC", required_dlc=["DLC Three", "DLC Four"]
    )
    assert mod.update_bookmark_country(
        "TEST_START", "---", history="UPDATED_OTHER_COUNTRIES_DESC"
    )
    assert mod.update_bookmark(
        "TEST_START", effect="{ randomize_weather = 54321 }"
    )
    preview = mod.preview()

    assert "9 8 7" in preview
    assert "political_power_gain" in preview
    assert "ideology = fascism" in preview
    assert "randomize_weather = 54321" in preview
    assert 'required_dlc = { "DLC Three" "DLC Four" }' in preview
    assert "UPDATED_OTHER_COUNTRIES_DESC" in preview
    result = mod.save()
    assert len(result.written_files) == 3

    ideology_text = (tmp_path / "common/ideologies/custom.txt").read_text(encoding="utf-8")
    dynamic_text = (tmp_path / "common/national_ideas/dynamic.txt").read_text(
        encoding="utf-8"
    )
    bookmark_text = (tmp_path / "common/bookmarks/start.txt").read_text(encoding="utf-8")
    assert "# color comment" in ideology_text
    assert "future_setting = { keep = yes }" in ideology_text
    assert "# modifier comment" in dynamic_text
    assert "future_setting = yes" in dynamic_text
    assert bookmark_text.count("ideology = democratic") == 1
    assert bookmark_text.count("ideology = fascism") == 1
    assert "# variant comment" in bookmark_text
    assert "randomize_weather = 54321" in bookmark_text
    assert "future_setting = yes" in bookmark_text
    assert 'required_dlc = { "DLC Three" "DLC Four" }' in bookmark_text
    assert '"---" = {' in bookmark_text
    assert "UPDATED_OTHER_COUNTRIES_DESC" in bookmark_text


def test_can_override_vanilla_ideology_into_mod_without_editing_game(tmp_path: Path) -> None:
    mod_root = tmp_path / "mod"
    game_root = tmp_path / "game"
    vanilla_path = game_root / "common/ideologies/vanilla.txt"
    vanilla_path.parent.mkdir(parents=True)
    vanilla_path.write_text(
        "ideologies = { democratic = { color = { 1 1 1 } future = yes } }\n",
        encoding="utf-8",
    )
    mod = Mod(mod_root, hoi4_install=game_root)

    assert mod.get_ideology("democratic").is_vanilla
    assert mod.update_ideology("democratic", color=(2, 3, 4))
    mod.save()

    assert vanilla_path.read_text(encoding="utf-8").count("1 1 1") == 1
    override = mod_root / "common/ideologies/00_mod_ideologies.txt"
    assert "2 3 4" in override.read_text(encoding="utf-8")
    assert "future = yes" in override.read_text(encoding="utf-8")


def test_create_extended_content_and_transaction_rollback(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    mod.create_ideology("new_ideology", color=(10, 20, 30))
    mod.create_dynamic_idea_group("ABC_dynamic")
    mod.create_dynamic_idea("ABC_dynamic", "ABC_new", modifier={"stability_factor": 0.1})
    bookmark = mod.create_bookmark(
        "NEW_START",
        default_country="ABC",
        countries=[BookmarkCountry("ABC"), BookmarkCountry("---")],
    )
    assert bookmark.name == "NEW_START"
    assert bookmark.effect == "randomize_weather = 22345"
    mod.save()

    bookmark_text = (tmp_path / "common/bookmarks/NEW_START.txt").read_text(
        encoding="utf-8"
    )
    assert "effect = {" in bookmark_text
    assert "randomize_weather = 22345" in bookmark_text
    assert '"---" = {' in bookmark_text

    reloaded = Mod(tmp_path)
    with reloaded.transaction():
        assert reloaded.update_dynamic_idea("ABC_new", modifier={"stability_factor": 0.2})
        assert "0.2" in reloaded.preview()
    assert reloaded.get_dynamic_idea("ABC_new").modifier["stability_factor"] == 0.1


def test_extended_duplicate_diagnostics_and_strict_loading(tmp_path: Path) -> None:
    for index in (1, 2):
        path = tmp_path / f"common/ideologies/{index}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ideologies = { duplicate = { color = { 1 2 3 } } }", encoding="utf-8")

    mod = Mod(tmp_path)
    errors = mod.validate()

    assert [error.code for error in errors].count("duplicate_ideology_id") == 1
    with pytest.raises(RuntimeError, match="Duplicate ideology"):
        Mod(tmp_path, strict_loading=True)


def test_validation_reports_progress_and_supports_cancellation(tmp_path: Path) -> None:
    _write_extended_mod(tmp_path)
    mod = Mod(tmp_path)
    events = []

    mod.validate(progress=events.append)

    assert events[0].phase == "load_diagnostics"
    assert events[-1].phase == "done"
    assert events[-1].fraction == 1.0
    with pytest.raises(OperationCancelled, match="validation cancelled"):
        mod.validate(cancelled=lambda: True)


def test_existing_country_update_does_not_emit_duplicate_generated_tag_and_delete_removes_source(
    tmp_path: Path,
) -> None:
    (tmp_path / "common/country_tags").mkdir(parents=True)
    (tmp_path / "common/countries").mkdir(parents=True)
    (tmp_path / "history/countries").mkdir(parents=True)
    tag_file = tmp_path / "common/country_tags/custom_tags.txt"
    tag_file.write_text('ABC = "countries/ABC.txt"\n', encoding="utf-8")
    (tmp_path / "common/countries/ABC.txt").write_text(
        "graphical_culture = western_european_gfx\n", encoding="utf-8"
    )
    (tmp_path / "history/countries/ABC - Test.txt").write_text(
        "capital = 1\nruling_party = democratic\n", encoding="utf-8"
    )
    mod = Mod(tmp_path)

    with mod.transaction():
        assert mod.update_country("ABC", capital=2)
        preview = mod.preview()
        assert "00_generated_tags.txt" not in preview

    assert mod.delete_country("ABC")
    assert "custom_tags.txt" in mod.preview()
    mod.save()
    assert not tag_file.exists() or "ABC =" not in tag_file.read_text(encoding="utf-8")


def test_delete_dynamic_group_preserves_unrelated_content_in_same_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "common/national_ideas/dynamic.txt"
    path.parent.mkdir(parents=True)
    path.write_text(
        DYNAMIC_SOURCE + "\n# unrelated footer\nother_setting = yes\n",
        encoding="utf-8",
    )
    mod = Mod(tmp_path)

    assert mod.delete_dynamic_idea_group("ABC_dynamic")
    mod.save()

    rendered = path.read_text(encoding="utf-8")
    assert "dynamic_country_ideas" not in rendered
    assert "# unrelated footer" in rendered
    assert "other_setting = yes" in rendered


def test_country_color_and_idea_assignment_match_studio_files(tmp_path: Path) -> None:
    (tmp_path / "common/country_tags").mkdir(parents=True)
    (tmp_path / "common/countries").mkdir(parents=True)
    (tmp_path / "history/countries").mkdir(parents=True)
    (tmp_path / "common/country_tags/tags.txt").write_text(
        'ABC = "countries/ABC.txt"\n', encoding="utf-8"
    )
    (tmp_path / "common/countries/ABC.txt").write_text(
        "graphical_culture = western_european_gfx\n", encoding="utf-8"
    )
    colors_path = tmp_path / "common/countries/colors.txt"
    colors_path.write_text(
        "# keep\nABC = { color = rgb { 1 2 3 } color_ui = rgb { 1 2 3 } future = yes }\n",
        encoding="utf-8",
    )
    history_path = tmp_path / "history/countries/ABC - Test.txt"
    history_path.write_text(
        "capital = 1\nadd_ideas = { old_idea }\nremove_ideas = blocked_idea\n",
        encoding="utf-8",
    )
    mod = Mod(tmp_path)

    assert mod.get_country("ABC").color == (1, 2, 3)
    assert mod.get_country("ABC").ideas == ["old_idea"]
    assert mod.update_country("ABC", color=(9, 8, 7), ideas=[])
    mod.save()

    colors = colors_path.read_text(encoding="utf-8")
    history = history_path.read_text(encoding="utf-8")
    assert "# keep" in colors and "future = yes" in colors
    assert colors.count("9 8 7") == 2
    assert "add_ideas" not in history
    assert "remove_ideas" not in history


def test_validation_covers_unmodeled_script_files_and_localization_bytes(
    tmp_path: Path,
) -> None:
    technology = tmp_path / "common/technologies/broken.txt"
    technology.parent.mkdir(parents=True)
    technology.write_text("technologies = {\n", encoding="utf-8")
    localization = tmp_path / "localisation/english/broken_l_english.yml"
    localization.parent.mkdir(parents=True)
    localization.write_text("not_a_header\n", encoding="utf-8")

    errors = Mod(tmp_path).validate()
    codes = {error.code for error in errors}

    assert {"script_syntax", "localization_bom", "localization_header"} <= codes
    syntax = next(error for error in errors if error.code == "script_syntax")
    assert syntax.line == 1


def test_validation_surfaces_non_duplicate_load_failures(tmp_path: Path) -> None:
    event_path = tmp_path / "events/broken.txt"
    event_path.parent.mkdir(parents=True)
    event_path.write_text("country_event = { id = broken.1", encoding="utf-8")

    mod = Mod(tmp_path)
    errors = mod.validate()

    assert any(error.code == "load_failure" for error in errors)


def test_bookmark_date_defines_participate_in_preview_transaction_and_save(
    tmp_path: Path,
) -> None:
    mod = Mod(tmp_path)
    target = tmp_path / "common/defines/zz_bookmark_dates.lua"

    with mod.transaction():
        assert mod.set_bookmark_date_range("1940.1.1.12", "1960.1.1.1") == target
        preview = mod.preview()
        assert "START_DATE" in preview and "END_DATE" in preview
    assert not target.exists()

    mod.set_bookmark_date_range("1940.1.1.12", "1960.1.1.1")
    mod.save()
    rendered = target.read_text(encoding="utf-8")
    assert 'NDefines.NGame.START_DATE = "1940.1.1.12"' in rendered
    assert 'NDefines.NGame.END_DATE = "1960.1.1.1"' in rendered


def test_updating_vanilla_country_creates_mod_override_not_game_write(
    tmp_path: Path,
) -> None:
    mod_root = tmp_path / "mod"
    game_root = tmp_path / "game"
    (game_root / "common/country_tags").mkdir(parents=True)
    (game_root / "common/countries").mkdir(parents=True)
    (game_root / "history/countries").mkdir(parents=True)
    tag_file = game_root / "common/country_tags/00_countries.txt"
    definition = game_root / "common/countries/Example.txt"
    history = game_root / "history/countries/ABC - Example.txt"
    tag_file.write_text('ABC = "countries/Example.txt"\n', encoding="utf-8")
    definition.write_text(
        "graphical_culture = western_european_gfx\ncolor = { 1 2 3 }\n",
        encoding="utf-8",
    )
    history.write_text("capital = 1\n# vanilla stays\n", encoding="utf-8")
    original_definition = definition.read_bytes()
    original_history = history.read_bytes()
    mod = Mod(mod_root, hoi4_install=game_root)

    assert mod.update_country("ABC", capital=2)
    mod.save()

    assert definition.read_bytes() == original_definition
    assert history.read_bytes() == original_history
    assert (mod_root / "common/countries/Example.txt").is_file()
    override = mod_root / "history/countries/ABC - Example.txt"
    assert "capital = 2" in override.read_text(encoding="utf-8")
    assert "# vanilla stays" in override.read_text(encoding="utf-8")
    assert not (mod_root / "common/country_tags/00_generated_tags.txt").exists()


def test_custom_ideology_drives_country_defaults_validation_and_save(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    mod.create_ideology("futurism", types=[SubIdeology("accelerant")])

    country = mod.create_country(
        "CUS",
        "Customland",
        ruling_party="futurism",
        popularities={"futurism": 100},
    )

    assert country.leader is not None
    assert country.leader.ideology == "accelerant"
    assert "futurism" in mod.available_ruling_parties()
    assert mod.available_leader_ideologies("futurism") == ("accelerant",)
    assert not any("invalid ruling party" in issue.message for issue in mod.validate())

    mod.save()
    history = next((tmp_path / "history/countries").glob("CUS*.txt")).read_text(
        encoding="utf-8"
    )
    localisation = (
        tmp_path / "localisation/english/CUS_country_l_english.yml"
    ).read_text(encoding="utf-8-sig")
    assert "futurism = 100" in history
    assert "ruling_party = futurism" in history
    assert "CUS_futurism:0" in localisation


def test_existing_country_leader_uses_recruited_character_identity(tmp_path: Path) -> None:
    (tmp_path / "common/country_tags").mkdir(parents=True)
    (tmp_path / "common/countries").mkdir(parents=True)
    (tmp_path / "history/countries").mkdir(parents=True)
    (tmp_path / "common/characters").mkdir(parents=True)
    (tmp_path / "common/country_tags/tags.txt").write_text(
        'ABC = "countries/ABC.txt"\n', encoding="utf-8"
    )
    (tmp_path / "common/countries/ABC.txt").write_text(
        "color = { 1 2 3 }\n", encoding="utf-8"
    )
    (tmp_path / "history/countries/ABC - Existing.txt").write_text(
        "capital = 1\nrecruit_character = ABC_actual\n",
        encoding="utf-8",
    )
    character_path = tmp_path / "common/characters/ABC.txt"
    character_path.write_text(
        """characters = {
    ABC_advisor = {
        name = "Advisor"
        advisor = { slot = political_advisor }
    }
    ABC_actual = {
        name = "Actual Leader" # keep leader note
        portraits = { civilian = { large = GFX_portrait_ABC_actual } }
        country_leader = {
            ideology = old_subtype
            future_setting = yes
        }
    }
}
""",
        encoding="utf-8",
    )
    mod = Mod(tmp_path)

    leader = mod.get_country("ABC").leader
    assert leader is not None
    assert leader.character_id == "ABC_actual"
    assert leader.name == "Actual Leader"
    assert leader.ideology == "old_subtype"

    assert mod.update_country(
        "ABC",
        leader_name="Edited Leader",
        leader_ideology="new_subtype",
        leader_portrait_slug="replacement",
    )
    mod.save()

    rendered = character_path.read_text(encoding="utf-8")
    assert 'name = "Advisor"' in rendered
    assert 'name = "Edited Leader" # keep leader note' in rendered
    assert "ideology = new_subtype" in rendered
    assert "future_setting = yes" in rendered
    assert "large = GFX_portrait_ABC_replacement" in rendered
    assert "ABC_leader_1" not in rendered
    loc = (tmp_path / "localisation/english/ABC_country_l_english.yml").read_text(
        encoding="utf-8-sig"
    )
    assert "ABC_actual:0" in loc
    assert "ABC_leader_1" not in loc
