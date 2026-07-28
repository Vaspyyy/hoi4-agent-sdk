from pathlib import Path
import json

import pytest

from hoi4 import (
    Mod,
    Focus,
    Country,
    State,
    Event,
    EventOption,
    Idea,
    Leader,
    VALIDATION_WARNING_CODES,
    ExternalModificationError,
)
from hoi4 import TECHNOLOGY_CATEGORIES, effect_block, scope_block
from hoi4.config import Config, find_config
from hoi4.validation import (
    validate_focus_tree,
    validate_country,
    validate_state,
    validate_event,
    validate_idea,
)
from hoi4.types import FocusTree


FIXTURES = Path(__file__).parent / "fixtures"


class TestModFocusTrees:
    def test_loads_focus_trees(self, tmp_mod):
        mod = tmp_mod.with_focus_tree("GER_focus.txt")
        trees = mod.list_focus_trees()
        assert "german_focus" in trees

    def test_get_focus_tree(self, tmp_mod):
        mod = tmp_mod.with_focus_tree("GER_focus.txt")
        tree = mod.get_focus_tree("german_focus")
        assert tree.country_tag == "GER"
        assert len(tree.focuses) == 3

    def test_add_focus(self, tmp_mod):
        mod = tmp_mod.with_focus_tree("GER_focus.txt")
        mod.add_focus("german_focus", Focus(id="GER_new", x=10, y=7))
        assert mod.get_focus("german_focus", "GER_new") is not None
        assert mod.get_focus("german_focus", "GER_new").x == 10

    def test_remove_focus(self, tmp_mod):
        mod = tmp_mod.with_focus_tree("GER_focus.txt")
        assert mod.remove_focus("german_focus", "GER_anschluss")
        assert mod.get_focus("german_focus", "GER_anschluss") is None

    def test_update_focus(self, tmp_mod):
        mod = tmp_mod.with_focus_tree("GER_focus.txt")
        mod.update_focus("german_focus", "GER_anschluss", x=99, cost=5)
        f = mod.get_focus("german_focus", "GER_anschluss")
        assert f.x == 99
        assert f.cost == 5

    def test_create_focus_tree(self, tmp_mod):
        mod = tmp_mod.mod
        tree = mod.create_focus_tree("soviet_focus", "SOV")
        assert tree.id == "soviet_focus"
        assert tree.country_tag == "SOV"
        assert "soviet_focus" in mod.list_focus_trees()

    def test_create_focus_tree_requires_explicit_overwrite(self, tmp_mod):
        mod = tmp_mod.mod
        mod.create_focus_tree("soviet_focus", "SOV")
        with pytest.raises(ValueError, match="overwrite=True"):
            mod.create_focus_tree("soviet_focus", "SOV")
        tree = mod.create_focus_tree("soviet_focus", "SOV", overwrite=True)
        assert tree.focuses == []

    def test_delete_focus_tree(self, tmp_mod):
        mod = tmp_mod.with_focus_tree("GER_focus.txt")
        assert mod.delete_focus_tree("german_focus")
        assert "german_focus" not in mod.list_focus_trees()

    def test_get_focus_tree_not_found(self, tmp_mod):
        with pytest.raises(KeyError):
            tmp_mod.mod.get_focus_tree("nonexistent")

    def test_skips_non_focus_files_in_national_focus_dir(self, tmp_mod):
        mod = tmp_mod.with_focus_tree("GER_focus.txt")
        style_dst = tmp_mod.root / "common" / "national_focus" / "00_titlebar_styles.txt"
        style_dst.write_text(
            "style = {\n\tname = default_style\n\tdefault = yes\n}\n",
            encoding="utf-8",
        )
        mod.discard()
        assert "german_focus" in mod.list_focus_trees()
        assert "00_titlebar_styles" not in mod.list_focus_trees()
        assert len(mod.list_focus_trees()) == 1

    def test_non_focus_file_not_corrupted_on_save(self, tmp_mod):
        mod = tmp_mod.with_focus_tree("GER_focus.txt")
        style_dst = tmp_mod.root / "common" / "national_focus" / "00_titlebar_styles.txt"
        original = "style = {\n\tname = default_style\n\tdefault = yes\n}\n"
        style_dst.write_text(original, encoding="utf-8")
        mod.discard()

        mod.add_focus("german_focus", Focus(id="GER_new", x=1, y=1))
        mod.save()

        assert style_dst.read_text(encoding="utf-8") == original


class TestModLocalization:
    def test_set_and_get_loc(self, tmp_mod):
        mod = tmp_mod.mod
        mod.set_loc("TEST_KEY", "Test Value")
        assert mod.get_loc("TEST_KEY") == "Test Value"

    def test_delete_loc(self, tmp_mod):
        mod = tmp_mod.mod
        mod.set_loc("TEST_KEY", "Test Value")
        assert mod.delete_loc("TEST_KEY")
        assert mod.get_loc("TEST_KEY") is None

    def test_search_loc(self, tmp_mod):
        mod = tmp_mod.mod
        mod.set_loc("GER_anschluss", "Anschluss")
        mod.set_loc("GER_rhineland", "Rhineland")
        results = mod.search_loc("anschluss")
        assert "GER_anschluss" in results

    def test_all_loc(self, tmp_mod):
        mod = tmp_mod.mod
        mod.set_loc("A", "1")
        mod.set_loc("B", "2")
        assert mod.all_loc() == {"A": "1", "B": "2"}

    def test_first_key_preserves_header_only_localization_file(self, tmp_path):
        path = tmp_path / "localisation" / "english" / "mod_l_english.yml"
        path.parent.mkdir(parents=True)
        path.write_text(
            "\ufeff# translator note\nl_english:\n # reserved section\n",
            encoding="utf-8",
        )

        mod = Mod(tmp_path)
        mod.set_loc("FIRST_KEY", "First value")
        mod.save(require_changes=True)

        rendered = path.read_text(encoding="utf-8-sig")
        assert "# translator note" in rendered
        assert "# reserved section" in rendered
        assert 'FIRST_KEY:0 "First value"' in rendered


class TestModSave:
    def test_save_persists_focus_tree(self, tmp_mod):
        mod = tmp_mod.with_focus_tree("GER_focus.txt")
        mod.add_focus("german_focus", Focus(id="GER_saved", x=1, y=1))
        mod.save()

        mod2 = Mod(tmp_mod.root)
        assert mod2.get_focus("german_focus", "GER_saved") is not None

    def test_save_persists_localization(self, tmp_mod):
        mod = tmp_mod.mod
        mod.set_loc("SAVED_KEY", "Saved Value")
        mod.save()

        mod2 = Mod(tmp_mod.root)
        assert mod2.get_loc("SAVED_KEY") == "Saved Value"

    def test_save_no_changes_warns_and_reports_noop(self, tmp_mod):
        with pytest.warns(RuntimeWarning, match="no dirty changes"):
            result = tmp_mod.mod.save()
        assert result.no_changes is True
        assert result.written_files == []
        assert "No changes written" in result.message
        assert str(result) == result.message

    def test_save_require_changes_raises_on_noop(self, tmp_mod):
        with pytest.warns(RuntimeWarning, match="no dirty changes"):
            with pytest.raises(RuntimeError, match="No changes written"):
                tmp_mod.mod.save(require_changes=True)

    def test_discard_reverts_changes(self, tmp_mod):
        mod = tmp_mod.with_focus_tree("GER_focus.txt")
        mod.add_focus("german_focus", Focus(id="GER_temp", x=1, y=1))
        mod.discard()
        assert mod.get_focus("german_focus", "GER_temp") is None


class TestValidation:
    def test_valid_tree_no_errors(self):
        tree = FocusTree(id="test", country_tag="TST")
        tree.focuses.append(Focus(id="A", x=1, y=1))
        errors = validate_focus_tree(tree)
        assert len(errors) == 0

    def test_duplicate_id(self):
        tree = FocusTree(id="test")
        tree.focuses.append(Focus(id="A", x=1, y=1))
        tree.focuses.append(Focus(id="A", x=2, y=2))
        errors = validate_focus_tree(tree)
        assert any("Duplicate" in e.message for e in errors)

    def test_position_collision(self):
        tree = FocusTree(id="test")
        tree.focuses.append(Focus(id="A", x=1, y=1))
        tree.focuses.append(Focus(id="B", x=1, y=1))
        errors = validate_focus_tree(tree)
        assert any("position" in e.message.lower() for e in errors)
        assert errors[0].severity == "warning"

    def test_missing_prerequisite(self):
        tree = FocusTree(id="test")
        tree.focuses.append(Focus(id="B", x=1, y=1, prerequisites=[["nonexistent"]]))
        errors = validate_focus_tree(tree)
        assert any("nonexistent" in e.message for e in errors)

    def test_missing_mutually_exclusive(self):
        tree = FocusTree(id="test")
        tree.focuses.append(Focus(id="A", x=1, y=1, mutually_exclusive=[["ghost"]]))
        errors = validate_focus_tree(tree)
        assert any("ghost" in e.message for e in errors)

    def test_focus_requires_wraps_single_prerequisite(self):
        assert Focus(id="A", requires="ROOT").prerequisites == [["ROOT"]]
        assert Focus(id="B", requires=["A", "C"]).prerequisites == [["A", "C"]]

    def test_validate_knows_unsaved_created_country_tags(self, tmp_mod):
        mod = tmp_mod.mod
        mod.create_country("SCL", "San Celeste")
        state = State(id=99, owner="SCL", cores=["SCL"])
        mod._states[99] = state
        errors = mod.validate()
        assert not any("SCL" in e.message and "known country tag" in e.message for e in errors)


class TestPreview:
    def test_preview_shows_diff(self, tmp_mod):
        mod = tmp_mod.with_focus_tree("GER_focus.txt")
        mod.add_focus("german_focus", Focus(id="GER_new", x=1, y=1))
        diff = mod.preview()
        assert "GER_new" in diff
        assert "+" in diff

    def test_preview_country(self, tmp_mod):
        mod = tmp_mod.mod
        mod.create_country("ZZZ", "Zoolandia")
        diff = mod.preview()
        assert "ZZZ" in diff
        assert "+" in diff

    def test_preview_state(self, tmp_mod):
        mod = tmp_mod.with_states()
        mod.set_state_owner(1, "SOV")
        diff = mod.preview()
        assert "SOV" in diff

    def test_preview_event(self, tmp_mod):
        mod = tmp_mod.with_events()
        mod.create_event("test.1", title="Test", options=[EventOption(name="OK")])
        mod.set_event_namespace("test.1", "test")
        diff = mod.preview()
        assert "test.1" in diff

    def test_preview_idea(self, tmp_mod):
        mod = tmp_mod.with_ideas()
        mod.create_idea("test_idea", modifier={"political_power_gain": 0.5})
        diff = mod.preview()
        assert "test_idea" in diff

    def test_preview_returns_empty_when_clean(self, tmp_mod):
        mod = tmp_mod.with_focus_tree("GER_focus.txt")
        assert mod.preview() == ""

    def test_only_dirty_loc_files_in_preview(self, tmp_mod):
        loc_dir = tmp_mod.root / "localisation" / "english"
        loc_dir.mkdir(parents=True, exist_ok=True)
        clean_file = loc_dir / "clean_l_english.yml"
        clean_content = 'l_english:\n CLEAN_KEY:0 "Clean Value"\n'
        clean_file.write_text(clean_content, encoding="utf-8-sig")

        dirty_file = loc_dir / "dirty_l_english.yml"
        dirty_file.write_text('l_english:\n OLD:0 "Old"\n', encoding="utf-8-sig")

        mod = tmp_mod.mod
        mod.discard()

        mod.set_loc("NEW_KEY", "New Value")

        diff = mod.preview()
        assert "NEW_KEY" in diff
        assert "CLEAN_KEY" not in diff

    def test_only_dirty_loc_files_saved(self, tmp_mod):
        loc_dir = tmp_mod.root / "localisation" / "english"
        loc_dir.mkdir(parents=True, exist_ok=True)
        clean_file = loc_dir / "clean_l_english.yml"
        clean_content = 'l_english:\n CLEAN_KEY:0 "Clean Value"\n'
        clean_file.write_text(clean_content, encoding="utf-8-sig")

        mod = tmp_mod.mod
        mod.discard()

        mod.set_loc("NEW_KEY", "New Value")
        mod.save()

        assert clean_file.read_text(encoding="utf-8-sig", errors="ignore") == clean_content


