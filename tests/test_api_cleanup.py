"""The 0.7.0 cleanup of the public ``Mod`` API: what is gone and what replaced it."""

from __future__ import annotations

import pytest

from hoi4 import Mod, NotFoundError, ReadOnlyError

# Removed in 0.7.0; CHANGELOG.md names the replacement of each one.
REMOVED_MOD_NAMES = [
    "declare_war",
    "create_wargoal",
    "start_civil_war",
    "reload",
    "effect_block",
    "scope_block",
    "ruling_parties",
    "leader_ideologies_for_party",
    "default_leader_ideology",
    "suggest_tag",
    "suggest_focus_icon",
    "suggest_idea_icon",
]


@pytest.mark.parametrize("name", REMOVED_MOD_NAMES)
def test_removed_mod_names_are_gone(name: str) -> None:
    assert not hasattr(Mod, name)


def test_removed_module_functions_are_gone() -> None:
    from hoi4 import bookmarks, decisions, focus, ideologies, localisation, on_actions
    from hoi4 import states, tags, validation

    for module, name in [
        (bookmarks, "load_bookmarks"),
        (decisions, "write_decisions_file"),
        (focus, "write_focus_tree"),
        (ideologies, "load_ideologies"),
        (localisation, "delete_localization_keys"),
        (on_actions, "write_on_actions_file"),
        (states, "ensure_state_in_mod"),
        (states, "patch_state_history_owner_cores"),
        (tags, "load_all_tags"),
        (validation, "collect_character_ids"),
    ]:
        assert not hasattr(module, name), f"{module.__name__}.{name}"


def test_create_idea_and_set_politics_lost_their_legacy_arguments(tmp_path) -> None:
    mod = Mod(tmp_path)
    with pytest.raises(TypeError):
        mod.create_idea("ABC_spirit", desc="ABC_spirit_desc")
    with pytest.raises(TypeError):
        Mod.effect_set_politics("democratic", elections_frequency=48)


def test_an_unknown_ruling_party_still_gets_a_default_leader_ideology(tmp_path) -> None:
    mod = Mod(tmp_path)
    country = mod.create_country("ABC", "Abcland", ruling_party="zzparty")
    assert country.leader is not None
    assert country.leader.ideology == "liberalism"


# ── delete_* ────────────────────────────────────────────────────────


def _write(root, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def game(tmp_path):
    """A tiny game install with one country, ideology, OOB and localisation key."""
    root = tmp_path / "game"
    _write(root, "common/country_tags/00_countries.txt", 'GER = "countries/Germany.txt"\n')
    _write(root, "common/countries/Germany.txt", "color = { 1 2 3 }\n")
    _write(root, "history/countries/GER - Germany.txt", "capital = 1\n")
    _write(
        root,
        "common/ideologies/00_ideologies.txt",
        "ideologies = { fascism = { types = { nazism = { } } } }\n",
    )
    _write(root, "history/units/GER_1936.txt", "units = { }\n")
    _write(root, "localisation/english/game_l_english.yml", 'l_english:\n GAME_key:0 "Text"\n')
    return root


def _missing_deletes(mod: Mod) -> dict[str, object]:
    return {
        "delete_bookmark": lambda: mod.delete_bookmark("zz_missing"),
        "delete_character": lambda: mod.delete_character("ABC_missing"),
        "delete_country": lambda: mod.delete_country("ZZX"),
        "delete_decision": lambda: mod.delete_decision("zz_missing"),
        "delete_definition": lambda: mod.delete_definition("scripted_effect", "zz_missing"),
        "delete_dynamic_modifier": lambda: mod.delete_dynamic_modifier("zz_missing"),
        "delete_event": lambda: mod.delete_event("zz.1"),
        "delete_focus_tree": lambda: mod.delete_focus_tree("zz_missing"),
        "delete_idea": lambda: mod.delete_idea("zz_missing"),
        "delete_ideology": lambda: mod.delete_ideology("zz_missing"),
        "delete_loc": lambda: mod.delete_loc("ZZ_MISSING"),
        "delete_on_action": lambda: mod.delete_on_action("on_zz_missing"),
        "delete_oob": lambda: mod.delete_oob("ZZX_missing"),
    }


@pytest.mark.parametrize("method", sorted(_missing_deletes(Mod.__new__(Mod))))
def test_every_delete_raises_not_found_for_a_missing_target(tmp_path, method: str) -> None:
    mod = Mod(tmp_path)
    with pytest.raises(NotFoundError):
        _missing_deletes(mod)[method]()
    assert mod.preview() == ""


def test_a_missing_child_raises_not_found(tmp_path) -> None:
    from hoi4 import BookmarkCountry, Character, CharacterInstance

    mod = Mod(tmp_path)
    mod.create_country("ABC", "Abcland")
    mod.create_bookmark("zz_bm", countries=[BookmarkCountry(tag="ABC")])
    with pytest.raises(NotFoundError, match="has no entry 1 for country 'ABC'"):
        mod.delete_bookmark_country("zz_bm", "ABC", occurrence=1)
    with pytest.raises(NotFoundError, match="has no entry 0 for country 'GER'"):
        mod.delete_bookmark_country("zz_bm", "GER")
    mod.create_character("ABC", Character(id="ABC_hero", instances=[CharacterInstance()]))
    with pytest.raises(NotFoundError, match="no instance occurrence 3"):
        mod.delete_character_instance("ABC_hero", 3)


def test_deletes_return_none(tmp_path) -> None:
    from hoi4 import BookmarkCountry

    mod = Mod(tmp_path)
    mod.create_country("ABC", "Abcland")
    mod.create_event("zz.1")
    mod.create_bookmark("zz_bm", countries=[BookmarkCountry(tag="ABC")])
    mod.set_loc("ZZ_KEY", "text")
    mod.create_definition("scripted_effect", "zz_effect", "add_political_power = 1")
    assert mod.delete_event("zz.1") is None
    assert mod.delete_bookmark_country("zz_bm", "ABC") is None
    assert mod.delete_bookmark("zz_bm") is None
    assert mod.delete_loc("ZZ_KEY") is None
    assert mod.delete_definition("scripted_effect", "zz_effect") is None
    assert mod.delete_country("ABC") is None


def test_deleting_game_content_raises_read_only(tmp_path, game) -> None:
    mod = Mod(tmp_path / "mod", hoi4_install=game)
    with pytest.raises(ReadOnlyError, match="game country 'GER'"):
        mod.delete_country("GER")
    mod.get_country("GER")  # loaded from the game: still read only
    with pytest.raises(ReadOnlyError, match="game country 'GER'"):
        mod.delete_country("GER")
    with pytest.raises(ReadOnlyError, match="Ideology 'fascism' is game content"):
        mod.delete_ideology("fascism")
    with pytest.raises(ReadOnlyError, match="game OOB 'GER_1936'"):
        mod.delete_oob("GER_1936")
    with pytest.raises(ReadOnlyError, match="game's own files"):
        mod.delete_loc("GAME_key")
    assert mod.preview() == ""
