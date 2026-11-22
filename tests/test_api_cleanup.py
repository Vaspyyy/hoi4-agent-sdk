"""The 0.7.0 cleanup of the public ``Mod`` API: what is gone and what replaced it."""

from __future__ import annotations

import pytest

import hoi4
from hoi4 import Mod, NotFoundError, ReadOnlyError, effects

# Removed in 0.7.0; CHANGELOG.md names the replacement of each one.
REMOVED_MOD_NAMES = [
    "declare_war",
    "create_wargoal",
    "start_civil_war",
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


def test_mod_has_no_effect_builders_left() -> None:
    assert [name for name in dir(Mod) if name.startswith("effect_")] == []


def test_the_builders_live_in_hoi4_effects() -> None:
    assert "effects" in hoi4.__all__
    assert not hasattr(hoi4, "effect_block") and not hasattr(hoi4, "scope_block")
    from hoi4 import script

    assert not hasattr(script, "effect_block") and not hasattr(script, "scope_block")
    assert len(effects.__all__) == 54  # 52 builders plus block and scope_block
    assert all(callable(getattr(effects, name)) for name in effects.__all__)
    assert not any(name.startswith("effect_") for name in effects.__all__)
    assert effects.add_political_power(50) == "add_political_power = 50"
    assert effects.transfer_state(115, "SCL") == "SCL = { transfer_state = 115 }"
    assert effects.block("set_rule", {"can_create_factions": True}) == (
        "set_rule = { can_create_factions = yes }"
    )
    assert effects.scope_block("GER", "add_stability = 0.1") == "GER = { add_stability = 0.1 }"


def test_builder_errors_name_the_new_function() -> None:
    with pytest.raises(ValueError, match=r"effects\.upgrade_idea_chain\(\)"):
        effects.upgrade_idea_chain(["only_one"])
    with pytest.raises(ValueError, match=r"effects\.spawn_revolution\(\)"):
        effects.spawn_revolution("ABC", [])


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
        effects.set_politics("democratic", elections_frequency=48)  # type: ignore[call-arg]


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
    with pytest.raises(AlreadyExistsError, match=r"Event option 'zz\.1\.b' already exists"):
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


# ── state setters ───────────────────────────────────────────────────


@pytest.fixture
def state_game(tmp_path, game):
    for state_id in (1, 2):
        _write(
            game,
            f"history/states/{state_id}-S.txt",
            f"state = {{ id = {state_id} name = STATE_{state_id} manpower = 1 "
            f"history = {{ owner = GER add_core_of = GER add_core_of = POL }} provinces = {{ {state_id} }} }}\n",
        )
    _write(game, "common/country_tags/00_countries.txt", 'GER = "countries/Germany.txt"\nPOL = "countries/Poland.txt"\nSOV = "countries/Soviet.txt"\n')
    return game


def test_set_state_owner_takes_one_state_or_many_and_is_atomic(tmp_path, state_game) -> None:
    mod = Mod(tmp_path / "mod", hoi4_install=state_game)
    assert mod.set_state_owner(1, "SOV") is None
    assert mod.get_state(1).owner == "SOV"
    assert mod.set_state_owner([1, 2], "POL", add_core=False) is None
    assert [mod.get_state(i).owner for i in (1, 2)] == ["POL", "POL"]
    before = mod.preview()
    with pytest.raises(NotFoundError, match="State 99 not found"):
        mod.set_state_owner([2, 99], "SOV")
    assert mod.get_state(2).owner == "POL"
    assert mod.preview() == before


def test_update_state_sets_lists_and_adds_or_removes_cores(tmp_path, state_game) -> None:
    from hoi4 import State

    mod = Mod(tmp_path / "mod", hoi4_install=state_game)
    state = mod.update_state(1, cores=["SOV"])
    assert isinstance(state, State)
    assert state.cores == ["SOV"]  # a list field is replaced, not appended to
    state = mod.update_state(2, add_cores=["SOV"], remove_cores=["POL"], manpower=5)
    assert state.cores == ["GER", "SOV"] and state.manpower == 5
    before = mod.preview()
    with pytest.raises(NotFoundError, match="Core 'POL' not found in state 2"):
        mod.update_state(2, remove_cores=["POL"], manpower=9)
    with pytest.raises(TypeError, match=r"update_state\(\): Unknown fields: \['zz'\]"):
        mod.update_state(2, zz=1)
    with pytest.raises(TypeError):
        mod.update_state(2, _victory_points_requested=True)
    with pytest.raises(NotFoundError, match="Country 'ZZX' not found"):
        mod.update_state(2, owner="ZZX")
    assert mod.preview() == before
    for name in ("set_state_properties", "patch_state_history", "batch_set_owner"):
        assert not hasattr(mod, name)


def test_validation_issue_is_a_record_and_the_old_name_is_gone() -> None:
    import hoi4

    assert "ValidationIssue" in hoi4.__all__
    assert not hasattr(hoi4, "ValidationError")
    assert not issubclass(hoi4.ValidationIssue, BaseException)


def test_no_removed_or_renamed_name_is_used_anywhere() -> None:
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/check_removed_api_names.py"],
        cwd=root, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout


# ── Leftovers closed after the cleanup (part 4) ───────────────────────


def test_add_state_core_raises_for_a_core_the_state_has_and_stages_nothing(
    tmp_path, state_game
) -> None:
    from hoi4 import AlreadyExistsError

    mod = Mod(tmp_path / "mod", hoi4_install=state_game)
    with pytest.raises(AlreadyExistsError, match="Core 'POL' already exists"):
        mod.add_state_core(1, "POL")
    assert mod.preview() == ""  # nothing was copied into the mod
    assert mod.add_state_core(1, "SOV").cores == ["GER", "POL", "SOV"]


def test_state_setters_that_change_nothing_stage_nothing(tmp_path, state_game) -> None:
    mod = Mod(tmp_path / "mod", hoi4_install=state_game)
    assert mod.set_state_owner(1, "GER") is None  # owner and core are there already
    assert mod.set_state_owner([1, 2], "GER", add_core=False) is None
    assert mod.update_state(1, owner="GER", manpower=1, cores=["GER", "POL"]) == mod.get_state(1)
    assert mod.update_state(2, add_cores=["POL"]).cores == ["GER", "POL"]
    assert mod.preview() == ""
    # The same owner with a core still missing is a change.
    mod.update_state(1, cores=["POL"])
    mod.set_state_owner(1, "GER")
    assert mod.get_state(1).cores == ["POL", "GER"]
    assert "1-S.txt" in mod.preview()


def test_update_state_refuses_raw_text_and_keeps_history(tmp_path, state_game) -> None:
    mod = Mod(tmp_path / "mod", hoi4_install=state_game)
    with pytest.raises(TypeError, match=r"update_state\(\): Unknown fields: \['raw_text'\]"):
        mod.update_state(1, raw_text="state = { id = 1 }")  # type: ignore[call-arg]
    assert mod.preview() == ""
    # ``history`` is the script of the history block: a caller may set it.
    history = "owner = GER\nadd_core_of = GER\nadd_core_of = POL\n1939.9.1 = { owner = SOV }"
    assert mod.update_state(1, history=history).history == history
    assert "1939.9.1 = {" in mod.preview()


def test_list_bookmarks_and_supply_nodes_return_sorted_ids_once(tmp_path) -> None:
    _write(
        tmp_path,
        "common/bookmarks/b.txt",
        "bookmarks = {\n"
        "    bookmark = { name = ZETA date = 1936.1.1.12 }\n"
        "    bookmark = { name = ALPHA date = 1939.8.14.12 }\n"
        "}\n",
    )
    _write(tmp_path, "common/bookmarks/c.txt", "bookmarks = { bookmark = { name = ZETA } }\n")
    _write(tmp_path, "map/supply_nodes.txt", "1 7\n1 4\n1 7\n")
    mod = Mod(tmp_path)
    assert mod.list_bookmarks() == ["ALPHA", "ZETA"]
    assert mod.list_supply_nodes() == [4, 7]


def test_create_industrial_branch_stages_nothing_when_it_raises(tmp_path) -> None:
    from hoi4 import AlreadyExistsError, Focus

    mod = Mod(tmp_path)
    mod.create_focus_tree("abc_tree", "ABC")
    mod.add_focus("abc_tree", Focus(id="ABC_root", x=0, y=0))
    mod.add_focus("abc_tree", Focus(id="ABC_support_domestic_steel", x=1, y=0))
    before = mod.preview()
    with pytest.raises(AlreadyExistsError, match="ABC_support_domestic_steel"):
        mod.create_industrial_branch("abc_tree", "ABC")
    with pytest.raises(AlreadyExistsError, match="ABC_support_domestic_steel"):
        mod.create_industrial_branch("abc_tree", "ABC", anchor_focus_id="ABC_root")
    with pytest.raises(NotFoundError, match="Focus 'ABC_nope' not found"):
        mod.create_industrial_branch("abc_tree", "ABC", anchor_focus_id="ABC_nope")
    assert mod.preview() == before
    assert mod.get_loc("ABC_map_domestic_industry", default=None) is None
    # A tree that does not exist yet is not created for a missing anchor.
    with pytest.raises(NotFoundError, match="Focus 'ABC_root' not found in focus tree 'new_tree'"):
        mod.create_industrial_branch("new_tree", "ABC", anchor_focus_id="ABC_root")
    assert "new_tree" not in mod.list_focus_trees()
    assert mod.preview() == before


def test_decision_creators_stage_nothing_when_they_raise(tmp_path) -> None:
    from hoi4 import AlreadyExistsError

    mod = Mod(tmp_path)
    mod.create_decision_category("cat")
    mod.create_decision("cat", "taken")
    mod.create_decision_category("other")
    mod.create_decision("other", "elsewhere")
    before = mod.preview()
    with pytest.raises(AlreadyExistsError, match="Decision 'taken' already exists"):
        mod.create_recovery_decision("new_cat", "taken", effect="add_stability = 0.1")
    assert "new_cat" not in mod.list_decision_categories()
    with pytest.raises(ValueError, match="Invalid decision target"):
        mod.create_targeted_decision("cat", "aimed", targets=["GER {"], path="common/decisions/x.txt")
    with pytest.raises(ValueError, match="Decision chain steps need an id"):
        mod.create_decision_chain("chain_cat", ["chain_1", {"cost": 5}])
    with pytest.raises(ValueError, match="is in category 'other'"):
        mod.create_decision_chain("chain_cat", ["chain_1", "elsewhere"])
    assert mod.preview() == before
    assert "chain_cat" not in mod.list_decision_categories()
    assert mod.get_decision("chain_1", default=None) is None


def test_create_decision_chain_with_a_path_updates_an_existing_step_in_place(tmp_path) -> None:
    mod = Mod(tmp_path)
    mod.create_decision_category("cat")
    mod.create_decision("cat", "step_1", cost=9)
    decisions = mod.create_decision_chain(
        "cat", ["step_1", "step_2"], path="common/decisions/chain.txt"
    )
    assert [decision.id for decision in decisions] == ["step_1", "step_2"]
    assert decisions[0].cost == 0
    assert {decision.path for decision in decisions} == {
        (tmp_path / "common/decisions/chain.txt").resolve()
    }


def test_create_country_stages_nothing_when_its_leader_id_is_taken(tmp_path) -> None:
    from hoi4 import AlreadyExistsError, Character

    mod = Mod(tmp_path)
    mod.create_character("ABC", Character(id="ABC_leader_1", name="Someone"), recruit=False)
    before = mod.preview()
    with pytest.raises(AlreadyExistsError, match="Character 'ABC_leader_1' already exists"):
        mod.create_country("ABC", "Abcland")
    assert mod.preview() == before
    assert "ABC" not in mod.list_countries()


def test_the_release_gate_uses_the_new_returns() -> None:
    import inspect

    from hoi4 import release_gate

    source = inspect.getsource(release_gate)
    assert "if not mod." not in source
    assert "Could not update" not in source


def test_a_refused_save_raises_save_refused_error(tmp_path) -> None:
    from hoi4 import Hoi4Error, SaveRefusedError

    assert issubclass(SaveRefusedError, Hoi4Error) and issubclass(SaveRefusedError, RuntimeError)
    mod = Mod(tmp_path)
    with pytest.warns(RuntimeWarning), pytest.raises(SaveRefusedError, match="No changes written"):
        mod.save(require_changes=True)


def test_a_strict_load_failure_raises_load_error(tmp_path) -> None:
    from hoi4 import Hoi4Error, LoadError

    assert issubclass(LoadError, Hoi4Error) and issubclass(LoadError, RuntimeError)
    _write(tmp_path, "events/broken.txt", "add_namespace = demo\ncountry_event = { id = demo.1 \n")
    with pytest.raises(LoadError, match="Failed to load event file"):
        Mod(tmp_path, strict_loading=True)


def test_the_module_docstrings_show_the_readme_quick_start() -> None:
    import re
    import textwrap
    from pathlib import Path

    import hoi4.mod

    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    quick_start = re.search(r"## Quick start\n\n```python\n(.*?)```", readme, re.S)
    assert quick_start is not None
    example = textwrap.indent(quick_start.group(1), "    ")
    for module in (hoi4, hoi4.mod):
        doc = module.__doc__ or ""
        assert "german_focus" not in doc and "/opt/steam" not in doc
        assert example in doc


def test_add_coastal_bunker_takes_the_state_first() -> None:
    # It kept a stray ``cls`` parameter from its time as a ``Mod`` method.
    assert effects.add_coastal_bunker(115, 2) == effects.add_bunker(115, 2).replace(
        "bunker", "coastal_bunker"
    )


# ── Round 1 of the review: fields that did nothing, typos that were ignored ──


def test_update_idea_refuses_desc_instead_of_doing_nothing(tmp_path) -> None:
    mod = Mod(tmp_path)
    mod.create_idea("zz_i")
    mod.save()
    mod = Mod(tmp_path)
    for call in (mod.update_idea, mod.ensure_idea):
        with pytest.raises(TypeError, match=r"set_loc\('zz_i_desc', text\)"):
            call("zz_i", desc="zz_i_desc")  # type: ignore[call-arg]
    assert mod.get_idea("zz_i").desc == ""
    assert mod.preview() == ""
    assert "desc" not in hoi4.fields.IdeaFields.__annotations__


def test_update_country_refuses_a_ui_colour_that_no_file_can_hold(tmp_path) -> None:
    mod = Mod(tmp_path)
    mod.create_country("ABC", "Abcland")
    mod.save()
    mod = Mod(tmp_path)
    before = mod.preview()
    with pytest.raises(ValueError, match="color_ui of 'ABC' cannot be set"):
        mod.update_country("ABC", color_ui=(4, 5, 6))
    assert mod.preview() == before
    assert mod.get_country("ABC").color_ui is None
    # The map colour of the same country can be set, and it is written.
    mod.update_country("ABC", color=(4, 5, 6))
    assert "4 5 6" in mod.preview()


def test_update_country_sets_the_ui_colour_of_a_game_country(tmp_path, state_game) -> None:
    mod = Mod(tmp_path / "mod", hoi4_install=state_game)
    mod.update_country("GER", color_ui=(4, 5, 6))
    assert mod.get_country("GER").color_ui == (4, 5, 6)


def test_a_decision_chain_refuses_a_misspelled_step_key(tmp_path) -> None:
    mod = Mod(tmp_path)
    with pytest.raises(
        TypeError, match=r"create_decision_chain\(\): Unknown fields in step 2: \['cots'\]\. Valid: "
    ):
        mod.create_decision_chain("zz_cat", [{"id": "zz_s1"}, {"id": "zz_s2", "cots": 7}])
    with pytest.raises(TypeError, match=r"Unknown fields in step 1: \[1, 'zz'\]"):
        mod.create_decision_chain("zz_cat", [{"id": "zz_s1", "zz": 1, 1: 2}])
    assert mod.list_decisions() == []
    assert mod.list_decision_categories() == []
    assert mod.preview() == ""
    chain = mod.create_decision_chain("zz_cat", [{"id": "zz_s1", "cost": 7, "flag": "zz_flag"}])
    assert chain[0].cost == 7


def test_the_wrappers_name_themselves_for_an_unknown_field(tmp_path, state_game) -> None:
    mod = Mod(tmp_path / "mod", hoi4_install=state_game)
    mod.create_decision_category("zz_cat")
    with pytest.raises(TypeError, match=r"create_mission\(\): Unknown fields: \['bogus'\]\. Valid: "):
        mod.create_mission("zz_cat", "zz_m", days_mission_timeout=10, bogus=1)  # type: ignore[call-arg]
    with pytest.raises(
        TypeError, match=r"create_targeted_decision\(\): Unknown fields: \['bogus'\]\. Valid: "
    ):
        mod.create_targeted_decision("zz_cat", "zz_t", target_trigger="always = yes", bogus=1)  # type: ignore[call-arg]
    with pytest.raises(TypeError, match=r"split_state\(\): Unknown fields: \['bogus'\]\. Valid: "):
        mod.split_state(1, [1], bogus=1)  # type: ignore[call-arg]
    assert mod.list_decisions() == []
    # The refusals of a mission keep their own message.
    with pytest.raises(ValueError, match="Missions cannot have cost"):
        mod.create_mission("zz_cat", "zz_m", days_mission_timeout=10, cost=5)  # type: ignore[call-arg]


@pytest.mark.parametrize("bad", [["bad"], [{"name": "x"}], "bad", 7])
def test_a_list_of_models_refuses_other_things_at_the_call(tmp_path, bad) -> None:
    """Before, the wrong item only failed inside ``preview()`` or ``save()``."""
    mod = Mod(tmp_path)
    with pytest.raises(TypeError, match=r"create_ideology\(\): types must"):
        mod.create_ideology("zz_ide", types=bad)
    mod.create_ideology("zz_ide", types=[hoi4.SubIdeology("zz_sub")])
    with pytest.raises(TypeError, match=r"update_ideology\(\): types must"):
        mod.update_ideology("zz_ide", types=bad)
    with pytest.raises(TypeError, match=r"create_event\(\): options must"):
        mod.create_event("zz.1", options=bad)
    mod.create_event("zz.1")
    with pytest.raises(TypeError, match=r"update_event\(\): options must"):
        mod.update_event("zz.1", options=bad)
    with pytest.raises(TypeError, match=r"create_bookmark\(\): countries must"):
        mod.create_bookmark("ZZ_START", countries=bad)
    assert [sub.name for sub in mod.get_ideology("zz_ide").types] == ["zz_sub"]
    assert [option.name for option in mod.get_event("zz.1").options] == ["zz.1.a"]
    mod.create_country("ABC", "Abcland")
    mod.create_character("ABC", hoi4.Character(id="ABC_x", name="X"))
    with pytest.raises(TypeError, match=r"update_character\(\): portraits must"):
        mod.update_character("ABC_x", portraits=bad)
    with pytest.raises(TypeError, match=r"create_oob\(\): templates must"):
        mod.create_oob("ABC_o", "ABC", templates=bad)
    mod.create_oob("ABC_o", "ABC")
    for field in ("templates", "divisions", "fleets", "air_wings"):
        with pytest.raises(TypeError, match=rf"update_oob\(\): {field} must"):
            mod.update_oob("ABC_o", **{field: bad})
    mod.preview()  # renders: no wrong item got into a model


# ── Round 1, from the probes of the two reviewers who were stopped ──


def test_delete_character_finds_a_game_character_that_nothing_loaded_yet(tmp_path, game) -> None:
    _write(
        game,
        "common/characters/GER.txt",
        "characters = {\n\tGER_boss = { name = GER_boss }\n\tGER_other = { name = GER_other }\n}\n",
    )
    mod = Mod(tmp_path / "mod", hoi4_install=game)
    mod.delete_character("GER_boss")  # no lookup before it
    assert "GER_boss = {" not in mod.preview().replace("-\tGER_boss = {", "")
    with pytest.raises(NotFoundError):
        mod.delete_character("GER_nobody")


def _game_with_a_decision_category(game):
    _write(game, "common/decisions/categories/00_cats.txt", "game_cat = { icon = x }\n")
    _write(game, "common/decisions/00_dec.txt", "game_cat = { game_dec = { cost = 5 } }\n")
    return game


def test_a_chain_and_a_recovery_decision_keep_the_games_category(tmp_path, game) -> None:
    mod = Mod(tmp_path / "mod", hoi4_install=_game_with_a_decision_category(game))
    mod.create_decision_chain("game_cat", ["zz_s1", "zz_s2"])
    mod.create_recovery_decision("game_cat", "zz_fix", effect="add_stability = 0.01")
    mod.save()
    assert not (mod.mod_root / "common/decisions/categories").exists()
    text = (mod.mod_root / "common/decisions/mod_decisions.txt").read_text(encoding="utf-8")
    assert "zz_s1 = {" in text and "zz_fix = {" in text
    # A category no one defines is still created, and an icon asks for a definition.
    mod.create_decision_chain("zz_new_cat", ["zz_n1"])
    mod.create_decision_chain("game_cat", ["zz_s3"], category_icon="GFX_zz")
    preview = mod.preview()
    assert "zz_new_cat = {" in preview
    assert "icon = GFX_zz" in preview


def test_a_chain_refuses_an_id_twice_before_anything_changes(tmp_path) -> None:
    mod = Mod(tmp_path)
    with pytest.raises(hoi4.AlreadyExistsError, match="'d1' is in this call twice"):
        mod.create_decision_chain("zz_cat", ["d1", "d1"])
    assert mod.list_decisions() == []
    assert mod.list_decision_categories() == []
    assert mod.preview() == ""


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("popularities", 5, TypeError),
        ("popularities", {7: 50}, TypeError),
        ("ideas", [123], TypeError),
        ("ideas", "one_idea", TypeError),
        ("technologies", [1], TypeError),
        ("color", (1, 2), ValueError),
        ("color", "red", ValueError),
        ("color", (1, 2, 300), ValueError),
    ],
)
def test_a_country_value_of_the_wrong_shape_changes_nothing(tmp_path, field, value, error) -> None:
    mod = Mod(tmp_path)
    with pytest.raises(error, match=rf"create_country\(\): {field} must"):
        mod.create_country("ABC", "Abcland", **{field: value})
    assert mod.list_countries() == []
    assert mod.preview() == ""
    mod.create_country("ABC", "Abcland")
    mod.save()
    before = mod.preview()
    with pytest.raises(error, match=rf"update_country\(\): {field} must"):
        mod.update_country("ABC", name="Renamed", **{field: value})
    assert mod.get_country("ABC").name == "Abcland"
    assert mod.preview() == before