class TestCatalogs:
    def test_effect_categories_importable(self):
        from hoi4 import EFFECT_CATEGORIES

        assert isinstance(EFFECT_CATEGORIES, list)
        assert len(EFFECT_CATEGORIES) > 0

    def test_modifier_categories_importable(self):
        from hoi4 import MODIFIER_CATEGORIES

        assert isinstance(MODIFIER_CATEGORIES, list)
        assert len(MODIFIER_CATEGORIES) > 0

    def test_effect_categories_have_name_and_effects(self):
        from hoi4 import EFFECT_CATEGORIES

        for cat_name, effects in EFFECT_CATEGORIES:
            assert isinstance(cat_name, str)
            for effect_name, effect_desc in effects:
                assert isinstance(effect_name, str)

    def test_modifier_categories_have_name_and_modifiers(self):
        from hoi4 import MODIFIER_CATEGORIES

        for cat_name, modifiers in MODIFIER_CATEGORIES:
            assert isinstance(cat_name, str)
            for entry in modifiers:
                assert len(entry) >= 2


class TestConfig:
    def test_find_config_in_cwd(self, tmp_path):
        cfg_path = tmp_path / ".hoi4.json"
        cfg_path.write_text(
            json.dumps(
                {
                    "mod_path": "/tmp/my_mod",
                    "hoi4_install": "/opt/hoi4",
                }
            )
        )
        cfg = find_config(tmp_path)
        assert cfg is not None
        assert cfg.mod_path == Path("/tmp/my_mod")
        assert cfg.hoi4_install == Path("/opt/hoi4")

    def test_find_config_in_parent(self, tmp_path):
        child = tmp_path / "subdir"
        child.mkdir()
        cfg_path = tmp_path / ".hoi4.json"
        cfg_path.write_text(json.dumps({"mod_path": "/tmp/mod"}))
        cfg = find_config(child)
        assert cfg is not None
        assert cfg.mod_path == Path("/tmp/mod")

    def test_find_config_not_found(self, tmp_path):
        assert find_config(tmp_path) is None

    def test_mod_from_config(self, tmp_path):
        mod_dir = tmp_path / "my_mod"
        mod_dir.mkdir()
        cfg_path = tmp_path / ".hoi4.json"
        cfg_path.write_text(json.dumps({"mod_path": str(mod_dir)}))
        mod = Mod.from_config(tmp_path)
        assert mod.mod_root == mod_dir

    def test_mod_from_config_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="No .hoi4.json"):
            Mod.from_config(tmp_path)

    def test_config_round_trip(self, tmp_path):
        cfg = Config(mod_path="/tmp/mod", hoi4_install="/opt/hoi4")
        p = tmp_path / ".hoi4.json"
        cfg.save(p)
        cfg2 = Config.from_dict(json.loads(p.read_text()))
        assert cfg2.mod_path == Path("/tmp/mod")
        assert cfg2.hoi4_install == Path("/opt/hoi4")

    def test_config_optional_fields(self, tmp_path):
        cfg = Config(mod_path="/tmp/mod")
        p = tmp_path / ".hoi4.json"
        cfg.save(p)
        cfg2 = Config.from_dict(json.loads(p.read_text()))
        assert cfg2.mod_path == Path("/tmp/mod")
        assert cfg2.hoi4_install is None


class TestPass1Fixes:
    def test_country_save_writes_localization(self, tmp_mod):
        mod = tmp_mod.mod
        mod.create_country("TST", "Testland")
        mod.save()
        loc_dir = tmp_mod.root / "localisation" / "english"
        assert loc_dir.exists()
        yml_files = list(loc_dir.glob("*.yml"))
        assert len(yml_files) > 0
        content = yml_files[0].read_text(encoding="utf-8-sig", errors="ignore")
        assert "Testland" in content

    def test_focus_not_saved_when_only_countries_dirty(self, tmp_mod):
        mod = tmp_mod.with_focus_tree("GER_focus.txt")
        focus_dir = tmp_mod.root / "common" / "national_focus"
        focus_file = focus_dir / "GER_focus.txt"
        original_mtime = focus_file.stat().st_mtime

        mod.create_country("ZZZ", "Zoolandia")
        mod.save()

        import time

        time.sleep(0.05)
        assert focus_file.stat().st_mtime == original_mtime

    def test_discard_clears_original_files(self, tmp_mod):
        mod = tmp_mod.with_focus_tree("GER_focus.txt")
        mod.add_focus("german_focus", Focus(id="GER_new", x=1, y=1))
        mod.discard()
        assert mod.preview() == ""

    def test_update_country_rejects_unknown_kwargs(self, tmp_mod):
        mod = tmp_mod.with_country("WST")
        with pytest.raises(TypeError, match="Unknown fields"):
            mod.update_country("WST", non_existent_field="value")

    def test_update_country_creates_leader_if_needed(self, tmp_mod):
        mod = tmp_mod.with_country("WST")
        country = mod.get_country("WST")
        country.leader = None
        mod.update_country("WST", leader_name="New Boss")
        assert mod.get_country("WST").leader is not None
        assert mod.get_country("WST").leader.name == "New Boss"

    def test_state_category_preserved(self, tmp_mod):
        mod = tmp_mod.with_states()
        state = mod.get_state(2)
        original_cat = state.state_category
        mod.set_state_owner(2, "GER")
        mod.save()
        mod2 = Mod(tmp_mod.root)
        state2 = mod2.get_state(2)
        assert state2.state_category == original_cat

    def test_save_not_atomic_on_error_preserves_dirty(self, tmp_mod):
        mod = tmp_mod.mod
        mod.create_country("TST", "Testland")
        mod.save()
        assert "countries" not in mod._dirty

    def test_preview_empty_after_save(self, tmp_mod):
        mod = tmp_mod.with_focus_tree("GER_focus.txt")
        mod.add_focus("german_focus", Focus(id="GER_new", x=1, y=1))
        mod.save()
        assert mod.preview() == ""

    def test_update_nonexistent_country_returns_false(self, tmp_mod):
        assert tmp_mod.mod.update_country("NOPE", name="X") is False

    def test_update_nonexistent_event_returns_false(self, tmp_mod):
        assert tmp_mod.mod.update_event("no.event", title="X") is False

    def test_update_nonexistent_idea_returns_false(self, tmp_mod):
        assert tmp_mod.mod.update_idea("no_idea", icon="X") is False

    def test_delete_nonexistent_returns_false(self, tmp_mod):
        mod = tmp_mod.mod
        assert mod.delete_country("NOPE") is False
        assert mod.delete_event("no.event") is False
        assert mod.delete_idea("no_idea") is False
        assert mod.delete_focus_tree("no_tree") is False

    def test_add_event_option_nonexistent_returns_false(self, tmp_mod):
        assert tmp_mod.mod.add_event_option("no.event", EventOption()) is False

    def test_remove_focus_not_found(self, tmp_mod):
        mod = tmp_mod.with_focus_tree("GER_focus.txt")
        assert mod.remove_focus("german_focus", "NONEXISTENT") is False

    def test_get_focus_not_found(self, tmp_mod):
        mod = tmp_mod.with_focus_tree("GER_focus.txt")
        assert mod.get_focus("german_focus", "NONEXISTENT") is None

    def test_delete_loc_nonexistent(self, tmp_mod):
        assert tmp_mod.mod.delete_loc("NO_KEY") is False

    def test_set_event_namespace_nonexistent(self, tmp_mod):
        mod = tmp_mod.mod
        mod.set_event_namespace("no.event", "ns")
        assert "events" not in mod._dirty


class TestPass2Fixes:
    def test_event_trigger_not_in_effect(self, tmp_mod):
        mod = tmp_mod.with_events()
        evt = mod.get_event("mymod.1.1")
        option_b = None
        for opt in evt.options:
            if opt.name and "b" in opt.name:
                option_b = opt
        if option_b:
            assert "has_war" not in option_b.effect or option_b.trigger

    def test_loc_key_with_colon_no_double_zero(self):
        from hoi4.localisation import serialize_localization_file

        result = serialize_localization_file({"KEY:0": "value"})
        assert "KEY:0:0" not in result
        assert "KEY:0" in result

    def test_loc_key_without_colon_gets_zero(self):
        from hoi4.localisation import serialize_localization_file

        result = serialize_localization_file({"KEY": "value"})
        assert "KEY:0" in result

    def test_update_event_rejects_unknown_kwargs(self, tmp_mod):
        mod = tmp_mod.with_events()
        with pytest.raises(TypeError, match="Unknown fields"):
            mod.update_event("mymod.1.1", nonexistent="bad")

    def test_update_idea_rejects_unknown_kwargs(self, tmp_mod):
        mod = tmp_mod.with_ideas()
        mod.create_idea("test", modifier={"x": 1})
        with pytest.raises(TypeError, match="Unknown fields"):
            mod.update_idea("test", nonexistent="bad")

    def test_update_idea_rejects_non_dict_modifier(self, tmp_mod):
        mod = tmp_mod.with_ideas()
        mod.create_idea("test", modifier={"x": 1})
        with pytest.raises(TypeError, match="modifier must be a dict"):
            mod.update_idea("test", modifier="not a dict")

    def test_update_focus_rejects_unknown_kwargs(self, tmp_mod):
        mod = tmp_mod.with_focus_tree("GER_focus.txt")
        with pytest.raises(TypeError, match="Unknown fields"):
            mod.update_focus("german_focus", "GER_anschluss", nonexistent="bad")

    def test_set_state_properties_rejects_unknown_kwargs(self, tmp_mod):
        mod = tmp_mod.with_states()
        with pytest.raises(TypeError, match="Unknown fields"):
            mod.set_state_properties(1, nonexistent="bad")

    def test_mod_on_empty_dir(self, tmp_path):
        mod = Mod(tmp_path)
        assert mod.list_countries() == []
        assert mod.list_states() == []
        assert mod.list_focus_trees() == []
        assert mod.list_events() == []
        assert mod.list_decisions() == []
        assert mod.list_ideas() == []
        assert mod.preview() == ""
        assert mod.validate() == []

    def test_validate_integration(self, tmp_mod):
        mod = tmp_mod.with_focus_tree("GER_focus.txt")
        tmp_mod.with_states()
        tmp_mod.with_events()
        errors = mod.validate()
        assert isinstance(errors, list)

    def test_event_round_trip(self, tmp_mod):
        mod = tmp_mod.with_events()
        evt = mod.get_event("mymod.1.1")
        mod.update_event("mymod.1.1", title=evt.title)
        mod.save()
        mod2 = Mod(tmp_mod.root)
        evt2 = mod2.get_event("mymod.1.1")
        assert evt2.id == evt.id
        assert evt2.title == evt.title
        assert len(evt2.options) == len(evt.options)


