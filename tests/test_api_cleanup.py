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
    "create_scripted_trigger",
    "create_scripted_effect",
    "create_country_leader_trait",
    "create_unit_leader_trait",
    "create_technology",
    "create_sub_unit",
    "upsert_focus",
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


# ── update_* ────────────────────────────────────────────────────────


@pytest.fixture
def full_mod(tmp_path) -> Mod:
    """A mod with one object of every kind that has an update method."""
    from hoi4 import (
        BookmarkCountry,
        Character,
        CharacterInstance,
        CountryLeaderRole,
        EventOption,
        Focus,
    )

    mod = Mod(tmp_path)
    mod.create_country("ABC", "Abcland")
    mod.create_bookmark("zz_bm", countries=[BookmarkCountry(tag="ABC")])
    mod.create_character(
        "ABC",
        Character(
            id="ABC_hero",
            roles=[CountryLeaderRole(ideology="liberalism")],
            instances=[CharacterInstance()],
        ),
    )
    mod.create_decision_category("zz_cat")
    mod.create_decision("zz_cat", "zz_dec")
    mod.create_dynamic_modifier("zz_dm")
    mod.create_event("zz.1", options=[EventOption(name="zz.1.a")])
    mod.create_focus_tree("zz_tree", "ABC")
    mod.add_focus("zz_tree", Focus(id="ABC_f"))
    mod.create_idea("zz_idea")
    mod.create_ideology("zz_ideo")
    mod.create_on_action("on_startup", effect="add_political_power = 1")
    mod.create_oob("ABC_1936", "ABC")
    return mod


def test_every_update_returns_the_object_it_changed(full_mod: Mod) -> None:
    from hoi4 import (
        Bookmark,
        BookmarkCountry,
        Character,
        CharacterInstance,
        CountryLeaderRole,
        Country,
        Decision,
        DecisionCategory,
        DynamicModifier,
        Event,
        EventOption,
        Focus,
        FocusTree,
        Idea,
        Ideology,
        OnAction,
        OrderOfBattle,
    )

    mod = full_mod
    cases = [
        (mod.update_bookmark("zz_bm", description="d"), Bookmark),
        (mod.update_bookmark_country("zz_bm", "ABC", history="h"), BookmarkCountry),
        (mod.update_character("ABC_hero", name="Hero"), Character),
        (mod.update_character_instance("ABC_hero", 0, name="Young hero"), CharacterInstance),
        (mod.update_character_role("ABC_hero", "country_leader", traits=[]), CountryLeaderRole),
        (mod.update_country("ABC", stability=0.5), Country),
        (mod.update_decision("zz_dec", cost=10), Decision),
        (mod.update_decision_category("zz_cat", icon="generic"), DecisionCategory),
        (mod.update_dynamic_modifier("zz_dm", icon="x"), DynamicModifier),
        (mod.update_event("zz.1", title="zz.1.t"), Event),
        (mod.update_event_option("zz.1", "zz.1.a", effect="add_stability = 0.1"), EventOption),
        (mod.update_focus("zz_tree", "ABC_f", cost=5), Focus),
        (mod.update_focus_tree("zz_tree", default=False), FocusTree),
        (mod.update_idea("zz_idea", removal_cost=1), Idea),
        (mod.update_ideology("zz_ideo", color=(1, 2, 3)), Ideology),
        (mod.update_on_action("on_startup", effect="add_stability = 0.1"), OnAction),
        (mod.update_oob("ABC_1936", templates=[]), OrderOfBattle),
    ]
    for result, expected in cases:
        assert isinstance(result, expected), (result, expected)
    assert mod.update_focus("zz_tree", "ABC_f", cost=7) is mod.get_focus("zz_tree", "ABC_f")