def test_update_country_refuses_none_for_a_mapping(tmp_path) -> None:
    mod = Mod(tmp_path)
    mod.create_country("ABC", "Abcland")
    mod.save()
    with pytest.raises(TypeError, match=r"update_country\(\): technologies cannot be None"):
        mod.update_country("ABC", name="Renamed", technologies=None)  # type: ignore[arg-type]
    assert mod.get_country("ABC").name == "Abcland"
    assert mod.preview() == ""


def test_an_empty_order_of_battle_is_still_a_file(tmp_path) -> None:
    mod = Mod(tmp_path)
    mod.create_country("ABC", "Abcland")
    mod.create_oob("ABC_x", "ABC")
    mod.save()
    assert (tmp_path / "history/units/ABC_x.txt").read_text(encoding="utf-8") == "units = {\n}\n"
    fresh = Mod(tmp_path)
    assert fresh.list_oobs() == ["ABC_x"]
    assert fresh.get_oob("ABC_x").name == "ABC_x"


def test_preview_lists_the_files_in_path_order(tmp_path) -> None:
    mod = Mod(tmp_path)
    mod.create_event("zz.1")
    mod.create_idea("zz_idea")
    mod.create_decision_category("zz_cat")
    mod.set_loc("zz_key", "Text")
    headers = [line[6:] for line in mod.preview().splitlines() if line.startswith("+++ b/")]
    assert headers == sorted(headers)
    assert len(headers) >= 4