class TestAgentFacingApis:
    def test_insert_focus_after_sets_prerequisite_and_relative_position(self, tmp_mod):
        mod = tmp_mod.with_focus_tree("GER_focus.txt")
        mod.insert_focus_after("german_focus", "GER_rhineland", Focus(id="GER_industry", x=0, y=1))
        focus = mod.get_focus("german_focus", "GER_industry")
        assert focus.prerequisites == [["GER_rhineland"]]
        assert focus.relative_position_id == "GER_rhineland"

    def test_append_to_focus_reward(self, tmp_mod):
        mod = tmp_mod.with_focus_tree("GER_focus.txt")
        assert mod.append_to_focus_reward("german_focus", "GER_rhineland", "add_stability = 0.05")
        focus = mod.get_focus("german_focus", "GER_rhineland")
        assert "add_political_power = 50" in focus.completion_reward
        assert "add_stability = 0.05" in focus.completion_reward

    def test_set_focus_loc_sets_name_and_description(self, tmp_mod):
        mod = tmp_mod.mod
        mod.set_focus_loc("TST_focus", "Focus Name", "Focus Description")
        assert mod.get_loc("TST_focus") == "Focus Name"
        assert mod.get_loc("TST_focus_desc") == "Focus Description"

    def test_country_context_collects_state_and_ideas(self, tmp_mod):
        mod = tmp_mod.mod
        mod.create_country("TST", "Testland", capital=1)
        mod.save()
        state_dir = tmp_mod.root / "history" / "states"
        state_dir.mkdir(parents=True, exist_ok=True)
        state_dir.joinpath("1-Testland.txt").write_text(
            "state = { id = 1 name = STATE_1 manpower = 1000 state_category = town "
            "provinces = { 1 } history = { owner = TST add_core_of = TST } }",
            encoding="utf-8",
        )
        ideas_dir = tmp_mod.root / "common" / "ideas"
        ideas_dir.mkdir(parents=True, exist_ok=True)
        ideas_dir.joinpath("testland.txt").write_text(
            "ideas = { industrial_concern = { TST_steel = { modifier = { local_resources_factor = 0.1 } } } }",
            encoding="utf-8",
        )
        mod.discard()
        context = mod.get_country_context("TST")
        assert context["capital"] == 1
        assert context["states"][0]["owner"] == "TST"
        assert context["ideas"][0]["id"] == "TST_steel"

    def test_create_industrial_branch_adds_grounded_focuses_and_loc(self, tmp_mod):
        mod = tmp_mod.mod
        mod.create_focus_tree("lux_focus", "LUX")
        focuses = mod.create_industrial_branch("lux_focus", "LUX")
        assert len(focuses) == 4
        assert all(mod.get_loc(focus.id) for focus in focuses)
        assert all(mod.get_loc(f"{focus.id}_desc") for focus in focuses)
        assert "FOCUS_FILTER_INDUSTRY" in focuses[0].search_filters
        assert "add_tech_bonus" in focuses[-1].completion_reward

    def test_war_effect_helpers(self):
        assert (
            Mod.effect_create_wargoal("fra")
            == "create_wargoal = { type = annex_everything target = FRA }"
        )
        assert (
            Mod.effect_declare_war("ger")
            == "declare_war_on = { type = annex_everything target = GER }"
        )
        assert (
            Mod.effect_declare_war_from("scl", "ita")
            == "SCL = { declare_war_on = { type = annex_everything target = ITA } }"
        )
        assert "start_civil_war" in Mod.effect_start_civil_war("fascism", size=0.4, capital=8)
        assert Mod.effect_load_focus_tree("FB_AUS_focus") == (
            "load_focus_tree = { tree = FB_AUS_focus keep_completed = no }\n"
            "mark_focus_tree_layout_dirty = yes"
        )
        civil_war_tree = Mod.effect_spawn_civil_war_with_focus_tree(
            "communism",
            "FB_AUS_focus",
            size=0.4,
            capital=4,
        )
        assert "start_civil_war = {" in civil_war_tree
        assert "ideology = communism" in civil_war_tree
        assert "size = 0.4" in civil_war_tree
        assert "capital = 4" in civil_war_tree
        assert (
            "load_focus_tree = { tree = FB_AUS_focus keep_completed = no }"
            in civil_war_tree
        )
        assert "mark_focus_tree_layout_dirty = yes" in civil_war_tree
        assert "D01 = {" not in civil_war_tree
        assert Mod.effect_add_state_core(115, "sic") == "115 = { add_core_of = SIC }"
        assert Mod.effect_remove_state_core(115, "sic") == "115 = { remove_core_of = SIC }"
        assert Mod.effect_transfer_state(115, "scl") == "SCL = { transfer_state = 115 }"
        assert Mod.effect_transfer_state_with_core(115, "scl") == (
            "SCL = { transfer_state = 115 }\n115 = { add_core_of = SCL }"
        )
        assert "type = bunker" in Mod.effect_add_bunker(115, level=3)
        assert scope_block(
            "SCL", effect_block("declare_war_on", {"type": "annex_everything", "target": "ITA"})
        ) == ("SCL = { declare_war_on = { type = annex_everything target = ITA } }")

    def test_equipment_and_technology_effect_helpers(self):
        assert Mod.effect_add_political_power(100) == "add_political_power = 100"
        assert Mod.effect_add_war_support(0.1) == "add_war_support = 0.1"
        assert Mod.effect_add_stability(0.05) == "add_stability = 0.05"
        assert Mod.effect_add_manpower(15000) == "add_manpower = 15000"
        assert Mod.effect_schedule_country_event("sic.1", days=58, target="scl") == (
            "SCL = { country_event = { id = sic.1 days = 58 } }"
        )
        assert 'division_template = { name = "Militia"' in Mod.effect_division_template(
            "Militia",
            "infantry = { x = 0 y = 0 }",
        )
        assert (
            Mod.effect_create_unit("Militia", owner="scl")
            == 'create_unit = { division = "Militia" owner = SCL }'
        )
        equipment = Mod.effect_add_equipment("infantry_equipment_0", 1000, producer="ger")
        assert (
            equipment
            == "add_equipment_to_stockpile = { type = infantry_equipment_0 amount = 1000 producer = GER }"
        )
        variant_equipment = Mod.effect_add_equipment(
            "light_tank_chassis_2", 100, "GER", "Panzer II Ausf. a"
        )
        assert 'variant_name = "Panzer II Ausf. a"' in variant_equipment
        assert (
            Mod.effect_set_technology("infantry_weapons", 1)
            == "set_technology = { infantry_weapons = 1 }"
        )
        assert Mod.effect_set_technology("infantry_weapons", 1, popup=False) == (
            "set_technology = { infantry_weapons = 1 popup = no }"
        )
        assert Mod.effect_set_technologies({"infantry_weapons": 1, "tech_support": 1}) == (
            "set_technology = { infantry_weapons = 1 tech_support = 1 }"
        )
        assert Mod.effect_add_army_experience(25) == "army_experience = 25"
        assert Mod.effect_add_navy_experience(25) == "navy_experience = 25"
        assert Mod.effect_add_air_experience(25) == "air_experience = 25"
        assert Mod.effect_set_politics("democratic", elections_allowed=True) == (
            "set_politics = { ruling_party = democratic elections_allowed = yes }"
        )
        with pytest.raises(ValueError, match="does not support elections_frequency"):
            Mod.effect_set_politics("democratic", elections_frequency=48)
        assert (
            Mod.effect_create_faction("Mediterranean League")
            == 'create_faction = "Mediterranean League"'
        )
        assert Mod.effect_add_to_faction("ita") == "add_to_faction = ITA"
        assert Mod.effect_add_target_to_faction("aus", "bay") == "AUS = { add_to_faction = BAY }"
        assert Mod.effect_join_faction("bay", "aus") == "AUS = { add_to_faction = BAY }"
        assert Mod.effect_release("slv") == "release = SLV"
        assert Mod.effect_release_puppet("slv") == "release_puppet = SLV"
        assert Mod.effect_end_puppet("slv", "yug") == "YUG = { end_puppet = SLV }"
        assert Mod.effect_set_autonomy("slv", "autonomy_free", freedom_level=1.0) == (
            "set_autonomy = { target = SLV autonomy_state = autonomy_free freedom_level = 1.0 }"
        )
        assert Mod.effect_convert_puppet_to_ally("slv", "yug", faction_leader="aus") == (
            "YUG = { end_puppet = SLV }\nAUS = { add_to_faction = SLV }"
        )
        assert Mod.effect_white_peace("fra") == "white_peace = FRA"
        assert (
            Mod.effect_set_rule("can_create_factions", True)
            == "set_rule = { can_create_factions = yes }"
        )
        assert Mod.effect_swap_idea("old_spirit", "new_spirit", target="ita") == (
            "ITA = { swap_ideas = { remove_idea = old_spirit add_idea = new_spirit } }"
        )
        chain = Mod.effect_upgrade_idea_chain(["spirit_1", "spirit_2", "spirit_3"], target="ita")
        assert "ITA = {" in chain
        assert "has_idea = spirit_2" in chain
        assert "remove_idea = spirit_2 add_idea = spirit_3" in chain
        revolution = Mod.effect_spawn_revolution(
            "bay",
            [52],
            overlord="aus",
            manpower=15000,
            equipment={"infantry_equipment_0": 500},
            technologies={"infantry_weapons": 1},
            division_template=Mod.effect_division_template("Militia", "infantry = { x = 0 y = 0 }"),
            units=["Militia"],
            faction_leader="ger",
        )
        assert "BAY = { transfer_state = 52 }" in revolution
        assert "52 = { add_core_of = BAY }" in revolution
        assert "GER = { add_to_faction = BAY }" in revolution
        assert "BAY = { declare_war_on = { type = annex_everything target = AUS } }" in revolution
        revolt = Mod.effect_convert_existing_or_spawn_revolt(
            "slv",
            [102],
            overlord="yug",
            manpower=5000,
            equipment={"infantry_equipment_0": 100},
        )
        assert "if = { limit = { SLV = { exists = yes } }" in revolt
        assert "limit = { exists = SLV }" not in revolt
        assert "YUG = { end_puppet = SLV }" in revolt
        assert "SLV = { transfer_state = 102 }" in revolt
        assert "else = {" in revolt

    def test_revolution_helper_warns_without_playable_baseline(self):
        with pytest.warns(RuntimeWarning, match="unplayable shell"):
            assert "BAY = { transfer_state = 52 }" in Mod.effect_spawn_revolution("bay", [52])

    def test_decision_chain_and_recovery_helpers(self, tmp_mod):
        mod = tmp_mod.mod
        decisions = mod.create_decision_chain(
            "SLO_revolt_decisions",
            [
                {
                    "id": "SLO_organize_cells",
                    "complete_effect": "add_political_power = 25",
                    "event": "slo.1",
                    "loc_name": "Organize Cells",
                    "loc_desc": "Prepare the network.",
                },
                {
                    "id": "SLO_launch_revolt",
                    "complete_effect": Mod.effect_convert_existing_or_spawn_revolt(
                        "slv", [102], overlord="yug", manpower=1
                    ),
                },
            ],
            final_event="slo.99",
        )
        assert len(decisions) == 2
        assert "set_country_flag = SLO_organize_cells_done" in decisions[0].complete_effect
        assert "country_event = { id = slo.1 }" in decisions[0].complete_effect
        assert "has_country_flag = SLO_organize_cells_done" in decisions[1].available
        assert "country_event = { id = slo.99 }" in decisions[1].complete_effect
        assert mod.get_loc("SLO_organize_cells") == "Organize Cells"

        repair = mod.create_recovery_decision(
            "SLO_revolt_decisions",
            "SLO_repair_live_save",
            effect=Mod.effect_load_focus_tree("SLO_focus"),
            hidden=True,
            loc_name="Repair Slovenia Revolt",
        )
        assert repair.visible == "always = no"
        assert "load_focus_tree" in repair.complete_effect
        assert mod.get_loc("SLO_repair_live_save") == "Repair Slovenia Revolt"

    def test_tech_bonus_helper_validates_categories(self):
        assert "infantry_weapons" in TECHNOLOGY_CATEGORIES
        assert "infantry" not in TECHNOLOGY_CATEGORIES
        assert "category = infantry_weapons" in Mod.effect_add_tech_bonus(
            "rifle_bonus", category="infantry_weapons"
        )
        with pytest.raises(ValueError):
            Mod.effect_add_tech_bonus("bad_bonus", category="infantry")

    def test_update_focus_tree(self, tmp_mod):
        mod = tmp_mod.with_focus_tree("GER_focus.txt")
        assert mod.update_focus_tree("german_focus", continuous_focus_position="x = 0 y = 1000")
        assert mod.get_focus_tree("german_focus").continuous_focus_position == "x = 0 y = 1000"

    def test_ensure_and_upsert_helpers_are_idempotent(self, tmp_mod):
        mod = tmp_mod.mod
        tree = mod.ensure_focus_tree("scl_focus", "SCL")
        assert tree.id == "scl_focus"
        assert mod.ensure_focus_tree("scl_focus", "SCL") is tree

        focus = mod.upsert_focus(
            "scl_focus", Focus(id="SCL_start", x=1, y=1, completion_reward="add_stability = 0.05")
        )
        assert focus.id == "SCL_start"
        mod.upsert_focus(
            "scl_focus", Focus(id="SCL_start", x=2, y=3, completion_reward="add_war_support = 0.05")
        )
        assert mod.get_focus("scl_focus", "SCL_start").x == 2
        assert "add_war_support" in mod.get_focus("scl_focus", "SCL_start").completion_reward

        idea = mod.ensure_idea("SCL_spirit", modifier={"political_power_gain": 0.1})
        assert idea.id == "SCL_spirit"
        mod.ensure_idea(
            "SCL_spirit",
            icon="GFX_new",
            modifier={"stability_factor": 0.05},
            merge_modifier=True,
        )
        assert mod.get_idea("SCL_spirit").icon == "GFX_new"
        assert mod.get_idea("SCL_spirit").modifier["political_power_gain"] == 0.1
        assert mod.get_idea("SCL_spirit").modifier["stability_factor"] == 0.05

        event = mod.ensure_event(
            "scl.1", options=[EventOption(name="scl.1.a", effect="add_stability = 0.05")]
        )
        assert event.id == "scl.1"
        mod.ensure_event("scl.1", immediate="add_political_power = 25")
        assert mod.get_event("scl.1").immediate == "add_political_power = 25"

    def test_focus_layout_helpers_place_and_guard_continuous_focus(self, tmp_mod):
        mod = tmp_mod.mod
        mod.create_focus_tree("scl_focus", "SCL")
        branch = mod.auto_layout_branch(
            "scl_focus",
            [
                Focus(id="SCL_a"),
                Focus(id="SCL_b"),
            ],
            x=4,
            y_start=2,
        )
        for focus in branch:
            mod.add_focus("scl_focus", focus)
        assert mod.focus_tree_bounds("scl_focus") == {
            "min_x": 4,
            "max_x": 4,
            "min_y": 2,
            "max_y": 3,
            "width": 1,
            "height": 2,
        }
        assert branch[1].prerequisites == [["SCL_a"]]
        position = mod.place_continuous_focus_below_tree("scl_focus", padding=400)
        assert position == "x = 50 y = 800"
        assert mod.assert_no_visual_overlap("scl_focus") is True
        mod.add_focus("scl_focus", Focus(id="SCL_overlap", x=4, y=3))
        with pytest.raises(ValueError, match="overlaps"):
            mod.assert_no_visual_overlap("scl_focus")
        errors = mod.validate()
        assert any(error.code == "visual_overlap" for error in errors)

    def test_faction_scope_validation_warns_only_for_ambiguous_shapes(self, tmp_mod):
        mod = tmp_mod.mod
        mod.create_country("AUS", "Austria")
        mod.create_country("BAY", "Bavaria")
        bare_errors = mod.validate_effect("add_to_faction = BAY")
        assert any(error.code == "faction_scope_footgun" for error in bare_errors)
        scoped_errors = mod.validate_effect(Mod.effect_add_target_to_faction("AUS", "BAY"))
        assert not any(error.code == "faction_scope_footgun" for error in scoped_errors)
        multiline_scoped_errors = mod.validate_effect("AUS = {\n\tadd_to_faction = BAY\n}")
        assert not any(error.code == "faction_scope_footgun" for error in multiline_scoped_errors)

    def test_strict_localization_and_idea_addability_validation(self, tmp_mod):
        mod = tmp_mod.mod
        mod.create_idea(
            "SCL_advisor", category="political_advisor", modifier={"political_power_gain": 0.1}
        )
        mod.create_event(
            "scl.1", options=[EventOption(name="scl.1.a", effect="add_ideas = SCL_advisor")]
        )
        errors = mod.validate(strict_localization=True)
        codes = {error.code for error in errors}
        assert "idea_not_addable" in codes
        assert "missing_localization" in codes

        mod.set_loc("scl.1.t", "Title")
        mod.set_loc("scl.1.d", "Description")
        mod.set_loc("scl.1.a", "Option")
        mod.set_loc("SCL_advisor", "Advisor")
        errors = mod.validate(strict_localization=True)
        assert not any(
            "scl.1" in error.message and error.code == "missing_localization" for error in errors
        )

    def test_revolt_and_resistance_semantic_validation(self, tmp_mod):
        state_dir = tmp_mod.root / "history" / "states"
        state_dir.mkdir(parents=True, exist_ok=True)
        state_dir.joinpath("102-Slovenia.txt").write_text(
            "state = { id = 102 name = STATE_102 manpower = 1 state_category = town "
            "provinces = { 1 } history = { owner = YUG add_core_of = YUG } }",
            encoding="utf-8",
        )
        mod = Mod(tmp_mod.root)
        mod.create_country("YUG", "Yugoslavia")
        mod.create_focus_tree("FB_AUS_focus", "AUS")

        missing_tree_errors = mod.validate_effect(
            Mod.effect_start_civil_war("communism", capital=102)
        )
        assert any(error.code == "civil_war_focus_tree_missing" for error in missing_tree_errors)

        load_errors = mod.validate_effect(Mod.effect_load_focus_tree("MISSING_focus"))
        assert any(error.code == "unknown_focus_tree_reference" for error in load_errors)
        known_load_errors = mod.validate_effect(Mod.effect_load_focus_tree("FB_AUS_focus"))
        assert not any(error.code == "unknown_focus_tree_reference" for error in known_load_errors)
        civil_war_with_tree_errors = mod.validate_effect(
            Mod.effect_spawn_civil_war_with_focus_tree(
                "communism", "FB_AUS_focus", capital=102
            )
        )
        assert not any(
            error.code in {"civil_war_focus_tree_missing", "unknown_country_scope"}
            for error in civil_war_with_tree_errors
        )

        resistance_errors = mod.validate_effect("102 = { add_resistance = 20 }")
        assert any(error.code == "resistance_on_core_state" for error in resistance_errors)
        no_op_transfer_errors = mod.validate_effect("YUG = { transfer_state = 102 }")
        assert any(error.code == "revolt_state_already_owned" for error in no_op_transfer_errors)

        mod.create_event("slo.1", options=[EventOption(name="slo.1.a")])
        errors = mod.validate()
        assert any(error.code == "event_option_no_effect" for error in errors)

    def test_patch_state_history_preserves_victory_points_text(self, tmp_mod):
        state_dir = tmp_mod.root / "history" / "states"
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file = state_dir / "52 - Test.txt"
        state_file.write_text(
            "state = {\n"
            "\tid = 52\n"
            "\thistory = {\n"
            "\t\towner = AUS\n"
            "\t\tadd_core_of = AUS\n"
            "\t\tvictory_points = { 123 5 }\n"
            "\t\tbuildings = { infrastructure = 2 }\n"
            "\t}\n"
            "}\n",
            encoding="utf-8",
        )
        state = tmp_mod.mod.patch_state_history(
            52, owner="BAY", add_cores=["BAY"], remove_cores=["AUS"]
        )
        assert "owner = BAY" in tmp_mod.mod.preview()
        tmp_mod.mod.save(require_changes=True)
        text = state_file.read_text(encoding="utf-8")
        assert state.owner == "BAY"
        assert state.cores == ["BAY"]
        assert "victory_points = { 123 5 }" in text
        assert "buildings = { infrastructure = 2 }" in text
        assert "owner = BAY" in text
        assert "add_core_of = AUS" not in text

    def test_validation_catches_icons_refs_and_tooltip_patterns(self, tmp_mod):
        mod = tmp_mod.mod
        interface_dir = tmp_mod.root / "interface"
        interface_dir.mkdir(parents=True, exist_ok=True)
        interface_dir.joinpath("goals.gfx").write_text(
            'spriteType = { name = "GFX_custom_navy_goal" texturefile = "gfx/interface/goals/navy.dds" }',
            encoding="utf-8",
        )
        tech_dir = tmp_mod.root / "common" / "technologies"
        tech_dir.mkdir(parents=True, exist_ok=True)
        tech_dir.joinpath("industry.txt").write_text(
            "known_tech = { research_cost = 1 }", encoding="utf-8"
        )
        equip_dir = tmp_mod.root / "common" / "units" / "equipment"
        equip_dir.mkdir(parents=True, exist_ok=True)
        equip_dir.joinpath("infantry.txt").write_text(
            "infantry_equipment_0 = { }", encoding="utf-8"
        )

        mod.create_focus_tree("scl_focus", "SCL")
        mod.create_event("known.1")
        mod.add_focus(
            "scl_focus",
            Focus(
                id="SCL_bad",
                icon="GFX_missing_icon",
                completion_reward="\n".join(
                    [
                        "remove_ideas = old_1",
                        "remove_ideas = old_2",
                        "add_ideas = missing_spirit",
                        "country_event = { id = missing.1 }",
                        "set_technology = { missing_tech = 1 }",
                        Mod.effect_add_equipment("missing_equipment", 10),
                    ]
                ),
            ),
        )
        mod.add_focus(
            "scl_focus", Focus(id="SCL_bad_fallback_icon", icon="GFX_goal_generic_navy", x=1)
        )
        errors = mod.validate(validate_icons=True)
        codes = {error.code for error in errors}
        assert "unknown_focus_icon" in codes
        assert any("GFX_goal_generic_navy" in error.message for error in errors)
        assert "unknown_idea_reference" in codes
        assert "unknown_event_reference" in codes
        assert "unknown_technology_reference" in codes
        assert "unknown_equipment_reference" in codes
        assert "bad_idea_tooltip_pattern" in codes
        assert mod.suggest_focus_icon("navy") == "GFX_custom_navy_goal"

    def test_focus_icon_scan_is_cached_but_discard_refreshes(self, tmp_mod):
        mod = tmp_mod.mod
        interface_dir = tmp_mod.root / "interface"
        interface_dir.mkdir(parents=True, exist_ok=True)
        icon_file = interface_dir / "goals.gfx"
        icon_file.write_text('spriteType = { name = "GFX_cached_icon" }', encoding="utf-8")

        assert "GFX_cached_icon" in mod._known_focus_icons()
        icon_file.write_text('spriteType = { name = "GFX_refreshed_icon" }', encoding="utf-8")
        assert "GFX_cached_icon" in mod._known_focus_icons()
        assert "GFX_refreshed_icon" not in mod._known_focus_icons()

        mod.discard()
        assert "GFX_refreshed_icon" in mod._known_focus_icons()

    def test_validation_warns_on_focus_event_idea_mutation_collision(self, tmp_mod):
        mod = tmp_mod.mod
        mod.create_idea("SCL_crisis", modifier={"stability_factor": -0.1})
        mod.create_focus_tree("scl_focus", "SCL")
        mod.add_focus(
            "scl_focus", Focus(id="SCL_focus", completion_reward="remove_ideas = SCL_crisis")
        )
        mod.create_event(
            "scl.1", options=[EventOption(name="scl.1.a", effect="add_ideas = SCL_crisis")]
        )
        errors = mod.validate()
        assert any(error.code == "idea_mutation_collision" for error in errors)

    def test_preview_summary_reports_semantic_focus_changes(self, tmp_mod):
        mod = tmp_mod.with_focus_tree("GER_focus.txt")
        mod.update_focus_tree("german_focus", continuous_focus_position="x = 50 y = 2600")
        mod.update_focus(
            "german_focus",
            "GER_rhineland",
            x=7,
            y=2,
            completion_reward="\n".join(
                [
                    Mod.effect_load_focus_tree("FB_AUS_focus"),
                    Mod.effect_convert_puppet_to_ally("SLV", "YUG", faction_leader="AUS"),
                    Mod.effect_transfer_state_with_core(102, "SLV"),
                    Mod.effect_declare_war_from("SLV", "YUG"),
                ]
            ),
        )
        summary = mod.preview_summary()
        assert "continuous_focus_position" in summary
        assert "GER_rhineland" in summary
        assert "position" in summary
        assert "load focus tree FB_AUS_focus" in summary
        assert "end puppet SLV under YUG" in summary
        assert "transfer state 102 to SLV" in summary
        assert "SLV joins AUS faction" in summary
        assert "SLV declares war on YUG" in summary

    def test_set_focuses_mutually_exclusive_sets_reciprocal_single_groups(self, tmp_mod):
        mod = tmp_mod.mod
        mod.create_focus_tree("alt_focus", "ALT")
        mod.add_focus("alt_focus", Focus(id="ALT_a"))
        mod.add_focus("alt_focus", Focus(id="ALT_b", x=1))
        assert mod.set_focuses_mutually_exclusive("alt_focus", "ALT_a", "ALT_b")
        assert mod.get_focus("alt_focus", "ALT_a").mutually_exclusive == [["ALT_b"]]
        assert mod.get_focus("alt_focus", "ALT_b").mutually_exclusive == [["ALT_a"]]

    def test_transaction_dry_run_restores_in_memory_state(self, tmp_mod):
        mod = tmp_mod.mod
        with mod.transaction():
            mod.create_focus_tree("dry_focus", "DRY")
            assert "dry_focus" in mod.list_focus_trees()
            assert "common/national_focus/DRY_focus.txt" in mod.preview()
        assert "dry_focus" not in mod.list_focus_trees()
        assert mod.preview() == ""

    def test_get_country_context_can_copy_states(self, tmp_path):
        mod_root = tmp_path / "mod"
        hoi4_root = tmp_path / "hoi4"
        mod_root.mkdir()
        country_tags = mod_root / "common" / "country_tags"
        country_tags.mkdir(parents=True)
        country_tags.joinpath("tags.txt").write_text(
            'TST = "countries/TST.txt"\n', encoding="utf-8"
        )
        countries = mod_root / "common" / "countries"
        countries.mkdir(parents=True)
        countries.joinpath("TST.txt").write_text("color = { 1 2 3 }\n", encoding="utf-8")
        history_countries = mod_root / "history" / "countries"
        history_countries.mkdir(parents=True)
        history_countries.joinpath("TST - Testland.txt").write_text(
            "capital = 1\nset_politics = { ruling_party = democratic }\n", encoding="utf-8"
        )
        vanilla_states = hoi4_root / "history" / "states"
        vanilla_states.mkdir(parents=True)
        vanilla_states.joinpath("1-Testland.txt").write_text(
            "state = { id = 1 name = STATE_1 manpower = 10 state_category = town "
            "provinces = { 1 } history = { owner = TST add_core_of = TST } }",
            encoding="utf-8",
        )
        mod = Mod(mod_root, hoi4_install=hoi4_root)
        context = mod.get_country_context("TST", copy_states=True)
        assert context["states"][0]["id"] == 1
        target = mod_root / "history" / "states" / "1-Testland.txt"
        assert not target.exists()
        assert "history/states/1-Testland.txt" in mod.preview()
        mod.save()
        assert target.exists()