def _missing_updates(mod: Mod) -> dict[str, object]:
    return {
        "update_bookmark": lambda: mod.update_bookmark("zz_missing", description="d"),
        "update_bookmark_country": lambda: mod.update_bookmark_country("zz_bm", "GER", history="h"),
        "update_bookmark_country occurrence": lambda: mod.update_bookmark_country(
            "zz_bm", "ABC", occurrence=1, history="h"
        ),
        "update_character": lambda: mod.update_character("ABC_missing", name="x"),
        "update_character_instance": lambda: mod.update_character_instance("ABC_hero", 4, name="x"),
        "update_character_role": lambda: mod.update_character_role("ABC_hero", "advisor", cost=1),
        "update_character_role occurrence": lambda: mod.update_character_role(
            "ABC_hero", "country_leader", occurrence=2, traits=[]
        ),
        "update_country": lambda: mod.update_country("ZZX", stability=0.5),
        "update_decision": lambda: mod.update_decision("zz_missing", cost=1),
        "update_decision_category": lambda: mod.update_decision_category("zz_missing", icon="x"),
        "update_dynamic_modifier": lambda: mod.update_dynamic_modifier("zz_missing", icon="x"),
        "update_event": lambda: mod.update_event("zz.9", title="t"),
        "update_event_option index": lambda: mod.update_event_option("zz.1", 5, effect="x = 1"),
        "update_event_option name": lambda: mod.update_event_option("zz.1", "zz.1.b", effect="x = 1"),
        "update_focus": lambda: mod.update_focus("zz_tree", "ABC_missing", cost=1),
        "update_focus tree": lambda: mod.update_focus("zz_missing", "ABC_f", cost=1),
        "update_focus_tree": lambda: mod.update_focus_tree("zz_missing", default=False),
        "update_idea": lambda: mod.update_idea("zz_missing", removal_cost=1),
        "update_ideology": lambda: mod.update_ideology("zz_missing", color=(1, 2, 3)),
        "update_on_action": lambda: mod.update_on_action("on_zz_missing", effect="x = 1"),
        "update_oob": lambda: mod.update_oob("ABC_missing", templates=[]),
    }


@pytest.mark.parametrize("case", sorted(_missing_updates(Mod.__new__(Mod))))
def test_every_update_raises_not_found_for_a_missing_target_or_child(full_mod: Mod, case: str) -> None:
    before = full_mod.preview()
    with pytest.raises(NotFoundError):
        _missing_updates(full_mod)[case]()
    assert full_mod.preview() == before


def test_an_unknown_field_names_the_method_and_changes_nothing(full_mod: Mod) -> None:
    mod = full_mod
    before = mod.preview()
    with pytest.raises(TypeError, match=r"update_idea\(\): Unknown fields: \['zz_bogus'\]\. Valid: \["):
        mod.update_idea("zz_idea", modifier={"stability_factor": 0.1}, zz_bogus=1)
    with pytest.raises(TypeError, match=r"update_character\(\): Unknown fields: .* Valid: \['name', 'portraits'\]"):
        mod.update_character("ABC_hero", zz_bogus=1)
    with pytest.raises(TypeError, match=r"update_character_instance\(\): .*Valid: \['allowed', 'name', 'portraits'\]"):
        mod.update_character_instance("ABC_hero", 0, zz_bogus=1)
    with pytest.raises(TypeError, match=r"update_country\(\): Unknown fields: \['leader_zz'\]"):
        mod.update_country("ABC", stability=0.2, leader_zz=1)
    with pytest.raises(TypeError, match=r"update_decision\(\): Unknown fields: \['path'\]"):
        mod.update_decision("zz_dec", path="common/decisions/other.txt")
    assert mod.get_idea("zz_idea").modifier == {}
    assert mod.preview() == before


@pytest.mark.parametrize(
    ("method", "call"),
    [
        ("update_event_option", lambda mod: mod.update_event_option("zz.1", 0, _source_body="x")),
        ("update_bookmark", lambda mod: mod.update_bookmark("zz_bm", touched=True)),
        ("update_on_action", lambda mod: mod.update_on_action("on_startup", touched=True)),
        ("update_focus", lambda mod: mod.update_focus("zz_tree", "ABC_f", touched=True)),
        ("update_ideology", lambda mod: mod.update_ideology("zz_ideo", is_vanilla=True)),
    ],
)
def test_bookkeeping_fields_are_not_accepted(full_mod: Mod, method: str, call) -> None:
    with pytest.raises((TypeError, ValueError)):
        call(full_mod)


def test_bookkeeping_fields_are_not_listed_as_valid(full_mod: Mod) -> None:
    with pytest.raises(TypeError) as caught:
        full_mod.update_event_option("zz.1", 0, zz_bogus=1)
    assert "_source_body" not in str(caught.value)
    with pytest.raises(TypeError) as caught:
        full_mod.update_bookmark("zz_bm", zz_bogus=1)
    assert "source_index" not in str(caught.value)
    with pytest.raises(TypeError) as caught:
        full_mod.update_decision_category("zz_cat", zz_bogus=1)
    assert "'paths'" not in str(caught.value)


