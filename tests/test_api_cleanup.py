"""The 0.7.0 cleanup of the public ``Mod`` API: what is gone and what replaced it."""

from __future__ import annotations

import pytest

from hoi4 import Mod

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