class TestModCountries:
    def test_create_country(self, tmp_mod):
        mod = tmp_mod.mod
        country = mod.create_country(
            "WST", "Westralia", adjective="Westralian", color=(59, 130, 246)
        )
        assert country.tag == "WST"
        assert country.name == "Westralia"
        assert "WST" in mod.list_countries()

    def test_overwrite_country_keeps_custom_definition_target_and_loc_source(
        self, tmp_path
    ):
        tags = tmp_path / "common" / "country_tags"
        definitions = tmp_path / "common" / "countries"
        histories = tmp_path / "history" / "countries"
        localization = tmp_path / "localisation" / "english"
        for directory in (tags, definitions, histories, localization):
            directory.mkdir(parents=True, exist_ok=True)
        tags.joinpath("tags.txt").write_text(
            'ABC = "countries/Custom Definition.txt"\n', encoding="utf-8"
        )
        custom_definition = definitions / "Custom Definition.txt"
        custom_definition.write_text(
            "graphical_culture = western_european_gfx\ncolor = { 1 2 3 }\n",
            encoding="utf-8",
        )
        old_history = histories / "ABC - Oldland.txt"
        old_history.write_text("capital = 1\n", encoding="utf-8")
        shared_loc = localization / "shared_l_english.yml"
        shared_loc.write_text(
            '\ufeffl_english:\n ABC:0 "Oldland"\n ABC_ADJ:0 "Oldlander"\n',
            encoding="utf-8",
        )

        mod = Mod(tmp_path)
        mod.create_country("ABC", "Newland", overwrite=True)
        mod.save(require_changes=True)

        assert not (definitions / "ABC.txt").exists()
        assert "color = { 128 128 128 }" in custom_definition.read_text(encoding="utf-8")
        assert not old_history.exists()
        assert (histories / "ABC - Newland.txt").exists()
        assert 'ABC:0 "Newland"' in shared_loc.read_text(encoding="utf-8-sig")
        generated_loc = localization / "ABC_country_l_english.yml"
        assert 'ABC:0 "Newland"' not in generated_loc.read_text(encoding="utf-8-sig")

    def test_overwrite_loaded_vanilla_country_targets_mod_not_install(self, tmp_path):
        mod_root = tmp_path / "mod"
        hoi4_root = tmp_path / "hoi4"
        mod_root.mkdir()
        tags = hoi4_root / "common" / "country_tags"
        definition = hoi4_root / "common" / "countries" / "Custom ABC.txt"
        history = hoi4_root / "history" / "countries" / "ABC - Oldland.txt"
        tags.mkdir(parents=True)
        definition.parent.mkdir(parents=True)
        history.parent.mkdir(parents=True)
        tags.joinpath("tags.txt").write_text(
            'ABC = "countries/Custom ABC.txt"\n', encoding="utf-8"
        )
        definition.write_text("color = { 1 2 3 }\n", encoding="utf-8")
        definition.parent.joinpath("colors.txt").write_text(
            "ABC = { color = rgb { 1 2 3 } color_ui = rgb { 1 2 3 } }\n",
            encoding="utf-8",
        )
        history.write_text("capital = 1\n", encoding="utf-8")

        mod = Mod(mod_root, hoi4_install=hoi4_root)
        mod.get_country("ABC")
        mod.create_country(
            "ABC",
            "Override Land",
            overwrite=True,
            allow_vanilla_override=True,
        )
        preview = mod.preview()

        assert "common/countries/Custom ABC.txt" in preview
        mod.save(require_changes=True)
        assert (mod_root / "common" / "countries" / "Custom ABC.txt").exists()
        assert definition.read_text(encoding="utf-8") == "color = { 1 2 3 }\n"

    def test_create_country_refuses_unregistered_orphan_files(self, tmp_path):
        definition = tmp_path / "common" / "countries" / "ABC.txt"
        definition.parent.mkdir(parents=True)
        original = "# not registered yet\nfuture_setting = yes\n"
        definition.write_text(original, encoding="utf-8")

        mod = Mod(tmp_path)
        with pytest.raises(FileExistsError, match="not registered"):
            mod.create_country("ABC", "Newland")

        assert definition.read_text(encoding="utf-8") == original
        assert mod.preview() == ""

    def test_create_country_defaults_leader_ideology_to_ruling_party(self, tmp_mod):
        country = tmp_mod.mod.create_country("RED", "Redland", ruling_party="communism")
        assert country.leader.ideology == "marxism"
        assert not any(
            issue.code == "leader_party_mismatch"
            for issue in tmp_mod.mod.validate()
        )
        assert "stalinism" in Mod.leader_ideologies_for_party("communism")

    def test_create_country_persists_research_slots(self, tmp_mod):
        mod = tmp_mod.mod
        mod.create_country("SCI", "Science Land", research_slots=3)
        mod.save()
        history_file = next((tmp_mod.root / "history" / "countries").glob("SCI*.txt"))
        assert "set_research_slots = 3" in history_file.read_text()
        mod2 = Mod(tmp_mod.root)
        assert mod2.get_country("SCI").research_slots == 3

    def test_create_country_rejects_existing_mod_tag(self, tmp_mod):
        mod = tmp_mod.mod
        mod.create_country("WST", "Westralia")
        with pytest.raises(ValueError, match="already exists"):
            mod.create_country("WST", "Other")

    def test_create_country_rejects_vanilla_tag_by_default(self, tmp_path):
        mod_root = tmp_path / "mod"
        hoi4_root = tmp_path / "hoi4"
        (hoi4_root / "common" / "country_tags").mkdir(parents=True)
        (hoi4_root / "common" / "country_tags" / "00_countries.txt").write_text(
            'SIC = "countries/Sichuan.txt"\n',
            encoding="utf-8",
        )
        mod = Mod(mod_root, hoi4_install=hoi4_root)
        assert not mod.is_country_tag_available("SIC")
        assert mod.country_tag_conflicts("SIC") == ["vanilla: SIC (SIC)"]
        with pytest.raises(ValueError, match="vanilla HOI4"):
            mod.create_country("SIC", "Sicily")
        country = mod.create_country("SIC", "Sicily", allow_vanilla_override=True)
        assert country.tag == "SIC"

    def test_country_tag_conflicts_include_vanilla_country_name(self, tmp_path):
        mod_root = tmp_path / "mod"
        hoi4_root = tmp_path / "hoi4"
        (hoi4_root / "common" / "country_tags").mkdir(parents=True)
        (hoi4_root / "common" / "country_tags" / "00_countries.txt").write_text(
            'SAR = "countries/SAR.txt"\n',
            encoding="utf-8",
        )
        (hoi4_root / "common" / "countries").mkdir(parents=True)
        (hoi4_root / "common" / "countries" / "SAR.txt").write_text(
            "color = { 1 2 3 }\n", encoding="utf-8"
        )
        (hoi4_root / "localisation" / "english").mkdir(parents=True)
        (hoi4_root / "localisation" / "english" / "countries_l_english.yml").write_text(
            'l_english:\n SAR:0 "Sarawak"\n',
            encoding="utf-8-sig",
        )
        mod = Mod(mod_root, hoi4_install=hoi4_root)

        assert mod.country_tag_conflicts("SAR") == ["vanilla: SAR (Sarawak)"]

    def test_suggest_tag_avoids_mod_and_vanilla_conflicts(self, tmp_path):
        mod_root = tmp_path / "mod"
        hoi4_root = tmp_path / "hoi4"
        (hoi4_root / "common" / "country_tags").mkdir(parents=True)
        (hoi4_root / "common" / "country_tags" / "00_countries.txt").write_text(
            'SIC = "countries/Sichuan.txt"\nSRD = "countries/Sardinia.txt"\n',
            encoding="utf-8",
        )
        mod = Mod(mod_root, hoi4_install=hoi4_root)
        mod.create_country("SCY", "Taken")
        suggestion = mod.suggest_tag("Sicily")
        assert suggestion not in {"SIC", "SCY", "SRD"}
        assert len(suggestion) == 3

    def test_get_country(self, tmp_mod):
        mod = tmp_mod.with_country("WST")
        country = mod.get_country("WST")
        assert country.name == "Westralia"
        assert country.color == (59, 130, 246)

    def test_update_country(self, tmp_mod):
        mod = tmp_mod.with_country("WST")
        mod.update_country("WST", name="New Westralia", capital=999)
        country = mod.get_country("WST")
        assert country.name == "New Westralia"
        assert country.capital == 999

    def test_update_country_leader(self, tmp_mod):
        mod = tmp_mod.with_country("WST")
        mod.update_country("WST", leader_name="New Boss", leader_ideology="communism")
        country = mod.get_country("WST")
        assert country.leader.name == "New Boss"
        assert country.leader.ideology == "communism"

    def test_delete_country(self, tmp_mod):
        mod = tmp_mod.with_country("WST")
        assert mod.delete_country("WST")
        assert "WST" not in mod.list_countries()

    def test_save_persists_country(self, tmp_mod):
        mod = tmp_mod.mod
        mod.create_country("TST", "Testland", color=(1, 2, 3))
        mod.save()

        mod2 = Mod(tmp_mod.root)
        country = mod2.get_country("TST")
        assert country.name == "Testland"
        assert country.color == (1, 2, 3)

    def test_discard_reverts_country(self, tmp_mod):
        mod = tmp_mod.mod
        mod.create_country("TMP", "Templand")
        mod.discard()
        assert "TMP" not in mod.list_countries()


