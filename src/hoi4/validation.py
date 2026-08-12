"""
Validation rules for HOI4 mod data.
"""

from __future__ import annotations

import re
from pathlib import Path

from .effects_catalog import TECHNOLOGY_CATEGORIES
from .events import VALID_EVENT_TYPES
from .parser import iter_assignment_blocks
from .patching import top_level_assignments
from .politics import IDEOLOGY_PARTY_MAP, RULING_PARTIES
from .script import validate_script_syntax
from .types import Country, Event, FocusTree, Idea, State, ValidationError

TAG_RE = re.compile(r"^[A-Z0-9]{3}$")
_TAG_DEFINITION_RE = re.compile(
    r'^\s*([A-Z0-9]{3})\s*=\s*"([^"]+)"(?:\s*#.*)?\s*$'
)
_CAPITAL_RE = re.compile(r"\bcapital\s*=\s*(\d+)\b")
_RECRUIT_CHARACTER_RE = re.compile(
    r'\brecruit_character\s*=\s*"?([A-Za-z0-9_.:%-]+)"?'
)
_UNSET = object()

VALIDATION_CODES: dict[str, str] = {
    "load_failure": "A discovered file could not be loaded by its structured reader.",
    "script_syntax": "Raw Paradox script has mismatched braces or quotes.",
    "localization_bom": "A localization file is missing the UTF-8 byte-order mark HOI4 expects.",
    "localization_encoding": "A localization file is not valid UTF-8.",
    "localization_header": "A localization file has no valid l_<language>: header.",
    "country_scope_core_effect": "Core add/remove effect appears outside an explicit state scope.",
    "history_set_owner_in_effect": "State history owner directive appears in runtime effect script.",
    "unknown_tech_bonus_category": "add_tech_bonus category is not in the technology category catalog.",
    "missing_effect_target": "War goal or war declaration block is missing target = TAG.",
    "missing_wargoal_type": "War goal or war declaration block is missing type = <wargoal_type>.",
    "unknown_country_scope": "Script scopes into a tag not known from vanilla or the mod.",
    "unknown_effect_token": "Effect token is absent from installed-game documentation.",
    "unknown_trigger_token": "Trigger token is absent from installed-game documentation.",
    "unknown_modifier_token": "Modifier token is absent from installed-game documentation.",
    "unsupported_effect_scope": "Effect token is used outside its documented scope.",
    "unsupported_trigger_scope": "Trigger token is used outside its documented scope.",
    "unsupported_modifier_scope": "Modifier token is used outside its documented scope.",
    "unseen_effect_token": "A documented effect has zero installed-game uses.",
    "unseen_trigger_token": "A documented trigger has zero installed-game uses.",
    "unseen_modifier_token": "A documented modifier has zero installed-game uses.",
    "leader_party_mismatch": "Country ruling party group does not match leader sub-ideology.",
    "unknown_assigned_idea": "Country history assigns an idea ID not loaded by the SDK.",
    "assigned_idea_not_country_category": "Country history assigns an idea outside ideas = { country = { ... } }.",
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
        "Idea picture values are bare stems; HOI4 prepends GFX_idea_ during lookup."
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
    "bad_idea_tooltip_pattern": "Effect removes several ideas and adds one idea; swap_ideas usually produces cleaner tooltips.",
    "idea_mutation_collision": "Focuses and delayed/runtime events mutate the same idea IDs.",
    "visual_overlap": "Focus tree layout contains visual overlap risk.",
    "faction_scope_footgun": "Faction effect direction depends on current scope.",
    "civil_war_scope_footgun": "Civil war effects need careful target/capital scope.",
    "civil_war_capital_ref": "A civil-war capital is not a known state ID.",
    "missing_localization": "Referenced HOI4 object has no localization entry.",
    "idea_not_addable": "Effect adds an idea that is not in the country idea category.",
    "civil_war_focus_tree_missing": "Civil war script starts a revolt without loading a focus tree.",
    "unknown_focus_tree_reference": "Effect script references a focus tree ID not loaded by the SDK.",
    "resistance_on_core_state": "Resistance effects target a state that is already a core of its owner.",
    "event_option_no_effect": "Triggered event option has no gameplay effect.",
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
    "oob_location_not_owned": "A starting division is placed outside its country's territory.",
    "oob_fleet_name": "A fleet has no name.",
    "duplicate_oob_fleet": "An OOB repeats a fleet name.",
    "invalid_oob_naval_base": "A fleet has an invalid naval-base location.",
    "oob_task_force_name": "A task force has no name.",
    "duplicate_oob_task_force": "A fleet repeats a task-force name.",
    "invalid_oob_naval_location": "A naval OOB entry has an invalid location.",
    "oob_ship_name": "A ship has no name.",
    "duplicate_oob_ship": "An OOB repeats a ship name.",
    "invalid_oob_ship_definition": "A ship has no unit definition.",
    "unknown_oob_ship_definition": "A ship uses an unknown unit definition.",
    "missing_oob_ship_equipment": "A ship has no equipment.",
    "invalid_oob_ship_equipment": "A ship has invalid equipment.",
    "duplicate_oob_ship_equipment": "A ship repeats an equipment type.",
    "invalid_oob_air_location": "An air wing has an invalid location.",
    "invalid_oob_air_equipment": "An air wing has invalid equipment.",
    "duplicate_oob_air_wing": "An OOB repeats an air wing at one location.",
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
}

