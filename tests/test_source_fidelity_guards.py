from __future__ import annotations

from pathlib import Path

import pytest

from hoi4 import Mod
from hoi4.content_validation import validate_ideology
from hoi4.ideologies import Ideology, SubIdeology, load_ideologies_file
from hoi4.ideologies import serialize_ideologies_file
from hoi4.parser import parse_pdx


def _assert_guarded_rewrite(mod: Mod, source_path: Path, original: str) -> None:
    message = "duplicate definitions skipped during loading would be lost"
    with pytest.raises(RuntimeError, match=message):
        mod.preview()
    with pytest.raises(RuntimeError, match=message):
        mod.save()
    assert source_path.read_text(encoding="utf-8") == original


def test_duplicate_ideology_cannot_be_lost_by_editing_same_file(tmp_path: Path) -> None:
    ideology_dir = tmp_path / "common" / "ideologies"
    ideology_dir.mkdir(parents=True)
    (ideology_dir / "00_first.txt").write_text(
        "ideologies = { shared = { color = { 1 2 3 } } }\n",
        encoding="utf-8",
    )
    second_path = ideology_dir / "01_second.txt"
    second_source = """ideologies = {
    shared = { color = { 4 5 6 } }
    editable = { color = { 7 8 9 } }
}
"""
    second_path.write_text(second_source, encoding="utf-8")
    mod = Mod(tmp_path)

    assert mod.update_ideology("editable", color=(9, 8, 7))

    _assert_guarded_rewrite(mod, second_path, second_source)


def test_duplicate_dynamic_idea_cannot_be_lost_by_editing_same_file(
    tmp_path: Path,
) -> None:
    ideas_dir = tmp_path / "common" / "national_ideas"
    ideas_dir.mkdir(parents=True)
    (ideas_dir / "00_first.txt").write_text(
        """dynamic_country_ideas = {
    name = first_group
    shared = { modifier = { stability_factor = 0.1 } }
}
""",
        encoding="utf-8",
    )
    second_path = ideas_dir / "01_second.txt"
    second_source = """dynamic_country_ideas = {
    name = second_group
    shared = { modifier = { stability_factor = 0.2 } }
    editable = { modifier = { stability_factor = 0.3 } }
}
"""
    second_path.write_text(second_source, encoding="utf-8")
    mod = Mod(tmp_path)

    assert mod.update_dynamic_idea("editable", modifier={"stability_factor": 0.4})

    _assert_guarded_rewrite(mod, second_path, second_source)


def test_duplicate_bookmark_cannot_be_lost_by_editing_same_file(tmp_path: Path) -> None:
    bookmark_dir = tmp_path / "common" / "bookmarks"
    bookmark_dir.mkdir(parents=True)
    (bookmark_dir / "00_first.txt").write_text(
        'bookmarks = { bookmark = { name = "SHARED_START" } }\n',
        encoding="utf-8",
    )
    second_path = bookmark_dir / "01_second.txt"
    second_source = """bookmarks = {
    bookmark = { name = "SHARED_START" }
    bookmark = { name = "EDITABLE_START" picture = GFX_original }
}
"""
    second_path.write_text(second_source, encoding="utf-8")
    mod = Mod(tmp_path)

    assert mod.update_bookmark("EDITABLE_START", picture="GFX_updated")

    _assert_guarded_rewrite(mod, second_path, second_source)


def test_duplicate_event_cannot_be_lost_by_editing_same_file(tmp_path: Path) -> None:
    events = tmp_path / "events"
    events.mkdir(parents=True)
    (events / "00_first.txt").write_text(
        "country_event = { id = shared.1 option = { name = shared.1.a } }\n",
        encoding="utf-8",
    )
    second_path = events / "01_second.txt"
    second_source = """country_event = { id = shared.1 option = { name = shared.1.b } }
country_event = { id = editable.1 picture = GFX_old option = { name = editable.1.a } }
"""
    second_path.write_text(second_source, encoding="utf-8")
    mod = Mod(tmp_path)

    assert mod.update_event("editable.1", picture="GFX_new")

    _assert_guarded_rewrite(mod, second_path, second_source)


def test_duplicate_decision_cannot_be_lost_by_editing_same_file(tmp_path: Path) -> None:
    decisions = tmp_path / "common/decisions"
    decisions.mkdir(parents=True)
    (decisions / "00_first.txt").write_text(
        "first_category = { shared_decision = { cost = 1 } }\n",
        encoding="utf-8",
    )
    second_path = decisions / "01_second.txt"
    second_source = """second_category = {
    shared_decision = { cost = 2 }
    editable_decision = { cost = 3 }
}
"""
    second_path.write_text(second_source, encoding="utf-8")
    mod = Mod(tmp_path)

    assert mod.update_decision("editable_decision", cost=4)

    _assert_guarded_rewrite(mod, second_path, second_source)


