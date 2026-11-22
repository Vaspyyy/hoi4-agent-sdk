"""validate() sees what the game inherits and what the API changed before save().

Each test is a false alarm or a missed finding a reviewer reproduced: a
catalog that ignored advisor idea tokens, focus ids or country files of the
game, or a cached scan of the mod's files that went stale after an unsaved
edit.
"""

from __future__ import annotations

from pathlib import Path

from hoi4 import Mod
from hoi4.types import ValidationError


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def codes(findings: list[ValidationError], code: str) -> list[ValidationError]:
    return [finding for finding in findings if finding.code == code]


def mod_root(tmp_path: Path) -> Path:
    root = tmp_path / "mod"
    write(root, "descriptor.mod", 'name = "test"\n')
    return root


# ── Advisor idea tokens ─────────────────────────────────────────────


def test_country_history_may_assign_an_advisor_idea_token_of_the_game(tmp_path):
    game = tmp_path / "game"
    write(
        game,
        "common/characters/TST.txt",
        "characters = {\n"
        " TST_chief = {\n"
        "  name = TST_chief\n"
        "  advisor = { slot = political_advisor idea_token = TST_chief_tok }\n"
        " }\n"
        "}\n",
    )
    root = mod_root(tmp_path)
    write(root, "common/country_tags/00_countries.txt", 'TST = "countries/Test.txt"\n')
    write(root, "common/countries/Test.txt", "color = { 1 2 3 }\n")
    history = write(
        root,
        "history/countries/TST - Test.txt",
        "capital = 1\nadd_ideas = {\n TST_chief_tok\n TST_typo_idea\n}\n",
    )
    findings = codes(Mod(root, hoi4_install=game).validate(stage="build"), "unknown_assigned_idea")
    assert [(f.idea_id, f.file_path, f.line) for f in findings] == [
        ("TST_typo_idea", str(history.resolve()), 4)
    ]


# ── Country tag files ───────────────────────────────────────────────


def test_a_tag_may_point_to_a_country_file_the_game_inherits(tmp_path):
    game = tmp_path / "game"
    write(game, "common/countries/Germany.txt", "color = { 1 2 3 }\n")
    root = mod_root(tmp_path)
    write(
        root,
        "common/country_tags/00_countries.txt",
        'GER = "countries/Germany.txt"\nXXA = "countries/Nowhere.txt"\n',
    )
    findings = codes(Mod(root, hoi4_install=game).validate(stage="build"), "tag_definition")
    assert [(f.country_tag, f.line) for f in findings] == [("XXA", 2)]


def test_replace_path_hides_an_inherited_country_file(tmp_path):
    game = tmp_path / "game"
    write(game, "common/countries/Germany.txt", "color = { 1 2 3 }\n")
    root = mod_root(tmp_path)
    write(root, "descriptor.mod", 'name = "test"\nreplace_path = "common/countries"\n')
    write(root, "common/country_tags/00_countries.txt", 'GER = "countries/Germany.txt"\n')
    findings = codes(Mod(root, hoi4_install=game).validate(stage="build"), "tag_definition")
    assert [f.country_tag for f in findings] == ["GER"]


# ── Focus ids of inherited files ────────────────────────────────────


def focus_game(tmp_path: Path) -> Path:
    game = tmp_path / "game"
    write(
        game,
        "common/national_focus/generic.txt",
        "focus_tree = {\n id = generic_focus\n"
        " focus = { id = army_effort x = 0 y = 0 }\n}\n",
    )
    write(
        game,
        "common/national_focus/congo_shared.txt",
        "shared_focus = { id = CONGO_free_state x = 3 y = 0 }\n",
    )
    return game


def test_a_mod_focus_may_require_a_shared_focus_of_the_game(tmp_path):
    game = focus_game(tmp_path)
    root = mod_root(tmp_path)
    write(
        root,
        "common/national_focus/belgium.txt",
        "focus_tree = {\n id = belgium_focus\n shared_focus = CONGO_free_state\n"
        " focus = { id = BEL_a x = 0 y = 0 prerequisite = { focus = CONGO_free_state } }\n"
        " focus = { id = BEL_b x = 1 y = 0 prerequisite = { focus = BEL_nowhere } }\n}\n",
    )
    findings = Mod(root, hoi4_install=game).validate(stage="build")
    missing = codes(findings, "missing_focus_prerequisite")
    assert [f.focus_id for f in missing] == ["BEL_b"]