def test_a_generator_of_models_is_read_once_and_kept(tmp_path) -> None:
    """Round 2: the check read a generator, and the call then found it empty."""
    from hoi4.oob import Battalion, DivisionTemplate

    mod = Mod(tmp_path)
    mod.create_event("zz.1", options=(hoi4.EventOption(name=name) for name in ("zz.1.a", "zz.1.b")))
    assert [option.name for option in mod.get_event("zz.1").options] == ["zz.1.a", "zz.1.b"]
    mod.update_event("zz.1", options=(hoi4.EventOption(name=name) for name in ("zz.1.c",)))
    assert [option.name for option in mod.get_event("zz.1").options] == ["zz.1.c"]
    mod.create_ideology("zz_i", types=(hoi4.SubIdeology(name) for name in ("zz_s",)))
    assert [sub.name for sub in mod.get_ideology("zz_i").types] == ["zz_s"]
    mod.update_ideology("zz_i", types=iter([hoi4.SubIdeology("zz_t")]))
    assert [sub.name for sub in mod.get_ideology("zz_i").types] == ["zz_t"]
    mod.create_bookmark("ZZ", countries=(hoi4.BookmarkCountry(tag=tag) for tag in ("GER",)))
    assert [entry.tag for entry in mod.get_bookmark("ZZ").countries] == ["GER"]
    mod.create_country("ABC", "Abcland")
    template = DivisionTemplate(name="Inf", battalions=[Battalion("infantry", 0, 0)])
    mod.create_oob("ABC_x", "ABC", templates=(item for item in [template]))
    assert [item.name for item in mod.get_oob("ABC_x").templates] == ["Inf"]
    mod.update_oob("ABC_x", templates=iter([DivisionTemplate(name="Mot", battalions=[Battalion("infantry", 0, 0)])]))
    assert [item.name for item in mod.get_oob("ABC_x").templates] == ["Mot"]
    assert "Mot" in mod.preview()