class TestModStates:
    def test_loads_states(self, tmp_mod):
        mod = tmp_mod.with_states()
        states = mod.list_states()
        assert 1 in states
        assert 2 in states

    def test_get_state(self, tmp_mod):
        mod = tmp_mod.with_states()
        state = mod.get_state(1)
        assert state.owner == "GER"
        assert state.manpower == "3200000"

    def test_vanilla_get_state_and_validation_are_read_only(self, tmp_path):
        mod_root = tmp_path / "mod"
        hoi4_root = tmp_path / "hoi4"
        mod_root.mkdir()
        vanilla_state = hoi4_root / "history" / "states" / "115-Sicily.txt"
        vanilla_state.parent.mkdir(parents=True)
        vanilla_state.write_text(
            "state = { id = 115 name = STATE_115 manpower = 1 state_category = town "
            "provinces = { 1 } history = { owner = ITA add_core_of = ITA } }",
            encoding="utf-8",
        )

        mod = Mod(mod_root, hoi4_install=hoi4_root)
        state = mod.get_state(115)

        assert state.path == vanilla_state
        assert state.source_path == vanilla_state
        assert not (mod_root / "history").exists()
        assert mod.preview() == ""

        mod.validate()
        assert not (mod_root / "history").exists()

    def test_vanilla_state_override_is_deferred_until_save(self, tmp_path):
        mod_root = tmp_path / "mod"
        hoi4_root = tmp_path / "hoi4"
        mod_root.mkdir()
        vanilla_state = hoi4_root / "history" / "states" / "115-Sicily.txt"
        vanilla_state.parent.mkdir(parents=True)
        original = (
            "state = { id = 115 name = STATE_115 manpower = 1 state_category = town "
            "provinces = { 1 } history = { owner = ITA add_core_of = ITA } }"
        )
        vanilla_state.write_text(original, encoding="utf-8")

        mod = Mod(mod_root, hoi4_install=hoi4_root)
        state = mod.set_state_owner(115, "GER")
        target = mod_root / "history" / "states" / "115-Sicily.txt"

        assert state.path == target
        assert state.source_path == vanilla_state
        assert not target.exists()
        assert "history/states/115-Sicily.txt" in mod.preview()

        result = mod.save(require_changes=True)
        assert target in result.written_files
        assert "owner = GER" in target.read_text(encoding="utf-8")
        assert vanilla_state.read_text(encoding="utf-8") == original

    def test_set_state_owner(self, tmp_mod):
        mod = tmp_mod.with_states()
        state = mod.set_state_owner(1, "SOV")
        assert state.owner == "SOV"
        assert "SOV" in state.cores

    def test_set_state_owner_no_core(self, tmp_mod):
        mod = tmp_mod.with_states()
        state = mod.set_state_owner(1, "SOV", add_core=False)
        assert state.owner == "SOV"
        assert "SOV" not in state.cores

    def test_set_state_properties(self, tmp_mod):
        mod = tmp_mod.with_states()
        mod.set_state_properties(1, manpower="9999999")
        state = mod.get_state(1)
        assert state.manpower == "9999999"

    def test_find_state_uses_localization_before_stale_filename(self, tmp_path):
        mod_root = tmp_path / "mod"
        hoi4_root = tmp_path / "hoi4"
        states_dir = hoi4_root / "history" / "states"
        states_dir.mkdir(parents=True)
        states_dir.joinpath("88-Kielce.txt").write_text(
            "state = { id = 88 name = STATE_88 manpower = 1 state_category = town "
            "provinces = { 1 } history = { owner = ITA add_core_of = ITA } }",
            encoding="utf-8",
        )
        states_dir.joinpath("89-Krakow.txt").write_text(
            "state = { id = 89 name = STATE_89 manpower = 1 state_category = town "
            "provinces = { 2 } history = { owner = ITA add_core_of = ITA } }",
            encoding="utf-8",
        )
        localization = hoi4_root / "localisation" / "english" / "states_l_english.yml"
        localization.parent.mkdir(parents=True)
        localization.write_text(
            '\ufeffl_english:\n STATE_88:0 "Kraków"\n STATE_89:0 "Stanisławów"\n',
            encoding="utf-8",
        )
        mod = Mod(mod_root, hoi4_install=hoi4_root)
        matches = mod.find_state("Krakow")
        assert matches[0]["id"] == 88
        assert matches[0]["display_name"] == "Kraków"
        assert matches[0]["file_name"] == "Kielce"
        assert matches[0]["matched"] == "display_name"
        assert matches[0]["source"] == "vanilla"

    def test_set_state_properties_appends_cores(self, tmp_mod):
        mod = tmp_mod.with_states()
        original = list(mod.get_state(1).cores)
        mod.set_state_properties(1, cores=["SOV"])
        state = mod.get_state(1)
        assert all(core in state.cores for core in original)
        assert "SOV" in state.cores

    def test_add_and_remove_state_core(self, tmp_mod):
        mod = tmp_mod.with_states()
        mod.add_state_core(1, "SOV")
        assert "SOV" in mod.get_state(1).cores
        mod.remove_state_core(1, "SOV")
        assert "SOV" not in mod.get_state(1).cores

    def test_batch_set_owner(self, tmp_mod):
        mod = tmp_mod.with_states()
        results = mod.batch_set_owner([1, 2], "USA")
        assert len(results) == 2
        assert mod.get_state(1).owner == "USA"
        assert mod.get_state(2).owner == "USA"

    def test_save_persists_states(self, tmp_mod):
        mod = tmp_mod.with_states()
        mod.set_state_owner(1, "SOV")
        mod.save()
        mod2 = Mod(tmp_mod.root)
        assert mod2.get_state(1).owner == "SOV"

    def test_discard_reverts_states(self, tmp_mod):
        mod = tmp_mod.with_states()
        mod.set_state_owner(1, "SOV")
        mod.discard()
        assert mod.get_state(1).owner == "GER"

    def test_get_state_not_found(self, tmp_mod):
        with pytest.raises(KeyError):
            tmp_mod.mod.get_state(999)


