from __future__ import annotations

import os
from pathlib import Path

import pytest

from hoi4 import EventOption, Focus, Mod, ParseError, parse_pdx, serialize_pdx
from hoi4.focus import load_focus_trees, serialize_focus_file
from hoi4.localisation import parse_localization_file, serialize_localization_file
from hoi4.script import pdx_string, validate_script_syntax


def test_output_paths_and_script_identifiers_reject_injection(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    with pytest.raises(ValueError, match="escapes mod root"):
        mod.create_on_action("safe_action", path="../outside.txt")
    with pytest.raises(ValueError, match="Invalid event ID"):
        mod.create_event("bad.1 } add_political_power = 999")
    with pytest.raises(ValueError, match="Invalid country tag"):
        mod.effect_transfer_state(1, "GER } add_political_power = 999")
    with pytest.raises(ValueError, match="Invalid war goal type"):
        mod.effect_declare_war("GER", "annex_everything } bad = yes")
    assert not (tmp_path.parent / "outside.txt").exists()


def test_relative_custom_paths_are_rooted_in_mod(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    mod.create_on_action("on_test", effect="add_stability = 0.1", path="common/on_actions/x.txt")
    result = mod.save(require_changes=True)
    expected = (tmp_path / "common" / "on_actions" / "x.txt").resolve()
    assert expected in result.written_files
    assert expected.exists()


def test_quoted_string_localization_and_empty_block_round_trip(tmp_path: Path) -> None:
    assert pdx_string('A "quoted" \\ name') == '"A \\"quoted\\" \\\\ name"'
    parsed = parse_pdx('empty = { } label = "A \\"quoted\\" value"')
    rendered = serialize_pdx(parsed)
    assert "empty = {" in rendered
    assert 'label = "A \\"quoted\\" value"' in rendered

    path = tmp_path / "loc.yml"
    path.write_text(
        serialize_localization_file({"TEST:0": 'A "quoted" \\ value'}), encoding="utf-8-sig"
    )
    assert parse_localization_file(path) == {"TEST": 'A "quoted" \\ value'}


@pytest.mark.parametrize("text", ["}", "outer = { inner = yes", 'key = "unterminated'])
def test_parser_rejects_malformed_input(text: str) -> None:
    with pytest.raises(ParseError):
        parse_pdx(text)


def test_script_syntax_reports_unclosed_quote() -> None:
    assert validate_script_syntax('name = "unterminated') == [
        "Unclosed quote from line 1, column 8"
    ]


def test_save_deletes_all_supported_objects_and_reports_files(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    mod.create_country("TST", "Testland")
    mod.create_event(
        "tst.1",
        options=[EventOption(name="tst.1.a", effect="add_political_power = 1")],
    )
    mod.create_idea("TST_spirit", modifier={"stability_factor": 0.1})
    mod.create_focus_tree("tst_focus", "TST")
    mod.add_focus("tst_focus", Focus(id="TST_start"))
    mod.set_focus_loc("TST_start", "Start", "Start here")
    first = mod.save(require_changes=True)
    assert len(first.written_files) >= 8

    reloaded = Mod(tmp_path)
    assert reloaded.delete_country("TST")
    assert reloaded.delete_event("tst.1")
    assert reloaded.delete_idea("TST_spirit")
    assert reloaded.delete_focus_tree("tst_focus")
    assert reloaded.delete_loc("TST_start")
    second = reloaded.save(require_changes=True)
    assert second.written_files

    final = Mod(tmp_path)
    assert "TST" not in final.list_countries()
    assert "tst.1" not in final.list_events()
    assert "TST_spirit" not in final.list_ideas()
    assert "tst_focus" not in final.list_focus_trees()
    assert final.get_loc("TST_start") is None


def test_state_patch_is_transactional_and_preserves_dated_history(tmp_path: Path) -> None:
    path = tmp_path / "history" / "states" / "1-Test.txt"
    path.parent.mkdir(parents=True)
    original = """state = {
 id = 1
 name = STATE_1
 manpower = 1
 state_category = town
 provinces = { 1 }
 history = {
  owner = GER
  add_core_of = GER
  1939.1.1 = { owner = FRA add_core_of = FRA }
 }
}
"""
    path.write_text(original, encoding="utf-8")
    mod = Mod(tmp_path)
    mod.patch_state_history(1, owner="TST", add_cores=["TST"], remove_cores=["GER"])
    assert path.read_text(encoding="utf-8") == original
    preview = mod.preview()
    assert "owner = TST" in preview
    mod.save(require_changes=True)
    saved = path.read_text(encoding="utf-8")
    assert "owner = TST" in saved
    assert "add_core_of = TST" in saved
    assert "add_core_of = GER" not in saved.split("1939.1.1", 1)[0]
    assert "1939.1.1 = { owner = FRA add_core_of = FRA }" in saved
    assert "\n \t1939.1.1" in saved


def test_transaction_rolls_back_in_memory_without_touching_disk(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    with mod.transaction():
        mod.create_country("TST", "Temporary")
        assert "TST" in mod.preview()
    assert not any(tmp_path.rglob("*.txt"))
    assert "TST" not in mod.list_countries()


def test_multi_tree_file_and_original_tag_selector_survive_update(tmp_path: Path) -> None:
    path = tmp_path / "common" / "national_focus" / "shared.txt"
    path.parent.mkdir(parents=True)
    original = """# shared header
focus_tree = {
 id = first_tree
 country = { factor = 0 modifier = { add = 10 original_tag = TST } }
 focus = { id = TST_one icon = GFX_goal_generic_construct_civ_factory x = 0 y = 0 cost = 5 custom = keep }
}
focus_tree = {
 id = second_tree
 default = yes
 focus = { id = OTHER_one icon = GFX_goal_generic_construct_civ_factory x = 0 y = 0 cost = 5 }
}
"""
    path.write_text(original, encoding="utf-8")
    trees = load_focus_trees(path)
    assert [tree.id for tree in trees] == ["first_tree", "second_tree"]
    assert trees[0].country_tag == "TST"
    trees[0].focuses[0].cost = 7
    trees[0].focuses[0].touched = True
    rendered = serialize_focus_file(trees, original)
    assert "original_tag = TST" in rendered
    assert "custom = keep" in rendered
    assert "id = second_tree" in rendered


def test_lossless_updates_preserve_unknown_fields_across_sections(tmp_path: Path) -> None:
    files = {
        "events/custom.txt": """add_namespace = tst
country_event = { id = tst.1 title = tst.1.t desc = tst.1.d custom_event_field = keep option = { name = tst.1.a custom_option_field = keep add_political_power = 1 } }
""",
        "common/ideas/custom.txt": """# ideas = { commented_template = { } }
ideas = { country = { TST_spirit = { picture = GFX_idea_generic custom_idea_field = keep modifier = { stability_factor = 0.1 } } } }
""",
        "common/decisions/custom.txt": """tst_category = { icon = generic_decision custom_category_field = keep tst_decision = { cost = 1 custom_decision_field = keep complete_effect = { add_political_power = 1 } } }
""",
        "common/on_actions/custom.txt": """on_actions = { on_startup = { custom_action_field = keep effect = { add_political_power = 1 } } }
""",
    }
    for relative, content in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    mod = Mod(tmp_path, strict_loading=True)
    mod.update_event("tst.1", picture="GFX_report_event_generic")
    mod.update_idea("TST_spirit", modifier={"war_support_factor": 0.2})
    mod.update_decision("tst_decision", cost=2)
    mod.update_on_action("on_startup", effect="add_stability = 0.1")
    mod.save(require_changes=True)

    assert "custom_event_field = keep" in (tmp_path / "events/custom.txt").read_text()
    assert "custom_option_field = keep" in (tmp_path / "events/custom.txt").read_text()
    assert "custom_idea_field = keep" in (tmp_path / "common/ideas/custom.txt").read_text()
    assert "custom_category_field = keep" in (tmp_path / "common/decisions/custom.txt").read_text()
    assert "custom_decision_field = keep" in (tmp_path / "common/decisions/custom.txt").read_text()
    assert "custom_action_field = keep" in (tmp_path / "common/on_actions/custom.txt").read_text()


def test_file_move_removes_old_file_and_identifiers_are_immutable(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    mod.create_on_action("on_test", effect="add_stability = 0.1", path="common/on_actions/old.txt")
    mod.save(require_changes=True)
    reloaded = Mod(tmp_path)
    with pytest.raises(ValueError, match="immutable"):
        reloaded.update_on_action("on_test", id="renamed")
    reloaded.update_on_action("on_test", path="common/on_actions/new.txt")
    reloaded.save(require_changes=True)
    assert not (tmp_path / "common/on_actions/old.txt").exists()
    assert (tmp_path / "common/on_actions/new.txt").exists()


def test_atomic_save_restores_every_file_after_mid_commit_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = Mod(tmp_path)
    mod.create_country("TST", "Testland")
    real_replace = os.replace
    calls = 0

    def fail_second(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated commit failure")
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_second)
    with pytest.raises(OSError, match="simulated commit failure"):
        mod.save(require_changes=True)
    assert not any(path.is_file() for path in tmp_path.rglob("*"))
    assert "TST" in mod.list_countries()
    assert mod.preview()


def test_loader_diagnostics_are_visible_and_strict_mode_raises(tmp_path: Path) -> None:
    path = tmp_path / "events" / "broken.txt"
    path.parent.mkdir(parents=True)
    path.write_text("country_event = { id = broken.1", encoding="utf-8")
    mod = Mod(tmp_path)
    assert len(mod.load_diagnostics) == 1
    assert mod.load_diagnostics[0].section == "event"
    assert mod.load_diagnostics[0].path == path
    with pytest.raises(RuntimeError, match="Failed to load event file"):
        Mod(tmp_path, strict_loading=True)


def test_effect_helpers_validate_tags_and_quote_faction_names() -> None:
    assert Mod.effect_create_faction('A "New" League') == 'create_faction = "A \\"New\\" League"'
    assert Mod.effect_add_target_to_faction("GER", "FRA") == "GER = { add_to_faction = FRA }"
    for helper in (
        lambda: Mod.effect_release("BAD TAG"),
        lambda: Mod.effect_schedule_country_event("bad event"),
        lambda: Mod.effect_set_rule("bad rule", True),
        lambda: Mod.effect_set_technology("bad technology"),
    ):
        with pytest.raises(ValueError):
            helper()