# ── Round 3 of the review: bookmarks ──


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        ({"countries": ["nope"]}, TypeError),
        ({"countries": [hoi4.BookmarkCountry(tag="not a tag")]}, ValueError),
        ({"path": "../../x.txt"}, ValueError),
    ],
)
def test_a_failed_overwrite_keeps_the_bookmark_that_was_there(tmp_path, arguments, error) -> None:
    mod = Mod(tmp_path)
    mod.create_bookmark("ZZ", countries=[hoi4.BookmarkCountry(tag="GER")], description="zzz")
    mod.save()
    path = tmp_path / "common/bookmarks/ZZ.txt"
    before = path.read_text(encoding="utf-8")
    mod = Mod(tmp_path)
    with pytest.raises(error):
        mod.create_bookmark("ZZ", overwrite=True, **arguments)
    assert mod.list_bookmarks() == ["ZZ"]
    assert mod.get_bookmark("ZZ").description == "zzz"
    assert mod.preview() == ""
    with pytest.warns(RuntimeWarning, match="No changes written"):
        mod.save()
    assert path.read_text(encoding="utf-8") == before


def test_create_bookmark_leaves_the_callers_entries_alone(tmp_path) -> None:
    mod = Mod(tmp_path)
    good = hoi4.BookmarkCountry(tag="ger")
    with pytest.raises(ValueError):
        mod.create_bookmark("ZZ", countries=[good, hoi4.BookmarkCountry(tag="not a tag")])
    assert good.tag == "ger"  # not rewritten by the call that failed
    assert mod.list_bookmarks() == []
    assert mod.create_bookmark("ZZ", countries=[good]).countries[0].tag == "GER"