class TestModEvents:
    def test_loads_events(self, tmp_mod):
        mod = tmp_mod.with_events()
        events = mod.list_events()
        assert "mymod.1.1" in events
        assert "mymod.1.2" in events

    def test_get_event(self, tmp_mod):
        mod = tmp_mod.with_events()
        event = mod.get_event("mymod.1.1")
        assert event.title == "mymod.1.1.t"
        assert len(event.options) == 2

    def test_create_event(self, tmp_mod):
        mod = tmp_mod.mod
        event = mod.create_event("test.1", title="test.1.t", description="test.1.d")
        assert event.id == "test.1"
        assert "test.1" in mod.list_events()

    def test_create_event_infers_namespace_and_file(self, tmp_mod):
        mod = tmp_mod.mod
        mod.create_event("sic.1", title="sic.1.t", description="sic.1.d")
        mod.save()
        event_file = tmp_mod.root / "events" / "sic_events.txt"
        content = event_file.read_text(encoding="utf-8")
        assert "add_namespace = sic" in content
        assert "id = sic.1" in content

    def test_create_event_normalizes_wrapped_mean_time_to_happen(self, tmp_mod):
        mod = tmp_mod.mod
        event = mod.create_event("sic.1", mean_time_to_happen="{ days = 1 }")
        assert event.mean_time_to_happen == "days = 1"
        mod.save()
        content = (tmp_mod.root / "events" / "sic_events.txt").read_text(encoding="utf-8")
        assert "mean_time_to_happen = {\n\t\tdays = 1\n\t}" in content
        assert "{ days = 1 }" not in content

    def test_create_event_with_options(self, tmp_mod):
        mod = tmp_mod.mod
        opts = [EventOption(name="test.1.a", effect="add_pp = 100")]
        event = mod.create_event("test.1", options=opts)
        assert len(event.options) == 1
        assert event.options[0].effect == "add_pp = 100"

    def test_create_event_requires_explicit_overwrite(self, tmp_mod):
        mod = tmp_mod.mod
        mod.create_event(
            "sic.1",
            title="sic.1.t",
            options=[EventOption(name="sic.1.a", effect="old_effect = yes")],
        )

        with pytest.raises(ValueError, match="overwrite=True"):
            mod.create_event(
                "sic.1",
                title="sic.1.t",
                options=[EventOption(name="sic.1.a", effect="new_effect = yes")],
            )

        event = mod.create_event(
            "sic.1",
            title="sic.1.t",
            options=[EventOption(name="sic.1.a", effect="new_effect = yes")],
            overwrite=True,
        )
        assert event.options[0].effect == "new_effect = yes"

    def test_create_event_overwrite_replaces_loaded_event_on_disk(self, tmp_mod):
        mod = tmp_mod.mod
        mod.create_event(
            "sic.1",
            title="sic.1.t",
            options=[EventOption(name="sic.1.a", effect="old_effect = yes")],
        )
        mod.save()

        mod2 = Mod(tmp_mod.root)
        mod2.create_event(
            "sic.1",
            title="sic.1.t",
            options=[EventOption(name="sic.1.a", effect=Mod.effect_transfer_state(115, "SCL"))],
            overwrite=True,
        )
        mod2.save()

        content = (tmp_mod.root / "events" / "sic_events.txt").read_text(encoding="utf-8")
        assert "old_effect = yes" not in content
        assert "SCL = { transfer_state = 115 }" in content

    def test_create_event_accepts_fire_once_and_immediate(self, tmp_mod):
        event = tmp_mod.mod.create_event(
            "test.1",
            fire_only_once=True,
            immediate="add_political_power = 10",
        )
        assert event.fire_only_once is True
        assert event.immediate == "add_political_power = 10"

    def test_validate_warns_for_unsafe_core_scope_and_bad_tech_category(self, tmp_mod):
        mod = tmp_mod.mod
        mod.create_event(
            "sic.1",
            options=[EventOption(name="sic.1.a", effect="add_core_of = SIC")],
        )
        mod.create_focus_tree("sic_focus", "SIC")
        mod.add_focus(
            "sic_focus",
            Focus(
                id="SIC_bad_bonus",
                completion_reward="add_tech_bonus = { name = bad bonus = 1.0 category = infantry }",
            ),
        )
        messages = [e.message for e in mod.validate()]
        assert any("country scope" in message for message in messages)
        assert any("Unknown add_tech_bonus category 'infantry'" in message for message in messages)

    def test_validate_warnings_can_be_suppressed_by_code(self, tmp_mod):
        mod = tmp_mod.mod
        mod.create_event(
            "sic.1",
            options=[EventOption(name="sic.1.a", effect="add_core_of = SIC")],
        )

        all_errors = mod.validate()
        assert any(error.code == "country_scope_core_effect" for error in all_errors)
        suppressed = mod.validate(suppress_warnings=["country_scope_core_effect"])
        assert not any(error.code == "country_scope_core_effect" for error in suppressed)
        assert "country_scope_core_effect" in VALIDATION_WARNING_CODES

    def test_validate_effect_warns_for_history_set_owner_runtime_effect(self, tmp_mod):
        tmp_mod.mod.create_country("SCL", "Sicily")
        errors = tmp_mod.mod.validate_effect("SCL = { set_owner = 115 }")
        assert any(error.code == "history_set_owner_in_effect" for error in errors)
        suppressed = tmp_mod.mod.validate_effect(
            "SCL = { set_owner = 115 }",
            suppress_warnings=["history_set_owner_in_effect"],
        )
        assert suppressed == []

    def test_validate_effect_warns_for_inverted_country_scoped_core_effect(self, tmp_mod):
        tmp_mod.mod.create_country("SCL", "Sicily")
        errors = tmp_mod.mod.validate_effect("SCL = { add_core_of = 115 }")
        assert any(
            error.code == "country_scope_core_effect"
            and "115 = { add_core_of = SCL }" in error.message
            for error in errors
        )

    def test_validate_warns_for_triggered_only_mtth_and_immediate_war(self, tmp_mod):
        mod = tmp_mod.mod
        mod.create_event(
            "war.1",
            is_triggered_only=True,
            immediate="declare_war_on = { type = annex_everything target = FRA }",
            mean_time_to_happen="days = 1",
        )
        messages = [e.message for e in mod.validate()]
        assert any(
            "is_triggered_only but also has mean_time_to_happen" in message for message in messages
        )
        assert any("declares war in immediate" in message for message in messages)

    def test_validate_effect_catches_braces_and_declare_war_shape(self, tmp_mod):
        mod = tmp_mod.mod
        mod.create_country("SCL", "Sicily")
        mod.create_country("ITA", "Italy")
        errors = mod.validate_effect("SCL = { declare_war_on = { target = ITA } }")
        assert any("missing required type" in error.message for error in errors)
        assert not any("unknown country tag 'SCL'" in error.message for error in errors)
        assert mod.validate_effect(Mod.effect_declare_war_from("SCL", "ITA")) == []
        brace_errors = mod.validate_effect("SCL = { declare_war_on = { target = ITA }")
        assert any("Script syntax issue" in error.message for error in brace_errors)

    def test_on_action_creates_startup_hook(self, tmp_mod):
        mod = tmp_mod.mod
        mod.create_event(
            "sic.1",
            title="sic.1.t",
            options=[EventOption(name="sic.1.a", effect="add_stability = 0.05")],
        )
        action = mod.create_on_action(
            "on_startup",
            effect=Mod.effect_schedule_country_event("sic.1", days=58, target="SCL"),
        )
        assert action.id == "on_startup"
        assert "country_event" in action.effect
        mod.save()

        on_action_file = tmp_mod.root / "common" / "on_actions" / "mod_on_actions.txt"
        content = on_action_file.read_text()
        assert "on_actions = {" in content
        assert "on_startup = {" in content
        assert "country_event = { id = sic.1 days = 58 }" in content

        mod2 = Mod(tmp_mod.root)
        assert "on_startup" in mod2.list_on_actions()
        assert "country_event" in mod2.get_on_action("on_startup").effect

    def test_delete_on_action_rewrites_source_file(self, tmp_mod):
        mod = tmp_mod.mod
        mod.create_on_action("on_startup", effect="add_stability = 0.05")
        mod.save()

        assert mod.delete_on_action("on_startup") is True
        mod.save()

        on_action_file = tmp_mod.root / "common" / "on_actions" / "mod_on_actions.txt"
        content = on_action_file.read_text()
        assert "on_startup" not in content
        assert "on_actions = {" in content

    def test_update_event(self, tmp_mod):
        mod = tmp_mod.with_events()
        mod.update_event("mymod.1.1", is_triggered_only=False)
        event = mod.get_event("mymod.1.1")
        assert event.is_triggered_only is False

    def test_update_event_option_by_index_and_name(self, tmp_mod):
        mod = tmp_mod.mod
        mod.create_event(
            "sic.1",
            options=[
                EventOption(name="sic.1.a", effect="old_a = yes"),
                EventOption(name="sic.1.b", effect="old_b = yes"),
            ],
        )

        assert mod.update_event_option("sic.1", 0, effect=Mod.effect_transfer_state(115, "SCL"))
        assert mod.update_event_option(
            "sic.1", "sic.1.b", trigger="{ has_war = no }", effect="add_stability = 0.05"
        )
        assert mod.update_event_option("sic.1", "missing", effect="noop = yes") is False
        event = mod.get_event("sic.1")
        assert event.options[0].effect == "SCL = { transfer_state = 115 }"
        assert event.options[1].trigger == "has_war = no"
        assert event.options[1].effect == "add_stability = 0.05"

    def test_delete_event(self, tmp_mod):
        mod = tmp_mod.with_events()
        assert mod.delete_event("mymod.1.1")
        assert "mymod.1.1" not in mod.list_events()

    def test_add_event_option(self, tmp_mod):
        mod = tmp_mod.with_events()
        mod.add_event_option(
            "mymod.1.2", EventOption(name="mymod.1.2.b", effect="add_war_support = 0.1")
        )
        event = mod.get_event("mymod.1.2")
        assert len(event.options) == 2

    def test_save_persists_events(self, tmp_mod):
        mod = tmp_mod.mod
        mod.create_event("persist.1", title="persist.1.t", description="persist.1.d")
        mod.save()
        mod2 = Mod(tmp_mod.root)
        assert "persist.1" in mod2.list_events()

    def test_discard_reverts_events(self, tmp_mod):
        mod = tmp_mod.mod
        mod.create_event("temp.1")
        mod.discard()
        assert "temp.1" not in mod.list_events()

    def test_get_event_not_found(self, tmp_mod):
        with pytest.raises(KeyError):
            tmp_mod.mod.get_event("nonexistent")