_ERROR_CODES = {
    "load_failure",
    "script_syntax",
    "localization_bom",
    "localization_encoding",
    "localization_header",
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
    "invalid_idea_picture_prefix",
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
    "duplicate_oob_grid_position",
    "invalid_oob_grid_position",
    "unknown_oob_template",
    "invalid_oob_location",
    "invalid_oob_factor",
    "unknown_oob_unit_type",
    "unknown_oob_province",
    "oob_water_location",
    "oob_location_not_owned",
    "oob_fleet_name",
    "duplicate_oob_fleet",
    "invalid_oob_naval_base",
    "oob_task_force_name",
    "duplicate_oob_task_force",
    "invalid_oob_naval_location",
    "oob_ship_name",
    "duplicate_oob_ship",
    "invalid_oob_ship_definition",
    "unknown_oob_ship_definition",
    "missing_oob_ship_equipment",
    "invalid_oob_ship_equipment",
    "duplicate_oob_ship_equipment",
    "invalid_oob_air_location",
    "invalid_oob_air_equipment",
    "duplicate_oob_air_wing",
    "unknown_oob_naval_location",
    "oob_naval_location_not_owned",
    "unknown_oob_air_location",
    "oob_air_location_not_owned",
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
}

VALIDATION_WARNING_CODES: dict[str, str] = {
    code: description
    for code, description in VALIDATION_CODES.items()
    if code not in _ERROR_CODES
}