def test_update_bookmark_replaces_the_country_entries(tmp_path) -> None:
    """Before, the field was accepted and then ``preview()`` and ``save()`` raised."""
    mod = Mod(tmp_path)
    mod.create_bookmark(
        "ZZ",
        countries=[hoi4.BookmarkCountry(tag="GER", history="GER_DESC"), hoi4.BookmarkCountry(tag="FRA")],
    )
    mod.save()
    mod = Mod(tmp_path)
    with pytest.raises(ValueError):
        mod.update_bookmark("ZZ", countries=[hoi4.BookmarkCountry(tag="not a tag")])
    assert mod.preview() == ""
    mod.update_bookmark("ZZ", countries=(hoi4.BookmarkCountry(tag=tag) for tag in ("ITA", "FRA")))
    assert '"GER"' not in mod.preview().replace('-\t\t"GER"', "")
    mod.save()
    fresh = Mod(tmp_path)
    assert [entry.tag for entry in fresh.get_bookmark("ZZ").countries] == ["ITA", "FRA"]
    assert not fresh.load_diagnostics


def test_a_model_handed_over_whole_is_checked_too(tmp_path) -> None:
    mod = Mod(tmp_path)
    mod.create_country("ABC", "Abcland")
    with pytest.raises(TypeError, match=r"create_character\(\): character.portraits must"):
        mod.create_character("ABC", hoi4.Character(id="ABC_x", name="X", portraits=["x"]))  # type: ignore[list-item]
    with pytest.raises(TypeError, match=r"create_character\(\): character must be a model"):
        mod.create_character("ABC", "ABC_x")  # type: ignore[arg-type]
    assert mod.list_characters(include_vanilla=False) == ["ABC_leader_1"]  # only the country's own
    mod.create_character("ABC", hoi4.Character(id="ABC_x", name="X"))
    with pytest.raises(TypeError, match=r"add_character_instance\(\): instance must be a model"):
        mod.add_character_instance("ABC_x", "x")  # type: ignore[arg-type]
    mod.create_event("zz.1")
    with pytest.raises(TypeError, match=r"add_event_option\(\): option must be a model"):
        mod.add_event_option("zz.1", "zz.1.b")  # type: ignore[arg-type]
    mod.preview()