class TestModIdeas:
    def test_loads_ideas(self, tmp_mod):
        mod = tmp_mod.with_ideas()
        ideas = mod.list_ideas()
        assert "GER_spirit_1" in ideas
        assert "GER_spirit_2" in ideas

    def test_get_idea(self, tmp_mod):
        mod = tmp_mod.with_ideas()
        idea = mod.get_idea("GER_spirit_1")
        assert idea.icon == "generic_army"
        assert idea.modifier["army_morale_factor"] == 0.1

    def test_create_idea(self, tmp_mod):
        mod = tmp_mod.mod
        idea = mod.create_idea(
            "TST_spirit",
            icon="GFX_idea_generic_political_support",
            modifier={"army_morale_factor": 0.2},
        )
        assert idea.id == "TST_spirit"
        assert idea.icon == "generic_political_support"
        assert idea.category == "country"
        assert idea.path == tmp_mod.root / "common" / "ideas" / "TST_ideas.txt"
        assert "TST_spirit" in mod.list_ideas()

        mod.save()
        source = idea.path.read_text(encoding="utf-8")
        assert "picture = generic_political_support" in source
        assert "picture = GFX_idea_" not in source

    def test_create_idea_description_and_removal_cost_round_trip(self, tmp_mod):
        mod = tmp_mod.mod
        mod.create_idea(
            "TST_spirit",
            modifier={"stability_factor": 0.1},
            desc="TST_spirit_desc",
            removal_cost=-1,
        )
        mod.save()

        loaded = Mod(tmp_mod.root).get_idea("TST_spirit")
        assert loaded.desc == ""
        assert loaded.removal_cost == -1
        source = loaded.path.read_text(encoding="utf-8")
        assert "picture = generic_political_support" in source
        assert "icon =" not in source
        assert "desc =" not in source

    def test_create_idea_requires_explicit_overwrite(self, tmp_mod):
        mod = tmp_mod.mod
        mod.create_idea("TST_spirit", icon="GFX_idea_old")
        with pytest.raises(ValueError, match="overwrite=True"):
            mod.create_idea("TST_spirit", icon="GFX_idea_new")
        idea = mod.create_idea("TST_spirit", icon="GFX_idea_new", overwrite=True)
        assert idea.icon == "new"

    def test_update_idea(self, tmp_mod):
        mod = tmp_mod.with_ideas()
        mod.update_idea("GER_spirit_1", icon="GFX_idea_new_icon")
        idea = mod.get_idea("GER_spirit_1")
        assert idea.icon == "new_icon"

    def test_update_idea_modifier_merge_is_explicit(self, tmp_mod):
        mod = tmp_mod.with_ideas()
        mod.update_idea("GER_spirit_1", modifier={"new_modifier": 0.5}, merge_modifier=True)
        idea = mod.get_idea("GER_spirit_1")
        assert idea.modifier["new_modifier"] == 0.5
        assert "army_morale_factor" in idea.modifier

    def test_update_idea_modifier_replaces_by_default_and_can_clear(self, tmp_mod):
        mod = tmp_mod.with_ideas()
        mod.update_idea("GER_spirit_1", modifier={"new_modifier": 0.5})
        assert mod.get_idea("GER_spirit_1").modifier == {"new_modifier": 0.5}

        mod.update_idea("GER_spirit_1", modifier={})
        assert mod.get_idea("GER_spirit_1").modifier == {}
        mod.save()
        assert Mod(tmp_mod.root).get_idea("GER_spirit_1").modifier == {}

    def test_delete_idea(self, tmp_mod):
        mod = tmp_mod.with_ideas()
        assert mod.delete_idea("GER_spirit_1")
        assert "GER_spirit_1" not in mod.list_ideas()

    def test_save_persists_ideas(self, tmp_mod):
        mod = tmp_mod.mod
        mod.create_idea("PERSIST_1", modifier={"test": 1})
        mod.save()
        mod2 = Mod(tmp_mod.root)
        assert "PERSIST_1" in mod2.list_ideas()

    def test_discard_reverts_ideas(self, tmp_mod):
        mod = tmp_mod.mod
        mod.create_idea("TEMP_1")
        mod.discard()
        assert "TEMP_1" not in mod.list_ideas()

    def test_get_idea_not_found(self, tmp_mod):
        with pytest.raises(KeyError):
            tmp_mod.mod.get_idea("nonexistent")

    def test_create_idea_with_path(self, tmp_mod):
        mod = tmp_mod.mod
        target = tmp_mod.root / "common" / "ideas" / "lux_test.txt"
        idea = mod.create_idea("LUX_spirit", modifier={"x": 1}, path=target)
        assert idea.path == target
        assert idea.category == "country"
        mod.save()
        assert target.exists()
        content = target.read_text()
        assert "ideas = {" in content
        assert "country = {" in content
        assert "LUX_spirit" in content

    def test_loads_ideas_from_common_ideas_dir(self, tmp_mod):
        ideas_dir = tmp_mod.root / "common" / "ideas"
        ideas_dir.mkdir(parents=True, exist_ok=True)
        ideas_dir.joinpath("test_country.txt").write_text(
            "ideas = {\n\tTEST_spirit = {\n\t\ticon = GFX_test\n\t\tmodifier = { x = 1 }\n\t}\n}\n"
        )
        tmp_mod.mod.discard()
        assert "TEST_spirit" in tmp_mod.mod.list_ideas()
        idea = tmp_mod.mod.get_idea("TEST_spirit")
        assert idea.modifier["x"] == 1

    def test_create_idea_auto_routes_to_country_file(self, tmp_mod):
        ideas_dir = tmp_mod.root / "common" / "ideas"
        ideas_dir.mkdir(parents=True, exist_ok=True)
        ideas_dir.joinpath("luxembourg.txt").write_text("ideas = {\n}\n")
        tmp_mod.mod.discard()

        tmp_mod.mod.create_country("LUX", "Luxembourg", color=(200, 50, 50))
        idea = tmp_mod.mod.create_idea("LUX_spirit", icon="GFX_test", modifier={"x": 1})
        assert idea.path == ideas_dir / "luxembourg.txt"
        tmp_mod.mod.save()
        content = (ideas_dir / "luxembourg.txt").read_text()
        assert "ideas = {" in content
        assert "LUX_spirit" in content

    def test_create_idea_falls_back_when_no_match(self, tmp_mod):
        tmp_mod.mod.create_country("TST", "Testland")
        idea = tmp_mod.mod.create_idea("generic_spirit", modifier={"x": 1})
        assert idea.path == tmp_mod.root / "common" / "ideas" / "mod_ideas.txt"

    def test_set_idea_path_routes(self, tmp_mod):
        target = tmp_mod.root / "common" / "ideas" / "custom.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("ideas = {\n}\n")
        tmp_mod.mod.discard()

        tmp_mod.mod.set_idea_path("LUX", target)
        idea = tmp_mod.mod.create_idea("LUX_spirit", modifier={"x": 1})
        assert idea.path == target

    def test_load_auto_matches_ideas_file_to_country(self, tmp_mod):
        ideas_dir = tmp_mod.root / "common" / "ideas"
        ideas_dir.mkdir(parents=True, exist_ok=True)
        ideas_dir.joinpath("luxembourg.txt").write_text("ideas = {\n}\n")

        mod = Mod(tmp_mod.root)
        mod.create_country("LUX", "Luxembourg", color=(200, 50, 50))
        mod.create_idea("LUX_spirit_1", modifier={"x": 1})
        mod.save()

        mod2 = Mod(tmp_mod.root)
        idea = mod2.create_idea("LUX_spirit_2", modifier={"x": 2})
        assert idea.path == ideas_dir / "luxembourg.txt"

    def test_create_idea_accepts_direct_category_argument(self, tmp_mod):
        idea = tmp_mod.mod.create_idea(
            "SCL_advisor", category="political_advisor", modifier={"political_power_gain": 0.1}
        )
        assert idea.category == "political_advisor"

    def test_validate_warns_for_unresolved_assigned_idea(self, tmp_mod):
        mod = tmp_mod.mod
        mod.create_country("SCL", "Sicily", ideas=["SCL_spirit"])
        errors = mod.validate()
        assert any(error.code == "unknown_assigned_idea" for error in errors)
        mod.create_idea("SCL_spirit", modifier={"political_power_gain": 0.1})
        assert not any(error.code == "unknown_assigned_idea" for error in mod.validate())