def test_a_mod_shared_focus_may_require_and_anchor_to_a_game_focus(tmp_path):
    game = focus_game(tmp_path)
    root = mod_root(tmp_path)
    write(
        root,
        "common/national_focus/shared.txt",
        "shared_focus = {\n id = MOD_shared\n prerequisite = { focus = army_effort }\n"
        " relative_position_id = army_effort\n x = 1 y = 1\n}\n",
    )
    findings = Mod(root, hoi4_install=game).validate(stage="build")
    assert not codes(findings, "missing_focus_prerequisite")
    assert not codes(findings, "unresolved_focus_position")


def test_a_mod_file_that_overrides_a_game_focus_file_hides_its_focuses(tmp_path):
    game = focus_game(tmp_path)
    root = mod_root(tmp_path)
    write(
        root,
        "common/national_focus/generic.txt",
        "focus_tree = {\n id = generic_focus\n focus = { id = mod_effort x = 0 y = 0 }\n}\n",
    )
    write(
        root,
        "common/national_focus/shared.txt",
        "shared_focus = { id = MOD_shared x = 1 y = 1 prerequisite = { focus = army_effort } }\n",
    )
    findings = Mod(root, hoi4_install=game).validate(stage="build")
    assert [f.focus_id for f in codes(findings, "missing_focus_prerequisite")] == ["MOD_shared"]


def test_an_unsaved_focus_deletion_is_not_undone_by_the_file_scan(tmp_path):
    game = focus_game(tmp_path)
    root = mod_root(tmp_path)
    write(
        root,
        "common/national_focus/a.txt",
        "focus_tree = {\n id = a_tree\n focus = { id = A_one x = 0 y = 0 }\n"
        " focus = { id = A_two x = 1 y = 0 }\n}\n",
    )
    write(
        root,
        "common/national_focus/b.txt",
        "focus_tree = {\n id = b_tree\n"
        " focus = { id = B_one x = 0 y = 0 prerequisite = { focus = A_one } }\n"
        " focus = { id = B_two x = 1 y = 0 prerequisite = { focus = army_effort } }\n}\n",
    )
    mod = Mod(root, hoi4_install=game)
    assert not codes(mod.validate(stage="build"), "missing_focus_prerequisite")
    mod.remove_focus("a_tree", "A_one")
    findings = codes(mod.validate(stage="build"), "missing_focus_prerequisite")
    assert [f.focus_id for f in findings] == ["B_one"]


# ── Unsaved API edits reach a second validate() ─────────────────────


def references(findings: list[ValidationError], code: str) -> list[str]:
    return sorted(f.message for f in codes(findings, code))


def test_a_deleted_event_is_unknown_in_the_next_run(tmp_path):
    root = mod_root(tmp_path)
    write(
        root,
        "events/e_ev.txt",
        "add_namespace = e_ev\ncountry_event = {\n id = e_ev.1\n is_triggered_only = yes\n"
        " title = e_ev.1.t\n desc = e_ev.1.d\n option = { name = e_ev.1.a }\n}\n"
        "country_event = {\n id = e_ev.2\n title = e_ev.2.t\n desc = e_ev.2.d\n"
        " option = { name = e_ev.2.a }\n}\n",
    )
    write(
        root,
        "common/national_focus/e.txt",
        "focus_tree = {\n id = e_tree\n focus = {\n  id = E_fire x = 0 y = 0\n"
        "  completion_reward = { country_event = e_ev.1 country_event = e_ev.999 }\n }\n}\n",
    )
    mod = Mod(root)
    first = codes(mod.validate(stage="release"), "unknown_event_reference")
    assert ["e_ev.999" in f.message for f in first] == [True]
    assert mod.delete_event("e_ev.1")
    second = codes(mod.validate(stage="release"), "unknown_event_reference")
    assert sorted("e_ev.1'" in f.message for f in second) == [False, True]