# ── Round 4 of the review: one level deeper, and None ──


def test_a_wrong_item_inside_a_nested_model_is_refused_at_the_call(tmp_path) -> None:
    """Before, only the outer list was checked; the inner one broke ``preview()``."""
    from hoi4.oob import DivisionTemplate

    mod = Mod(tmp_path)
    mod.create_country("ABC", "Abcland")
    bad_instance = hoi4.CharacterInstance(name="i", portraits=["x"])  # type: ignore[list-item]
    with pytest.raises(TypeError, match=r"create_character\(\): character\.instances\[0\]\.portraits must"):
        mod.create_character("ABC", hoi4.Character(id="ABC_x", name="n", instances=[bad_instance]))
    bad_option = hoi4.EventOption(name="zz.1.a", conditional_names=["x"])  # type: ignore[list-item]
    with pytest.raises(TypeError, match=r"create_event\(\): options\[0\]\.conditional_names must"):
        mod.create_event("zz.1", options=[bad_option])
    mod.create_event("zz.1")
    with pytest.raises(TypeError, match=r"update_event\(\): options\[0\]\.conditional_names must"):
        mod.update_event("zz.1", options=[bad_option])
    with pytest.raises(TypeError, match=r"add_event_option\(\): option\.conditional_names must"):
        mod.add_event_option("zz.1", bad_option)
    with pytest.raises(TypeError, match=r"create_oob\(\): templates\[0\]\.battalions must"):
        mod.create_oob("ABC_o", "ABC", templates=[DivisionTemplate(name="T", battalions=["x"])])  # type: ignore[list-item]
    assert mod.list_oobs() == []
    assert mod.list_characters(include_vanilla=False) == ["ABC_leader_1"]
    mod.preview()


