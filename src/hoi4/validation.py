"""
Validation rules for HOI4 mod data.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable
from pathlib import Path
from typing import Callable, Sequence

from .decisions import decision_has_cost, decision_has_mission_timeout
from .effects_catalog import TECHNOLOGY_CATEGORIES
from .events import VALID_EVENT_TYPES
from .focus_layout import (
    FocusBranches,
    continuous_focus_position_issue,
    resolve_focus_positions,
)
from .parser import iter_assignment_blocks
from .patching import top_level_assignments
from .politics import IDEOLOGY_PARTY_MAP, RULING_PARTIES
from .syntax import parse as parse_script
from .script import validate_script_syntax
from .syntax import Assignment, parse
from .textio import read_text
from .types import Country, Decision, Event, FocusTree, Idea, State, ValidationError

TAG_RE = re.compile(r"^[A-Z0-9]{3}$")
_UNSET = object()

VALIDATION_CODES: dict[str, str] = {
    "load_failure": "A discovered file could not be loaded by its structured reader.",
    "script_syntax": "Raw Paradox script has mismatched braces or quotes.",
    "localization_bom": "A localization file is missing the UTF-8 byte-order mark HOI4 expects.",
    "localization_encoding": "A localization file is not valid UTF-8.",
    "localization_header": "A localization file has no valid l_<language>: header.",
    "localization_language_mismatch": (
        "A localization file's l_<language>: header does not match its language folder."
    ),
    "missing_translation": (
        "English localization keys are missing from another language the mod ships."
    ),
    "mission_with_cost": "A mission (days_mission_timeout) also declares a cost.",
    "mission_activation_without_timeout": (
        "A decision without days_mission_timeout has an activation trigger, which the "
        "game ignores."
    ),
    "country_scope_core_effect": "Core add/remove effect appears outside an explicit state scope.",
    "history_set_owner_in_effect": "State history owner directive appears in runtime effect script.",
    "unknown_tech_bonus_category": "add_tech_bonus category is not in the technology category catalog.",
    "missing_effect_target": "War goal or war declaration block is missing target = TAG.",
    "missing_wargoal_type": "War goal or war declaration block is missing type = <wargoal_type>.",
    "unknown_country_scope": (
        "Script scopes into a tag that no country tag, tag alias or dynamic tag of the "
        "game, a base mod or the mod declares."
    ),
    "unknown_effect_token": "Effect token is absent from installed-game documentation.",
    "unknown_trigger_token": "Trigger token is absent from installed-game documentation.",
    "unknown_modifier_token": "Modifier token is absent from installed-game documentation.",
    "unsupported_effect_scope": "Effect token is used outside its documented scope.",
    "unsupported_trigger_scope": "Trigger token is used outside its documented scope.",
    "unsupported_modifier_scope": "Modifier token is used outside its documented scope.",
    "unseen_effect_token": "A documented effect has zero installed-game uses.",
    "unseen_trigger_token": "A documented trigger has zero installed-game uses.",
    "unseen_modifier_token": "A documented modifier has zero installed-game uses.",
    "leader_party_mismatch": "No recruited leader of the country leads its ruling party; the game shows a generated one.",
    "unknown_assigned_idea": "Country history assigns an idea ID not loaded by the SDK.",
    "unknown_idea_reference": "Effect script references an idea ID not loaded from the mod or configured HOI4 install.",
    "unknown_event_reference": "Effect script references an event ID not loaded from the mod or configured HOI4 install.",
    "unknown_technology_reference": "Effect script references a technology ID not found in configured data.",
    "unknown_equipment_reference": "Effect script references an equipment ID not found in configured data.",
    "unknown_focus_icon": "Focus icon GFX key was not found in interface files.",
    "unknown_idea_icon": "Idea picture stem did not resolve to a GFX_idea_ sprite.",
    "invalid_idea_icon_key": (
        "Idea definitions must use a bare picture stem, not an icon assignment."
    ),
    "invalid_idea_picture_prefix": (
        "An idea picture repeats the GFX_idea_ prefix. The game accepts it; the bare "
        "stem is the convention."
    ),
    "invalid_idea_desc_key": (
        "Idea descriptions use the fixed <idea_id>_desc localization key, not a desc assignment."
    ),
    "invalid_character_roles_key": (
        "Characters declare roles through role blocks, not a top-level roles assignment."
    ),
    "invalid_decision_category_layout": (
        "Decision-category metadata belongs in common/decisions/categories."
    ),
    "invalid_set_politics_field": (
        "The set_politics effect contains a field unsupported by the installed game grammar."
    ),
    "game_log_error": "HOI4 reported an engine error for a file owned by this mod.",
    "stale_game_log": "The supplied HOI4 error log predates the audited mod files.",
    "country_colors_shadow_vanilla": (
        "A mod colors.txt shadows vanilla country color entries that it does not repeat."
    ),
    "country_colors_parse": (
        "A country colors table changed after loading and could not be parsed during validation."
    ),
    "bad_idea_tooltip_pattern": (
        "An effect removes several ideas and adds one; swap_ideas usually gives a "
        "cleaner tooltip."
    ),
    "idea_mutation_collision": "Focuses and delayed/runtime events mutate the same idea IDs.",
    "unresolved_focus_position": "A focus position has a missing or cyclic local anchor.",
    "visual_overlap": (
        "Two focuses share a position and the game can show both (they are not on "
        "mutually exclusive or allow_branch branches)."
    ),
    "faction_scope_footgun": "Faction effect direction depends on current scope.",
    "civil_war_scope_footgun": "Civil war effects need careful target/capital scope.",
    "civil_war_capital_ref": "A civil-war capital is not a known state ID.",
    "missing_localization": (
        "Shown text (a focus, event, idea, country or leader name) has a localization "
        "key that exists nowhere: not in the mod, a base mod or the game."
    ),
    "missing_optional_localization": (
        "An optional localization key (an idea description, a country's _DEF name) is "
        "missing; the game falls back to other text."
    ),
    "idea_not_addable": (
        "An effect adds an idea whose category is not 'country'. The game accepts "
        "ideas of any category."
    ),
    "civil_war_focus_tree_missing": "Civil war script starts a revolt without loading a focus tree.",
    "unknown_focus_tree_reference": "Effect script references a focus tree ID not loaded by the SDK.",
    "resistance_on_core_state": "Resistance effects target a state that is already a core of its owner.",
    "event_option_no_effect": (
        "An event option only closes the event. That is normal for flavour events."
    ),
    "event_conditional_text_missing": (
        "A conditional event title, description or option name has no text key."
    ),
    "revolt_state_already_owned": "Revolt script transfers a state already owned by the target country.",
    "state_buildings_outside_history": "State buildings must be declared inside the history block.",
    "duplicate_country_tag": "A country tag is declared in more than one mod file.",
    "duplicate_character_id": "A character ID is defined more than once.",
    "duplicate_state_id": "A state ID is defined in more than one state file.",
    "duplicate_focus_tree_id": "A focus tree ID is defined more than once.",
    "duplicate_focus_id": "A focus ID is defined more than once in one tree.",
    "duplicate_event_id": "An event ID is defined more than once.",
    "duplicate_decision_category_id": "A decision category repeats within one file.",
    "duplicate_decision_id": "A decision ID is defined more than once.",
    "duplicate_idea_id": "An idea ID is defined more than once.",
    "duplicate_ideology_id": "An ideology ID is defined more than once.",
    "duplicate_dynamic_modifier_id": "A dynamic modifier ID is defined more than once.",
    "duplicate_bookmark_name": "A bookmark name is defined more than once.",
    "tag_definition": "A country tag points to a missing country definition file.",
    "capital_ref": "A country history capital points to a missing state.",
    "character_ref": "A recruited character is not defined in mod or vanilla data.",
    "missing_country_flag": "An SDK-created country is missing one or more required flag sizes.",
    "missing_country_leader": "A country has no recruited character with a country-leader role.",
    "missing_character_portrait": "A visible character has no portrait reference.",
    "missing_character_portrait_gfx": "A character portrait has no interface sprite declaration.",
    "missing_character_portrait_texture": "A character portrait sprite points to a missing texture.",
    "insufficient_political_advisors": "A complete country needs at least two recruited political advisors.",
    "insufficient_military_commanders": "A complete country needs at least two recruited army commanders.",
    "undefined_recruited_character": "Country history recruits a character that is not defined.",
    "unrecruited_character": "A character created for immediate recruitment is not recruited.",
    "missing_character_localization": "A visible character is missing name localization.",
    "missing_land_oob": "A complete country has no unambiguous land order of battle.",
    "empty_land_oob": "An assigned land OOB has no template or no starting division.",
    "missing_oob_reference": "Country history refers to an OOB file that does not exist.",
    "unloaded_missing_oob_reference": "A country that owns no state at the start refers to an OOB file that does not exist.",
    "mixed_oob_kinds": (
        "One OOB file mixes land, naval, or air content instead of using the "
        "engine's separate assignment effects."
    ),
    "oob_kind_mismatch": "An OOB assignment kind does not match its file content.",
    "oob_template_name": "A division template has no name.",
    "duplicate_oob_template": "An OOB repeats a division-template name.",
    "invalid_oob_battalion": "A division template contains an empty battalion type.",
    "duplicate_oob_grid_position": "A division template repeats a battalion grid position.",
    "invalid_oob_grid_position": "A battalion grid position is outside the supported range.",
    "unknown_oob_template": "A starting division refers to an undefined template.",
    "invalid_oob_location": "A starting division has an invalid province location.",
    "invalid_oob_factor": "An OOB experience or equipment factor is outside 0..1.",
    "unknown_oob_unit_type": "A division template uses an unknown sub-unit type.",
    "unknown_oob_province": "A starting division references an unknown province.",
    "oob_water_location": "A starting land division is placed in a water province.",
    "oob_load_error": "An order of battle referenced by a country could not be read.",
    "oob_location_not_owned": "A starting division is placed outside its country's territory.",
    "oob_fleet_name": "A fleet has no name.",
    "duplicate_oob_fleet": "An OOB repeats a fleet name.",
    "invalid_oob_naval_base": "A fleet has an invalid naval-base location.",
    "oob_task_force_name": "A task force has no name.",
    "duplicate_oob_task_force": "A fleet repeats a task-force name.",
    "invalid_oob_naval_location": "A naval OOB entry has an invalid location.",
    "oob_ship_name": "A ship has neither a name nor an ordered_name.",
    "duplicate_oob_ship": "An OOB repeats a ship name.",
    "invalid_oob_ship_definition": "A ship has no unit definition.",
    "unknown_oob_ship_definition": "A ship uses an unknown unit definition.",
    "missing_oob_ship_equipment": "A ship has no equipment.",
    "invalid_oob_ship_equipment": "A ship has invalid equipment.",
    "duplicate_oob_ship_equipment": "A ship repeats an equipment type.",
    "invalid_oob_air_location": "An air wing has an invalid location.",
    "invalid_oob_air_equipment": "An air wing has invalid equipment.",
    "unknown_oob_naval_location": "A naval OOB entry references an unknown province.",
    "oob_naval_location_not_owned": "A naval OOB entry is outside its country's territory.",
    "unknown_oob_air_location": "An air OOB entry references an unknown state or province.",
    "oob_air_location_not_owned": "An air OOB entry is outside its country's territory.",
    "unknown_oob_equipment": "A naval or air OOB entry uses unknown equipment.",
    "unknown_oob_equipment_owner": "A naval or air equipment entry uses an unknown country tag.",
    "legacy_naval_oob_without_dlc_fallback": (
        "Legacy naval equipment is active when Man the Guns may be enabled."
    ),
    "mtg_naval_oob_uses_legacy_equipment": (
        "A Man the Guns naval OOB uses legacy equipment types."
    ),
    "ungated_mtg_naval_oob": (
        "A hull-based naval OOB is not gated behind Man the Guns."
    ),
    "missing_mtg_ship_variant_name": (
        "A hull-based ship has no equipment variant name."
    ),
    "missing_mtg_equipment_variant": (
        "A hull-based ship refers to no compatible country-history variant."
    ),
    "ungated_bba_air_oob": (
        "An airframe-based air OOB is not gated behind By Blood Alone."
    ),
    "missing_bba_air_variant_name": (
        "An airframe-based air wing has no equipment variant name."
    ),
    "missing_bba_equipment_variant": (
        "An airframe-based air wing refers to no compatible country-history variant."
    ),
    "missing_country_activation": (
        "A generated country has neither scenario-start territory nor a runtime "
        "release/setup path."
    ),
    "runtime_capital_not_assigned": (
        "A runtime-created country does not receive its declared capital."
    ),
    "runtime_capital_not_cored": (
        "A runtime-created country receives its capital without a matching core."
    ),
    "no_owned_territory": "A complete country owns no states.",
    "capital_not_owned": "A country's capital state is not owned by that country.",
    "capital_not_cored": "A country's capital state is not cored by that country.",
    "disconnected_country_territory": "Owned territory is significantly disconnected from the capital.",
    "enclosed_foreign_territory": (
        "A foreign land component is completely enclosed by the target country."
    ),
    "unreachable_focus": "A focus cannot be reached from any root focus.",
    "unfired_event": "A triggered-only event has no modeled incoming reference.",
    "ungranted_idea": "A country idea is never granted by modeled content.",
    "flag_set_never_read": "A scripted flag is written but never read.",
    "flag_read_never_set": "A scripted flag is read but never written by the mod.",
    "unused_localization": "A localization key is not referenced by modeled content.",
    "focus_cycle": "A focus tree contains a prerequisite cycle.",
    "invalid_ideology_id": "An ideology ID is not valid Paradox Script syntax.",
    "invalid_ideology_color": "An ideology color channel is outside 0..255.",
    "duplicate_subideology_id": "A subtype is repeated within an ideology.",
    "invalid_subideology_id": "An ideology subtype ID is invalid.",
    "invalid_ideology_ai_behavior": "An ideology AI behavior is unknown.",
    "invalid_dynamic_modifier_id": "A dynamic modifier ID is invalid.",
    "invalid_dynamic_modifier_script": "A dynamic modifier contains malformed script.",
    "unsupported_dynamic_country_ideas": (
        "dynamic_country_ideas is not a supported HOI4 construct."
    ),
    "invalid_bookmark_date": "A bookmark date is invalid.",
    "invalid_bookmark_country_tag": "A bookmark country tag is invalid.",
    "invalid_bookmark_available": "A bookmark availability block is malformed.",
    "invalid_bookmark_required_dlc": "A bookmark required DLC list is invalid.",
    "invalid_bookmark_effect": "A bookmark effect block is malformed.",
    "bookmark_randomize_weather_missing": (
        "A bookmark is missing its obligatory randomize_weather effect."
    ),
    "bookmark_default_country_missing": "The default country has no bookmark entry.",
    "duplicate_bookmark_country_variant": "A bookmark repeats an indistinguishable country variant.",
    "missing_country_name_pool": "A country that needs generated names has no common/names entry.",
    "missing_character_small_portrait": "An advisor character has no civilian small portrait.",
    "runtime_recruit_character": (
        "recruit_character runs outside country history, where the game ignores it."
    ),
    # Countries.
    "invalid_country_tag": "A country tag is not three letters or digits.",
    "missing_country_name": "No name localisation exists for a country, in any ideology variant.",
    "country_popularity_sum": "A country's party popularities do not add up to 100.",
    "invalid_ruling_party": "A country's ruling party is not a known ideology group.",
    "invalid_country_color": "A country color is malformed or has a channel outside 0..255.",
    # Focus trees.
    "missing_focus_id": "A focus has no id.",
    "invalid_focus_kind": "A focus block kind (focus, shared_focus, joint_focus) is not allowed where it is.",
    "missing_focus_prerequisite": "A focus prerequisite names a focus that does not exist.",
    "missing_focus_mutually_exclusive": "A focus mutually_exclusive entry names a focus that does not exist.",
    "unknown_focus_search_filter": (
        "A focus search filter has no GFX_<filter> sprite in the game, a base mod or the mod."
    ),
    "unknown_state_reference": "Focus effect script scopes into a state id that does not exist.",
    "continuous_focus_position_high": (
        "The continuous focus panel starts above the bottom row of the focus tree."
    ),
    # States.
    "missing_state_id": "A state has no id.",
    "missing_state_owner": "A state has no owner.",
    "unknown_state_owner": "A state owner is not a known country tag.",
    "unknown_state_core": "A state core is not a known country tag.",
    "state_load_error": "A state file could not be read during validation.",
    # Events.
    "missing_event_id": "An event has no id.",
    "missing_event_title": "A visible event has no title.",
    "missing_event_description": "A visible event has no description.",
    "missing_event_options": "A visible event has no option, so its window cannot be closed.",
    "invalid_event_type": "An event block type is not one HOI4 knows.",
    "undeclared_event_namespace": (
        "A dotted event id uses a namespace that no add_namespace in the game, a base mod "
        "or the mod declares."
    ),
    "triggered_only_with_mtth": (
        "A triggered-only event also has mean_time_to_happen, which the game ignores."
    ),
    "war_declaration_in_immediate": (
        "An event declares war in immediate, before the player picks an option."
    ),
    # Ideas.
    "missing_idea_id": "An idea has no id.",
    "unknown_idea_category": (
        "An idea's category is not a category, slot or character_slot declared in "
        "common/idea_tags of the game, a base mod or the mod."
    ),
    "idea_without_effect": "An idea has no modifier, bonus, rule or effect block.",
    # Inherited content and definitions.
    "duplicate_identifier": "A loaded object id is defined more than once.",
    "duplicate_definition_id": "A scripted effect, trigger or other definition is defined twice.",
    "unknown_character_trait": "A character uses a trait no trait file defines.",
    "equipment_variant_chassis_not_unlocked": (
        "Country history creates an equipment variant before its chassis technology."
    ),
    # Map.
    "province_in_multiple_states": "A land province belongs to more than one state.",
    "province_missing_state": "A land province belongs to no state.",
    "unknown_state_province": "A state lists a province that does not exist.",
    "non_land_state_province": "A state lists a sea province.",
    "duplicate_strategic_region_id": "A strategic region id is defined more than once.",
    "empty_strategic_region": "A strategic region has no provinces.",
    "province_in_multiple_strategic_regions": "A province belongs to more than one strategic region.",
    "unknown_strategic_region_province": "A strategic region lists a province that does not exist.",
    "province_missing_strategic_region": "A province belongs to no strategic region.",
    "strategic_region_parse_error": "A strategic region file could not be read.",
    "invalid_supply_node": "A supply node is not on a known land province.",
    "malformed_railway": "A railway line is malformed.",
    "invalid_railway": "A railway has an invalid level, province or connection.",
    "malformed_map_building": "A buildings.txt line is malformed.",
    "map_building_unknown_state": "A buildings.txt line names a state that does not exist.",
    "map_building_unknown_province": "A buildings.txt line names a province that does not exist.",
}

_ERROR_CODES = {
    "load_failure",
    "script_syntax",
    "localization_bom",
    "localization_encoding",
    "localization_header",
    "localization_language_mismatch",
    "missing_effect_target",
    "state_buildings_outside_history",
    "duplicate_country_tag",
    "duplicate_character_id",
    "duplicate_state_id",
    "duplicate_focus_tree_id",
    "duplicate_focus_id",
    "duplicate_event_id",
    "duplicate_decision_category_id",
    "duplicate_decision_id",
    "duplicate_idea_id",
    "duplicate_ideology_id",
    "duplicate_dynamic_modifier_id",
    "duplicate_bookmark_name",
    "tag_definition",
    "capital_ref",
    "focus_cycle",
    "invalid_ideology_id",
    "invalid_ideology_color",
    "duplicate_subideology_id",
    "invalid_subideology_id",
    "invalid_ideology_ai_behavior",
    "invalid_dynamic_modifier_id",
    "invalid_dynamic_modifier_script",
    "unsupported_dynamic_country_ideas",
    "invalid_bookmark_date",
    "invalid_bookmark_country_tag",
    "invalid_bookmark_available",
    "invalid_bookmark_required_dlc",
    "invalid_bookmark_effect",
    "bookmark_randomize_weather_missing",
    "invalid_idea_desc_key",
    "invalid_character_roles_key",
    "invalid_decision_category_layout",
    "invalid_set_politics_field",
    "game_log_error",
    "stale_game_log",
    "country_colors_parse",
    "missing_country_flag",
    "missing_country_leader",
    "missing_character_portrait",
    "missing_character_portrait_gfx",
    "missing_character_portrait_texture",
    "insufficient_political_advisors",
    "insufficient_military_commanders",
    "undefined_recruited_character",
    "unrecruited_character",
    "missing_character_localization",
    "missing_land_oob",
    "empty_land_oob",
    "missing_oob_reference",
    "oob_kind_mismatch",
    "oob_template_name",
    "duplicate_oob_template",
    "invalid_oob_battalion",
    "invalid_oob_grid_position",
    "unknown_oob_template",
    "invalid_oob_location",
    "invalid_oob_factor",
    "unknown_oob_unit_type",
    "unknown_oob_province",
    "oob_water_location",
    "oob_load_error",
    "oob_fleet_name",
    "duplicate_oob_fleet",
    "invalid_oob_naval_base",
    "oob_task_force_name",
    "duplicate_oob_task_force",
    "invalid_oob_naval_location",
    "invalid_oob_ship_definition",
    "unknown_oob_ship_definition",
    "missing_oob_ship_equipment",
    "invalid_oob_ship_equipment",
    "duplicate_oob_ship_equipment",
    "invalid_oob_air_location",
    "invalid_oob_air_equipment",
    "unknown_oob_naval_location",
    "unknown_oob_air_location",
    "unknown_oob_equipment",
    "unknown_oob_equipment_owner",
    "legacy_naval_oob_without_dlc_fallback",
    "mtg_naval_oob_uses_legacy_equipment",
    "ungated_mtg_naval_oob",
    "missing_mtg_ship_variant_name",
    "missing_mtg_equipment_variant",
    "ungated_bba_air_oob",
    "missing_bba_air_variant_name",
    "missing_bba_equipment_variant",
    "missing_country_activation",
    "runtime_capital_not_assigned",
    "runtime_capital_not_cored",
    "no_owned_territory",
    "capital_not_owned",
    "capital_not_cored",
    "missing_country_name_pool",
    "missing_character_small_portrait",
    "runtime_recruit_character",
    "invalid_country_tag",
    "invalid_ruling_party",
    "invalid_country_color",
    "missing_focus_id",
    "invalid_focus_kind",
    "missing_focus_prerequisite",
    "missing_focus_mutually_exclusive",
    "missing_state_id",
    "state_load_error",
    "missing_event_id",
    "missing_event_options",
    "invalid_event_type",
    "undeclared_event_namespace",
    "missing_idea_id",
    "unknown_idea_category",
    "duplicate_identifier",
    "duplicate_definition_id",
    "equipment_variant_chassis_not_unlocked",
    "province_in_multiple_states",
    "unknown_state_province",
    "non_land_state_province",
    "duplicate_strategic_region_id",
    "province_in_multiple_strategic_regions",
    "unknown_strategic_region_province",
    "strategic_region_parse_error",
    "invalid_supply_node",
    "malformed_railway",
    "invalid_railway",
    "malformed_map_building",
    "map_building_unknown_state",
    "map_building_unknown_province",
}

# Advice: style hints and things that are legal but often unintended. They are
# returned only by ``Mod.validate(advice=True)``.
_INFO_CODES = {
    "missing_optional_localization",
    "event_option_no_effect",
    "idea_without_effect",
    "triggered_only_with_mtth",
    "war_declaration_in_immediate",
    "invalid_idea_picture_prefix",
    "mission_activation_without_timeout",
    "continuous_focus_position_high",
    "bad_idea_tooltip_pattern",
    "idea_not_addable",
    "faction_scope_footgun",
    "civil_war_scope_footgun",
    "civil_war_focus_tree_missing",
    # A leader of another party is normal; the game makes one up for the
    # ruling party.
    "leader_party_mismatch",
    # Units may start in a subject's or an ally's territory.
    "oob_location_not_owned",
    "oob_naval_location_not_owned",
    "oob_air_location_not_owned",
    # Documented by the game but never used in its own files: legal, unproven.
    "unseen_effect_token",
    "unseen_trigger_token",
    "unseen_modifier_token",
}

#: Every validation code with its severity: ``"error"`` (the game rejects
#: the content or behaves wrongly), ``"warning"`` (probably a mistake) or
#: ``"info"`` (advice; only returned by ``Mod.validate(advice=True)``). A code
#: has exactly one severity.
VALIDATION_SEVERITIES: dict[str, str] = {
    code: "error" if code in _ERROR_CODES else "info" if code in _INFO_CODES else "warning"
    for code in VALIDATION_CODES
}

VALIDATION_ERROR_CODES: dict[str, str] = {
    code: description
    for code, description in VALIDATION_CODES.items()
    if VALIDATION_SEVERITIES[code] == "error"
}

VALIDATION_WARNING_CODES: dict[str, str] = {
    code: description
    for code, description in VALIDATION_CODES.items()
    if VALIDATION_SEVERITIES[code] == "warning"
}

VALIDATION_INFO_CODES: dict[str, str] = {
    code: description
    for code, description in VALIDATION_CODES.items()
    if VALIDATION_SEVERITIES[code] == "info"
}

SEVERITIES: tuple[str, ...] = ("error", "warning", "info")


def validation_suppressions(entries: Iterable[str]) -> dict[str, set[str]]:
    """Read suppression entries into ``{code: ids}``.

    ``"code"`` suppresses every finding with that code (an empty id set);
    ``"code:ID"`` only findings about the object ``ID``. Raises
    ``ValueError`` for an unknown code or for an error code: errors are
    never suppressed.
    """

    if isinstance(entries, str):
        raise ValueError("suppress takes a list of codes, not one string")
    rules: dict[str, set[str]] = {}
    for entry in entries:
        code, _, object_id = str(entry).strip().partition(":")
        code = code.strip()
        object_id = object_id.strip()
        severity = VALIDATION_SEVERITIES.get(code)
        if severity is None:
            raise ValueError(
                f"Cannot suppress {entry!r}: {code!r} is not a validation code. "
                "Suppress by code (see VALIDATION_CODES), optionally as 'code:ID'."
            )
        if severity == "error":
            raise ValueError(f"Cannot suppress {entry!r}: '{code}' is an error code")
        if code in rules and not rules[code]:
            continue
        if object_id:
            rules.setdefault(code, set()).add(object_id)
        else:
            rules[code] = set()
    return rules


def validate_tag_definition_targets(mod_root: Path) -> list[ValidationError]:
    """Report mod tag declarations whose country definition file is absent."""

    issues: list[ValidationError] = []
    tags_dir = mod_root / "common" / "country_tags"
    countries_dir = mod_root / "common" / "countries"
    if not tags_dir.is_dir():
        return issues
    for path in sorted(tags_dir.glob("*.txt")):
        doc = parse(read_text(path))
        for item in doc.root.assignments():
            declared_path = item.scalar
            if item.op != "=" or declared_path is None or not TAG_RE.match(item.name):
                continue
            tag = item.name
            line_number = doc.line_col(item.start)[0]
            target = countries_dir / Path(declared_path).name
            if target.is_file():
                continue
            issues.append(
                ValidationError(
                    message=(
                        f"Tag {tag} references {declared_path} but file does not exist"
                    ),
                    severity="error",
                    code="tag_definition",
                    file_path=str(path),
                    country_tag=tag,
                    line=line_number,
                )
            )
    return issues


def collect_character_ids(*roots: Path | None) -> set[str]:
    """Collect direct character IDs from ``characters = { ... }`` containers."""

    character_ids: set[str] = set()
    for root in roots:
        if root is None:
            continue
        directory = root / "common" / "characters"
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.txt")):
            try:
                text = read_text(path)
                for body, _, _ in iter_assignment_blocks(text, "characters"):
                    character_ids.update(
                        span.key
                        for span in top_level_assignments(body)
                        if span.is_block
                    )
            except (OSError, ValueError):
                # Source syntax/read failures are surfaced by the source-tree
                # validation pass; do not turn one malformed catalog into a
                # validation crash here.
                continue
    return character_ids


def validate_country_history_references(
    mod_root: Path,
    *,
    known_state_ids: set[int],
    known_character_ids: set[str],
) -> list[ValidationError]:
    """Validate capital and recruited-character references in mod histories."""

    issues: list[ValidationError] = []
    histories_dir = mod_root / "history" / "countries"
    if not histories_dir.is_dir():
        return issues
    for path in sorted(histories_dir.glob("*.txt")):
        doc = parse(read_text(path))
        # Every depth: a capital or recruitment inside a date or ``if`` block
        # is still a reference. Comments and strings are never statements.
        statements = sorted(
            (item for item, _ in doc.root.walk() if item.op == "="),
            key=lambda item: item.start,
        )
        for item in statements:
            value = item.scalar
            if value is None:
                continue
            if item.name == "capital" and value.isdigit():
                state_id = int(value)
                if state_id in known_state_ids:
                    continue
                issues.append(
                    ValidationError(
                        message=f"Capital state {state_id} does not exist",
                        severity="error",
                        code="capital_ref",
                        file_path=str(path),
                        state_id=state_id,
                        line=doc.line_col(item.start)[0],
                    )
                )
            elif item.name == "recruit_character" and value:
                if value in known_character_ids:
                    continue
                issues.append(
                    ValidationError(
                        message=f"Character {value} is not defined",
                        severity="warning",
                        code="character_ref",
                        file_path=str(path),
                        line=doc.line_col(item.start)[0],
                    )
                )
    return issues


def _script_warnings(
    script: str,
    *,
    file_path: str | None = None,
    focus_id: str | None = None,
    event_id: str | None = None,
    known_tags: set[str] | None = None,
    root_scope: str = "COUNTRY",
) -> list[ValidationError]:
    """Scope and shape hints for one effect script, read from its syntax tree.

    ``root_scope`` is the scope the script starts in: ``"COUNTRY"`` for
    focuses and country events, ``"STATE"`` for state events.
    """

    errors: list[ValidationError] = []
    if not script:
        return errors

    def finding(message: str, severity: str, code: str) -> ValidationError:
        return ValidationError(
            message=message,
            severity=severity,
            code=code,
            file_path=file_path,
            focus_id=focus_id,
            event_id=event_id,
        )

    for issue in validate_script_syntax(script):
        errors.append(finding(f"Script syntax issue: {issue}", "error", "script_syntax"))

    statements = list(parse(script).root.walk())

    def at_root_scope(parents: tuple[Assignment, ...]) -> bool:
        return all(_keeps_scope(parent, parents[:index]) for index, parent in enumerate(parents))

    for item, parents in statements:
        name = item.name.lower()
        if name not in {"add_core_of", "remove_core_of"}:
            continue
        value = item.scalar or ""
        scoping = next(
            (
                parent
                for index, parent in reversed(list(enumerate(parents)))
                if not _keeps_scope(parent, parents[:index])
            ),
            None,
        )
        if value.isdigit() and scoping is not None and _TAG_SCOPE_RE.match(scoping.name):
            errors.append(
                finding(
                    f"'{scoping.name} = {{ {item.name} = {value} }}' looks country-scoped. "
                    f"Use '{value} = {{ {item.name} = {scoping.name} }}' for a state-scoped "
                    "core change.",
                    "warning",
                    "country_scope_core_effect",
                )
            )
        elif root_scope == "COUNTRY" and at_root_scope(parents):
            errors.append(
                finding(
                    f"'{item.name}' appears at country scope. Use a state scope such as "
                    f"'115 = {{ {item.name} = TAG }}' to avoid changing every owned state.",
                    "warning",
                    "country_scope_core_effect",
                )
            )

    if any(item.name.lower() == "set_owner" for item, _ in statements):
        errors.append(
            finding(
                "'set_owner' is a state history directive, not a runtime effect; use "
                "transfer_state in event/focus effects",
                "warning",
                "history_set_owner_in_effect",
            )
        )

    for item, parents in statements:
        if item.name.lower() != "add_to_faction":
            continue
        if any(parent.name.lower() in {"every_country", "every_other_country"} for parent in parents):
            message = (
                "'add_to_faction' inside every_country/every_other_country is usually wrong "
                "because the target joins each iterated country's faction. Use an explicit "
                "leader scope."
            )
        elif at_root_scope(parents):
            message = (
                "'add_to_faction = TAG' adds TAG to the current scope's faction. "
                "Use Mod.effect_add_target_to_faction(leader, target) or "
                "Mod.effect_join_faction(actor, leader) to make direction explicit."
            )
        else:
            continue
        errors.append(finding(message, "info", "faction_scope_footgun"))
        break

    civil_wars = [item for item, _ in statements if item.name.lower() == "start_civil_war"]
    if civil_wars:
        if any(
            item.block is None or item.block.first("capital", fold=True) is None
            for item in civil_wars
        ):
            errors.append(
                finding(
                    "'start_civil_war' has no capital = state_id. Civil wars without explicit "
                    "capital/target setup often spawn fragile revolts; consider "
                    "Mod.effect_spawn_revolution(...).",
                    "info",
                    "civil_war_scope_footgun",
                )
            )
        if not any(item.name.lower() == "load_focus_tree" for item, _ in statements):
            errors.append(
                finding(
                    "'start_civil_war' is not paired with load_focus_tree. Dynamic civil-war "
                    "countries often get a generic tree unless the revolt explicitly loads one.",
                    "info",
                    "civil_war_focus_tree_missing",
                )
            )

    for item, _ in statements:
        if item.name.lower() != "add_tech_bonus" or item.block is None:
            continue
        for category_item in item.block.assignments("category", fold=True):
            category = category_item.scalar or ""
            if category and category not in TECHNOLOGY_CATEGORIES:
                examples = "industry, infantry_weapons, artillery, armor, electronics, land_doctrine"
                errors.append(
                    finding(
                        f"Unknown add_tech_bonus category '{category}'. Common valid "
                        f"categories: {examples}.",
                        "warning",
                        "unknown_tech_bonus_category",
                    )
                )

    for item, _ in statements:
        effect_name = item.name.lower()
        if effect_name not in {"declare_war_on", "create_wargoal"} or item.block is None:
            continue
        if item.block.first("target", fold=True) is None:
            errors.append(
                finding(
                    f"'{effect_name}' is missing required target = TAG",
                    "error",
                    "missing_effect_target",
                )
            )
        if item.block.first("type", fold=True) is None:
            errors.append(
                finding(
                    f"'{effect_name}' is missing required type = <wargoal_type>",
                    "warning",
                    "missing_wargoal_type",
                )
            )

    if known_tags is not None:
        for item, _ in statements:
            tag = item.name
            if (
                item.block is None
                or not _TAG_SCOPE_RE.match(tag)
                or tag in _LOGIC_KEYS
                or tag in known_tags
                or re.fullmatch(r"D[0-9]{2}", tag)
            ):
                continue
            errors.append(
                finding(
                    f"Script scopes into unknown country tag '{tag}'",
                    "warning",
                    "unknown_country_scope",
                )
            )

    return errors


_TAG_SCOPE_RE = re.compile(r"^[A-Z][A-Z0-9]{2}$")
_LOGIC_KEYS = frozenset({"AND", "NOT", "NOR"})
# Blocks that run their content in the scope around them.
_SCOPE_KEEPING_BLOCKS = frozenset({
    "if",
    "else_if",
    "else",
    "limit",
    "hidden_effect",
    "effect_tooltip",
    "custom_effect_tooltip",
    "random",
    "random_list",
    "and",
    "or",
    "not",
    "nor",
    "nand",
    "count_triggers",
    "custom_trigger_tooltip",
})


def _keeps_scope(item: Assignment, parents: tuple[Assignment, ...]) -> bool:
    """Return whether ``item``'s block runs in the scope around it.

    A weight inside ``random_list`` is a number like a state id, but keeps
    the scope.
    """

    name = item.name.lower()
    if name in _SCOPE_KEEPING_BLOCKS:
        return True
    return bool(parents) and parents[-1].name.lower() == "random_list"


def validate_country(
    country: Country,
    *,
    known_parties: set[str] | None = None,
    has_localisation: Callable[[str], bool] | None = None,
    leader_ideologies: Sequence[str] | None = None,
    dynamic_tag: bool = False,
) -> list[ValidationError]:
    """Check one country's history values.

    ``has_localisation`` tells whether a localisation key exists. The game
    names a country by ``TAG``, ``TAG_DEF`` or, per ruling ideology,
    ``TAG_<ideology>`` and ``TAG_<ideology>_DEF``; many countries only have
    the ideology names. ``leader_ideologies`` are the ideologies of every
    recruited character with a country-leader role. A ``dynamic_tag``
    (declared after ``dynamic_tags = yes``) takes its name at runtime from
    the country that spawns it.
    """

    errors: list[ValidationError] = []
    file_path = str(country.history_path) if country.history_path is not None else None

    if not TAG_RE.match(country.tag):
        errors.append(
            ValidationError(
                message=f"Invalid country tag '{country.tag}' (must be 3 alphanumeric characters)",
                severity="error",
                code="invalid_country_tag",
                country_tag=country.tag,
                file_path=file_path,
            )
        )

    valid_parties = set(RULING_PARTIES) | set(known_parties or ())
    name_keys = [country.tag, f"{country.tag}_DEF"]
    for name_party in sorted(valid_parties):
        name_keys.extend(
            (f"{country.tag}_{name_party}", f"{country.tag}_{name_party}_DEF")
        )
    if not country.name and not dynamic_tag and not (
        has_localisation is not None and any(has_localisation(key) for key in name_keys)
    ):
        errors.append(
            ValidationError(
                message=(
                    f"Country '{country.tag}' has no name: none of "
                    f"{country.tag}, {country.tag}_DEF or "
                    f"{country.tag}_<ideology> is localised, so the game shows the tag."
                ),
                severity="warning",
                code="missing_country_name",
                country_tag=country.tag,
                file_path=file_path,
            )
        )

    total_pop = sum(country.popularities.values())
    if total_pop > 0 and total_pop != 100:
        errors.append(
            ValidationError(
                message=f"Country '{country.tag}' popularities sum to {total_pop}, expected 100",
                severity="warning",
                code="country_popularity_sum",
                country_tag=country.tag,
                file_path=file_path,
            )
        )

    if country.ruling_party not in valid_parties:
        errors.append(
            ValidationError(
                message=f"Country '{country.tag}' has invalid ruling party '{country.ruling_party}'",
                severity="error",
                code="invalid_ruling_party",
                country_tag=country.tag,
                file_path=file_path,
            )
        )

    # Every ideology has its own leader; the game shows the leader of the
    # ruling party and makes one up when no recruited character leads it.
    # A leader of another party is normal, so this is advice only.
    ideologies = (
        list(leader_ideologies)
        if leader_ideologies is not None
        else [country.leader.ideology]
        if country.leader and country.leader.ideology
        else []
    )
    parties = {
        party
        for ideology in ideologies
        if (party := IDEOLOGY_PARTY_MAP.get(ideology)) is not None
    }
    if (
        parties
        and country.ruling_party in valid_parties
        and country.ruling_party not in parties
    ):
        errors.append(
            ValidationError(
                message=(
                    f"Country '{country.tag}' ruling party '{country.ruling_party}' has no "
                    f"recruited leader (leader ideologies: {', '.join(sorted(set(ideologies)))}), "
                    "so the game shows a generated leader. ruling_party uses party groups "
                    "(democratic, fascism, communism, neutrality); leader_ideology uses "
                    "sub-ideologies such as liberalism, stalinism, nazism, or despotism."
                ),
                severity="info",
                code="leader_party_mismatch",
                country_tag=country.tag,
                file_path=file_path,
            )
        )

    try:
        r, g, b = country.color
        if not (0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255):
            errors.append(
                ValidationError(
                    message=f"Country '{country.tag}' has invalid color {country.color}",
                    severity="error",
                    code="invalid_country_color",
                    country_tag=country.tag,
                )
            )
    except (TypeError, ValueError):
        errors.append(
            ValidationError(
                message=f"Country '{country.tag}' has malformed color {country.color!r}",
                severity="error",
                code="invalid_country_color",
                country_tag=country.tag,
            )
        )

    return errors


#: Search filters of the base game: every ``GFX_FOCUS_FILTER_*`` sprite in its
#: ``interface/*.gfx`` files (HOI4 1.19). A filter needs a sprite of the same
#: name with a ``GFX_`` prefix; with a configured game install the sprites are
#: read from the game, base mods and the mod instead.
DEFAULT_FOCUS_SEARCH_FILTERS: frozenset[str] = frozenset({
    "FOCUS_FILTER_AIR_XP",
    "FOCUS_FILTER_ANNEXATION",
    "FOCUS_FILTER_ARMY_XP",
    "FOCUS_FILTER_BALANCE_OF_POWER",
    "FOCUS_FILTER_CHI_INFLATION",
    "FOCUS_FILTER_COMPLETED_BY_EVENT",
    "FOCUS_FILTER_CONSOLIDATION",
    "FOCUS_FILTER_FOLKHEMMET",
    "FOCUS_FILTER_FRA_OCCUPATION_COST",
    "FOCUS_FILTER_FRA_POLITICAL_VIOLENCE",
    "FOCUS_FILTER_GRE_DEBT_TO_IFC",
    "FOCUS_FILTER_HISTORICAL",
    "FOCUS_FILTER_IMPERIAL_INFLUENCE",
    "FOCUS_FILTER_INDUSTRY",
    "FOCUS_FILTER_INNER_CIRCLE",
    "FOCUS_FILTER_INTERNAL_AFFAIRS",
    "FOCUS_FILTER_INTERNATIONAL_TRADE",
    "FOCUS_FILTER_ITA_MISSIOLINI",
    "FOCUS_FILTER_MANPOWER",
    "FOCUS_FILTER_MEX_CAUDILLO_REBELLION",
    "FOCUS_FILTER_MEX_CHURCH_AUTHORITY",
    "FOCUS_FILTER_MILITARY_CHARACTER",
    "FOCUS_FILTER_NAVY_XP",
    "FOCUS_FILTER_POLITICAL",
    "FOCUS_FILTER_POLITICAL_CHARACTER",
    "FOCUS_FILTER_POWER_STRUGGLE",
    "FOCUS_FILTER_PROPAGANDA",
    "FOCUS_FILTER_RESEARCH",
    "FOCUS_FILTER_SOV_POLITICAL_PARANOIA",
    "FOCUS_FILTER_SPA_CARLIST_UPRISING",
    "FOCUS_FILTER_SPA_CIVIL_WAR",
    "FOCUS_FILTER_STABILITY",
    "FOCUS_FILTER_SWI_MILITARY_READINESS",
    "FOCUS_FILTER_TFV_AUTONOMY",
    "FOCUS_FILTER_TUR_KEMALISM",
    "FOCUS_FILTER_TUR_KURDISTAN",
    "FOCUS_FILTER_TUR_TRADITIONALISM",
    "FOCUS_FILTER_USA_CONGRESS",
    "FOCUS_FILTER_WAR_SUPPORT",
})


def focus_search_filters_from_sprites(sprite_names: Collection[str]) -> set[str]:
    """Return the search filters a set of sprite names declares.

    The focus view shows a filter with the sprite ``GFX_<filter>``, so every
    sprite name is a possible filter once its ``GFX_`` prefix is removed.
    """

    return {name[4:] for name in sprite_names if name.startswith("GFX_") and len(name) > 4}


def validate_focus_tree(
    tree: FocusTree,
    known_focus_ids: set[str] | None = None,
    known_state_ids: set[int] | None = None,
    known_tags: set[str] | None = None,
    *,
    known_search_filters: Collection[str] | None = None,
) -> list[ValidationError]:
    """Check one focus tree.

    ``known_search_filters`` are the filter names that have a ``GFX_<filter>``
    sprite; ``None`` uses the filters of the base game
    (``DEFAULT_FOCUS_SEARCH_FILTERS``).
    """

    errors: list[ValidationError] = []
    local_ids = {f.id for f in tree.focuses}
    all_known = local_ids | (known_focus_ids or set())

    seen_ids: set[str] = set()
    layout = resolve_focus_positions(tree)
    by_id = {focus.id: focus for focus in tree.focuses}
    for focus_id, reason in layout.unresolved.items():
        if tree.kind == "file":
            # Top-level shared/joint focuses may anchor to a focus of the tree
            # they are shared into; that is fine when the anchor exists.
            anchor, seen = focus_id, set()
            while anchor in by_id and anchor not in seen:
                seen.add(anchor)
                anchor = by_id[anchor].relative_position_id
            if anchor not in by_id and anchor in all_known:
                continue
        errors.append(
            ValidationError(
                message=f"Focus '{focus_id}' position unresolved: {reason}",
                severity="warning",
                code="unresolved_focus_position",
                focus_id=focus_id,
                file_path=str(tree.path) if tree.path else None,
            )
        )
    seen_positions: dict[tuple[int, int], list[str]] = {}
    valid_filters = (
        DEFAULT_FOCUS_SEARCH_FILTERS if known_search_filters is None else known_search_filters
    )
    branches = FocusBranches(tree)

    for focus in tree.focuses:
        if not focus.id:
            errors.append(
                ValidationError(
                    message="Focus has no ID",
                    severity="error",
                    code="missing_focus_id",
                    file_path=str(tree.path) if tree.path else None,
                )
            )
            continue

        if focus.id in seen_ids:
            errors.append(
                ValidationError(
                    message=f"Duplicate focus ID '{focus.id}'",
                    severity="error",
                    code="duplicate_focus_id",
                    focus_id=focus.id,
                    file_path=str(tree.path) if tree.path else None,
                )
            )
        seen_ids.add(focus.id)

        expected_kind = focus.kind if tree.kind == "file" else "focus"
        if focus.kind != expected_kind or focus.kind not in (
            "focus", "shared_focus", "joint_focus"
        ):
            errors.append(
                ValidationError(
                    message=(
                        f"Focus '{focus.id}' has kind '{focus.kind}', which is not allowed "
                        f"in {tree.kind} '{tree.id}'"
                    ),
                    severity="error",
                    code="invalid_focus_kind",
                    focus_id=focus.id,
                    file_path=str(tree.path) if tree.path else None,
                )
            )

        # The top-level blocks of one file are placed into different trees,
        # so their positions do not share one canvas. Two focuses may share a
        # position when the game never shows both: their branches are
        # mutually exclusive, or both hang on an allow_branch condition.
        pos = layout.positions.get(focus.id) if tree.kind != "file" else None
        if pos is not None:
            sharing = seen_positions.setdefault(pos, [])
            clash = next(
                (other for other in sharing if not branches.never_shown_together(focus.id, other)),
                None,
            )
            if clash is not None:
                errors.append(
                    ValidationError(
                        message=(
                            f"Focus '{focus.id}' shares position ({pos[0]}, {pos[1]}) "
                            f"with '{clash}', and both can be shown at once"
                        ),
                        severity="warning",
                        code="visual_overlap",
                        focus_id=focus.id,
                        file_path=str(tree.path) if tree.path else None,
                    )
                )
            sharing.append(focus.id)

        for group in focus.prerequisites:
            for ref in group:
                if ref not in all_known:
                    errors.append(
                        ValidationError(
                            message=f"Focus '{focus.id}' prerequisite '{ref}' not found",
                            severity="error",
                            code="missing_focus_prerequisite",
                            focus_id=focus.id,
                            file_path=str(tree.path) if tree.path else None,
                        )
                    )

        for group in focus.mutually_exclusive:
            for ref in group:
                if ref not in all_known:
                    errors.append(
                        ValidationError(
                            message=f"Focus '{focus.id}' mutually_exclusive '{ref}' not found",
                            severity="error",
                            code="missing_focus_mutually_exclusive",
                            focus_id=focus.id,
                            file_path=str(tree.path) if tree.path else None,
                        )
                    )

        for focus_filter in focus.search_filters:
            if focus_filter not in valid_filters:
                errors.append(
                    ValidationError(
                        message=(
                            f"Focus '{focus.id}' has unknown search filter '{focus_filter}': "
                            f"no sprite 'GFX_{focus_filter}' is declared"
                        ),
                        severity="warning",
                        code="unknown_focus_search_filter",
                        focus_id=focus.id,
                        file_path=str(tree.path) if tree.path else None,
                    )
                )

        script = "\n".join(
            [
                focus.completion_reward,
                focus.available,
                focus.bypass,
                focus.select_effect,
                focus.complete_tooltip,
                focus.allow_branch,
            ]
        )
        if known_state_ids is not None:
            # ``123 = { ... }`` at any depth is a state scope, except the
            # weighted options of a ``random_list``.
            state_scopes = [
                item
                for item, parents in parse_script(script).root.walk()
                if item.name.isdigit()
                and item.block is not None
                and not (parents and parents[-1].name.lower() == "random_list")
            ]
            for item in sorted(state_scopes, key=lambda found: found.start):
                sid = int(item.name)
                if sid not in known_state_ids:
                    errors.append(
                        ValidationError(
                            message=f"Focus '{focus.id}' references unknown state ID {sid}",
                            severity="warning",
                            code="unknown_state_reference",
                            focus_id=focus.id,
                            state_id=sid,
                            file_path=str(tree.path) if tree.path else None,
                        )
                    )

        errors.extend(
            _script_warnings(
                script,
                file_path=str(tree.path) if tree.path else None,
                focus_id=focus.id,
                known_tags=known_tags,
            )
        )

    continuous = continuous_focus_position_issue(tree, layout)
    if continuous:
        errors.append(
            ValidationError(
                message=f"Focus tree '{tree.id}': {continuous}",
                severity="info",
                code="continuous_focus_position_high",
                focus_tree_id=tree.id,
                file_path=str(tree.path) if tree.path else None,
            )
        )

    cycle_root = _focus_prerequisite_cycle_root(tree)
    if cycle_root is not None:
        errors.append(
            ValidationError(
                message=f"Focus prerequisite cycle detected involving {cycle_root}",
                severity="error",
                code="focus_cycle",
                focus_id=cycle_root,
                file_path=str(tree.path) if tree.path else None,
            )
        )

    return errors


def _focus_prerequisite_cycle_root(tree: FocusTree) -> str | None:
    """Return the first focus whose dependency walk encounters a cycle."""

    adjacency = {
        focus.id: [
            prerequisite
            for group in focus.prerequisites
            for prerequisite in group
        ]
        for focus in tree.focuses
        if focus.id
    }
    visited: set[str] = set()
    in_stack: set[str] = set()

    def visit(focus_id: str) -> bool:
        visited.add(focus_id)
        in_stack.add(focus_id)
        for prerequisite in adjacency.get(focus_id, []):
            if prerequisite in in_stack:
                return True
            if prerequisite not in visited and visit(prerequisite):
                return True
        in_stack.discard(focus_id)
        return False

    for focus in tree.focuses:
        if focus.id not in visited and visit(focus.id):
            return focus.id
    return None


def validate_state(
    state: State,
    known_tags: set[str] | None = None,
) -> list[ValidationError]:
    errors: list[ValidationError] = []
    source_path = state.source_path or state.path
    file_path = str(source_path) if source_path else None

    if not state.id:
        errors.append(
            ValidationError(
                message="State has no ID",
                severity="error",
                code="missing_state_id",
                state_id=state.id,
                file_path=file_path,
            )
        )

    if not state.owner and known_tags is not None:
        errors.append(
            ValidationError(
                message=f"State {state.id} has no owner",
                severity="warning",
                code="missing_state_owner",
                state_id=state.id,
                file_path=file_path,
            )
        )

    if state.owner and known_tags and state.owner not in known_tags:
        errors.append(
            ValidationError(
                message=f"State {state.id} owner '{state.owner}' is not a known country tag",
                severity="warning",
                code="unknown_state_owner",
                state_id=state.id,
                file_path=file_path,
            )
        )

    for core in state.cores:
        if known_tags and core not in known_tags:
            errors.append(
                ValidationError(
                    message=f"State {state.id} core '{core}' is not a known country tag",
                    severity="warning",
                    code="unknown_state_core",
                    state_id=state.id,
                    file_path=file_path,
                )
            )

    if state.raw_text:
        try:
            state_span = next(
                (
                    span
                    for span in top_level_assignments(state.raw_text)
                    if span.key == "state"
                    and span.is_block
                    and span.body_start is not None
                    and span.body_end is not None
                ),
                None,
            )
            if state_span is not None:
                state_body = state.raw_text[state_span.body_start : state_span.body_end]
                if any(span.key == "buildings" for span in top_level_assignments(state_body)):
                    errors.append(
                        ValidationError(
                            message=(
                                f"State {state.id} declares buildings at state scope; "
                                "move the buildings block inside history"
                            ),
                            severity="error",
                            code="state_buildings_outside_history",
                            state_id=state.id,
                            file_path=file_path,
                        )
                    )
        except ValueError:
            # Parser diagnostics are reported separately; validation should not
            # crash while inspecting already-loaded raw text.
            pass

    return errors


def validate_event(
    event: Event,
    namespace: str | None | object = _UNSET,
    known_tags: set[str] | None = None,
    *,
    namespaces: Collection[str] | None = None,
) -> list[ValidationError]:
    """Check one event.

    The namespace check runs when ``namespaces`` (every namespace declared by
    an ``add_namespace`` in the game, base mods and the mod) or the older
    single ``namespace`` is given. The game compares namespaces without
    regard to case, and a namespace declared in any event file can be used
    by events of every file.
    """

    errors: list[ValidationError] = []
    file_path = str(event.path) if event.path else None

    if not event.id:
        errors.append(
            ValidationError(
                message="Event has no ID",
                severity="error",
                code="missing_event_id",
                event_id=event.id,
                file_path=file_path,
            )
        )
        return errors

    # A hidden event never opens a window, so it needs no title, description
    # or option.
    hidden = event_is_hidden(event)

    if not hidden and not event.title and not event.conditional_titles:
        errors.append(
            ValidationError(
                message=f"Event '{event.id}' has no title",
                severity="warning",
                code="missing_event_title",
                event_id=event.id,
                file_path=file_path,
            )
        )

    if not hidden and not event.description and not event.conditional_descriptions:
        errors.append(
            ValidationError(
                message=f"Event '{event.id}' has no description",
                severity="warning",
                code="missing_event_description",
                event_id=event.id,
                file_path=file_path,
            )
        )

    conditional_texts = [
        ("title", entry) for entry in event.conditional_titles
    ] + [("description", entry) for entry in event.conditional_descriptions]
    conditional_texts += [
        (f"option '{option.name or '<unnamed>'}' name", entry)
        for option in event.options
        for entry in option.conditional_names
    ]
    for label, entry in conditional_texts:
        if not entry.text:
            errors.append(
                ValidationError(
                    message=f"Event '{event.id}' has a conditional {label} without text",
                    severity="warning",
                    code="event_conditional_text_missing",
                    event_id=event.id,
                    file_path=file_path,
                )
            )

    if not hidden and not event.options:
        errors.append(
            ValidationError(
                message=(
                    f"Event '{event.id}' has no options; a visible event needs at "
                    "least one option to close its window"
                ),
                severity="error",
                code="missing_event_options",
                event_id=event.id,
                file_path=file_path,
            )
        )

    if event.event_type not in VALID_EVENT_TYPES:
        errors.append(
            ValidationError(
                message=f"Event '{event.id}' has invalid type '{event.event_type}'",
                severity="error",
                code="invalid_event_type",
                event_id=event.id,
                file_path=file_path,
            )
        )

    declared: Collection[str] | None = namespaces
    if declared is None and namespace is not _UNSET:
        declared = [namespace] if isinstance(namespace, str) and namespace else []
    if declared is not None and "." in event.id:
        prefix = event.id.split(".", 1)[0]
        if prefix.lower() not in {name.lower() for name in declared}:
            errors.append(
                ValidationError(
                    message=(
                        f"Event '{event.id}' uses namespace '{prefix}', but no "
                        f"add_namespace = {prefix} is declared; dotted event IDs "
                        "need their namespace declared"
                    ),
                    severity="error",
                    code="undeclared_event_namespace",
                    event_id=event.id,
                    file_path=file_path,
                )
            )

    if event.is_triggered_only and event.mean_time_to_happen:
        errors.append(
            ValidationError(
                message=(
                    f"Event '{event.id}' is_triggered_only but also has "
                    "mean_time_to_happen, which the game ignores"
                ),
                severity="info",
                code="triggered_only_with_mtth",
                event_id=event.id,
                file_path=file_path,
            )
        )

    effect_script = "\n".join(
        [event.trigger, event.immediate, event.mean_time_to_happen]
        + [opt.trigger + "\n" + opt.effect for opt in event.options]
        + [entry.trigger for _, entry in conditional_texts]
    )
    errors.extend(
        _script_warnings(
            effect_script,
            file_path=file_path,
            event_id=event.id,
            known_tags=known_tags,
            root_scope=(
                "COUNTRY"
                if event.event_type in {"country_event", "news_event"}
                else event.event_type.removesuffix("_event").upper()
            ),
        )
    )

    if event.immediate and any(
        item.name.lower() == "declare_war_on" for item, _ in parse(event.immediate).root.walk()
    ):
        errors.append(
            ValidationError(
                message=(
                    f"Event '{event.id}' declares war in immediate; immediate runs before "
                    "the option is chosen. Put war effects in an option unless that is "
                    "intentional."
                ),
                severity="info",
                code="war_declaration_in_immediate",
                event_id=event.id,
                file_path=file_path,
            )
        )

    if not hidden:
        for option in event.options:
            if not option.effect.strip():
                errors.append(
                    ValidationError(
                        message=(
                            f"Event '{event.id}' option '{option.name or '<unnamed>'}' "
                            "has no gameplay effect"
                        ),
                        severity="info",
                        code="event_option_no_effect",
                        event_id=event.id,
                        file_path=file_path,
                    )
                )

    return errors


def event_is_hidden(event: Event) -> bool:
    """Return whether the event says ``hidden = yes`` (keys in any case)."""

    # Most events never mention the key; skip parsing them.
    if not event.raw_block or "hidden" not in event.raw_block.lower():
        return False
    value = parse(event.raw_block).root.scalar("hidden", fold=True)
    return (value or "").lower() == "yes"


#: Idea categories of the base game (HOI4 1.19 ``common/idea_tags``): every
#: category and every ``slot`` and ``character_slot`` it declares. With a
#: configured game install the declarations are read from the game, base
#: mods and the mod and added to these.
DEFAULT_IDEA_CATEGORIES: frozenset[str] = frozenset({
    "academy_spirit", "air_chief", "air_force_academy_spirit", "air_force_command_spirit",
    "air_force_spirit", "aircraft_manufacturer", "army_chief", "army_spirit", "country",
    "division_command_spirit", "economy", "government", "hidden_ideas", "high_command",
    "industrial_concern", "materiel_manufacturer", "military_staff", "mobilization_laws",
    "naval_academy_spirit", "naval_command_spirit", "naval_manufacturer", "navy_chief",
    "navy_spirit", "political_advisor", "research_production", "tank_manufacturer",
    "theorist", "trade_laws",
})


def idea_categories_from_script(text: str) -> set[str]:
    """Return the idea categories one ``common/idea_tags`` file declares.

    An idea may sit in a category of ``idea_categories`` or in one of the
    ``slot`` or ``character_slot`` names a category lists.
    """

    categories: set[str] = set()
    for top in parse(text).root.assignments("idea_categories", fold=True):
        if top.block is None:
            continue
        for category in top.block.assignments():
            if category.block is None:
                continue
            categories.add(category.name)
            for slot in category.block.assignments():
                if slot.name.lower() in {"slot", "character_slot"} and slot.scalar:
                    categories.add(slot.scalar)
    return categories


def validate_idea_category(idea: Idea, known_categories: Collection[str]) -> list[ValidationError]:
    """Report an idea whose category no ``common/idea_tags`` file declares."""

    if not idea.id or not idea.category or idea.category in known_categories:
        return []
    return [
        ValidationError(
            message=(
                f"Idea '{idea.id}' is in category '{idea.category}', which no "
                "common/idea_tags file declares as a category or slot"
            ),
            severity="error",
            code="unknown_idea_category",
            idea_id=idea.id,
            file_path=str(idea.path) if idea.path else None,
        )
    ]


# Idea blocks that give the idea an effect besides ``modifier``.
_IDEA_EFFECT_KEYS = frozenset({
    "modifier",
    "research_bonus",
    "equipment_bonus",
    "targeted_modifier",
    "rule",
    "on_add",
    "on_remove",
    "do_effect",
})


def validate_idea(idea: Idea) -> list[ValidationError]:
    errors: list[ValidationError] = []

    if not idea.id:
        errors.append(
            ValidationError(
                message="Idea has no ID",
                severity="error",
                code="missing_idea_id",
                file_path=str(idea.path) if idea.path else None,
            )
        )
        return errors

    statements = (
        [item for item in parse(idea.raw_block).root.items if type(item) is Assignment]
        if idea.raw_block
        else []
    )
    has_effect = bool(idea.modifier or idea.research_bonus) or any(
        item.key.value in _IDEA_EFFECT_KEYS for item in statements
    )
    if not has_effect:
        errors.append(
            ValidationError(
                message=f"Idea '{idea.id}' has no modifiers or other effects",
                severity="info",
                code="idea_without_effect",
                idea_id=idea.id,
                file_path=str(idea.path) if idea.path else None,
            )
        )

    if any(item.op == "=" and item.key.value == "icon" for item in statements):
        errors.append(
            ValidationError(
                message=(
                    f"Idea '{idea.id}' uses 'icon ='. HOI4 ideas require "
                    "'picture = <bare_stem>'; the matching interface sprite is "
                    "'GFX_idea_<bare_stem>'."
                ),
                severity="warning",
                code="invalid_idea_icon_key",
                idea_id=idea.id,
                file_path=str(idea.path) if idea.path else None,
            )
        )

    if statements:
        prefixed_picture = next(
            (
                item
                for item in statements
                if item.op == "="
                and item.key.value == "picture"
                and (item.scalar or "").strip().startswith("GFX_idea_")
            ),
            None,
        )
        if prefixed_picture is not None:
            errors.append(
                ValidationError(
                    message=(
                        f"Idea '{idea.id}' picture value is prefixed with "
                        "'GFX_idea_'. The game accepts it, but the convention is the "
                        "bare stem; HOI4 adds the prefix during sprite lookup."
                    ),
                    severity="info",
                    code="invalid_idea_picture_prefix",
                    idea_id=idea.id,
                    file_path=str(idea.path) if idea.path else None,
                )
            )

    if any(
        item.op == "=" and item.key.value == "desc" and item.scalar is not None
        for item in statements
    ):
        errors.append(
            ValidationError(
                message=(
                    f"Idea '{idea.id}' uses unsupported 'desc ='. HOI4 resolves "
                    f"its description from localization key '{idea.id}_desc'."
                ),
                severity="error",
                code="invalid_idea_desc_key",
                idea_id=idea.id,
                file_path=str(idea.path) if idea.path else None,
            )
        )

    return errors


def validate_decision(decision: Decision) -> list[ValidationError]:
    """Light structural checks for missions and targeted decisions."""
    errors: list[ValidationError] = []
    file_path = str(decision.path) if decision.path else None
    is_mission = decision_has_mission_timeout(decision)
    # A selectable mission is started by the player, who pays its cost.
    if (
        is_mission
        and not decision.selectable_mission
        and (decision_has_cost(decision) or decision.custom_cost_trigger.strip())
    ):
        errors.append(
            ValidationError(
                message=(
                    f"Decision '{decision.id}' is a mission (days_mission_timeout) but "
                    "also has a cost; missions start from activation and are never "
                    "paid for, so remove cost/custom_cost_trigger"
                ),
                severity="warning",
                code="mission_with_cost",
                decision_id=decision.id,
                file_path=file_path,
            )
        )
    if decision.activation.strip() and not is_mission:
        errors.append(
            ValidationError(
                message=(
                    f"Decision '{decision.id}' has an activation trigger but no "
                    "days_mission_timeout; the game ignores activation outside missions"
                ),
                severity="info",
                code="mission_activation_without_timeout",
                decision_id=decision.id,
                file_path=file_path,
            )
        )
    return errors
