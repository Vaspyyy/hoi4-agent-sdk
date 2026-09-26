import pytest

from hoi4 import Mod
from hoi4.mod import ExternalModificationError


def source(tmp_path, text, name="source.txt"):
    path = tmp_path / name
    path.write_bytes(text.encode("utf-8"))
    return path


def test_import_laws_transaction_atomic_save_and_same_instance_edit(tmp_path):
    src = source(
        tmp_path,
        "# siblings and category metadata stay\nideas = { economy = { law = { picture = generic_political_support modifier = { stability_factor = 0.1 } } sibling = { picture = generic_political_support cost = 12 } } }\n",
    )
    mod = Mod(tmp_path / "mod")
    rel = "common/ideas/_economic.txt"
    with mod.transaction():
        mod.import_script_file(src, rel)
        assert mod.get_idea("law").category == "economy"
        assert "siblings and category metadata" in mod.preview()
    assert mod.list_ideas() == []
    assert not mod.preview()
    target = mod.import_script_file(src, rel)
    assert not target.exists()
    mod.update_idea("law", modifier={"stability_factor": 0.2})
    result = mod.save(require_changes=True)
    assert result.written_files == [target]
    text = target.read_text()
    assert "stability_factor = 0.2" in text and "sibling" in text
    assert "siblings and category metadata" in text
    assert "stability_factor = 0.1" in src.read_text()


def test_import_preserves_bytes_and_refuses_path_or_syntax_errors(tmp_path):
    src = source(tmp_path, "\ufeff# untouched\r\ncheck = { always = yes }\r\n")
    mod = Mod(tmp_path / "mod")
    target = mod.import_script_file(src, "common/scripted_triggers/source.txt")
    mod.save(require_changes=True)
    assert target.read_bytes() == src.read_bytes()
    for relative in ("../escaped.txt", "history/countries/ABC.txt", "common/ideas/file.lua"):
        with pytest.raises(ValueError):
            mod.import_script_file(src, relative)
    invalid = source(tmp_path, "check = {", "invalid.txt")
    with pytest.raises(Exception):
        mod.import_script_file(invalid, "common/scripted_triggers/invalid.txt")
    assert not (mod.mod_root / "common/scripted_triggers/invalid.txt").exists()


def test_import_conflicts_and_external_writes_are_not_silently_overwritten(tmp_path):
    src = source(
        tmp_path, "ideas = { country = { test_idea = { picture = generic_political_support } } }"
    )
    mod = Mod(tmp_path / "mod")
    target = mod.import_script_file(src, "common/ideas/laws.txt")
    with pytest.raises(FileExistsError):
        mod.import_script_file(src, "common/ideas/laws.txt")
    with pytest.raises(ValueError, match="conflicts"):
        mod.import_script_file(src, "common/ideas/other.txt")
    mod.save(require_changes=True)
    mod.import_script_file(src, "common/ideas/laws.txt", overwrite=True)
    target.write_text("external = yes")
    with pytest.raises(ExternalModificationError):
        mod.save()
    assert target.read_text() == "external = yes"


def test_script_import_composes_with_trigger_creation_without_cross_domain_collision(tmp_path):
    src = source(tmp_path, "shared_name = { always = yes }")
    mod = Mod(tmp_path / "mod")
    mod.import_script_file(src, "common/scripted_effects/source.txt")
    mod.create_scripted_trigger("shared_name", "always = no")
    with pytest.raises(ValueError, match="conflict"):
        mod.import_script_file(src, "common/scripted_triggers/duplicate.txt")
    mod.import_script_file(
        source(tmp_path, "other = { always = yes }", "other.txt"),
        "common/scripted_triggers/source.txt",
    )
    mod.create_scripted_trigger(
        "sibling", "always = yes", path="common/scripted_triggers/source.txt"
    )
    mod.save(require_changes=True)
    assert "sibling" in (mod.mod_root / "common/scripted_triggers/source.txt").read_text()