def test_a_whole_model_of_the_wrong_class_is_refused_at_the_call(tmp_path) -> None:
    mod = Mod(tmp_path)
    mod.create_country("ABC", "Abcland")
    mod.create_character("ABC", hoi4.Character(id="ABC_x", name="X"))
    mod.create_focus_tree("zz_tree", "ABC")
    mod.add_focus("zz_tree", hoi4.Focus(id="ABC_a", x=0, y=0))
    mod.create_bookmark("ZZ")
    for call, pattern in [
        (lambda: mod.add_character_role("ABC_x", "advisor"), r"add_character_role\(\): role must be a model"),  # type: ignore[arg-type]
        (lambda: mod.add_focus("zz_tree", "x"), r"add_focus\(\): focus must be a model"),  # type: ignore[arg-type]
        (lambda: mod.ensure_focus("zz_tree", "x"), r"ensure_focus\(\): focus must be a model"),  # type: ignore[arg-type]
        (
            lambda: mod.insert_focus_after("zz_tree", "ABC_a", "x"),  # type: ignore[arg-type]
            r"insert_focus_after\(\): focus must be a model",
        ),
        (lambda: mod.insert_branch("zz_tree", "ABC_a", ["x"]), r"insert_branch\(\): focuses must hold Focus"),  # type: ignore[list-item]
        (lambda: mod.create_equipment_variant("ABC", "x"), r"create_equipment_variant\(\): variant must be a model"),  # type: ignore[arg-type]
        (lambda: mod.add_bookmark_country("ZZ", "GER"), r"add_bookmark_country\(\): country must be a model"),  # type: ignore[arg-type]
    ]:
        before = mod.preview()
        with pytest.raises(TypeError, match=pattern):
            call()
        assert mod.preview() == before


