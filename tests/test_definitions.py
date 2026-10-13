from __future__ import annotations

from pathlib import Path

import pytest

from hoi4 import DEFINITION_KINDS, Mod
from hoi4.definitions import get_definition_kind, remove_definition, upsert_definition
from hoi4.parser import parse_pdx


TECH_SOURCE = (
    "@1918 = 0\n"
    "# header comment\n"
    "technologies = {\n"
    "\tbase_tech = {\n"
    "\t\tresearch_cost = 1 # keep me\n"
    "\t\tunknown_future_key = { a = b }\n"
    "\t}\n"
    "\n"
    "\tother_tech = { research_cost = 2 }\n"
    "}\n"
)


def _write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_unchanged_container_file_round_trips_byte_for_byte() -> None:
    kind = get_definition_kind("technology")
    assert upsert_definition(TECH_SOURCE, kind, "base_tech",
                             "research_cost = 1 # keep me\nunknown_future_key = { a = b }") == TECH_SOURCE
    assert remove_definition(TECH_SOURCE, kind, "missing") == TECH_SOURCE


def test_container_edit_preserves_comments_variables_and_siblings() -> None:
    kind = get_definition_kind("technology")
    edited = upsert_definition(TECH_SOURCE, kind, "other_tech", "research_cost = 3")
    assert edited == TECH_SOURCE.replace("other_tech = { research_cost = 2 }",
                                         "other_tech = { research_cost = 3 }")
    added = upsert_definition(TECH_SOURCE, kind, "new_tech", "research_cost = 4")
    assert added.startswith(TECH_SOURCE[: TECH_SOURCE.index("\tother_tech")])
    assert "\tnew_tech = {\n\t\tresearch_cost = 4\n\t}\n}\n" in added
    parse_pdx(added)
    removed = remove_definition(added, kind, "base_tech")
    assert "base_tech" not in removed and "@1918 = 0" in removed and "# header comment" in removed
    parse_pdx(removed)


def test_crlf_files_keep_their_line_endings() -> None:
    kind = get_definition_kind("opinion_modifier")
    source = "opinion_modifiers = {\r\n\tfirst = {\r\n\t\tvalue = 1\r\n\t}\r\n}\r\n"
    updated = upsert_definition(source, kind, "second", "value = 2\ndecay = 1")
    assert "\n" not in updated.replace("\r\n", "")
    assert [n.key for n in parse_pdx(updated).children[0].children if n.key] == ["first", "second"]


def test_entry_style_domains_use_identifier_fields() -> None:
    kind = get_definition_kind("autonomy_state")
    source = "autonomy_state = {\n\tid = autonomy_a\n\tis_puppet = yes # note\n}\n"
    edited = upsert_definition(source, kind, "autonomy_a", "is_puppet = no # note")
    assert edited == source.replace("is_puppet = yes", "is_puppet = no")
    added = upsert_definition(source, kind, "autonomy_b", "is_puppet = yes")
    assert added.startswith(source) and "id = autonomy_b" in added