class TestIdeaValidation:
    def test_valid_idea(self):
        idea = Idea(id="t", modifier={"x": 1})
        errors = validate_idea(idea)
        assert len(errors) == 0

    def test_no_modifiers(self):
        idea = Idea(id="t")
        errors = validate_idea(idea)
        assert any("no modifiers" in e.message for e in errors)

    def test_no_id(self):
        idea = Idea(id="")
        errors = validate_idea(idea)
        assert any("no ID" in e.message for e in errors)

    def test_legacy_icon_assignment_warns_even_when_sprite_exists(self, tmp_path):
        path = tmp_path / "common" / "ideas" / "ideas.txt"
        path.parent.mkdir(parents=True)
        path.write_text(
            "ideas = { country = { t = { icon = GFX_idea_valid modifier = { x = 1 } } } }\n",
            encoding="utf-8",
        )
        interface = tmp_path / "interface" / "ideas.gfx"
        interface.parent.mkdir(parents=True)
        interface.write_text(
            'spriteTypes = { spriteType = { name = "GFX_idea_valid" } }\n',
            encoding="utf-8",
        )

        errors = Mod(tmp_path).validate(validate_icons=True)

        issue = next(error for error in errors if error.code == "invalid_idea_icon_key")
        assert issue.severity == "warning"
        assert issue.idea_id == "t"
        assert issue.file_path == str(path)
        assert not any(error.code == "unknown_idea_icon" for error in errors)

    def test_prefixed_picture_is_error_even_when_resolved_sprite_exists(self, tmp_path):
        path = tmp_path / "common" / "ideas" / "ideas.txt"
        path.parent.mkdir(parents=True)
        path.write_text(
            "ideas = { country = { t = { picture = GFX_idea_valid modifier = { x = 1 } } } }\n",
            encoding="utf-8",
        )
        interface = tmp_path / "interface" / "ideas.gfx"
        interface.parent.mkdir(parents=True)
        interface.write_text(
            'spriteTypes = { spriteType = { name = "GFX_idea_valid" } }\n',
            encoding="utf-8",
        )

        errors = Mod(tmp_path).validate(validate_icons=True)

        issue = next(
            error for error in errors if error.code == "invalid_idea_picture_prefix"
        )
        assert issue.severity == "error"
        assert issue.idea_id == "t"
        assert not any(error.code == "unknown_idea_icon" for error in errors)

    def test_bare_picture_resolves_against_configured_vanilla_interface(self, tmp_path):
        mod_root = tmp_path / "mod"
        game_root = tmp_path / "game"
        interface = game_root / "interface" / "ideas.gfx"
        interface.parent.mkdir(parents=True)
        interface.write_text(
            'spriteTypes = { spriteType = { name = "GFX_idea_vanilla_valid" } }\n',
            encoding="utf-8",
        )
        mod = Mod(mod_root, hoi4_install=game_root)
        mod.create_idea("TST_spirit", icon="vanilla_valid", modifier={"x": 1})

        errors = mod.validate(validate_icons=True)

        assert not any(error.code == "unknown_idea_icon" for error in errors)
        assert mod.suggest_idea_icon("GFX_idea_vanila_valid") == "vanilla_valid"


class TestEventValidation:
    def test_valid_event(self):
        event = Event(
            id="t.1",
            title="T",
            description="D",
            options=[EventOption(name="t.1.a", effect="add_political_power = 1")],
        )
        errors = validate_event(event)
        assert len(errors) == 0

    def test_no_title(self):
        event = Event(id="t.1", options=[EventOption(name="a")])
        errors = validate_event(event)
        assert any("title" in e.message for e in errors)

    def test_no_description(self):
        event = Event(id="t.1", options=[EventOption(name="a")])
        errors = validate_event(event)
        assert any("description" in e.message for e in errors)

    def test_no_options(self):
        event = Event(id="t.1", title="T", description="D")
        errors = validate_event(event)
        assert any("no options" in e.message for e in errors)

    def test_invalid_type(self):
        event = Event(id="t.1", event_type="bad_type", options=[EventOption(name="a")])
        errors = validate_event(event)
        assert any("invalid type" in e.message for e in errors)

    def test_leader_event_types_are_valid(self):
        for event_type in ("unit_leader_event", "operative_leader_event"):
            event = Event(
                id="t.1",
                event_type=event_type,
                title="T",
                description="D",
                options=[EventOption(name="a")],
            )
            assert not any(
                "invalid type" in error.message for error in validate_event(event)
            )


class TestStateValidation:
    def test_valid_state(self):
        state = State(id=1, owner="GER")
        errors = validate_state(state, known_tags={"GER"})
        assert len(errors) == 0

    def test_no_owner(self):
        state = State(id=1)
        errors = validate_state(state, known_tags={"GER"})
        assert any("no owner" in e.message for e in errors)

    def test_unknown_owner(self):
        state = State(id=1, owner="XXX")
        errors = validate_state(state, known_tags={"GER"})
        assert any("not a known country" in e.message for e in errors)

    def test_unknown_core(self):
        state = State(id=1, owner="GER", cores=["GER", "XXX"])
        errors = validate_state(state, known_tags={"GER"})
        assert any("core" in e.message and "XXX" in e.message for e in errors)


class TestCountryValidation:
    def test_valid_country(self):
        country = Country(tag="TST", name="Testland")
        errors = validate_country(country)
        assert len(errors) == 0

    def test_invalid_tag(self):
        country = Country(tag="toolong", name="Test")
        errors = validate_country(country)
        assert any("Invalid country tag" in e.message for e in errors)

    def test_missing_name(self):
        country = Country(tag="TST")
        errors = validate_country(country)
        assert any("no name" in e.message for e in errors)

    def test_popularities_not_100(self):
        country = Country(tag="TST", name="T", popularities={"democratic": 50, "fascism": 20})
        errors = validate_country(country)
        assert any("popularities" in e.message for e in errors)

    def test_invalid_ruling_party(self):
        country = Country(tag="TST", name="T", ruling_party="anarchism")
        errors = validate_country(country)
        assert any("ruling party" in e.message for e in errors)

    def test_leader_party_mismatch_warns(self):
        country = Country(
            tag="TST",
            name="T",
            ruling_party="democratic",
            leader=Leader(name="Boss", ideology="nazism"),
        )
        errors = validate_country(country)
        assert any("does not match" in e.message for e in errors)


class ModHelper:
    def __init__(self, root: Path, mod: Mod):
        self.root = root
        self.mod = mod

    def with_focus_tree(self, filename: str) -> Mod:
        src = FIXTURES / filename
        dst = self.root / "common" / "national_focus" / filename
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(src.read_text())
        self.mod.discard()
        return self.mod

    def with_country(self, tag: str) -> Mod:
        tag = tag.upper()
        for subdir, filename in [
            ("common/country_tags", "countries.txt"),
            ("common/countries", f"{tag}.txt"),
            ("history/countries", None),
            ("common/characters", None),
            ("localisation/english", None),
        ]:
            dst_dir = self.root / subdir
            dst_dir.mkdir(parents=True, exist_ok=True)
            src_dir = FIXTURES / subdir
            if filename and (src_dir / filename).exists():
                src = src_dir / filename
                dst = dst_dir / filename
                if not dst.exists():
                    dst.write_text(src.read_text())
            else:
                for src in src_dir.glob(f"*{tag}*"):
                    dst = dst_dir / src.name
                    if not dst.exists():
                        dst.write_text(src.read_text())
        self.mod.discard()
        return self.mod

    def with_states(self) -> Mod:
        dst_dir = self.root / "history" / "states"
        src_dir = FIXTURES / "history" / "states"
        dst_dir.mkdir(parents=True, exist_ok=True)
        for src in src_dir.glob("*.txt"):
            dst = dst_dir / src.name
            if not dst.exists():
                dst.write_text(src.read_text())
        self.mod.discard()
        return self.mod

    def with_events(self) -> Mod:
        dst_dir = self.root / "events"
        src_dir = FIXTURES / "events"
        dst_dir.mkdir(parents=True, exist_ok=True)
        for src in src_dir.glob("*.txt"):
            dst = dst_dir / src.name
            if not dst.exists():
                dst.write_text(src.read_text())
        self.mod.discard()
        return self.mod

    def with_ideas(self) -> Mod:
        dst_dir = self.root / "common" / "national_ideas"
        src_dir = FIXTURES / "common" / "national_ideas"
        dst_dir.mkdir(parents=True, exist_ok=True)
        for src in src_dir.glob("*.txt"):
            dst = dst_dir / src.name
            if not dst.exists():
                dst.write_text(src.read_text())
        self.mod.discard()
        return self.mod


@pytest.fixture
def tmp_mod(tmp_path):
    mod = Mod(tmp_path)
    return ModHelper(tmp_path, mod)


def test_save_rejects_external_changes_until_reload(tmp_path: Path) -> None:
    path = tmp_path / "common/ideas/custom.txt"
    path.parent.mkdir(parents=True)
    source = "ideas = { country = { custom_spirit = { desc = OLD_DESC } } }\n"
    path.write_text(source, encoding="utf-8")
    mod = Mod(tmp_path)
    assert mod.update_idea("custom_spirit", desc="custom_spirit_desc")

    external = source.rstrip() + "\n# legacy writer changed this file\n"
    path.write_text(external, encoding="utf-8")

    with pytest.raises(ExternalModificationError, match="changed after Mod loaded"):
        mod.preview()
    with pytest.raises(ExternalModificationError, match="Call reload"):
        mod.save()
    assert path.read_text(encoding="utf-8") == external

    mod.reload()
    assert mod.update_idea("custom_spirit", desc="custom_spirit_desc")
    mod.save()
    assert path.read_text(encoding="utf-8") == external.replace(
        "{ desc = OLD_DESC }", "{}"
    )