def test_a_deleted_focus_tree_is_unknown_in_the_next_run(tmp_path):
    root = mod_root(tmp_path)
    write(
        root,
        "common/national_focus/e_tree.txt",
        "focus_tree = {\n id = e_tree\n focus = { id = E_one x = 0 y = 0 }\n}\n",
    )
    write(
        root,
        "common/national_focus/e_tree2.txt",
        "focus_tree = {\n id = e_tree2\n focus = {\n  id = E_load x = 0 y = 0\n"
        "  completion_reward = { load_focus_tree = { tree = e_tree } }\n }\n}\n",
    )
    mod = Mod(root)
    assert not codes(mod.validate(stage="release"), "unknown_focus_tree_reference")
    assert mod.delete_focus_tree("e_tree")
    findings = codes(mod.validate(stage="release"), "unknown_focus_tree_reference")
    assert [f.focus_id for f in findings] == ["E_load"]


def test_a_created_advisor_is_known_before_save(tmp_path):
    from hoi4.characters import AdvisorRole, Character

    root = mod_root(tmp_path)
    write(root, "common/country_tags/00_countries.txt", 'CCH = "countries/Cch.txt"\n')
    write(root, "common/countries/Cch.txt", "color = { 1 2 3 }\n")
    write(
        root,
        "history/countries/CCH - Cch.txt",
        "capital = 1\nrecruit_character = CCH_adv\n",
    )
    write(
        root,
        "common/national_focus/c.txt",
        "focus_tree = {\n id = c_tree\n focus = {\n  id = C_hire x = 0 y = 0\n"
        "  completion_reward = { add_ideas = cch_adv_tok }\n }\n}\n",
    )
    mod = Mod(root)
    first = mod.validate(stage="release")
    assert codes(first, "character_ref")
    assert codes(first, "unknown_idea_reference")
    mod.create_character(
        "CCH",
        Character(
            id="CCH_adv",
            name="CCH_adv",
            roles=[AdvisorRole(slot="political_advisor", idea_token="cch_adv_tok")],
        ),
        recruit=False,
    )
    second = mod.validate(stage="release")
    assert not codes(second, "character_ref")
    assert not codes(second, "unknown_idea_reference")


def test_a_deleted_character_stays_deleted_and_save_keeps_the_catalogs_fresh(tmp_path):
    root = mod_root(tmp_path)
    write(
        root,
        "common/characters/c.txt",
        "characters = {\n CCH_a = {\n  name = CCH_a\n"
        "  advisor = { slot = political_advisor idea_token = cch_a_tok }\n }\n}\n",
    )
    mod = Mod(root)
    assert "CCH_a" in mod._known_character_ids()
    assert "cch_a_tok" in mod._known_advisor_idea_ids()
    assert mod.delete_character("CCH_a")
    assert "CCH_a" not in mod._known_character_ids()
    assert "cch_a_tok" not in mod._known_advisor_idea_ids()
    mod.save()
    assert "CCH_a" not in mod._known_character_ids()
    assert "cch_a_tok" not in mod._known_advisor_idea_ids()


def test_a_deleted_idea_does_not_come_back_after_save(tmp_path):
    root = mod_root(tmp_path)
    write(
        root,
        "common/ideas/i.txt",
        "ideas = {\n country = {\n  I_keep = { }\n  I_drop = { }\n }\n}\n",
    )
    mod = Mod(root)
    assert {"I_keep", "I_drop"} <= mod._known_idea_ids()
    assert mod.delete_idea("I_drop")
    assert "I_drop" not in mod._known_idea_ids()
    mod.save()
    assert "I_drop" not in mod._known_idea_ids()
    assert "I_keep" in mod._known_idea_ids()