def validate_tag_definition_targets(mod_root: Path) -> list[ValidationError]:
    """Report mod tag declarations whose country definition file is absent."""

    issues: list[ValidationError] = []
    tags_dir = mod_root / "common" / "country_tags"
    countries_dir = mod_root / "common" / "countries"
    if not tags_dir.is_dir():
        return issues
    for path in sorted(tags_dir.glob("*.txt")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_number, line in enumerate(text.splitlines(), 1):
            match = _TAG_DEFINITION_RE.match(line)
            if match is None:
                continue
            tag, declared_path = match.groups()
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
                text = path.read_text(encoding="utf-8", errors="ignore")
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
        text = path.read_text(encoding="utf-8", errors="ignore")
        source = _mask_script_comments(text)
        for match in _CAPITAL_RE.finditer(source):
            state_id = int(match.group(1))
            if state_id in known_state_ids:
                continue
            issues.append(
                ValidationError(
                    message=f"Capital state {state_id} does not exist",
                    severity="error",
                    code="capital_ref",
                    file_path=str(path),
                    state_id=state_id,
                    line=source.count("\n", 0, match.start()) + 1,
                )
            )
        for match in _RECRUIT_CHARACTER_RE.finditer(source):
            character_id = match.group(1)
            if character_id in known_character_ids:
                continue
            issues.append(
                ValidationError(
                    message=f"Character {character_id} is not defined",
                    severity="warning",
                    code="character_ref",
                    file_path=str(path),
                    line=source.count("\n", 0, match.start()) + 1,
                )
            )
    return issues


def _mask_script_comments(text: str) -> str:
    """Replace comment bytes with spaces while preserving offsets and newlines."""

    chars = list(text)
    in_quote = False
    index = 0
    while index < len(chars):
        char = chars[index]
        if char == "\\" and in_quote:
            index += 2
            continue
        if char == '"':
            in_quote = not in_quote
            index += 1
            continue
        if char == "#" and not in_quote:
            while index < len(chars) and chars[index] != "\n":
                chars[index] = " "
                index += 1
            continue
        index += 1
    return "".join(chars)


def _script_warnings(
    script: str,
    *,
    file_path: str | None = None,
    focus_id: str | None = None,
    event_id: str | None = None,
    known_tags: set[str] | None = None,
) -> list[ValidationError]:
    errors: list[ValidationError] = []
    if not script:
        return errors

    for issue in validate_script_syntax(script):
        errors.append(
            ValidationError(
                message=f"Script syntax issue: {issue}",
                severity="error",
                code="script_syntax",
                file_path=file_path,
                focus_id=focus_id,
                event_id=event_id,
            )
        )

    for effect_name in ("add_core_of", "remove_core_of"):
        if re.search(rf"(?m)^\s*{effect_name}\s*=", script):
            errors.append(
                ValidationError(
                    message=(
                        f"'{effect_name}' appears at country scope. Use a state scope such as "
                        f"'115 = {{ {effect_name} = TAG }}' to avoid changing every owned state."
                    ),
                    severity="warning",
                    code="country_scope_core_effect",
                    file_path=file_path,
                    focus_id=focus_id,
                    event_id=event_id,
                )
            )
        for scope, value in re.findall(
            rf"\b([A-Z][A-Z0-9]{{2}})\s*=\s*\{{[^{{}}]*\b{effect_name}\s*=\s*([0-9]+)\b", script
        ):
            errors.append(
                ValidationError(
                    message=(
                        f"'{scope} = {{ {effect_name} = {value} }}' looks country-scoped. "
                        f"Use '{value} = {{ {effect_name} = {scope} }}' for a state-scoped core change."
                    ),
                    severity="warning",
                    code="country_scope_core_effect",
                    file_path=file_path,
                    focus_id=focus_id,
                    event_id=event_id,
                )
            )

    if re.search(r"\bset_owner\s*=", script):
        errors.append(
            ValidationError(
                message="'set_owner' is a state history directive, not a runtime effect; use transfer_state in event/focus effects",
                severity="warning",
                code="history_set_owner_in_effect",
                file_path=file_path,
                focus_id=focus_id,
                event_id=event_id,
            )
        )

    if re.search(r"\badd_to_faction\s*=", script):
        iterated_scope = re.search(
            r"\bevery_(?:other_)?country\s*=\s*\{[^{}]*\badd_to_faction\s*=",
            script,
            flags=re.DOTALL,
        )
        explicit_country_scope = re.search(
            r"\b[A-Z][A-Z0-9]{2}\s*=\s*\{[^{}]*\badd_to_faction\s*=", script, flags=re.DOTALL
        )
        bare_effect = (
            re.search(r"(?m)^\s*add_to_faction\s*=", script) and not explicit_country_scope
        )
        if iterated_scope:
            message = (
                "'add_to_faction' inside every_country/every_other_country is usually wrong because "
                "the target joins each iterated country's faction. Use an explicit leader scope."
            )
        elif bare_effect:
            message = (
                "'add_to_faction = TAG' adds TAG to the current scope's faction. "
                "Use Mod.effect_add_target_to_faction(leader, target) or Mod.effect_join_faction(actor, leader) "
                "to make direction explicit."
            )
        else:
            message = ""
        if message:
            errors.append(
                ValidationError(
                    message=message,
                    severity="warning",
                    code="faction_scope_footgun",
                    file_path=file_path,
                    focus_id=focus_id,
                    event_id=event_id,
                )
            )

    if re.search(r"\bstart_civil_war\s*=", script) and not re.search(r"\bcapital\s*=", script):
        errors.append(
            ValidationError(
                message=(
                    "'start_civil_war' has no capital = state_id. Civil wars without explicit capital/target setup "
                    "often spawn fragile revolts; consider Mod.effect_spawn_revolution(...)."
                ),
                severity="warning",
                code="civil_war_scope_footgun",
                file_path=file_path,
                focus_id=focus_id,
                event_id=event_id,
            )
        )

    if re.search(r"\bstart_civil_war\s*=", script) and not re.search(
        r"\bload_focus_tree\s*=", script
    ):
        errors.append(
            ValidationError(
                message=(
                    "'start_civil_war' is not paired with load_focus_tree. Dynamic civil-war countries often get "
                    "a generic tree unless the revolt explicitly loads one."
                ),
                severity="warning",
                code="civil_war_focus_tree_missing",
                file_path=file_path,
                focus_id=focus_id,
                event_id=event_id,
            )
        )

    if "add_tech_bonus" in script:
        for category in re.findall(r"\bcategory\s*=\s*([A-Za-z0-9_]+)", script):
            if category not in TECHNOLOGY_CATEGORIES:
                examples = (
                    "industry, infantry_weapons, artillery, armor, electronics, land_doctrine"
                )
                errors.append(
                    ValidationError(
                        message=(
                            f"Unknown add_tech_bonus category '{category}'. Common valid categories: {examples}."
                        ),
                        severity="warning",
                        code="unknown_tech_bonus_category",
                        file_path=file_path,
                        focus_id=focus_id,
                        event_id=event_id,
                    )
                )

    for effect_name in ("declare_war_on", "create_wargoal"):
        for body, _, _ in iter_assignment_blocks(script, effect_name):
            if not re.search(r"\btarget\s*=", body):
                errors.append(
                    ValidationError(
                        message=f"'{effect_name}' is missing required target = TAG",
                        severity="error",
                        code="missing_effect_target",
                        file_path=file_path,
                        focus_id=focus_id,
                        event_id=event_id,
                    )
                )
            if not re.search(r"\btype\s*=", body):
                errors.append(
                    ValidationError(
                        message=f"'{effect_name}' is missing required type = <wargoal_type>",
                        severity="warning",
                        code="missing_wargoal_type",
                        file_path=file_path,
                        focus_id=focus_id,
                        event_id=event_id,
                    )
                )

    if known_tags is not None:
        for tag in re.findall(r"(?m)^\s*([A-Z][A-Z0-9]{2})\s*=\s*\{", script):
            if tag not in known_tags and not re.fullmatch(r"D[0-9]{2}", tag):
                errors.append(
                    ValidationError(
                        message=f"Script scopes into unknown country tag '{tag}'",
                        severity="warning",
                        code="unknown_country_scope",
                        file_path=file_path,
                        focus_id=focus_id,
                        event_id=event_id,
                    )
                )

    return errors


def validate_country(
    country: Country, *, known_parties: set[str] | None = None
) -> list[ValidationError]:
    errors: list[ValidationError] = []

    if not TAG_RE.match(country.tag):
        errors.append(
            ValidationError(
                message=f"Invalid country tag '{country.tag}' (must be 3 alphanumeric characters)",
                severity="error",
                country_tag=country.tag,
            )
        )

    if not country.name:
        errors.append(
            ValidationError(
                message=f"Country '{country.tag}' has no name",
                severity="warning",
                country_tag=country.tag,
            )
        )

    total_pop = sum(country.popularities.values())
    if total_pop > 0 and total_pop != 100:
        errors.append(
            ValidationError(
                message=f"Country '{country.tag}' popularities sum to {total_pop}, expected 100",
                severity="warning",
                country_tag=country.tag,
            )
        )

    valid_parties = set(RULING_PARTIES) | set(known_parties or ())
    if country.ruling_party not in valid_parties:
        errors.append(
            ValidationError(
                message=f"Country '{country.tag}' has invalid ruling party '{country.ruling_party}'",
                severity="error",
                country_tag=country.tag,
            )
        )

    if country.leader and country.leader.ideology:
        expected_party = IDEOLOGY_PARTY_MAP.get(country.leader.ideology)
        if (
            expected_party
            and country.ruling_party in valid_parties
            and expected_party != country.ruling_party
        ):
            errors.append(
                ValidationError(
                    message=(
                        f"Country '{country.tag}' ruling party '{country.ruling_party}' does not match "
                        f"leader ideology '{country.leader.ideology}'. ruling_party uses party groups "
                        "(democratic, fascism, communism, neutrality); leader_ideology uses sub-ideologies "
                        "such as liberalism, stalinism, nazism, or despotism."
                    ),
                    severity="warning",
                    code="leader_party_mismatch",
                    country_tag=country.tag,
                )
            )

    try:
        r, g, b = country.color
        if not (0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255):
            errors.append(
                ValidationError(
                    message=f"Country '{country.tag}' has invalid color {country.color}",
                    severity="error",
                    country_tag=country.tag,
                )
            )
    except (TypeError, ValueError):
        errors.append(
            ValidationError(
                message=f"Country '{country.tag}' has malformed color {country.color!r}",
                severity="error",
                country_tag=country.tag,
            )
        )

    return errors


def validate_focus_tree(
    tree: FocusTree,
    known_focus_ids: set[str] | None = None,
    known_state_ids: set[int] | None = None,
    known_tags: set[str] | None = None,
) -> list[ValidationError]:
    errors: list[ValidationError] = []
    local_ids = {f.id for f in tree.focuses}
    all_known = local_ids | (known_focus_ids or set())

    seen_ids: set[str] = set()
    seen_positions: dict[tuple[int, int], str] = {}
    valid_filters = {
        "FOCUS_FILTER_POLITICAL",
        "FOCUS_FILTER_RESEARCH",
        "FOCUS_FILTER_INDUSTRY",
        "FOCUS_FILTER_STABILITY",
        "FOCUS_FILTER_WAR_SUPPORT",
        "FOCUS_FILTER_MANPOWER",
        "FOCUS_FILTER_ANNEXATION",
        "FOCUS_FILTER_ARMY_XP",
        "FOCUS_FILTER_NAVY_XP",
        "FOCUS_FILTER_AIR_XP",
        "FOCUS_FILTER_INTERNATIONAL_TRADE",
        "FOCUS_FILTER_DIPLOMACY",
        "FOCUS_FILTER_EXCLUSIONS",
        "FOCUS_FILTER_HISTORICAL",
    }

    for focus in tree.focuses:
        if not focus.id:
            errors.append(
                ValidationError(
                    message="Focus has no ID",
                    severity="error",
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

        pos = (focus.x, focus.y)
        if pos in seen_positions:
            errors.append(
                ValidationError(
                    message=(
                        f"Focus '{focus.id}' shares position ({focus.x}, {focus.y}) "
                        f"with '{seen_positions[pos]}'"
                    ),
                    severity="warning",
                    focus_id=focus.id,
                    file_path=str(tree.path) if tree.path else None,
                )
            )
        else:
            seen_positions[pos] = focus.id

        for group in focus.prerequisites:
            for ref in group:
                if ref not in all_known:
                    errors.append(
                        ValidationError(
                            message=f"Focus '{focus.id}' prerequisite '{ref}' not found",
                            severity="error",
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
                            focus_id=focus.id,
                            file_path=str(tree.path) if tree.path else None,
                        )
                    )

        for focus_filter in focus.search_filters:
            if focus_filter not in valid_filters:
                errors.append(
                    ValidationError(
                        message=f"Focus '{focus.id}' has unknown search filter '{focus_filter}'",
                        severity="warning",
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
            for raw_id in re.findall(r"(?m)^\s*(\d+)\s*=\s*\{", script):
                sid = int(raw_id)
                if sid not in known_state_ids:
                    errors.append(
                        ValidationError(
                            message=f"Focus '{focus.id}' references unknown state ID {sid}",
                            severity="warning",
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
                state_id=state.id,
                file_path=file_path,
            )
        )

    if not state.owner and known_tags is not None:
        errors.append(
            ValidationError(
                message=f"State {state.id} has no owner",
                severity="warning",
                state_id=state.id,
                file_path=file_path,
            )
        )

    if state.owner and known_tags and state.owner not in known_tags:
        errors.append(
            ValidationError(
                message=f"State {state.id} owner '{state.owner}' is not a known country tag",
                severity="warning",
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
) -> list[ValidationError]:
    errors: list[ValidationError] = []

    if not event.id:
        errors.append(
            ValidationError(
                message="Event has no ID",
                severity="error",
                event_id=event.id,
            )
        )
        return errors

    if not event.title:
        errors.append(
            ValidationError(
                message=f"Event '{event.id}' has no title",
                severity="warning",
                event_id=event.id,
            )
        )

    if not event.description:
        errors.append(
            ValidationError(
                message=f"Event '{event.id}' has no description",
                severity="warning",
                event_id=event.id,
            )
        )

    if not event.options:
        errors.append(
            ValidationError(
                message=f"Event '{event.id}' has no options",
                severity="error",
                event_id=event.id,
            )
        )

    if event.event_type not in VALID_EVENT_TYPES:
        errors.append(
            ValidationError(
                message=f"Event '{event.id}' has invalid type '{event.event_type}'",
                severity="error",
                event_id=event.id,
            )
        )

    if namespace is not _UNSET:
        inferred_namespace = event.id.split(".", 1)[0] if "." in event.id else None
        if inferred_namespace and not namespace:
            errors.append(
                ValidationError(
                    message=f"Event '{event.id}' has no add_namespace = {inferred_namespace}; dotted event IDs need their namespace declared",
                    severity="error",
                    event_id=event.id,
                    file_path=str(event.path) if event.path else None,
                )
            )
        elif inferred_namespace and namespace != inferred_namespace:
            errors.append(
                ValidationError(
                    message=f"Event '{event.id}' namespace '{namespace}' does not match ID prefix '{inferred_namespace}'",
                    severity="warning",
                    event_id=event.id,
                    file_path=str(event.path) if event.path else None,
                )
            )

    if event.is_triggered_only and event.mean_time_to_happen:
        errors.append(
            ValidationError(
                message=f"Event '{event.id}' is_triggered_only but also has mean_time_to_happen; it will not fire randomly",
                severity="warning",
                event_id=event.id,
                file_path=str(event.path) if event.path else None,
            )
        )

    effect_script = "\n".join(
        [event.trigger, event.immediate, event.mean_time_to_happen]
        + [opt.trigger + "\n" + opt.effect for opt in event.options]
    )
    errors.extend(
        _script_warnings(
            effect_script,
            file_path=str(event.path) if event.path else None,
            event_id=event.id,
            known_tags=known_tags,
        )
    )

    if event.immediate and "declare_war_on" in event.immediate:
        errors.append(
            ValidationError(
                message=(
                    f"Event '{event.id}' declares war in immediate; immediate runs before the option is chosen. "
                    "Put war effects in an option unless that is intentional."
                ),
                severity="warning",
                event_id=event.id,
                file_path=str(event.path) if event.path else None,
            )
        )

    for option in event.options:
        if not option.effect.strip():
            errors.append(
                ValidationError(
                    message=f"Event '{event.id}' option '{option.name or '<unnamed>'}' has no gameplay effect",
                    severity="warning",
                    code="event_option_no_effect",
                    event_id=event.id,
                    file_path=str(event.path) if event.path else None,
                )
            )

    return errors


def validate_idea(idea: Idea) -> list[ValidationError]:
    errors: list[ValidationError] = []

    if not idea.id:
        errors.append(
            ValidationError(
                message="Idea has no ID",
                severity="error",
            )
        )
        return errors

    if not idea.modifier:
        errors.append(
            ValidationError(
                message=f"Idea '{idea.id}' has no modifiers",
                severity="warning",
                idea_id=idea.id,
            )
        )

    if idea.raw_block and any(
        span.key == "icon" for span in top_level_assignments(idea.raw_block)
    ):
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

    if idea.raw_block:
        prefixed_picture = next(
            (
                span
                for span in top_level_assignments(idea.raw_block)
                if span.key == "picture"
                and not span.is_block
                and idea.raw_block[span.value_start : span.value_end]
                .strip()
                .strip('"')
                .startswith("GFX_idea_")
            ),
            None,
        )
        if prefixed_picture is not None:
            errors.append(
                ValidationError(
                    message=(
                        f"Idea '{idea.id}' picture value is prefixed with "
                        "'GFX_idea_'. Store only the bare stem; HOI4 adds that "
                        "prefix during sprite lookup."
                    ),
                    severity="error",
                    code="invalid_idea_picture_prefix",
                    idea_id=idea.id,
                    file_path=str(idea.path) if idea.path else None,
                )
            )

    if idea.raw_block and any(
        span.key == "desc" and not span.is_block
        for span in top_level_assignments(idea.raw_block)
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
