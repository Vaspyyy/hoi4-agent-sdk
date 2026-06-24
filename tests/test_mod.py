from pathlib import Path
import json

import pytest

from hoi4 import Mod, Focus, Country, State, Event, EventOption, Idea, Leader
from hoi4 import TECHNOLOGY_CATEGORIES, effect_block, scope_block
from hoi4.config import Config, find_config
from hoi4.validation import validate_focus_tree, validate_country, validate_state, validate_event, validate_idea
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
            'style = {\n\tname = default_style\n\tdefault = yes\n}\n',
            encoding="utf-8",
        )
        mod.discard()
        assert "german_focus" in mod.list_focus_trees()
        assert "00_titlebar_styles" not in mod.list_focus_trees()
        assert len(mod.list_focus_trees()) == 1

    def test_non_focus_file_not_corrupted_on_save(self, tmp_mod):
        mod = tmp_mod.with_focus_tree("GER_focus.txt")
        style_dst = tmp_mod.root / "common" / "national_focus" / "00_titlebar_styles.txt"
        original = 'style = {\n\tname = default_style\n\tdefault = yes\n}\n'
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
        mod.set_state_owner(1, "GER")
        diff = mod.preview()
        assert "GER" in diff

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
        cfg_path.write_text(json.dumps({
            "mod_path": "/tmp/my_mod",
            "hoi4_install": "/opt/hoi4",
        }))
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
        assert Mod.effect_create_wargoal("fra") == "create_wargoal = { type = annex_everything target = FRA }"
        assert Mod.effect_declare_war("ger") == "declare_war_on = { type = annex_everything target = GER }"
        assert Mod.effect_declare_war_from("scl", "ita") == "SCL = { declare_war_on = { type = annex_everything target = ITA } }"
        assert "start_civil_war" in Mod.effect_start_civil_war("fascism", size=0.4, capital=8)
        assert Mod.effect_add_state_core(115, "sic") == "115 = { add_core_of = SIC }"
        assert Mod.effect_remove_state_core(115, "sic") == "115 = { remove_core_of = SIC }"
        assert Mod.effect_transfer_state(115, "scl") == "SCL = { transfer_state = 115 }"
        assert "type = bunker" in Mod.effect_add_bunker(115, level=3)
        assert scope_block("SCL", effect_block("declare_war_on", {"type": "annex_everything", "target": "ITA"})) == (
            "SCL = { declare_war_on = { type = annex_everything target = ITA } }"
        )

    def test_equipment_and_technology_effect_helpers(self):
        equipment = Mod.effect_add_equipment("infantry_equipment_0", 1000, producer="ger")
        assert equipment == "add_equipment_to_stockpile = { type = infantry_equipment_0 amount = 1000 producer = GER }"
        variant_equipment = Mod.effect_add_equipment("light_tank_chassis_2", 100, "GER", "Panzer II Ausf. a")
        assert 'variant_name = "Panzer II Ausf. a"' in variant_equipment
        assert Mod.effect_set_technology("infantry_weapons", 1) == "set_technology = { infantry_weapons = 1 }"
        assert Mod.effect_set_technology("infantry_weapons", 1, popup=False) == (
            "set_technology = { infantry_weapons = 1 popup = no }"
        )
        assert Mod.effect_set_technologies({"infantry_weapons": 1, "tech_support": 1}) == (
            "set_technology = { infantry_weapons = 1 tech_support = 1 }"
        )

    def test_tech_bonus_helper_validates_categories(self):
        assert "infantry_weapons" in TECHNOLOGY_CATEGORIES
        assert "infantry" not in TECHNOLOGY_CATEGORIES
        assert "category = infantry_weapons" in Mod.effect_add_tech_bonus("rifle_bonus", category="infantry_weapons")
        with pytest.raises(ValueError):
            Mod.effect_add_tech_bonus("bad_bonus", category="infantry")

    def test_update_focus_tree(self, tmp_mod):
        mod = tmp_mod.with_focus_tree("GER_focus.txt")
        assert mod.update_focus_tree("german_focus", continuous_focus_position="x = 0 y = 1000")
        assert mod.get_focus_tree("german_focus").continuous_focus_position == "x = 0 y = 1000"

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
        country_tags.joinpath("tags.txt").write_text('TST = "countries/TST.txt"\n', encoding="utf-8")
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
        assert (mod_root / "history" / "states" / "1-Testland.txt").exists()