def test_discard_forgets_the_references_of_changed_files_only(tmp_path):
    root = mod_root(tmp_path)
    effects = write(
        root, "common/scripted_effects/e.txt", "my_effect = { add_political_power = 1 }\n"
    )
    events = write(
        root, "events/e.txt", "add_namespace = e\ncountry_event = { id = e.1 }\n"
    )
    mod = Mod(root)
    mod.validate(stage="release")
    assert {effects, events} <= set(mod._own_reference_cache)
    # Nothing changed: the entries (checked against the text on every use) stay.
    mod.discard()
    assert {effects, events} <= set(mod._own_reference_cache)
    # An edit unloads the events section, and its files' entries go with it.
    mod.update_event("e.1", title="e.1.t")
    mod.discard()
    assert effects in mod._own_reference_cache
    assert events not in mod._own_reference_cache
    # A file changed on disk is forgotten as well.
    effects.write_text("my_effect = { add_political_power = 2 }\n", encoding="utf-8")
    mod.discard()
    assert effects not in mod._own_reference_cache
    assert mod.validate(stage="release") == Mod(root).validate(stage="release")


def own_corpus(mod: Mod) -> list[tuple[object, ...]]:
    """The references of the mod's own files, with the scope of each."""

    return [
        (
            found.relative,
            tuple(
                (owner.kind, owner.id, owner.scope, tuple(
                    (ref.kind, ref.action, ref.value, ref.scope, ref.line) for ref in owner.refs
                ))
                for owner in found.owners
            ),
        )
        for found in mod._build_reference_corpus(inherited=False)
    ]


def test_cached_own_references_follow_the_character_names(tmp_path):
    """The references of own files are read with the character ids, cached by
    text and by the identity of the id set: they must equal a fresh Mod's
    after character edits, a partial discard and a section that reloads."""

    from hoi4.characters import AdvisorRole, Character

    root = mod_root(tmp_path)
    write(root, "common/country_tags/00_countries.txt", 'CCH = "countries/Cch.txt"\n')
    write(root, "common/countries/Cch.txt", "color = { 1 2 3 }\n")
    write(
        root,
        "common/characters/CCH.txt",
        "characters = {\n CCH_hero = {\n  name = CCH_hero\n"
        "  advisor = { slot = political_advisor idea_token = cch_hero_tok }\n }\n}\n",
    )
    write(
        root,
        "events/e.txt",
        "add_namespace = e\ncountry_event = { id = e.1 is_triggered_only = yes\n"
        " immediate = {\n  CCH_hero = { owner = { set_country_flag = hero_flag } }\n"
        "  CCH_new = { owner = { set_country_flag = new_flag } }\n } }\n",
    )
    new_character = Character(
        id="CCH_new",
        name="CCH_new",
        roles=[AdvisorRole(slot="political_advisor", idea_token="cch_new_tok")],
    )
    mod = Mod(root)
    start = own_corpus(mod)
    assert start == own_corpus(Mod(root))

    assert mod.delete_character("CCH_hero")
    deleted = own_corpus(mod)
    fresh = Mod(root)
    assert fresh.delete_character("CCH_hero")
    assert deleted == own_corpus(fresh)
    assert deleted != start

    mod.discard()
    assert own_corpus(mod) == start

    mod.create_character("CCH", new_character)
    created = own_corpus(mod)
    fresh = Mod(root)
    fresh.create_character("CCH", new_character)
    assert created == own_corpus(fresh)
    assert created != start

    # A partial discard keeps the entries of files no unloaded section reads,
    # and the names go back to the saved ones.
    mod.discard()
    assert own_corpus(mod) == start
    mod.update_event("e.1", title="e.1.t")
    mod.discard()
    assert own_corpus(mod) == start
    first = mod.validate(stage="release")
    assert mod.delete_character("CCH_hero")
    assert mod.validate(stage="release") == fresh_validate(root, delete="CCH_hero")
    mod.discard()
    assert mod.validate(stage="release") == first


def fresh_validate(root: Path, *, delete: str) -> list[ValidationError]:
    mod = Mod(root)
    assert mod.delete_character(delete)
    return mod.validate(stage="release")


# ── Small things ────────────────────────────────────────────────────