def test_a_model_handed_over_whole_is_not_rewritten(tmp_path) -> None:
    mod = Mod(tmp_path)
    portraits: list[hoi4.CharacterPortrait] = []
    character = hoi4.Character(id="ZZZ_x", name="X", portraits=portraits)
    with pytest.raises(NotFoundError):
        mod.create_character("ZZZ", character)  # no such country: the call fails
    assert character.portraits is portraits


@pytest.mark.parametrize(
    ("method", "arguments", "field"),
    [
        ("update_bookmark", ("ZZ",), "countries"),
        ("update_bookmark_country", ("ZZ", "ABC"), "ideas"),
        ("update_event", ("zz.1",), "options"),
        ("update_event", ("zz.1",), "trigger"),
        ("update_event", ("zz.1",), "event_type"),
        ("update_idea", ("zz_idea",), "traits"),
        ("update_idea", ("zz_idea",), "modifier"),
        ("update_decision_category", ("zz_cat",), "decisions"),
        ("update_focus_tree", ("zz_tree",), "focuses"),
        ("update_focus", ("zz_tree", "ABC_a"), "x"),
        ("update_ideology", ("zz_i",), "types"),
        ("update_ideology", ("zz_i",), "color"),
        ("update_on_action", ("on_startup",), "events"),
        ("update_character", ("ABC_x",), "portraits"),
        ("update_character_role", ("ABC_x", "advisor"), "traits"),
        ("update_character_role", ("ABC_x", "advisor"), "slot"),
    ],
)
def test_none_is_refused_for_a_field_that_cannot_be_empty(tmp_path, method, arguments, field) -> None:
    """Before, the call went through and ``preview()`` and ``save()`` raised, or content was lost."""
    mod = Mod(tmp_path)
    mod.create_country("ABC", "Abcland")
    mod.create_bookmark("ZZ", countries=[hoi4.BookmarkCountry(tag="ABC")])
    mod.create_event("zz.1")
    mod.create_idea("zz_idea")
    mod.create_decision_category("zz_cat")
    mod.create_focus_tree("zz_tree", "ABC")
    mod.add_focus("zz_tree", hoi4.Focus(id="ABC_a", x=0, y=0))
    mod.create_ideology("zz_i", types=[hoi4.SubIdeology("zz_s")])
    mod.create_on_action("on_startup", effect="add_stability = 0.01")
    mod.create_character(
        "ABC",
        hoi4.Character(id="ABC_x", name="X", roles=[hoi4.AdvisorRole(slot="political_advisor", traits=["zz"])]),
    )
    mod.save()
    mod = Mod(tmp_path)
    with pytest.raises(TypeError, match=rf"{method}\(\): {field} cannot be None"):
        getattr(mod, method)(*arguments, **{field: None})
    assert mod.preview() == ""


def test_none_still_clears_a_field_that_can_be_empty(tmp_path) -> None:
    mod = Mod(tmp_path)
    mod.create_event("zz.1", fire_only_once=True)
    assert mod.update_event("zz.1", fire_only_once=None).fire_only_once is None
    mod.create_country("ABC", "Abcland")
    mod.create_oob("ABC_o", "ABC")
    mod.update_oob("ABC_o", templates=None)  # a keyword that means "not given"
    mod.preview()