# ── add_*, remove_*, set_*, insert_*, assign_* ──────────────────────


def test_focus_additions_return_the_focus_and_refuse_duplicates(full_mod: Mod) -> None:
    from hoi4 import AlreadyExistsError, Focus

    mod = full_mod
    added = mod.add_focus("zz_tree", Focus(id="ABC_g", x=2))
    assert isinstance(added, Focus) and added.id == "ABC_g"
    with pytest.raises(AlreadyExistsError, match="Focus 'ABC_g' already exists"):
        mod.add_focus("zz_tree", Focus(id="ABC_g"))
    inserted = mod.insert_focus_after("zz_tree", "ABC_f", Focus(id="ABC_h"))
    assert inserted.prerequisites == [["ABC_f"]]
    with pytest.raises(AlreadyExistsError):
        mod.insert_focus_after("zz_tree", "ABC_f", Focus(id="ABC_h"))
    before = [focus.id for focus in mod.get_focus_tree("zz_tree").focuses]
    with pytest.raises(AlreadyExistsError):
        mod.insert_branch("zz_tree", "ABC_f", [Focus(id="ABC_i"), Focus(id="ABC_g")])
    assert [focus.id for focus in mod.get_focus_tree("zz_tree").focuses] == before
    branch = mod.insert_branch("zz_tree", "ABC_f", [Focus(id="ABC_i"), Focus(id="ABC_j")])
    assert [focus.id for focus in branch] == ["ABC_i", "ABC_j"]
    with pytest.raises(NotFoundError, match="Focus 'ABC_nope' not found in focus tree 'zz_tree'"):
        mod.auto_layout_branch("zz_tree", [Focus(id="ABC_k")], anchor_focus_id="ABC_nope")
    rewarded = mod.append_to_focus_reward("zz_tree", "ABC_f", "add_stability = 0.1")
    assert rewarded.completion_reward == "add_stability = 0.1"
    with pytest.raises(NotFoundError):
        mod.append_to_focus_reward("zz_tree", "ABC_nope", "add_stability = 0.1")
    assert mod.set_focuses_mutually_exclusive("zz_tree", "ABC_i", "ABC_j") is None
    with pytest.raises(NotFoundError):
        mod.set_focuses_mutually_exclusive("zz_tree", "ABC_i", "ABC_nope")
    assert mod.remove_focus("zz_tree", "ABC_j") is None
    with pytest.raises(NotFoundError):
        mod.remove_focus("zz_tree", "ABC_j")


def test_assert_no_visual_overlap_returns_none(full_mod: Mod) -> None:
    assert full_mod.assert_no_visual_overlap("zz_tree") is None


def test_event_options_and_namespaces(full_mod: Mod) -> None:
    from hoi4 import AlreadyExistsError, EventOption

    mod = full_mod
    added = mod.add_event_option("zz.1", EventOption(name="zz.1.b"))
    assert isinstance(added, EventOption) and added.name == "zz.1.b"
    with pytest.raises(AlreadyExistsError, match="Event option 'zz.1.b' already exists"):
        mod.add_event_option("zz.1", EventOption(name="zz.1.b"))
    with pytest.raises(NotFoundError):
        mod.add_event_option("zz.9", EventOption(name="zz.9.a"))
    with pytest.raises(NotFoundError):
        mod.set_event_namespace("zz.9", "zz")


def test_bookmark_country_additions_refuse_duplicates(full_mod: Mod) -> None:
    from hoi4 import AlreadyExistsError, BookmarkCountry

    added = full_mod.add_bookmark_country("zz_bm", BookmarkCountry(tag="GER"))
    assert isinstance(added, BookmarkCountry) and added.tag == "GER"
    with pytest.raises(AlreadyExistsError, match="Bookmark country 'GER' already exists"):
        full_mod.add_bookmark_country("zz_bm", BookmarkCountry(tag="GER"))
    # The same tag behind another DLC gate is a different entry.
    full_mod.add_bookmark_country(
        "zz_bm", BookmarkCountry(tag="GER", required_dlc=("Man the Guns",))
    )
    assert full_mod.set_bookmark_date_range("1936.1.1.12", "1950.1.1.1") is None