def test_facade_creates_typed_definitions_transactionally(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    with mod.transaction():
        mod.create_scripted_effect("ABC_effect", "add_political_power = 5")
        assert "ABC_effect" in mod.preview()
    assert not mod.preview()

    mod.create_scripted_effect("ABC_effect", "add_political_power = 5")
    mod.create_scripted_localisation("GetABCName", [("tag = ABC", "ABC_NAME_A"), ("", "ABC_NAME_B")])
    mod.create_country_leader_trait("abc_trait", "political_power_factor = 0.1")
    mod.create_unit_leader_trait("abc_general", "type = land\nattack_skill = 1")
    mod.create_technology("abc_tech", "research_cost = 1")
    mod.create_sub_unit("abc_battalion", "group = infantry\nmax_strength = 10")
    mod.create_ai_strategy("abc_ai", [("antagonize", "XYZ", 100), ("role_ratio", "armor", 10)],
                           enable="tag = ABC")
    mod.create_opinion_modifier("abc_opinion", value=25, decay=1, trade=True)
    mod.create_static_modifier("abc_static", {"stability_factor": 0.05})
    mod.create_definition("wargoal", "abc_wargoal", "expire = 0")
    mod.create_definition("game_rule", "abc_rule", 'name = "RULE_ABC"\ngroup = "RULE_GROUP_GENERAL"')
    result = mod.save(require_changes=True)
    assert len(result.written_files) == 11

    def read(relative: str) -> str:
        return (tmp_path / relative).read_text(encoding="utf-8")

    assert "ABC_effect = {" in read("common/scripted_effects/00_sdk_generated.txt")
    loc = read("common/scripted_localisation/00_sdk_generated.txt")
    assert "defined_text = {\n\tname = GetABCName" in loc
    assert loc.index("ABC_NAME_A") < loc.index("ABC_NAME_B")
    assert read("common/country_leader/00_sdk_generated.txt").startswith("leader_traits = {\n\tabc_trait = {")
    assert read("common/unit_leader/00_sdk_generated.txt").startswith("leader_traits = {")
    assert read("common/technologies/00_sdk_generated.txt").startswith("technologies = {")
    assert read("common/units/00_sdk_generated.txt").startswith("sub_units = {")
    ai = read("common/ai_strategy/00_sdk_generated.txt")
    assert "ai_strategy = { type = antagonize id = XYZ value = 100 }" in ai
    opinion = read("common/opinion_modifiers/00_sdk_generated.txt")
    assert "value = 25" in opinion and "trade = yes" in opinion
    assert read("common/wargoals/00_sdk_generated.txt").startswith("wargoal_types = {")
    for written in result.written_files:
        parse_pdx(written.read_text(encoding="utf-8"))

    reloaded = Mod(tmp_path)
    assert reloaded.get_definition("technology", "abc_tech") is not None
    localisation = reloaded.get_definition("scripted_localisation", "GetABCName")
    assert localisation is not None and "name =" not in localisation.body


def test_identifiers_are_unique_and_overwrite_patches_in_place(tmp_path: Path) -> None:
    _write(tmp_path, "common/technologies/infantry.txt", TECH_SOURCE)
    mod = Mod(tmp_path)
    with pytest.raises(ValueError, match="already exists in common/technologies/infantry.txt"):
        mod.create_technology("base_tech", "research_cost = 5")
    with pytest.raises(ValueError, match="update it there"):
        mod.create_technology("base_tech", "research_cost = 5", overwrite=True,
                              path="common/technologies/other.txt")
    mod.update_definition("technology", "base_tech", "research_cost = 5 # keep me\nunknown_future_key = { a = b }")
    mod.save()
    text = (tmp_path / "common/technologies/infantry.txt").read_text(encoding="utf-8")
    assert text == TECH_SOURCE.replace("research_cost = 1 # keep me", "research_cost = 5 # keep me")
    mod.delete_definition("technology", "other_tech")
    mod.save()
    assert "other_tech" not in (tmp_path / "common/technologies/infantry.txt").read_text(encoding="utf-8")
    with pytest.raises(KeyError):
        mod.update_definition("technology", "other_tech", "research_cost = 1")


def test_inherited_definitions_are_copied_into_the_mod_before_editing(tmp_path: Path) -> None:
    game = tmp_path / "game"
    _write(game, "common/opinion_modifiers/00_opinion.txt",
           "opinion_modifiers = {\n\tvanilla_a = { value = 10 }\n\tvanilla_b = { value = 20 }\n}\n")
    root = tmp_path / "mod"
    mod = Mod(root, hoi4_install=game)
    assert {d.id for d in mod.list_definitions("opinion_modifier")} >= {"vanilla_a", "vanilla_b"}
    mod.update_definition("opinion_modifier", "vanilla_a", "value = 15")
    mod.save()
    copied = (root / "common/opinion_modifiers/00_opinion.txt").read_text(encoding="utf-8")
    assert "vanilla_a = { value = 15 }" in copied and "vanilla_b = { value = 20 }" in copied
    assert "value = 10" in (game / "common/opinion_modifiers/00_opinion.txt").read_text(encoding="utf-8")


def test_bodies_cannot_escape_and_paths_must_match_domain(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    with pytest.raises(ValueError):
        mod.create_scripted_effect("bad", "} evil = {")
    with pytest.raises(ValueError):
        mod.create_scripted_effect("bad id", "add_stability = 0.1")
    with pytest.raises(ValueError, match="common/technologies"):
        mod.create_technology("ok", "research_cost = 1", path="common/scripted_effects/x.txt")
    with pytest.raises(ValueError, match="common/units"):
        mod.create_sub_unit("ok", "group = infantry", path="common/units/nested/x.txt")
    with pytest.raises(ValueError, match="identifier"):
        mod.create_definition("autonomy_state", "auto", "id = other")
    with pytest.raises(ValueError, match="Unknown definition kind"):
        mod.create_definition("nope", "x", "a = b")
    assert not mod.preview()


def test_sub_units_ignore_nested_equipment_directory(tmp_path: Path) -> None:
    _write(tmp_path, "common/units/equipment/guns.txt", "equipment = {\n\tgun = { year = 1918 }\n}\n")
    _write(tmp_path, "common/units/infantry.txt", "sub_units = {\n\tinfantry = { group = infantry }\n}\n")
    mod = Mod(tmp_path)
    assert [d.id for d in mod.list_definitions("sub_unit")] == ["infantry"]
    assert [d.id for d in mod.list_definitions("equipment")] == ["gun"]
    with pytest.raises(ValueError, match="common/units"):
        mod.create_sub_unit("x", "group = infantry", path="common/units/equipment/x.txt")


def test_created_scripted_effects_and_technologies_are_known_to_validation(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    mod.create_scripted_effect("ABC_custom_effect", "add_political_power = 1")
    mod.create_technology("abc_new_tech", "research_cost = 1")
    assert "ABC_custom_effect" in mod._effective_custom_script_tokens()["effect"]
    issues = mod.validate()
    assert not [issue for issue in issues if "abc_new_tech" in issue.message]


def test_validation_reports_duplicate_mod_definitions(tmp_path: Path) -> None:
    _write(tmp_path, "common/modifiers/a.txt", "shared_mod = { stability_factor = 0.1 }\n")
    _write(tmp_path, "common/modifiers/b.txt", "shared_mod = { stability_factor = 0.2 }\n")
    issues = Mod(tmp_path).validate()
    assert any(issue.code == "duplicate_definition_id" for issue in issues)


def test_unknown_character_traits_are_reported_with_an_install(tmp_path: Path) -> None:
    game = tmp_path / "game"
    _write(game, "common/country_leader/00_traits.txt", "leader_traits = {\n\tknown_trait = { }\n}\n")
    root = tmp_path / "mod"
    _write(root, "common/characters/ABC.txt",
           "characters = {\n\tABC_leader = {\n\t\tname = ABC_leader\n"
           "\t\tcountry_leader = { ideology = liberalism traits = { known_trait missing_trait } }\n\t}\n}\n")
    issues = Mod(root, hoi4_install=game).validate()
    unknown = [issue for issue in issues if issue.code == "unknown_character_trait"]
    assert len(unknown) == 1 and "missing_trait" in unknown[0].message


def test_import_script_file_accepts_new_domains(tmp_path: Path) -> None:
    source = _write(tmp_path / "src", "bop.txt", "my_bop = { initial_value = 0 }\n")
    mod = Mod(tmp_path / "mod")
    for relative in ("common/bop/x.txt", "common/continuous_focus/x.txt",
                     "common/doctrines/grand_doctrines/x.txt", "common/game_rules/x.txt"):
        mod.import_script_file(source, relative)
    assert len(mod.save().written_files) == 4


def test_every_kind_has_a_valid_default_path() -> None:
    for kind in DEFINITION_KINDS.values():
        assert kind.default_path.startswith("common/") and kind.default_path.endswith(".txt")
        assert not (kind.container and kind.entry_key)