def test_repeated_decision_category_in_one_file_cannot_be_collapsed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "common/decisions/repeated.txt"
    path.parent.mkdir(parents=True)
    source = """shared_category = { first_decision = { cost = 1 } }
shared_category = { second_decision = { cost = 2 } }
"""
    path.write_text(source, encoding="utf-8")
    mod = Mod(tmp_path)

    diagnostic = next(
        item for item in mod.load_diagnostics if item.section == "decision_category"
    )
    assert diagnostic.identifier == "shared_category"
    assert any(
        issue.code == "duplicate_decision_category_id" for issue in mod.validate()
    )
    assert mod.update_decision("first_decision", cost=3)

    _assert_guarded_rewrite(mod, path, source)


def test_duplicate_idea_cannot_be_lost_by_editing_same_file(tmp_path: Path) -> None:
    ideas = tmp_path / "common/ideas"
    ideas.mkdir(parents=True)
    (ideas / "00_first.txt").write_text(
        "ideas = { country = { shared_idea = { modifier = { stability_factor = 0.1 } } } }\n",
        encoding="utf-8",
    )
    second_path = ideas / "01_second.txt"
    second_source = """ideas = { country = {
    shared_idea = { modifier = { stability_factor = 0.2 } }
    editable_idea = { modifier = { stability_factor = 0.3 } }
} }
"""
    second_path.write_text(second_source, encoding="utf-8")
    mod = Mod(tmp_path)

    assert mod.update_idea("editable_idea", modifier={"stability_factor": 0.4})

    _assert_guarded_rewrite(mod, second_path, second_source)


def test_duplicate_focus_cannot_be_lost_by_editing_same_file(tmp_path: Path) -> None:
    focus_path = tmp_path / "common/national_focus/duplicate.txt"
    focus_path.parent.mkdir(parents=True)
    source = """focus_tree = {
    id = duplicate_tree
    focus = { id = shared_focus x = 0 y = 0 }
    focus = { id = shared_focus x = 1 y = 0 }
    focus = { id = editable_focus x = 2 y = 0 }
}
"""
    focus_path.write_text(source, encoding="utf-8")
    mod = Mod(tmp_path)

    assert mod.update_focus("duplicate_tree", "editable_focus", x=3)

    _assert_guarded_rewrite(mod, focus_path, source)


def test_compositional_on_actions_across_files_are_retained_and_selectable(
    tmp_path: Path,
) -> None:
    actions = tmp_path / "common/on_actions"
    actions.mkdir(parents=True)
    first_path = actions / "00_first.txt"
    first_source = "on_actions = { shared_action = { effect = { first = yes } } }\n"
    first_path.write_text(
        first_source,
        encoding="utf-8",
    )
    second_path = actions / "01_second.txt"
    second_source = """on_actions = {
    shared_action = { effect = { second = yes } }
    editable_action = { effect = { old = yes } }
}
"""
    second_path.write_text(second_source, encoding="utf-8")
    mod = Mod(tmp_path, strict_loading=True)

    assert mod.load_diagnostics == ()
    assert len(mod.get_on_action_occurrences("shared_action")) == 2
    assert not any(error.code == "duplicate_on_action_id" for error in mod.validate())
    with pytest.raises(ValueError, match="compositional occurrences"):
        mod.update_on_action("shared_action", effect="ambiguous = yes")

    assert mod.update_on_action("shared_action", occurrence=1, effect="second = changed")
    mod.save(require_changes=True)

    assert first_path.read_text(encoding="utf-8") == first_source
    assert second_path.read_text(encoding="utf-8") == second_source.replace(
        "second = yes", "second = changed"
    )


def test_repeated_on_action_in_one_file_edits_and_deletes_exact_occurrence(
    tmp_path: Path,
) -> None:
    actions = tmp_path / "common/on_actions"
    actions.mkdir(parents=True)
    path = actions / "actions.txt"
    source = """on_actions = {
    on_startup = { effect = { first = yes } }
    between = { effect = { untouched = yes } }
    on_startup = { effect = { second = yes } }
}
"""
    path.write_text(source, encoding="utf-8")
    mod = Mod(tmp_path, strict_loading=True)

    assert len(mod.get_on_action_occurrences("on_startup")) == 2
    assert mod.update_on_action("on_startup", occurrence=1, effect="second = changed")
    mod.save(require_changes=True)
    edited = source.replace("second = yes", "second = changed")
    assert path.read_text(encoding="utf-8") == edited

    assert mod.delete_on_action("on_startup", occurrence=0)
    mod.save(require_changes=True)
    content = path.read_text(encoding="utf-8")
    assert "first = yes" not in content
    assert "second = changed" in content
    assert "untouched = yes" in content


def test_create_on_action_adds_extension_in_a_different_file(tmp_path: Path) -> None:
    actions = tmp_path / "common/on_actions"
    actions.mkdir(parents=True)
    original_path = actions / "existing.txt"
    original_source = "on_actions = { on_startup = { effect = { existing = yes } } }\n"
    original_path.write_text(original_source, encoding="utf-8")
    mod = Mod(tmp_path)

    created = mod.create_on_action("on_startup", effect="extension = yes")
    mod.save(require_changes=True)

    assert created.path == actions / "mod_on_actions.txt"
    assert original_path.read_text(encoding="utf-8") == original_source
    assert "extension = yes" in (actions / "mod_on_actions.txt").read_text(
        encoding="utf-8"
    )
    assert len(mod.get_on_action_occurrences("on_startup")) == 2
    with pytest.raises(ValueError, match="already has an occurrence"):
        mod.create_on_action("on_startup", effect="accidental_duplicate = yes")