def test_character_roles_raise_for_a_missing_role(full_mod: Mod) -> None:
    with pytest.raises(NotFoundError, match="has no 'advisor' role"):
        full_mod.remove_character_role("ABC_hero", "advisor")
    assert full_mod.remove_character_role("ABC_hero", "country_leader") is None


def test_oob_assignments_raise_instead_of_inventing(full_mod: Mod) -> None:
    mod = full_mod
    with pytest.raises(NotFoundError, match="has no land OOB assignment of 'ABC_1936'"):
        mod.unassign_country_oob("ABC", "ABC_1936", date="1939.1.1")
    assert mod.unassign_country_oob("ABC", "ABC_1936") is None
    with pytest.raises(NotFoundError, match="Country 'ZZX' not found"):
        mod.assign_country_oob("ZZX", "ABC_1936")
    assert "ZZX" not in mod.list_countries()


def test_equipment_variants_need_a_defined_country(tmp_path) -> None:
    from hoi4 import EquipmentVariant

    mod = Mod(tmp_path)
    with pytest.raises(NotFoundError, match="Country 'ZZX' not found"):
        mod.create_equipment_variant(
            "ZZX", EquipmentVariant(name="V", equipment_type="light_tank_chassis_1")
        )
    assert mod.preview() == ""


def test_remove_state_core_raises_for_a_tag_that_is_not_a_core(tmp_path, game) -> None:
    _write(game, "history/states/1-Berlin.txt", "state = { id = 1 history = { owner = GER add_core_of = GER } provinces = { 1 } }\n")
    mod = Mod(tmp_path / "mod", hoi4_install=game)
    with pytest.raises(NotFoundError, match="Core 'POL' not found in state 1"):
        mod.remove_state_core(1, "POL")
    assert mod.preview() == ""  # nothing was copied into the mod
    assert mod.remove_state_core(1, "GER") is None


def test_references_to_an_undefined_country_raise_when_the_game_is_known(tmp_path, game) -> None:
    mod = Mod(tmp_path / "mod", hoi4_install=game)
    _write(game, "history/states/1-Berlin.txt", "state = { id = 1 history = { owner = GER } provinces = { 1 } }\n")
    for call in (
        lambda: mod.create_focus_tree("zz_tree", "ZZX"),
        lambda: mod.create_oob("ZZX_1936", "ZZX", assign=False),
        lambda: mod.set_country_name_pool("ZZX", male_names=["A"], surnames=["B"]),
        lambda: mod.add_state_core(1, "ZZX"),
        lambda: mod.set_state_owner(1, "ZZX"),
    ):
        with pytest.raises(NotFoundError, match="Country 'ZZX' not found"):
            call()
    assert mod.preview() == ""
    # A tag the game defines is fine.
    mod.create_focus_tree("ger_tree", "GER")


def test_references_are_accepted_without_a_game_install(tmp_path) -> None:
    # Without a game install the tag may be the game's; validate() checks it.
    mod = Mod(tmp_path)
    mod.create_focus_tree("ger_tree", "GER")
    mod.create_oob("GER_new", "GER", assign=False)
    mod.set_country_name_pool("GER", male_names=["A"], surnames=["B"])


def test_create_decision_never_invents_a_category_definition(tmp_path, game) -> None:
    _write(game, "common/decisions/categories/00_cat.txt", "political_actions = { icon = generic }\n")
    mod = Mod(tmp_path / "mod", hoi4_install=game)
    mod.create_decision("political_actions", "zz_dec")
    preview = mod.preview()
    assert "common/decisions/mod_decisions.txt" in preview
    assert "categories" not in preview
    with pytest.raises(NotFoundError, match="Decision category 'zz_nope' not found"):
        mod.create_decision("zz_nope", "zz_dec2")

    no_game = Mod(tmp_path / "plain")
    no_game.create_decision("political_actions", "zz_dec")
    assert "categories" not in no_game.preview()


# ── get_* ───────────────────────────────────────────────────────────