def test_replacing_imported_law_file_removes_obsolete_models(tmp_path):
    src = source(tmp_path, "ideas = { economy = { first = { cost = 1 } sibling = { cost = 2 } } }")
    mod = Mod(tmp_path / "mod")
    mod.import_script_file(src, "common/ideas/laws.txt")
    replacement = source(tmp_path, "ideas = { economy = { second = { cost = 3 } } }", "second.txt")
    mod.import_script_file(replacement, "common/ideas/laws.txt", overwrite=True)
    assert mod.list_ideas() == ["second"]
    mod.save(require_changes=True)
    assert mod.list_ideas() == ["second"]


@pytest.mark.parametrize(
    "relative",
    [
        "common/ai_navy/goals/generic.txt",
        "common/technologies/mechanical.txt",
        "common/units/equipment/hull.txt",
        "common/doctrines/folders/folders.txt",
        "common/special_projects/projects/project.txt",
        "common/factions/rules/groups/groups.txt",
        "common/peace_conference/ai_peace/generic.txt",
        "common/military_industrial_organization/organizations/generic.txt",
        "common/ai_templates/generic.txt",
        "common/ai_equipment/generic.txt",
        "common/unit_medals/generic.txt",
        "common/collections/generic.txt",
        "common/resistance_compliance_modifiers/generic.txt",
        "common/raids/generic.txt",
        "common/raids/categories/generic.txt",
        "common/operations/generic.txt",
        "common/operation_phases/generic.txt",
        "common/intelligence_agencies/generic.txt",
        "common/intelligence_agency_upgrades/generic.txt",
    ],
)
def test_source_derived_mechanical_import_stays_in_output(tmp_path, relative):
    src = source(tmp_path, "generic = { value = 1 }\n")
    mod = Mod(tmp_path / "mod")
    target = mod.import_script_file(src, relative)
    assert not target.exists()
    result = mod.save(require_changes=True)
    assert result.written_files == [target]
    assert src.read_bytes() == target.read_bytes()
    with pytest.raises(ValueError):
        mod.import_script_file(src, "history/units/unlock.txt")


def test_complete_conversion_colors_use_effective_countries_only(tmp_path):
    root = tmp_path / "mod"
    root.mkdir()
    mod = Mod(root)
    mod.create_country("ABC", "Example", color=(80, 20, 30))
    with pytest.raises(ValueError, match="country-tag replacement"):
        mod.rebuild_country_color_table()
    (root / "descriptor.mod").write_text('replace_path="common/country_tags"\n')
    mod.create_country("DEF", "Second", color=(20, 80, 30))
    with mod.transaction():
        mod.rebuild_country_color_table()
    assert "colors.txt" not in mod.preview()
    target = mod.rebuild_country_color_table()
    result = mod.save(require_changes=True)
    assert target in result.written_files
    from hoi4.countries import country_color_tags

    assert country_color_tags(target.read_text()) == {"ABC", "DEF"}
    assert "80 20 30" in target.read_text()


def test_import_overwrite_preview_marks_missing_newlines_without_changing_saved_bytes(tmp_path):
    from hoi4.release_gate import measure_unified_diff

    original = "check = { always = yes }"
    replacement = "check = { always = no }"
    rel = "common/scripted_triggers/source.txt"
    mod = Mod(tmp_path / "mod")
    target = mod.import_script_file(source(tmp_path, original), rel)
    mod.save(require_changes=True)
    src = source(tmp_path, replacement, "replacement.txt")
    mod.import_script_file(src, rel, overwrite=True)

    preview = mod.preview()

    assert preview == (
        f"--- a/{rel}\n+++ b/{rel}\n@@ -1 +1 @@\n"
        f"-{original}\n\\ No newline at end of file\n"
        f"+{replacement}\n\\ No newline at end of file\n"
    )
    stats = measure_unified_diff(preview)
    assert (stats.additions, stats.deletions, stats.changed_lines) == (1, 1, 2)
    assert target.read_bytes() == original.encode("utf-8")
    result = mod.save(require_changes=True)
    assert result.written_files == [target]
    assert target.read_bytes() == src.read_bytes() == replacement.encode("utf-8")
    assert mod.preview() == ""
