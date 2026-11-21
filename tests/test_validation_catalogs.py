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


def test_discard_forgets_the_references_of_the_mods_files(tmp_path):
    root = mod_root(tmp_path)
    write(root, "common/scripted_effects/e.txt", "my_effect = { add_political_power = 1 }\n")
    mod = Mod(root)
    mod.validate(stage="release")
    assert mod._own_reference_cache
    mod.discard()
    assert not mod._own_reference_cache


# ── Small things ────────────────────────────────────────────────────


def test_suppress_as_one_string_names_the_mistake(tmp_path):
    import pytest

    mod = Mod(mod_root(tmp_path))
    for kwargs in ({"suppress": "visual_overlap"}, {"suppress_warnings": "visual_overlap"}):
        with pytest.raises(ValueError, match="takes a list of codes, not one string"):
            mod.validate(stage="build", **kwargs)
    with pytest.raises(ValueError, match="takes a list of codes, not one string"):
        mod.validate_effect("add_political_power = 1", suppress_warnings="visual_overlap")


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