def _missing_lookups(mod: Mod) -> dict[str, object]:
    return {
        "get_bookmark": lambda **kw: mod.get_bookmark("zz_missing", **kw),
        "get_character": lambda **kw: mod.get_character("ABC_missing", **kw),
        "get_country": lambda **kw: mod.get_country("ZZX", **kw),
        "get_country_context": lambda **kw: mod.get_country_context("ZZX", **kw),
        "get_decision": lambda **kw: mod.get_decision("zz_missing", **kw),
        "get_decision_category": lambda **kw: mod.get_decision_category("zz_missing", **kw),
        "get_definition": lambda **kw: mod.get_definition("scripted_effect", "zz_missing", **kw),
        "get_dynamic_modifier": lambda **kw: mod.get_dynamic_modifier("zz_missing", **kw),
        "get_event": lambda **kw: mod.get_event("zz.9", **kw),
        "get_focus": lambda **kw: mod.get_focus("zz_tree", "ABC_missing", **kw),
        "get_focus missing tree": lambda **kw: mod.get_focus("zz_missing", "ABC_f", **kw),
        "get_focus_tree": lambda **kw: mod.get_focus_tree("zz_missing", **kw),
        "get_idea": lambda **kw: mod.get_idea("zz_missing", **kw),
        "get_ideology": lambda **kw: mod.get_ideology("zz_missing", **kw),
        "get_loc": lambda **kw: mod.get_loc("ZZ_MISSING", **kw),
        "get_on_action": lambda **kw: mod.get_on_action("on_zz_missing", **kw),
        "get_on_action occurrence": lambda **kw: mod.get_on_action("on_startup", occurrence=4, **kw),
        "get_on_action_occurrences": lambda **kw: mod.get_on_action_occurrences("on_zz_missing", **kw),
        "get_oob": lambda **kw: mod.get_oob("ABC_missing", **kw),
        "get_state": lambda **kw: mod.get_state(99999, **kw),
        "get_strategic_region": lambda **kw: mod.get_strategic_region(99999, **kw),
    }


@pytest.mark.parametrize("case", sorted(_missing_lookups(Mod.__new__(Mod))))
def test_every_lookup_raises_or_returns_the_default(full_mod: Mod, case: str) -> None:
    lookup = _missing_lookups(full_mod)[case]
    with pytest.raises(NotFoundError):
        lookup()
    sentinel = object()
    assert lookup(default=sentinel) is sentinel
    assert lookup(default=None) is None


def test_a_found_lookup_ignores_the_default(full_mod: Mod) -> None:
    assert full_mod.get_event("zz.1", default=None).id == "zz.1"
    assert full_mod.get_focus("zz_tree", "ABC_f", default=None).id == "ABC_f"


def test_get_country_never_invents_or_caches_a_phantom(tmp_path) -> None:
    mod = Mod(tmp_path)
    with pytest.raises(NotFoundError):
        mod.get_country("ZZX")
    assert "ZZX" not in mod.list_countries()
    assert mod.is_country_tag_available("ZZX")
    assert mod.get_country("ZZX", default=None) is None
    assert "ZZX" not in mod.list_countries()


def test_get_country_reads_a_game_country(tmp_path, game) -> None:
    mod = Mod(tmp_path / "mod", hoi4_install=game)
    assert mod.get_country("GER").tag == "GER"
    assert mod.get_country_context("GER")["tag"] == "GER"


# ── ensure_* ────────────────────────────────────────────────────────


def test_ensure_methods_return_the_object_on_both_paths(full_mod: Mod) -> None:
    from hoi4 import Decision, DecisionCategory, Event, Focus, FocusTree, Idea, OnAction

    mod = full_mod
    for result, expected in [
        (mod.ensure_event("zz.1", title="zz.1.t"), Event),
        (mod.ensure_event("zz.2", title="zz.2.t"), Event),
        (mod.ensure_idea("zz_idea", removal_cost=2), Idea),
        (mod.ensure_idea("zz_idea2", removal_cost=2), Idea),
        (mod.ensure_decision("zz_cat", "zz_dec", cost=3), Decision),
        (mod.ensure_decision("zz_cat", "zz_dec2", cost=3), Decision),
        (mod.ensure_decision_category("zz_cat", icon="x"), DecisionCategory),
        (mod.ensure_decision_category("zz_cat2", icon="x"), DecisionCategory),
        (mod.ensure_on_action("on_startup", effect="add_stability = 0.1"), OnAction),
        (mod.ensure_on_action("on_daily", effect="add_stability = 0.1"), OnAction),
        (mod.ensure_focus_tree("zz_tree", "ABC", default=True), FocusTree),
        (mod.ensure_focus_tree("zz_tree2", "ABC", default=False), FocusTree),
        (mod.ensure_focus("zz_tree", Focus(id="ABC_f", cost=3)), Focus),
        (mod.ensure_focus("zz_tree", Focus(id="ABC_new")), Focus),
    ]:
        assert isinstance(result, expected), (result, expected)
    assert mod.get_focus("zz_tree", "ABC_f").cost == 3
    assert not hasattr(mod, "upsert_focus")