def test_suppress_as_one_string_names_the_mistake(tmp_path):
    import pytest

    mod = Mod(mod_root(tmp_path))
    with pytest.raises(ValueError, match="takes a list of codes, not one string"):
        mod.validate(stage="build", suppress="visual_overlap")
    with pytest.raises(ValueError, match="takes a list of codes, not one string"):
        mod.validate_effect("add_political_power = 1", suppress="visual_overlap")
    # The older name of the same list is gone.
    with pytest.raises(TypeError):
        mod.validate(stage="build", suppress_warnings=["visual_overlap"])
    with pytest.raises(TypeError):
        mod.validate_effect("add_political_power = 1", suppress_warnings=["visual_overlap"])
    # Every option is keyword-only, so no positional argument changes meaning.
    with pytest.raises(TypeError):
        mod.validate(None, False)


def test_every_error_code_is_listed_once():
    import ast
    import inspect

    import hoi4.validation as validation

    tree = ast.parse(inspect.getsource(validation))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_ERROR_CODES" for target in node.targets
        ):
            assert isinstance(node.value, ast.Set)
            names = [element.value for element in node.value.elts if isinstance(element, ast.Constant)]
            assert len(names) == len(set(names))
            break
    else:
        raise AssertionError("_ERROR_CODES not found")


def test_country_history_references_read_the_unsaved_history(tmp_path):
    from hoi4.characters import AdvisorRole, Character

    root = mod_root(tmp_path)
    write(root, "common/country_tags/00_countries.txt", 'CCH = "countries/Cch.txt"\n')
    write(root, "common/countries/Cch.txt", "color = { 1 2 3 }\n")
    write(
        root,
        "common/characters/CCH.txt",
        "characters = {\n CCH_boss = {\n  name = CCH_boss\n"
        "  country_leader = { ideology = liberalism }\n }\n}\n",
    )
    write(root, "history/countries/CCH - Cch.txt", "capital = 1\nrecruit_character = CCH_boss\n")
    mod = Mod(root)
    assert not codes(mod.validate(stage="build"), "character_ref")
    mod.create_character(
        "CCH",
        Character(
            id="CCH_new",
            name="CCH_new",
            roles=[AdvisorRole(slot="political_advisor", idea_token="cch_new_tok")],
        ),
    )
    assert "recruit_character = CCH_new" in mod.preview()
    assert not codes(mod.validate(stage="build"), "character_ref")
    # Deleting the character but keeping its unsaved recruitment is a real mistake.
    assert mod.delete_character("CCH_new", remove_recruitment=False)
    findings = codes(mod.validate(stage="build"), "character_ref")
    assert [f.message for f in findings] == ["Character CCH_new is not defined"]


def test_a_focus_id_defined_twice_is_one_finding(tmp_path):
    focus = (
        "focus = {{ id = {id} icon = GFX_goal_generic_production x = {x} y = 0 cost = 1 "
        "completion_reward = {{ add_political_power = 1 }} }}"
    )
    write(
        tmp_path,
        "common/national_focus/twice.txt",
        "focus_tree = {\n\tid = twice_tree\n\t"
        + "\n\t".join(
            [focus.format(id="twice_a", x=1), focus.format(id="twice_a", x=1), focus.format(id="twice_b", x=3)]
        )
        + "\n}\n",
    )
    findings = Mod(tmp_path).validate()

    duplicates = codes(findings, "duplicate_focus_id")
    assert len(duplicates) == 1
    assert "twice_a" in duplicates[0].message
    # The second definition is not an overlap of the focus with itself, and
    # its missing texts are not reported a second time.
    assert not codes(findings, "visual_overlap")
    messages = [finding.message for finding in codes(findings, "missing_localization")]
    assert len(messages) == len(set(messages))


# ── Country tags ────────────────────────────────────────────────────


def _focus_scoping_into(tag: str) -> str:
    return (
        "focus_tree = {\n id = tag_tree\n focus = {\n  id = TAG_scope x = 0 y = 0\n"
        f"  completion_reward = {{ {tag} = {{ add_political_power = 5 }} }}\n }}\n}}\n"
    )


