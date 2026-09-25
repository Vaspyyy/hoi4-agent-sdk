from hoi4 import Mod


def test_pending_effect_is_validated_before_save(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("unsafe = { recruit_character = ABC_person }")
    mod = Mod(tmp_path / "mod")
    mod.import_script_file(source, "common/scripted_effects/source.txt")
    before = {error.code for error in mod.validate(stage="build")}
    assert "runtime_recruit_character" in before
    mod.save(require_changes=True)
    assert {error.code for error in mod.validate(stage="build")} == before


def test_pending_replacement_hides_obsolete_effect(tmp_path):
    root = tmp_path / "mod"
    target = root / "common/scripted_effects/source.txt"
    target.parent.mkdir(parents=True)
    target.write_text("unsafe = { recruit_character = ABC_person }")
    source = tmp_path / "source.txt"
    source.write_text("safe = { add_stability = 0.1 }")
    mod = Mod(root)
    assert any(error.code == "runtime_recruit_character" for error in mod.validate(stage="build"))
    mod.import_script_file(source, "common/scripted_effects/source.txt", overwrite=True)
    assert not any(
        error.code == "runtime_recruit_character" for error in mod.validate(stage="build")
    )


def test_pending_and_inherited_custom_tokens_are_known(tmp_path):
    game = tmp_path / "game"
    docs = game / "documentation"
    docs.mkdir(parents=True)
    (docs / "effects_documentation.md").write_text(
        "## add_stability\n\n* Supported Scopes: COUNTRY\n\n## if\n"
    )
    (docs / "triggers_documentation.md").write_text("## always\n")
    usage = game / "common/scripted_effects/vanilla.txt"
    usage.parent.mkdir(parents=True)
    usage.write_text(
        "vanilla_effect = {\n if = {\n limit = {\n always = yes\n }\n add_stability = 0.1\n }\n }"
    )
    base = tmp_path / "base"
    folder = base / "common/scripted_triggers"
    folder.mkdir(parents=True)
    (folder / "base.txt").write_text("base_check = { always = yes }")
    mod = Mod(tmp_path / "mod", hoi4_install=game, base_mod_paths=[base])
    mod.create_scripted_trigger("pending_check", "base_check = yes")
    source = tmp_path / "source.txt"
    source.write_text(
        "pending_effect = { if = { limit = { pending_check = yes } add_stability = 0.1 } }"
    )
    mod.import_script_file(source, "common/scripted_effects/source.txt")
    assert mod.validate_script_vocabulary() == []
    assert mod.validate_effect("pending_effect = yes") == []
    mod.save(require_changes=True)
    assert mod.validate_script_vocabulary() == []


def test_trigger_rejects_body_escaping_wrapper_without_mutation(tmp_path):
    import pytest

    mod = Mod(tmp_path)
    with pytest.raises(ValueError, match="enclosing block"):
        mod.create_scripted_trigger("first", "always = yes } injected = { always = no")
    assert mod.preview() == ""


def test_inherited_duplicate_ids_are_rejected_atomically(tmp_path):
    import pytest

    base = tmp_path / "base"
    events = base / "events"
    events.mkdir(parents=True)
    (events / "base.txt").write_text(
        "add_namespace = base\ncountry_event = { id = base.1 }\ncountry_event = { id = base.1 }"
    )
    mod = Mod(tmp_path / "mod", base_mod_paths=[base])
    with pytest.raises(ValueError, match="conflicts"):
        mod.load_inherited_content("events/base.txt")
    assert mod.list_events() == []
    assert mod.preview() == ""


def test_imported_catalogs_refresh_warm_caches_and_rollback(tmp_path):
    mod = Mod(tmp_path / "mod")
    technology = tmp_path / "technology.txt"
    technology.write_text(
        "technologies = {\n test_chassis = {\n enable_equipments = { test_hull }\n }\n}"
    )
    equipment = tmp_path / "equipment.txt"
    equipment.write_text("equipments = {\n test_hull = { year = 1936 }\n}")
    assert "test_chassis" not in mod._known_technology_ids()
    assert "test_hull" not in mod._known_equipment_ids()
    assert mod._equipment_unlock_technologies() == {}
    with mod.transaction():
        mod.import_script_file(technology, "common/technologies/test.txt")
        mod.import_script_file(equipment, "common/units/equipment/test.txt")
        assert "test_chassis" in mod._known_technology_ids()
        assert "test_hull" in mod._known_equipment_ids()
        assert mod._equipment_unlock_technologies()["test_hull"] == ("test_chassis",)
    assert "test_chassis" not in mod._known_technology_ids()
    assert "test_hull" not in mod._known_equipment_ids()
    assert mod._equipment_unlock_technologies() == {}
    mod.import_script_file(technology, "common/technologies/test.txt")
    mod.import_script_file(equipment, "common/units/equipment/test.txt")
    before = (
        mod._known_technology_ids(),
        mod._known_equipment_ids(),
        mod._equipment_unlock_technologies(),
    )
    mod.save(require_changes=True)
    assert before == (
        mod._known_technology_ids(),
        mod._known_equipment_ids(),
        mod._equipment_unlock_technologies(),
    )


def test_import_replacement_drops_obsolete_idea_references(tmp_path):
    root = tmp_path / "mod"
    target = root / "common/ideas/source.txt"
    target.parent.mkdir(parents=True)
    target.write_text("ideas = { country = { obsolete = { picture = generic } } }")
    mod = Mod(root)
    assert "obsolete" in mod._known_idea_ids()
    source = tmp_path / "source.txt"
    source.write_text("ideas = { country = { replacement = { picture = generic } } }")
    mod.import_script_file(source, "common/ideas/source.txt", overwrite=True)
    assert "obsolete" not in mod._known_idea_ids()
    assert "replacement" in mod._known_idea_ids()
    mod.save(require_changes=True)
    assert "obsolete" not in mod._known_idea_ids()