@pytest.mark.parametrize(
    ("method", "call"),
    [
        ("ensure_event", lambda mod, new: mod.ensure_event("zz.9" if new else "zz.1", zz_bogus=1)),
        ("ensure_idea", lambda mod, new: mod.ensure_idea("zz_new" if new else "zz_idea", zz_bogus=1)),
        ("ensure_decision", lambda mod, new: mod.ensure_decision("zz_cat", "zz_new" if new else "zz_dec", zz_bogus=1)),
        ("ensure_decision_category", lambda mod, new: mod.ensure_decision_category("zz_new" if new else "zz_cat", zz_bogus=1)),
        ("ensure_on_action", lambda mod, new: mod.ensure_on_action("on_daily" if new else "on_startup", zz_bogus=1)),
        ("ensure_focus_tree", lambda mod, new: mod.ensure_focus_tree("zz_new" if new else "zz_tree", "ABC", zz_bogus=1)),
    ],
)
@pytest.mark.parametrize("new", [True, False])
def test_ensure_names_itself_for_an_unknown_field_and_changes_nothing(
    full_mod: Mod, method: str, call, new: bool
) -> None:
    before = full_mod.preview()
    with pytest.raises(TypeError, match=rf"^{method}\(\): Unknown fields: \['zz_bogus'\]\. Valid: "):
        call(full_mod, new)
    assert full_mod.preview() == before


def test_ensure_refuses_a_different_parent(full_mod: Mod) -> None:
    full_mod.create_country("DEF", "Defland")
    with pytest.raises(ValueError, match="belongs to 'ABC', not 'DEF'"):
        full_mod.ensure_focus_tree("zz_tree", "DEF")
    full_mod.create_decision_category("zz_other")
    with pytest.raises(ValueError, match="is in category 'zz_cat', not 'zz_other'"):
        full_mod.ensure_decision("zz_other", "zz_dec", cost=1)


# ── definitions ─────────────────────────────────────────────────────


def test_definition_writers_return_the_definition(tmp_path) -> None:
    from hoi4 import AlreadyExistsError, ScriptDefinition

    mod = Mod(tmp_path)
    created = mod.create_definition("scripted_trigger", "zz_trigger", "always = yes")
    assert isinstance(created, ScriptDefinition)
    assert created.relative_path == "common/scripted_triggers/00_sdk_generated.txt"
    assert created.pending
    updated = mod.update_definition("scripted_trigger", "zz_trigger", "always = no")
    assert isinstance(updated, ScriptDefinition) and "always = no" in updated.body
    with pytest.raises(AlreadyExistsError):
        mod.create_definition("scripted_trigger", "zz_trigger", "always = yes")
    for result in (
        mod.create_static_modifier("zz_static", {"stability_factor": 0.1}),
        mod.create_opinion_modifier("zz_opinion", value=10),
        mod.create_ai_strategy("zz_plan", [("antagonize", "GER", 100)]),
        mod.create_scripted_localisation("GetZz", [("", "ZZ_KEY")]),
    ):
        assert isinstance(result, ScriptDefinition)
    assert mod.list_definitions("scripted_trigger") == ["zz_trigger"]


# ── create_* ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "call",
    [
        lambda mod: mod.create_country("ABC", "Again"),
        lambda mod: mod.create_bookmark("zz_bm"),
        lambda mod: mod.create_decision_category("zz_cat"),
        lambda mod: mod.create_decision("zz_cat", "zz_dec"),
        lambda mod: mod.create_dynamic_modifier("zz_dm"),
        lambda mod: mod.create_event("zz.1"),
        lambda mod: mod.create_focus_tree("zz_tree", "ABC"),
        lambda mod: mod.create_idea("zz_idea"),
        lambda mod: mod.create_ideology("zz_ideo"),
        lambda mod: mod.create_on_action("on_startup"),
        lambda mod: mod.create_oob("ABC_1936", "ABC"),
    ],
)
def test_creating_an_existing_id_raises_already_exists(full_mod: Mod, call) -> None:
    from hoi4 import AlreadyExistsError

    before = full_mod.preview()
    with pytest.raises(AlreadyExistsError):
        call(full_mod)
    assert full_mod.preview() == before