def test_deleting_last_occurrence_preserves_existing_file_comments(
    tmp_path: Path,
) -> None:
    actions = tmp_path / "common/on_actions"
    actions.mkdir(parents=True)
    target = actions / "startup.txt"
    target.write_text(
        "# header\non_actions = {\n    on_startup = { effect = { old = yes } }\n}\n# footer\n",
        encoding="utf-8",
    )
    (actions / "other.txt").write_text(
        "on_actions = { on_daily = { effect = { keep = yes } } }\n",
        encoding="utf-8",
    )
    mod = Mod(tmp_path)

    assert mod.delete_on_action("on_startup")
    mod.save(require_changes=True)

    assert target.exists()
    content = target.read_text(encoding="utf-8")
    assert content.startswith("# header\n")
    assert content.endswith("# footer\n")
    assert "on_actions = {" in content
    assert "on_startup" not in content


def test_create_then_delete_on_action_does_not_create_empty_file(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    mod.create_on_action("on_startup", effect="temporary = yes")
    assert mod.delete_on_action("on_startup")

    assert mod.preview() == ""
    assert not (tmp_path / "common/on_actions/mod_on_actions.txt").exists()


def test_subideology_edit_preserves_unknown_source_and_comments(tmp_path: Path) -> None:
    path = tmp_path / "ideologies.txt"
    source = """ideologies = {
    custom = {
        types = {
            custom_main = {
                can_be_randomly_selected = no # selection comment
                future_setting = { keep = yes } # future comment
            }
            # between subtypes
            custom_second = { }
        }
        color = { 1 2 3 }
    }
}
"""
    path.write_text(source, encoding="utf-8")
    ideology = load_ideologies_file(path)[0]

    assert "future_setting = { keep = yes }" in ideology.types[0].raw_block
    ideology.types[0].can_be_randomly_selected = True
    ideology.types.append(SubIdeology("custom_added", False))
    ideology.touched_fields.add("types")

    rendered = serialize_ideologies_file([ideology], original=source)

    assert "can_be_randomly_selected = yes # selection comment" in rendered
    assert "future_setting = { keep = yes } # future comment" in rendered
    assert "# between subtypes" in rendered
    assert "custom_second = { }" in rendered
    assert "custom_added = {" in rendered
    assert "can_be_randomly_selected = no" in rendered
    parse_pdx(rendered)


@pytest.mark.parametrize("color", [(1, 2), (1, 2, 3, 4)])
def test_ideology_color_requires_exactly_three_channels(color: tuple[int, ...]) -> None:
    ideology = Ideology(id="custom", color=color)  # type: ignore[arg-type]

    issues = validate_ideology(ideology)

    assert [issue.code for issue in issues] == ["invalid_ideology_color"]
    assert "exactly three RGB channels" in issues[0].message


def test_ideology_loader_retains_invalid_color_cardinality_for_validation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ideologies.txt"
    path.write_text(
        "ideologies = { malformed = { color = { 1 2 3 4 } } }\n",
        encoding="utf-8",
    )

    ideology = load_ideologies_file(path)[0]
    issues = validate_ideology(ideology)

    assert ideology.color == (1, 2, 3, 4)
    assert [issue.code for issue in issues] == ["invalid_ideology_color"]


def test_new_focus_tree_preserves_non_focus_content_in_target_file(tmp_path: Path) -> None:
    path = tmp_path / "common" / "national_focus" / "ABC_focus.txt"
    path.parent.mkdir(parents=True)
    original = "# shared declarations\nstyle = { name = custom_style default = yes }\n"
    path.write_text(original, encoding="utf-8")

    mod = Mod(tmp_path)
    mod.create_focus_tree("abc_focus", "ABC")
    mod.save(require_changes=True)

    rendered = path.read_text(encoding="utf-8")
    assert original in rendered
    assert "focus_tree = {" in rendered


def test_new_dynamic_group_preserves_mixed_target_file(tmp_path: Path) -> None:
    path = tmp_path / "common" / "national_ideas" / "ABC_dynamic.txt"
    path.parent.mkdir(parents=True)
    original = "# shared declarations\nother_setting = yes\n"
    path.write_text(original, encoding="utf-8")

    mod = Mod(tmp_path)
    mod.create_dynamic_idea_group("ABC_dynamic")
    mod.create_dynamic_idea(
        "ABC_dynamic",
        "ABC_growing",
        modifier={"political_power_gain": 0.1},
    )
    mod.save(require_changes=True)

    rendered = path.read_text(encoding="utf-8")
    assert original in rendered
    assert "dynamic_country_ideas = {" in rendered
    assert "ABC_growing = {" in rendered