def test_replace_path_hides_the_country_tags_of_the_game(tmp_path):
    game = tmp_path / "game"
    write(game, "common/country_tags/00_countries.txt", 'GER = "countries/Germany.txt"\n')
    write(game, "common/countries/Germany.txt", "color = { 1 2 3 }\n")
    root = mod_root(tmp_path)
    write(root, "common/country_tags/mine.txt", 'AAA = "countries/Germany.txt"\n')
    write(root, "common/national_focus/tag.txt", _focus_scoping_into("GER"))
    assert not codes(Mod(root, hoi4_install=game).validate(), "unknown_country_scope")

    write(root, "descriptor.mod", 'name = "test"\nreplace_path = "common/country_tags"\n')
    findings = codes(Mod(root, hoi4_install=game).validate(), "unknown_country_scope")
    assert ["'GER'" in f.message for f in findings] == [True]


def test_a_deleted_country_is_an_unknown_tag_in_the_next_run(tmp_path):
    root = mod_root(tmp_path)
    mod = Mod(root)
    mod.create_country("CNN", "Ninety-nine")
    mod.save()
    write(root, "common/national_focus/tag.txt", _focus_scoping_into("CNN"))

    mod = Mod(root)
    assert not codes(mod.validate(), "unknown_country_scope")
    mod.delete_country("CNN")
    findings = codes(mod.validate(), "unknown_country_scope")
    assert ["'CNN'" in f.message for f in findings] == [True]
    mod.discard()
    assert not codes(mod.validate(), "unknown_country_scope")


# ── Focus trees and the container of a file's shared focuses ────────


def test_a_focus_of_a_tree_may_anchor_to_the_shared_focuses_the_tree_pulls_in(tmp_path):
    game = focus_game(tmp_path)
    # A shared focus below the one the tree pulls in comes with it.
    write(
        game,
        "common/national_focus/congo_more.txt",
        "shared_focus = {\n id = CONGO_child\n prerequisite = { focus = CONGO_free_state }\n"
        " relative_position_id = CONGO_free_state\n x = 0 y = 1\n}\n"
        "shared_focus = { id = OTHER_shared x = 9 y = 0 }\n",
    )
    root = mod_root(tmp_path)
    write(
        root,
        "common/national_focus/tree.txt",
        "focus_tree = {\n id = my_test_tree\n shared_focus = CONGO_free_state\n"
        " focus = {\n  id = MOD_on_shared\n  prerequisite = { focus = CONGO_free_state }\n"
        "  relative_position_id = CONGO_free_state\n  x = 0 y = 1\n }\n"
        " focus = { id = MOD_on_child relative_position_id = CONGO_child x = 0 y = 1 }\n"
        # Known focuses, but none of them is on this tree's canvas.
        " focus = { id = MOD_on_other_tree relative_position_id = army_effort x = 0 y = 1 }\n"
        " focus = { id = MOD_on_other_shared relative_position_id = OTHER_shared x = 0 y = 1 }\n"
        " focus = { id = MOD_lost relative_position_id = MOD_nowhere x = 0 y = 1 }\n}\n",
    )
    findings = codes(
        Mod(root, hoi4_install=game).validate(stage="build"), "unresolved_focus_position"
    )
    assert sorted(f.focus_id for f in findings) == [
        "MOD_lost",
        "MOD_on_other_shared",
        "MOD_on_other_tree",
    ]


def test_load_focus_tree_cannot_name_the_container_of_a_files_shared_focuses(tmp_path):
    root = mod_root(tmp_path)
    write(
        root,
        "common/national_focus/my_shared_container.txt",
        "shared_focus = {\n id = MOD_sf_1\n x = 0 y = 0\n cost = 5\n"
        " completion_reward = { load_focus_tree = { tree = my_shared_container } }\n}\n",
    )
    # With no tree at all nothing can be said about a tree name.
    write(
        root,
        "common/national_focus/real.txt",
        "focus_tree = {\n id = real_tree\n focus = { id = REAL_one x = 0 y = 0 }\n}\n",
    )
    mod = Mod(root)
    assert "my_shared_container" in mod.list_focus_trees()
    findings = codes(mod.validate(), "unknown_focus_tree_reference")
    assert [f.focus_id for f in findings] == ["MOD_sf_1"]
    effect = mod.validate_effect("load_focus_tree = { tree = my_shared_container }")
    assert [f.code for f in effect] == ["unknown_focus_tree_reference"]