class TestModCountries:
    def test_create_country(self, tmp_mod):
        mod = tmp_mod.mod
        country = mod.create_country("WST", "Westralia", adjective="Westralian", color=(59, 130, 246))
        assert country.tag == "WST"
        assert country.name == "Westralia"
        assert "WST" in mod.list_countries()

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
        (hoi4_root / "common" / "countries" / "SAR.txt").write_text("color = { 1 2 3 }\n", encoding="utf-8")
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

    def test_find_state_by_vanilla_filename_name(self, tmp_path):
        mod_root = tmp_path / "mod"
        hoi4_root = tmp_path / "hoi4"
        states_dir = hoi4_root / "history" / "states"
        states_dir.mkdir(parents=True)
        states_dir.joinpath("115-Sicily.txt").write_text(
            "state = { id = 115 name = STATE_115 manpower = 1 state_category = town "
            "provinces = { 1 } history = { owner = ITA add_core_of = ITA } }",
            encoding="utf-8",
        )
        mod = Mod(mod_root, hoi4_install=hoi4_root)
        matches = mod.find_state("Sicily")
        assert matches[0]["id"] == 115
        assert matches[0]["display_name"] == "Sicily"
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
        tree = mod.create_focus_tree("sic_focus", "SIC")
        mod.add_focus("sic_focus", Focus(
            id="SIC_bad_bonus",
            completion_reward="add_tech_bonus = { name = bad bonus = 1.0 category = infantry }",
        ))
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

    def test_validate_effect_warns_for_history_set_owner_runtime_effect(self, tmp_mod):
        tmp_mod.mod.create_country("SCL", "Sicily")
        errors = tmp_mod.mod.validate_effect("SCL = { set_owner = 115 }")
        assert any(error.code == "history_set_owner_in_effect" for error in errors)
        suppressed = tmp_mod.mod.validate_effect(
            "SCL = { set_owner = 115 }",
            suppress_warnings=["history_set_owner_in_effect"],
        )
        assert suppressed == []

    def test_validate_warns_for_triggered_only_mtth_and_immediate_war(self, tmp_mod):
        mod = tmp_mod.mod
        mod.create_event(
            "war.1",
            is_triggered_only=True,
            immediate="declare_war_on = { type = annex_everything target = FRA }",
            mean_time_to_happen="days = 1",
        )
        messages = [e.message for e in mod.validate()]
        assert any("is_triggered_only but also has mean_time_to_happen" in message for message in messages)
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
        assert mod.update_event_option("sic.1", "sic.1.b", trigger="{ has_war = no }", effect="add_stability = 0.05")
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
        mod.add_event_option("mymod.1.2", EventOption(name="mymod.1.2.b", effect="add_war_support = 0.1"))
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
        assert idea.icon == "GFX_idea_generic_army"
        assert idea.modifier["army_morale_factor"] == 0.1

    def test_create_idea(self, tmp_mod):
        mod = tmp_mod.mod
        idea = mod.create_idea("TST_spirit", icon="GFX_test", modifier={"army_morale_factor": 0.2})
        assert idea.id == "TST_spirit"
        assert "TST_spirit" in mod.list_ideas()

    def test_update_idea(self, tmp_mod):
        mod = tmp_mod.with_ideas()
        mod.update_idea("GER_spirit_1", icon="GFX_new_icon")
        idea = mod.get_idea("GER_spirit_1")
        assert idea.icon == "GFX_new_icon"

    def test_update_idea_modifier(self, tmp_mod):
        mod = tmp_mod.with_ideas()
        mod.update_idea("GER_spirit_1", modifier={"new_modifier": 0.5})
        idea = mod.get_idea("GER_spirit_1")
        assert idea.modifier["new_modifier"] == 0.5
        assert "army_morale_factor" in idea.modifier

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
        mod.save()
        assert target.exists()
        content = target.read_text()
        assert "ideas = {" in content
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
        assert idea.path is None

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


class TestEventValidation:
    def test_valid_event(self):
        event = Event(id="t.1", title="T", description="D",
                      options=[EventOption(name="t.1.a")])
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
        event = Event(id="t.1", event_type="bad_type",
                      options=[EventOption(name="a")])
        errors = validate_event(event)
        assert any("invalid type" in e.message for e in errors)


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
        country = Country(tag="TST", name="T", ruling_party="democratic",
                          leader=Leader(name="Boss", ideology="nazism"))
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
