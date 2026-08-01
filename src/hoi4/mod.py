"""
Mod facade - the primary entry point for AI agents.

Usage:
    from hoi4 import Mod

    mod = Mod("/path/to/my_mod", hoi4_install="/opt/steam/hoi4")
    tree = mod.get_focus_tree("german_focus")
    mod.add_focus("german_focus", Focus(id="GER_new", x=5, y=3))
    errors = mod.validate()
    diff = mod.preview()
    mod.save()
"""

from __future__ import annotations

import copy
import csv
import dataclasses
import os
import re
import tempfile
import unicodedata
from datetime import datetime
from heapq import nlargest
import warnings
from contextlib import contextmanager
from pathlib import Path
from difflib import SequenceMatcher
from typing import Any, Literal, Optional, Sequence, TypedDict, cast

from .bookmarks import (
    DEFAULT_BOOKMARK_EFFECT,
    Bookmark,
    BookmarkCountry,
    load_bookmarks_file,
    normalize_required_dlc,
    patch_bookmark_dates_defines,
    require_bookmark_date,
    require_bookmark_country_key,
    serialize_bookmarks_file,
)
from .assets import FlagAssetSet
from .config import find_config
from .content_validation import (
    validate_bookmark,
    validate_dynamic_modifier,
    validate_ideology,
)
from .content_graph import ContentLivenessReport, analyze_content_liveness
from .countries import (
    _country_localisation_suffixes,
    country_color_tags,
    country_file_paths,
    read_country,
    seed_country_colors_file,
    serialize_country_colors_file,
    serialize_country_files,
)
from .characters import (
    AdvisorRole,
    ArmyCommanderRole,
    Character,
    CharacterInstance,
    CharacterPortrait,
    CharacterRole,
    CountryLeaderRole,
    load_characters_file,
    role_key,
    serialize_characters_file,
)
from .country_package import CountryPackageReport
from .decisions import (
    load_decision_categories_file,
    load_decisions_file,
    serialize_decision_categories_file,
    serialize_decisions_file,
)
from .diff import unified_diff
from .dynamic_modifiers import (
    DynamicModifier,
    load_dynamic_modifiers_file,
    serialize_dynamic_modifiers_file,
)
from .events import load_events_file, scan_event_ids_file, serialize_events_file
from .effects_catalog import TECHNOLOGY_CATEGORIES
from .focus import load_focus_tree, load_focus_trees, serialize_focus_file
from .idea_icons import (
    DEFAULT_IDEA_ICON,
    IDEA_SPRITE_PREFIX,
    normalize_idea_icon,
    resolve_idea_sprite,
)
from .ideas import read_ideas_file, scan_idea_ids_file, serialize_ideas_file
from .ideologies import Ideology, SubIdeology, load_ideologies_file, serialize_ideologies_file
from .localisation import (
    normalize_localization_key,
    parse_localization_dir,
    serialize_localization_file,
)
from .map_topology import (
    TerritoryComponent,
    find_country_territory_components,
    find_enclosed_foreign_components,
)
from .on_actions import load_on_actions_file, serialize_on_actions_file
from .oob import (
    AirWing,
    DivisionTemplate,
    DivisionUnit,
    EquipmentVariant,
    Fleet,
    OOBKind,
    OOBReference,
    OrderOfBattle,
    find_equipment_variants,
    find_oob_references,
    load_oob_file,
    remove_oob_reference,
    serialize_equipment_variant,
    serialize_oob_assignment,
    serialize_oob,
    validate_oob,
)
from .paths import (
    require_country_tag,
    require_event_namespace,
    require_script_id,
    resolve_mod_output_path,
    safe_file_stem,
)
from .parser import (
    ParseError,
    PdxNode,
    iter_assignment_blocks,
    parse_pdx,
    strip_comments,
)
from .patching import AssignmentSpan, append_assignment, set_block, top_level_assignments
from .politics import LEADER_IDEOLOGIES_BY_PARTY, RULING_PARTIES
from .progress import CancelCallback, ProgressCallback, check_cancelled, report_progress
from .states import (
    build_state_index,
    find_state_file,
    patch_state_history_owner_cores_text,
    read_state,
    serialize_state,
)
from .script import (
    effect_block,
    normalize_block_body,
    pdx_string,
    pdx_value,
    scope_block,
    validate_script_syntax,
)
from .script_vocabulary import (
    GameScriptVocabulary,
    ScriptSource,
    load_game_script_vocabulary,
    validate_script_sources,
)
from .tags import (
    _parse_tag_file_mapping,
    load_all_tags,
    load_vanilla_tags,
    sort_generated_country_tags,
)
from .types import (
    Country,
    Decision,
    DecisionCategory,
    Event,
    EventOption,
    Focus,
    FocusTree,
    Idea,
    Leader,
    LoadDiagnostic,
    OnAction,
    SaveResult,
    State,
    ValidationError,
)
from .validation import (
    collect_character_ids,
    validate_country,
    validate_country_history_references,
    validate_event,
    validate_focus_tree,
    validate_idea,
    validate_state,
    validate_tag_definition_targets,
)
from .validation_stages import ValidationStage, require_validation_stage

COMMON_FOCUS_ICONS: tuple[str, ...] = (
    "GFX_goal_generic_construct_civ_factory",
    "GFX_goal_generic_construct_infrastructure",
    "GFX_goal_generic_construct_mil_factory",
    "GFX_goal_generic_consumer_goods",
    "GFX_goal_generic_dangerous_deal",
    "GFX_goal_generic_demand_territory",
    "GFX_goal_generic_forceful_treaty",
    "GFX_goal_generic_intelligence_exchange",
    "GFX_goal_generic_major_war",
    "GFX_goal_generic_political_pressure",
    "GFX_goal_generic_production",
    "GFX_goal_generic_secret_weapon",
    "GFX_goal_generic_small_arms",
    "GFX_goal_generic_territory_or_war",
)


class ExternalModificationError(RuntimeError):
    """A source file changed on disk after this ``Mod`` instance loaded it."""


_IDEA_EFFECT_RE = re.compile(r"\b(?:add_ideas|remove_ideas)\s*=\s*([A-Za-z0-9_.:-]+)")
_HAS_IDEA_RE = re.compile(r"\bhas_idea\s*=\s*([A-Za-z0-9_.:-]+)")
_EVENT_REF_RE = re.compile(
    r"\b(?:country_event|state_event|news_event|unit_leader_event|operative_leader_event)"
    r"\s*=\s*\{[^{}]*\bid\s*=\s*([A-Za-z0-9_.:-]+)"
)
_EQUIPMENT_STOCKPILE_RE = re.compile(r"\badd_equipment_to_stockpile\s*=\s*\{([^{}]*)\}")
_TECH_BLOCK_RE = re.compile(r"\bset_technology\s*=\s*\{([^{}]*)\}")
_LOAD_FOCUS_TREE_RE = re.compile(r"\bload_focus_tree\s*=\s*\{[^{}]*\btree\s*=\s*([A-Za-z0-9_.:-]+)")
_SCRIPT_BLOCK_ID_RE = re.compile(r"(?m)^\s*([A-Za-z0-9_.:-]+)\s*=\s*\{")
_GFX_NAME_RE = re.compile(r"\bname\s*=\s*\"?([A-Za-z0-9_.:-]+)\"?")
_COUNTRY_TAG_LINE_RE = re.compile(
    r'^\s*([A-Z0-9]{3})\s*=\s*"([^"]+)"(?:\s*#.*)?\s*$'
)
_DATE_ASSIGNMENT_RE = re.compile(r"\d{1,4}\.\d{1,2}\.\d{1,2}(?:\.\d{1,2})?")


@dataclasses.dataclass(frozen=True)
class _HistoryTechnologyGrant:
    technology: str
    required_dlc: tuple[str, ...]
    excluded_dlc: tuple[str, ...]
    offset: int


@dataclasses.dataclass(frozen=True)
class _HistoryVariantOccurrence:
    variant: EquipmentVariant
    offset: int


_DLCCondition = tuple[tuple[str, ...], tuple[str, ...]]
_DLCFormula = tuple[_DLCCondition, ...]


def _idea_mutation_ids(
    script: str,
    *,
    ignore_guarded_additions: bool = False,
) -> set[str]:
    """Return ideas mutated by script, optionally ignoring safe guarded adds."""

    mutations: set[str] = set()

    def scalar(container: str, span: AssignmentSpan) -> str:
        return container[span.value_start : span.value_end].strip().strip('"')

    def block(container: str, span: AssignmentSpan) -> str:
        if span.body_start is None or span.body_end is None:
            return ""
        return container[span.body_start : span.body_end]

    def values(container: str, span: AssignmentSpan) -> set[str]:
        if not span.is_block:
            value = scalar(container, span)
            return {value} if value else set()
        return {
            value
            for value in re.findall(
                r'(?<![A-Za-z0-9_.:-])"?([A-Za-z0-9_.:-]+)"?',
                strip_comments(block(container, span)),
            )
            if value.lower() not in {"yes", "no"}
        }

    def negative_has_idea_guards(body: str) -> set[str]:
        guarded: set[str] = set()

        def visit(fragment: str, positive: bool = True) -> None:
            try:
                spans = top_level_assignments(fragment)
            except (ParseError, ValueError):
                return
            for span in spans:
                if span.key == "has_idea" and not positive and not span.is_block:
                    idea_id = scalar(fragment, span)
                    if idea_id:
                        guarded.add(idea_id)
                    continue
                if not span.is_block:
                    continue
                nested = block(fragment, span)
                if span.key == "NOT":
                    visit(nested, not positive)
                elif span.key == "AND" and positive:
                    visit(nested, positive)
                elif span.key == "OR" and not positive:
                    visit(nested, positive)

        visit(body)
        return guarded

    def process(
        container: str,
        span: AssignmentSpan,
        guarded_absent: set[str],
    ) -> None:
        key = span.key
        if key in {"add_ideas", "remove_ideas"}:
            for idea_id in values(container, span):
                if (
                    key == "add_ideas"
                    and ignore_guarded_additions
                    and idea_id in guarded_absent
                ):
                    continue
                mutations.add(idea_id)
            return
        if key == "add_timed_idea" and span.is_block:
            try:
                timed_spans = top_level_assignments(block(container, span))
            except (ParseError, ValueError):
                timed_spans = []
            timed_body = block(container, span)
            for timed_span in timed_spans:
                if timed_span.key != "idea" or timed_span.is_block:
                    continue
                idea_id = scalar(timed_body, timed_span)
                if not (
                    ignore_guarded_additions and idea_id in guarded_absent
                ):
                    mutations.add(idea_id)
            return
        if key == "swap_ideas" and span.is_block:
            swap_body = block(container, span)
            try:
                swap_spans = top_level_assignments(swap_body)
            except (ParseError, ValueError):
                swap_spans = []
            for swap_span in swap_spans:
                if (
                    swap_span.key in {"add_idea", "remove_idea"}
                    and not swap_span.is_block
                ):
                    mutations.add(scalar(swap_body, swap_span))
            return
        if key in {"if", "else_if"} and span.is_block:
            walk_if(block(container, span), guarded_absent)
            return
        if span.is_block:
            walk(block(container, span), guarded_absent)

    def walk_if(body: str, inherited_guards: set[str]) -> None:
        try:
            spans = top_level_assignments(body)
        except (ParseError, ValueError):
            return
        local_guards = set(inherited_guards)
        for span in spans:
            if span.key == "limit" and span.is_block:
                local_guards.update(negative_has_idea_guards(block(body, span)))
        for span in spans:
            if span.key == "limit":
                continue
            if span.key == "else" and span.is_block:
                walk(block(body, span), inherited_guards)
                continue
            process(body, span, local_guards)

    def walk(body: str, guarded_absent: set[str]) -> None:
        try:
            spans = top_level_assignments(body)
        except (ParseError, ValueError):
            return
        for span in spans:
            process(body, span, guarded_absent)

    walk(script, set())
    return {idea_id for idea_id in mutations if idea_id}


def _infer_event_namespace(event_id: str) -> str | None:
    if "." not in event_id:
        return None
    namespace = event_id.split(".", 1)[0]
    if namespace.replace("_", "").isalnum() and not namespace[0].isdigit():
        return namespace
    return None


class StateHistoryPatch(TypedDict):
    owner: str | None
    add_cores: list[str]
    remove_cores: list[str]


class _DuplicateIdentifierError(RuntimeError):
    pass


def _set_fields(obj: object, kwargs: dict[str, Any], *, allow_path: bool = False) -> None:
    from .types import _ensure_nested

    internal = {
        "id",
        "tag",
        "raw_block",
        "raw_definition",
        "raw_history",
        "raw_character",
        "source_path",
        "source_occurrence",
        "definition_path",
        "definition_raw_block",
        "history_path",
        "character_path",
        "touched",
        "touched_fields",
        "modifier_merge",
        "category_raw_block",
    }
    if not allow_path:
        internal.add("path")
    if not dataclasses.is_dataclass(obj):
        raise TypeError(f"Expected dataclass instance, got {type(obj).__name__}")
    valid = {f.name for f in dataclasses.fields(cast(Any, obj))} - internal
    unknown = set(kwargs) - valid
    if unknown:
        raise TypeError(f"Unknown fields: {sorted(unknown)}. Valid: {sorted(valid)}")
    for key, value in kwargs.items():
        if key in ("prerequisites", "mutually_exclusive") and isinstance(value, list):
            value = _ensure_nested(value)
        setattr(obj, key, value)


def _append_unique(existing: list, values: list) -> list:
    out = list(existing)
    for value in values:
        if value not in out:
            out.append(value)
    return out


def _inherit_fleet_sources(
    replacements: list[Fleet],
    existing: list[Fleet],
) -> None:
    """Carry source coordinates into explicit naval replacements."""

    for fleet_index, fleet in enumerate(replacements):
        if fleet_index >= len(existing):
            continue
        source_fleet = existing[fleet_index]
        if fleet.source_index < 0 and not fleet.raw_block:
            fleet.source_index = source_fleet.source_index
            fleet.raw_block = source_fleet.raw_block
        fleet.touched_fields.update({"name", "naval_base", "task_forces"})
        for force_index, task_force in enumerate(fleet.task_forces):
            if force_index >= len(source_fleet.task_forces):
                continue
            source_force = source_fleet.task_forces[force_index]
            if task_force.source_index < 0 and not task_force.raw_block:
                task_force.source_index = source_force.source_index
                task_force.raw_block = source_force.raw_block
            task_force.touched_fields.update({"name", "location", "ships"})
            for ship_index, ship in enumerate(task_force.ships):
                if ship_index >= len(source_force.ships):
                    continue
                source_ship = source_force.ships[ship_index]
                if ship.source_index < 0 and not ship.raw_block:
                    ship.source_index = source_ship.source_index
                    ship.raw_block = source_ship.raw_block
                ship.touched_fields.update(
                    {"name", "definition", "equipment", "pride_of_the_fleet"}
                )
                for equipment_index, equipment in enumerate(ship.equipment):
                    if equipment_index >= len(source_ship.equipment):
                        continue
                    source_equipment = source_ship.equipment[equipment_index]
                    if equipment.source_index < 0 and not equipment.raw_block:
                        equipment.source_index = source_equipment.source_index
                        equipment.raw_block = source_equipment.raw_block
                    equipment.touched_fields.update(
                        {"amount", "owner", "creator", "version_name"}
                    )


def _default_oob_equipment_owners(oob: OrderOfBattle) -> None:
    """Fill the engine-required equipment owner from the OOB country."""

    if not oob.country_tag:
        return
    for fleet in oob.fleets:
        for task_force in fleet.task_forces:
            for ship in task_force.ships:
                for equipment in ship.equipment:
                    if not equipment.owner:
                        equipment.owner = oob.country_tag
                        if equipment.raw_block:
                            equipment.touched_fields.add("owner")
    for wing in oob.air_wings:
        if not wing.owner:
            wing.owner = oob.country_tag
            if wing.raw_block:
                wing.touched_fields.add("owner")


def _oob_content_kind(
    templates: Sequence[DivisionTemplate],
    divisions: Sequence[DivisionUnit],
    fleets: Sequence[Fleet],
    air_wings: Sequence[AirWing],
) -> OOBKind | None:
    kinds: list[OOBKind] = []
    if templates or divisions:
        kinds.append("land")
    if fleets:
        kinds.append("naval")
    if air_wings:
        kinds.append("air")
    if len(kinds) > 1:
        return "mixed"
    return kinds[0] if kinds else None


def _join_script_parts(parts: Sequence[str | None]) -> str:
    return "\n".join(part.strip() for part in parts if part and part.strip())


def _normalize_event_option(option: EventOption) -> EventOption:
    option.trigger = normalize_block_body(option.trigger)
    option.ai_chance = normalize_block_body(option.ai_chance)
    return option


def _normalize_name_token(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def _fold_search_text(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value).strip().casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _decision_category_definition_path(mod_root: Path, decision_path: Path) -> Path:
    """Derive a category-metadata file from a decision-content file."""

    resolved = resolve_mod_output_path(mod_root, decision_path)
    decisions_root = mod_root / "common" / "decisions"
    if resolved.parent == decisions_root / "categories":
        raise ValueError(
            "Decision content path must be under common/decisions, not its categories directory"
        )
    stem = resolved.stem
    if stem.endswith("_decisions"):
        stem = stem[: -len("_decisions")]
    return decisions_root / "categories" / f"{stem}_decision_categories.txt"


def _filter_validation_errors(
    errors: list[ValidationError],
    suppress_warnings: list[str] | tuple[str, ...] | set[str] | None = None,
) -> list[ValidationError]:
    if not suppress_warnings:
        return errors
    suppress = set(suppress_warnings)
    return [
        error
        for error in errors
        if not (
            error.severity == "warning"
            and (error.code in suppress or any(token in error.message for token in suppress))
        )
    ]


_ISSUE_LOCATION_RE = re.compile(r"line (\d+), column (\d+)")
_LOCALIZATION_HEADER_RE = re.compile(r"^l_[A-Za-z_]+\s*:")


def _validate_source_tree(mod_root: Path) -> list[ValidationError]:
    """Validate script and localization files even when no model loader owns them."""

    errors: list[ValidationError] = []
    script_paths: set[Path] = set()
    for directory_name in ("common", "events", "history", "interface"):
        directory = mod_root / directory_name
        if not directory.is_dir():
            continue
        for suffix in ("*.txt", "*.gfx"):
            script_paths.update(path for path in directory.rglob(suffix) if path.is_file())
    descriptor = mod_root / "descriptor.mod"
    if descriptor.is_file():
        script_paths.add(descriptor)

    for path in sorted(script_paths):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for issue in validate_script_syntax(text):
            location = _ISSUE_LOCATION_RE.search(issue)
            errors.append(
                ValidationError(
                    message=f"{path}: {issue}",
                    severity="error",
                    code="script_syntax",
                    file_path=str(path),
                    line=int(location.group(1)) if location else None,
                    column=int(location.group(2)) if location else None,
                )
            )
        try:
            has_unsupported_dynamic_ideas = any(
                span.key == "dynamic_country_ideas"
                for span in top_level_assignments(text)
            )
        except ValueError:
            has_unsupported_dynamic_ideas = False
        if has_unsupported_dynamic_ideas:
            errors.append(
                ValidationError(
                    message=(
                        f"{path}: dynamic_country_ideas is not a HOI4 construct. "
                        "Migrate this content to common/dynamic_modifiers and use "
                        "add_dynamic_modifier/remove_dynamic_modifier effects."
                    ),
                    severity="error",
                    code="unsupported_dynamic_country_ideas",
                    file_path=str(path),
                )
            )

        relative = path.relative_to(mod_root).as_posix()
        try:
            if relative.startswith("common/characters/"):
                for characters_body, characters_start, _ in iter_assignment_blocks(
                    text, "characters"
                ):
                    body_offset = text.find("{", characters_start) + 1
                    for character_span in top_level_assignments(characters_body):
                        if (
                            not character_span.is_block
                            or character_span.body_start is None
                            or character_span.body_end is None
                        ):
                            continue
                        character_body = characters_body[
                            character_span.body_start : character_span.body_end
                        ]
                        for role_span in top_level_assignments(character_body):
                            if role_span.key != "roles":
                                continue
                            absolute = body_offset + character_span.body_start + role_span.start
                            errors.append(
                                ValidationError(
                                    message=(
                                        f"{path}: character '{character_span.key}' uses "
                                        "unsupported top-level 'roles ='. Declare "
                                        "country_leader, advisor, corps_commander, or "
                                        "another role block directly."
                                    ),
                                    severity="error",
                                    code="invalid_character_roles_key",
                                    file_path=str(path),
                                    line=text.count("\n", 0, absolute) + 1,
                                )
                            )

            if relative.startswith("common/decisions/") and not relative.startswith(
                "common/decisions/categories/"
            ):
                for category_span in top_level_assignments(text):
                    if (
                        not category_span.is_block
                        or category_span.body_start is None
                        or category_span.body_end is None
                    ):
                        continue
                    category_body = text[
                        category_span.body_start : category_span.body_end
                    ]
                    metadata = next(
                        (
                            span
                            for span in top_level_assignments(category_body)
                            if span.key in {"icon", "allowed", "visible"}
                        ),
                        None,
                    )
                    if metadata is None:
                        continue
                    absolute = category_span.body_start + metadata.start
                    errors.append(
                        ValidationError(
                            message=(
                                f"{path}: decision category '{category_span.key}' "
                                f"declares '{metadata.key}' beside its decisions. Move "
                                "category metadata to common/decisions/categories."
                            ),
                            severity="error",
                            code="invalid_decision_category_layout",
                            file_path=str(path),
                            line=text.count("\n", 0, absolute) + 1,
                        )
                    )

            for politics_body, politics_start, _ in iter_assignment_blocks(
                text, "set_politics"
            ):
                invalid = next(
                    (
                        span
                        for span in top_level_assignments(politics_body)
                        if span.key == "elections_frequency"
                    ),
                    None,
                )
                if invalid is None:
                    continue
                body_offset = text.find("{", politics_start) + 1
                errors.append(
                    ValidationError(
                        message=(
                            f"{path}: set_politics does not support "
                            "elections_frequency in current HOI4."
                        ),
                        severity="error",
                        code="invalid_set_politics_field",
                        file_path=str(path),
                        line=text.count("\n", 0, body_offset + invalid.start) + 1,
                    )
                )
        except ValueError:
            # The syntax failure above already reports malformed source.
            pass

    localization = mod_root / "localisation"
    if localization.is_dir():
        for path in sorted(localization.rglob("*.yml")):
            raw = path.read_bytes()
            if not raw.startswith(b"\xef\xbb\xbf"):
                errors.append(
                    ValidationError(
                        message=f"Localization file is missing a UTF-8 BOM: {path}",
                        severity="error",
                        code="localization_bom",
                        file_path=str(path),
                        line=1,
                    )
                )
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError as error:
                errors.append(
                    ValidationError(
                        message=f"Localization file is not valid UTF-8: {path}: {error}",
                        severity="error",
                        code="localization_encoding",
                        file_path=str(path),
                        line=1,
                    )
                )
                continue
            first_content = next(
                (
                    line.strip()
                    for line in text.splitlines()
                    if line.strip() and not line.lstrip().startswith("#")
                ),
                "",
            )
            if not _LOCALIZATION_HEADER_RE.match(first_content):
                errors.append(
                    ValidationError(
                        message=f"Localization file has no l_<language>: header: {path}",
                        severity="error",
                        code="localization_header",
                        file_path=str(path),
                        line=1,
                    )
                )
    return errors


class Mod:
    def __init__(
        self,
        mod_root: str | Path,
        hoi4_install: str | Path | None = None,
        *,
        strict_loading: bool = False,
    ):
        self.mod_root = Path(mod_root).resolve()
        self.hoi4_install = Path(hoi4_install).resolve() if hoi4_install else None
        self.strict_loading = strict_loading
        self._load_diagnostics: list[LoadDiagnostic] = []

        self._focus_trees: dict[str, FocusTree] = {}
        self._dirty_focus_trees: set[str] = set()
        self._dirty_focus_files: set[Path] = set()
        self._countries: dict[str, Country] = {}
        self._dirty_countries: set[str] = set()
        self._deleted_countries: dict[str, Country] = {}
        self._created_country_tags: set[str] = set()
        self._characters: dict[str, Character] = {}
        self._dirty_characters: set[str] = set()
        self._dirty_character_files: set[Path] = set()
        self._oobs: dict[str, OrderOfBattle] = {}
        self._dirty_oobs: set[str] = set()
        self._dirty_oob_files: set[Path] = set()
        self._states: dict[int, State] = {}
        self._state_ids: list[int] = []
        self._state_source_paths: dict[int, Path] = {}
        self._dirty_states: set[int] = set()
        self._state_history_patches: dict[int, StateHistoryPatch] = {}
        self._events: dict[str, Event] = {}
        self._event_namespaces: dict[str, Optional[str]] = {}
        self._dirty_events: set[str] = set()
        self._dirty_event_files: set[Path] = set()
        self._event_file_namespaces: dict[Path, Optional[str]] = {}
        self._on_actions: dict[str, OnAction] = {}
        self._on_action_occurrences: dict[str, list[OnAction]] = {}
        self._dirty_on_actions: set[str] = set()
        self._dirty_on_action_files: set[Path] = set()
        self._decisions: dict[str, Decision] = {}
        self._decision_categories: dict[str, DecisionCategory] = {}
        self._dirty_decision_categories: set[str] = set()
        self._dirty_decision_files: set[Path] = set()
        self._dirty_decision_category_files: set[Path] = set()
        self._ideas: dict[str, Idea] = {}
        self._dirty_ideas: set[str] = set()
        self._dirty_idea_files: set[Path] = set()
        self._idea_file_containers: dict[Path, str] = {}
        self._cached_idea_file: dict[str, Path] = {}
        self._ideologies: dict[str, Ideology] = {}
        self._vanilla_ideologies: dict[str, Ideology] = {}
        self._dirty_ideologies: set[str] = set()
        self._dirty_ideology_files: set[Path] = set()
        self._dynamic_modifiers: dict[str, DynamicModifier] = {}
        self._dynamic_modifier_sources: dict[str, Path] = {}
        self._dirty_dynamic_modifiers: set[str] = set()
        self._dirty_dynamic_modifier_files: set[Path] = set()
        self._bookmarks: list[Bookmark] = []
        self._dirty_bookmarks: set[tuple[Path, int | None]] = set()
        self._dirty_bookmark_files: set[Path] = set()
        self._bookmark_date_defines: tuple[Path, str, str] | None = None
        self._loc_entries: dict[str, str] = {}
        self._loc_sources: dict[str, Path] = {}
        self._dirty_loc_keys: set[str] = set()
        self._dirty_loc_files: set[Path] = set()
        self._original_files: dict[Path, str] = {}
        self._source_baseline: dict[Path, bytes] = {}
        self._pending_asset_writes: dict[Path, bytes] = {}
        self._pending_asset_baselines: dict[Path, bytes | None] = {}
        self._country_name_pool_updates: dict[str, str] = {}
        self._dirty: set[str] = set()
        self._vanilla_tags: set[str] = (
            load_vanilla_tags(self.hoi4_install) if self.hoi4_install else set()
        )
        self._scan_cache: dict[str, set[str]] = {}
        self._script_vocabulary_cache: GameScriptVocabulary | None = None
        self._equipment_unlock_cache: dict[str, tuple[str, ...]] | None = None
        self._sprite_texture_cache: dict[str, str] | None = None
        self._state_index_cache: dict[bool, list[dict]] = {}
        self._vanilla_state_loc_entries: dict[str, str] | None = None
        self._country_context_cache: dict[str, dict] = {}
        self._country_tag_mappings: dict[Path, dict[str, str]] = {
            base: _parse_tag_file_mapping(base / "common" / "country_tags")
            for base in (self.mod_root, self.hoi4_install)
            if base is not None
        }

        self._load()
        self._capture_source_baseline()

    @classmethod
    def from_config(
        cls,
        start: str | Path | None = None,
        *,
        strict_loading: bool = False,
    ) -> Mod:
        """Create a Mod instance from a .hoi4.json config file.

        Searches for .hoi4.json starting from `start` (defaults to cwd)
        and walks up to the filesystem root.
        """
        cfg = find_config(start)
        if cfg is None or cfg.mod_path is None:
            raise FileNotFoundError(
                "No .hoi4.json found (or mod_path not set). "
                'Create one with: {"mod_path": "/path/to/mod", "hoi4_install": "/path/to/hoi4"}'
            )
        return cls(
            cfg.mod_path,
            hoi4_install=cfg.hoi4_install,
            strict_loading=strict_loading,
        )

    def _load(self) -> None:
        self._load_countries()
        self._load_characters()
        self._load_oobs()
        self._load_states()
        self._load_focus_trees()
        self._load_events()
        self._load_on_actions()
        self._load_decisions()
        self._load_ideas()
        self._load_ideologies()
        self._load_dynamic_modifiers()
        self._load_bookmarks()
        self._load_localization()

    @property
    def load_diagnostics(self) -> tuple[LoadDiagnostic, ...]:
        """Files skipped while loading; empty means all discovered files loaded cleanly."""
        return tuple(self._load_diagnostics)

    def _record_load_error(self, section: str, path: Path, error: Exception) -> None:
        diagnostic = LoadDiagnostic(
            section=section,
            path=path,
            error_type=type(error).__name__,
            message=str(error),
        )
        self._load_diagnostics.append(diagnostic)
        if self.strict_loading:
            raise RuntimeError(
                f"Failed to load {section} file {path}: {type(error).__name__}: {error}"
            ) from error

    def _record_duplicate_identifier(
        self,
        section: str,
        identifier: str,
        first_path: Path,
        duplicate_path: Path,
    ) -> None:
        label = section.replace("_", " ")
        message = (
            f"Duplicate {label} ID '{identifier}': first defined in {first_path}; "
            f"also defined in {duplicate_path}"
        )
        diagnostic = LoadDiagnostic(
            section=section,
            path=duplicate_path,
            related_path=first_path,
            identifier=identifier,
            error_type="DuplicateIdentifierError",
            message=message,
        )
        self._load_diagnostics.append(diagnostic)
        if self.strict_loading:
            raise _DuplicateIdentifierError(message)

    def _assert_no_unmodeled_duplicates_in_file(
        self,
        path: Path,
        *,
        sections: frozenset[str],
    ) -> None:
        """Refuse a rewrite that would discard definitions skipped during loading."""

        target = path.resolve(strict=False)
        conflicts = [
            diagnostic
            for diagnostic in self._load_diagnostics
            if diagnostic.error_type == "DuplicateIdentifierError"
            and diagnostic.section in sections
            and diagnostic.path.resolve(strict=False) == target
        ]
        if not conflicts:
            return
        definitions = ", ".join(
            f"{diagnostic.section.replace('_', ' ')} "
            f"'{diagnostic.identifier or '<unknown>'}'"
            for diagnostic in conflicts
        )
        raise RuntimeError(
            f"Refusing to rewrite {path}: duplicate definitions skipped during loading "
            f"would be lost ({definitions}). Resolve the duplicate diagnostics first or "
            "reopen the mod with strict_loading=True to fail during loading."
        )

    def _load_mod_country_tag_mapping(self) -> dict[str, str]:
        tags_dir = self.mod_root / "common" / "country_tags"
        mapping: dict[str, str] = {}
        sources: dict[str, Path] = {}
        if not tags_dir.is_dir():
            return mapping
        for path in sorted(tags_dir.glob("*.txt")):
            text = path.read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                match = _COUNTRY_TAG_LINE_RE.match(line)
                if match is None:
                    continue
                tag, target = match.groups()
                if tag in sources:
                    self._record_duplicate_identifier(
                        "country_tag", tag, sources[tag], path
                    )
                    continue
                sources[tag] = path
                mapping[tag] = target
        return mapping

    def _load_countries(self) -> None:
        from .countries import _build_loc_cache

        loc_cache: dict[str, dict[str, str]] = {}
        for base in (self.hoi4_install, self.mod_root):
            if base is None:
                continue
            for tag, entries in _build_loc_cache(base).items():
                loc_cache.setdefault(tag, {}).update(entries)
        mod_mapping = self._load_mod_country_tag_mapping()
        self._country_tag_mappings[self.mod_root] = mod_mapping
        generated_tags = (
            self.mod_root
            / "common"
            / "country_tags"
            / "00_generated_tags.txt"
        )
        if generated_tags.is_file():
            self._created_country_tags.update(
                match.group(1)
                for line in generated_tags.read_text(
                    encoding="utf-8",
                    errors="ignore",
                ).splitlines()
                if (match := _COUNTRY_TAG_LINE_RE.match(line)) is not None
            )
        override_tags = set(mod_mapping)
        history_dir = self.mod_root / "history" / "countries"
        if history_dir.is_dir():
            override_tags.update(
                match.group(1)
                for path in history_dir.glob("*.txt")
                if (match := re.match(r"([A-Z0-9]{3})(?:\s+-|\b)", path.name))
                and match.group(1) in self._vanilla_tags
            )
        vanilla_mapping = (
            self._country_tag_mappings.get(self.hoi4_install, {})
            if self.hoi4_install is not None
            else {}
        )
        for tag, relative in vanilla_mapping.items():
            if (self.mod_root / "common" / relative).is_file():
                override_tags.add(tag)
        for tag in sorted(override_tags):
            try:
                self._countries[tag] = read_country(
                    self.mod_root,
                    tag,
                    self.hoi4_install,
                    _loc_cache=loc_cache,
                    _tag_mappings=self._country_tag_mappings,
                )
            except Exception as error:
                self._record_load_error("country", self.mod_root, error)
                self._countries[tag] = Country(tag=tag)
            for path in country_file_paths(self.mod_root, self._countries[tag]):
                if path.exists():
                    self._original_files.setdefault(
                        path, path.read_text(encoding="utf-8", errors="ignore")
                    )

    def _load_states(self) -> None:
        states_dir = self.mod_root / "history" / "states"
        if not states_dir.exists():
            return
        for entry in build_state_index(states_dir):
            state_id = entry["id"]
            path = Path(entry["path"])
            first_path = self._state_source_paths.get(state_id)
            if first_path is not None:
                self._record_duplicate_identifier(
                    "state", str(state_id), first_path, path
                )
                continue
            self._state_source_paths[state_id] = path
            self._state_ids.append(state_id)

    def _load_characters(self) -> None:
        directory = self.mod_root / "common" / "characters"
        if not directory.is_dir():
            return
        for path in sorted(directory.glob("*.txt")):
            try:
                for character in load_characters_file(path):
                    existing = self._characters.get(character.id)
                    if existing is not None:
                        self._record_duplicate_identifier(
                            "character",
                            character.id,
                            existing.path or path,
                            character.path or path,
                        )
                        continue
                    self._characters[character.id] = character
                self._original_files[path] = path.read_text(
                    encoding="utf-8", errors="ignore"
                )
            except _DuplicateIdentifierError:
                raise
            except Exception as error:
                self._record_load_error("character", path, error)

    def _load_oobs(self) -> None:
        directory = self.mod_root / "history" / "units"
        if not directory.is_dir():
            return
        assigned: dict[str, tuple[str, OOBReference]] = {}
        for country in self._countries.values():
            for reference in self._country_oob_references(country):
                assigned.setdefault(reference.name, (country.tag, reference))
        for path in sorted(directory.glob("*.txt")):
            try:
                assignment = assigned.get(path.stem)
                oob = load_oob_file(
                    path,
                    name=path.stem,
                    country_tag=assignment[0] if assignment is not None else "",
                    kind=assignment[1].kind if assignment is not None else None,
                    required_dlc=(
                        assignment[1].required_dlc if assignment is not None else ()
                    ),
                    excluded_dlc=(
                        assignment[1].excluded_dlc if assignment is not None else ()
                    ),
                )
                self._oobs[oob.name] = oob
                self._original_files[path] = path.read_text(
                    encoding="utf-8-sig", errors="ignore"
                )
            except Exception as error:
                self._record_load_error("oob", path, error)

    def _load_focus_trees(self) -> None:
        focus_dir = self.mod_root / "common" / "national_focus"
        if not focus_dir.exists():
            return
        for f in sorted(focus_dir.glob("*.txt")):
            try:
                trees = load_focus_trees(f)
                # Preserve non-focus companion files too. A subsequently
                # created tree may legitimately target the same file and must
                # append to, not replace, its unmodeled declarations.
                self._original_files[f] = f.read_text(encoding="utf-8", errors="ignore")
                if not trees:
                    continue
                for tree in trees:
                    focus_sources: dict[str, Path] = {}
                    for focus in tree.focuses:
                        first_path = focus_sources.get(focus.id)
                        if first_path is not None:
                            self._record_duplicate_identifier(
                                "focus", focus.id, first_path, tree.path or f
                            )
                            continue
                        focus_sources[focus.id] = tree.path or f
                    existing = self._focus_trees.get(tree.id)
                    if existing is not None:
                        self._record_duplicate_identifier(
                            "focus_tree",
                            tree.id,
                            existing.path or f,
                            tree.path or f,
                        )
                        continue
                    self._focus_trees[tree.id] = tree
            except _DuplicateIdentifierError:
                raise
            except Exception as error:
                self._record_load_error("focus", f, error)
                continue

    def _load_localization(self) -> None:
        loc_dir = self.mod_root / "localisation" / "english"
        if not loc_dir.exists():
            return
        self._loc_entries, self._loc_sources = parse_localization_dir(loc_dir)
        # Keep every localization source, including header/comment-only files.
        # Otherwise adding the first modeled key to such a file would serialize
        # from an empty baseline and silently discard its existing content.
        for path in loc_dir.rglob("*.yml"):
            if path.is_file():
                self._original_files[path] = path.read_text(
                    encoding="utf-8-sig", errors="ignore"
                )

    def _load_events(self) -> None:
        events_dir = self.mod_root / "events"
        if not events_dir.exists():
            return
        for f in sorted(events_dir.glob("*.txt")):
            try:
                namespace, events = load_events_file(f)
                self._event_file_namespaces[f] = namespace
                for event in events:
                    existing = self._events.get(event.id)
                    if existing is not None:
                        self._record_duplicate_identifier(
                            "event",
                            event.id,
                            existing.path or f,
                            event.path or f,
                        )
                        continue
                    self._events[event.id] = event
                    self._event_namespaces[event.id] = namespace
                self._original_files[f] = f.read_text(encoding="utf-8", errors="ignore")
            except _DuplicateIdentifierError:
                raise
            except Exception as error:
                self._record_load_error("event", f, error)
                continue

    def _load_on_actions(self) -> None:
        on_actions_dir = self.mod_root / "common" / "on_actions"
        if not on_actions_dir.exists():
            return
        for f in sorted(on_actions_dir.glob("*.txt")):
            try:
                for action in load_on_actions_file(f):
                    # Unlike most Paradox objects, on-actions are hooks.  The
                    # game composes repeated definitions across files (and
                    # even repeated definitions in one file), so retain every
                    # occurrence instead of diagnosing or dropping duplicates.
                    self._on_action_occurrences.setdefault(action.id, []).append(action)
                    self._on_actions.setdefault(action.id, action)
                self._original_files[f] = f.read_text(encoding="utf-8", errors="ignore")
            except Exception as error:
                self._record_load_error("on_action", f, error)
                continue

    def _load_decisions(self) -> None:
        decisions_dir = self.mod_root / "common" / "decisions"
        if not decisions_dir.exists():
            return
        category_sources: dict[str, Path] = {}
        categories_dir = decisions_dir / "categories"
        if categories_dir.is_dir():
            for f in sorted(categories_dir.glob("*.txt")):
                try:
                    for category in load_decision_categories_file(f):
                        first_path = category_sources.get(category.id)
                        if first_path is not None:
                            self._record_duplicate_identifier(
                                "decision_category",
                                category.id,
                                first_path,
                                category.definition_path or f,
                            )
                            continue
                        category_sources[category.id] = category.definition_path or f
                        self._decision_categories[category.id] = category
                    self._original_files[f] = f.read_text(
                        encoding="utf-8", errors="ignore"
                    )
                except _DuplicateIdentifierError:
                    raise
                except Exception as error:
                    self._record_load_error("decision_category", f, error)
                    continue
        for f in sorted(decisions_dir.glob("*.txt")):
            try:
                categories = load_decisions_file(f)
                file_category_sources: dict[str, Path] = {}
                for category in categories:
                    first_category_path = file_category_sources.get(category.id)
                    if first_category_path is not None:
                        self._record_duplicate_identifier(
                            "decision_category",
                            category.id,
                            first_category_path,
                            category.path or f,
                        )
                        continue
                    file_category_sources[category.id] = category.path or f
                    unique_decisions: list[Decision] = []
                    pending_sources: dict[str, Path] = {}
                    for decision in category.decisions:
                        existing = self._decisions.get(decision.id)
                        pending_source = pending_sources.get(decision.id)
                        if existing is not None or pending_source is not None:
                            first_path = (
                                existing.path or f
                                if existing is not None
                                else cast(Path, pending_source)
                            )
                            self._record_duplicate_identifier(
                                "decision",
                                decision.id,
                                first_path,
                                decision.path or f,
                            )
                            continue
                        unique_decisions.append(decision)
                        pending_sources[decision.id] = decision.path or f
                    for decision in unique_decisions:
                        self._decisions[decision.id] = decision
                    category.decisions = unique_decisions
                    definition = self._decision_categories.get(category.id)
                    if definition is not None:
                        category.icon = definition.icon
                        category.allowed = definition.allowed
                        category.visible = definition.visible
                        category.definition_path = definition.definition_path
                        category.definition_raw_block = (
                            definition.definition_raw_block
                        )
                    else:
                        category.definition_path = _decision_category_definition_path(
                            self.mod_root, category.path or f
                        )
                    self._decision_categories[category.id] = category
                self._original_files[f] = f.read_text(encoding="utf-8", errors="ignore")
            except _DuplicateIdentifierError:
                raise
            except Exception as error:
                self._record_load_error("decision", f, error)
                continue

    def _load_ideas(self) -> None:
        dirs = [
            self.mod_root / "common" / "national_ideas",
            self.mod_root / "common" / "ideas",
        ]
        for ideas_dir in dirs:
            if not ideas_dir.exists():
                continue
            for f in sorted(ideas_dir.glob("*.txt")):
                try:
                    ideas, container = read_ideas_file(f)
                    for idea in ideas:
                        existing = self._ideas.get(idea.id)
                        if existing is not None:
                            self._record_duplicate_identifier(
                                "idea",
                                idea.id,
                                existing.path or f,
                                idea.path or f,
                            )
                            continue
                        self._ideas[idea.id] = idea
                    self._idea_file_containers[f] = container
                    self._original_files[f] = f.read_text(encoding="utf-8", errors="ignore")
                except _DuplicateIdentifierError:
                    raise
                except Exception as error:
                    self._record_load_error("idea", f, error)
                    continue

        self._match_idea_files_to_countries()

    def _load_ideologies(self) -> None:
        if self.hoi4_install is not None:
            directory = self.hoi4_install / "common" / "ideologies"
            if directory.is_dir():
                for path in sorted(directory.glob("*.txt")):
                    try:
                        for ideology in load_ideologies_file(path, is_vanilla=True):
                            self._vanilla_ideologies[ideology.id] = ideology
                    except Exception:
                        # Vanilla is reference data; malformed or version-specific files
                        # must not make an otherwise valid mod impossible to open.
                        continue

        directory = self.mod_root / "common" / "ideologies"
        if not directory.is_dir():
            return
        for path in sorted(directory.glob("*.txt")):
            try:
                for ideology in load_ideologies_file(path):
                    existing = self._ideologies.get(ideology.id)
                    if existing is not None:
                        self._record_duplicate_identifier(
                            "ideology",
                            ideology.id,
                            existing.path or path,
                            ideology.path or path,
                        )
                        continue
                    self._ideologies[ideology.id] = ideology
                self._original_files[path] = path.read_text(encoding="utf-8", errors="ignore")
            except _DuplicateIdentifierError:
                raise
            except Exception as error:
                self._record_load_error("ideology", path, error)

    def _load_dynamic_modifiers(self) -> None:
        directory = self.mod_root / "common" / "dynamic_modifiers"
        if not directory.is_dir():
            return
        for path in sorted(directory.glob("*.txt")):
            try:
                self._original_files[path] = path.read_text(
                    encoding="utf-8", errors="ignore"
                )
                for modifier in load_dynamic_modifiers_file(path):
                    existing = self._dynamic_modifiers.get(modifier.id)
                    if existing is not None:
                        self._record_duplicate_identifier(
                            "dynamic_modifier",
                            modifier.id,
                            existing.path or path,
                            modifier.path or path,
                        )
                        continue
                    self._dynamic_modifiers[modifier.id] = modifier
                    self._dynamic_modifier_sources[modifier.id] = (
                        modifier.path or path
                    )
            except _DuplicateIdentifierError:
                raise
            except Exception as error:
                self._record_load_error("dynamic_modifier", path, error)

    def _load_bookmarks(self) -> None:
        directory = self.mod_root / "common" / "bookmarks"
        if not directory.is_dir():
            return
        sources: dict[str, Path] = {}
        for path in sorted(directory.glob("*.txt")):
            try:
                for bookmark in load_bookmarks_file(path):
                    first_path = sources.get(bookmark.name)
                    if first_path is not None:
                        self._record_duplicate_identifier(
                            "bookmark", bookmark.name, first_path, bookmark.path or path
                        )
                        continue
                    sources[bookmark.name] = bookmark.path or path
                    self._bookmarks.append(bookmark)
                self._original_files[path] = path.read_text(encoding="utf-8", errors="ignore")
            except _DuplicateIdentifierError:
                raise
            except Exception as error:
                self._record_load_error("bookmark", path, error)

    def _match_idea_files_to_countries(self) -> None:
        ideas_dir = self.mod_root / "common" / "ideas"
        if not ideas_dir.exists():
            return
        for f in ideas_dir.glob("*.txt"):
            stem = f.stem.lower()
            for tag, country in self._countries.items():
                name_lower = country.name.lower()
                if stem == name_lower or stem == name_lower.replace(" ", "_"):
                    self._cached_idea_file[tag] = f
                    break

    @property
    def default_loc_file(self) -> Path:
        return self.mod_root / "localisation" / "english" / "mod_l_english.yml"

    # ── Countries ────────────────────────────────────────────────

    def list_countries(self) -> list[str]:
        return sorted(self._countries.keys())

    def is_country_tag_available(self, tag: str) -> bool:
        tag = tag.upper()
        return tag not in self._countries and tag not in self._vanilla_tags

    def country_tag_conflicts(self, tag: str) -> list[str]:
        tag = tag.upper()
        conflicts: list[str] = []
        if tag in self._countries:
            country = self._countries[tag]
            label = country.name or tag
            conflicts.append(f"mod: {tag} ({label})")
        if tag in self._vanilla_tags:
            country = read_country(self.mod_root, tag, self.hoi4_install)
            label = country.name or tag
            conflicts.append(f"vanilla: {tag} ({label})")
        return conflicts

    def suggest_tag(self, name: str) -> str:
        suggestions = self.suggest_tags(name, count=1)
        if not suggestions:
            raise ValueError("No available 3-character country tags remain")
        return suggestions[0]

    def suggest_tags(self, name: str, count: int = 5) -> list[str]:
        taken = set(self._countries) | self._vanilla_tags
        token = _normalize_name_token(name)
        candidates: list[str] = []

        def add(candidate: str) -> None:
            candidate = _normalize_name_token(candidate)[:3]
            if len(candidate) == 3 and candidate not in candidates and candidate not in taken:
                candidates.append(candidate)

        if len(token) >= 3:
            add(token[:3])
            consonants = "".join(ch for ch in token if ch not in "AEIOU")
            if len(consonants) >= 3:
                add(consonants[:3])
            add(token[0] + token[1] + token[-1])
            add(token[0] + token[-2:])
            for i in range(1, len(token) - 1):
                add(token[0] + token[i] + token[-1])
            for i in range(len(token) - 2):
                add(token[i : i + 3])

        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        prefix = token[:1] or "X"
        for second in alphabet:
            for third in alphabet:
                add(prefix + second + third)
                if len(candidates) >= count:
                    return candidates[:count]
        for first in alphabet:
            for second in alphabet:
                for third in alphabet:
                    add(first + second + third)
                    if len(candidates) >= count:
                        return candidates[:count]
        return candidates[:count]

    def get_country(self, tag: str) -> Country:
        tag = require_country_tag(tag)
        if tag in self._countries:
            return self._countries[tag]
        country = read_country(
            self.mod_root,
            tag,
            self.hoi4_install,
            _tag_mappings=self._country_tag_mappings,
        )
        self._countries[tag] = country
        for path in country_file_paths(self.mod_root, country):
            if path.exists():
                self._original_files.setdefault(
                    path, path.read_text(encoding="utf-8", errors="ignore")
                )
                if path.resolve(strict=False).is_relative_to(self.mod_root):
                    self._source_baseline.setdefault(
                        path.resolve(strict=False), path.read_bytes()
                    )
        return country

    def create_country(
        self,
        tag: str,
        name: str,
        adjective: str = "",
        color: tuple[int, int, int] = (128, 128, 128),
        capital: int = 1,
        research_slots: int | None = None,
        ruling_party: str = "democratic",
        elections_allowed: bool = True,
        popularities: dict[str, int] | None = None,
        leader_name: str = "Leader",
        leader_ideology: str | None = None,
        ideas: list[str] | None = None,
        stability: str | int | float | None = None,
        war_support: str | int | float | None = None,
        technologies: dict[str, int] | None = None,
        oob: str = "",
        overwrite: bool = False,
        allow_vanilla_override: bool = False,
    ) -> Country:
        tag = require_country_tag(tag)
        existing = self._countries.get(tag)
        if existing is not None and not overwrite:
            raise ValueError(
                f"Country tag '{tag}' already exists in the mod. "
                "Use overwrite=True only when intentionally replacing that mod country."
            )
        if existing is None and not overwrite:
            orphan_candidates = [
                self.mod_root / "common" / "countries" / f"{tag}.txt",
                self.mod_root / "common" / "characters" / f"{tag}_characters.txt",
                self.mod_root / "common" / "characters" / f"{tag}.txt",
            ]
            history_dir = self.mod_root / "history" / "countries"
            if history_dir.is_dir():
                orphan_candidates.extend(sorted(history_dir.glob(f"{tag}*.txt")))
            conflicts = [path for path in orphan_candidates if path.exists()]
            if conflicts:
                displayed = ", ".join(self._display_path(path) for path in conflicts)
                raise FileExistsError(
                    f"Country tag '{tag}' is not registered, but country files already "
                    f"exist and would be overwritten: {displayed}. Pass overwrite=True "
                    "only if replacing those orphaned files is intentional."
                )
        if tag in self._vanilla_tags and not allow_vanilla_override:
            raise ValueError(
                f"Country tag '{tag}' is already used by vanilla HOI4. "
                "Choose an unused tag or pass allow_vanilla_override=True intentionally."
            )
        if leader_ideology is None:
            leader_ideology = self._default_leader_ideology_for_party(ruling_party)

        def preserved_mod_path(path: Path | None) -> Path | None:
            if path is None:
                return None
            resolved = path.resolve(strict=False)
            if resolved.is_relative_to(self.mod_root):
                return resolved
            if self.hoi4_install is not None and resolved.is_relative_to(self.hoi4_install):
                return resolve_mod_output_path(
                    self.mod_root,
                    resolved.relative_to(self.hoi4_install),
                )
            return None

        leader = Leader(
            name=leader_name,
            character_id=f"{tag}_leader_1",
            ideology=leader_ideology,
            portrait_slug="leader_1",
        )
        country = Country(
            tag=tag,
            name=name,
            adjective=adjective or name,
            color=color,
            capital=capital,
            research_slots=research_slots,
            ruling_party=ruling_party,
            elections_allowed=elections_allowed,
            popularities=popularities or {},
            leader=leader,
            ideas=ideas or [],
            stability=stability,
            war_support=war_support,
            technologies=dict(technologies or {}),
            oob=oob,
            recruited_characters=[leader.character_id],
            definition_path=(
                preserved_mod_path(existing.definition_path) if existing is not None else None
            ),
            history_path=(
                preserved_mod_path(existing.history_path) if existing is not None else None
            ),
            character_path=(
                preserved_mod_path(existing.character_path) if existing is not None else None
            ),
            touched_fields={"*"},
        )
        self._countries[tag] = country
        self._created_country_tags.add(tag)
        self._dirty.add("countries")
        self._dirty_countries.add(tag)
        self._sync_country_loc(country)
        leader_character = Character(
            id=leader.character_id,
            country_tag=tag,
            name=leader.name,
            portraits=[
                CharacterPortrait(
                    channel="civilian",
                    large=f"GFX_portrait_{tag}_{leader.portrait_slug}",
                )
            ],
            roles=[CountryLeaderRole(ideology=leader.ideology)],
        )
        self.create_character(
            tag,
            leader_character,
            recruit=False,
            overwrite=overwrite,
        )

        ideas_dir = self.mod_root / "common" / "ideas"
        if ideas_dir.exists():
            name_lower = safe_file_stem(name.lower(), fallback=tag.lower())
            candidates = [
                name_lower,
                tag.lower(),
            ]
            for stem in candidates:
                p = ideas_dir / f"{stem}.txt"
                if p.exists():
                    self._cached_idea_file[tag] = p
                    break

        return country

    def update_country(self, tag: str, **kwargs) -> bool:
        try:
            tag = require_country_tag(tag)
        except ValueError:
            return False
        country = self._countries.get(tag)
        if country is None and tag in self._vanilla_tags:
            country = self.get_country(tag)
        if country is None:
            return False
        self._materialize_country_override(country)
        leader_kwargs = {}
        other_kwargs = {}
        for key, value in kwargs.items():
            if key.startswith("leader_"):
                if key == "leader_character_id":
                    raise ValueError("Leader character IDs are immutable")
                leader_kwargs[key.removeprefix("leader_")] = value
            else:
                other_kwargs[key] = value
        if other_kwargs:
            _set_fields(country, other_kwargs)
        if leader_kwargs:
            if country.leader is None:
                country.leader = Leader(name="Leader", character_id=f"{tag}_leader_1")
            _set_fields(country.leader, leader_kwargs)
        country.touched_fields.update(other_kwargs)
        country.touched_fields.update(f"leader_{key}" for key in leader_kwargs)
        if {"name", "adjective", "popularities", "ruling_party"} & set(
            other_kwargs
        ) or leader_kwargs:
            self._sync_country_loc(country)
        if leader_kwargs and country.leader is not None:
            self._sync_leader_character_model(country)
        self._dirty.add("countries")
        self._dirty_countries.add(tag)
        return True

    def _sync_leader_character_model(self, country: Country) -> None:
        leader = country.leader
        if leader is None:
            return
        character = self._characters.get(leader.character_id)
        if character is None:
            return
        self._materialize_character_override(character)
        character.name = leader.name
        portrait_slug = (
            leader.portrait_slug
            or leader.character_id.removeprefix(country.tag + "_")
        )
        character.portraits = [
            CharacterPortrait(
                channel="civilian",
                large=f"GFX_portrait_{country.tag}_{portrait_slug}",
            )
        ]
        character.touched_fields.update({"name", "portraits"})
        roles = [
            role
            for role in character.roles
            if isinstance(role, CountryLeaderRole)
        ]
        if len(roles) == 1:
            roles[0].ideology = leader.ideology
            roles[0].touched_fields.add("ideology")
            character.touched_fields.add("roles")
        self._mark_character_dirty(character)

    # ── Characters ──────────────────────────────────────────────

    def list_characters(
        self,
        tag: str | None = None,
        *,
        include_vanilla: bool = True,
    ) -> list[str]:
        normalized = require_country_tag(tag) if tag is not None else None
        if include_vanilla and self.hoi4_install is not None:
            self._load_vanilla_characters(normalized)
        return sorted(
            character_id
            for character_id, character in self._characters.items()
            if normalized is None or character.country_tag == normalized
        )

    def get_character(
        self,
        character_id: str,
        *,
        include_vanilla: bool = True,
    ) -> Character:
        character_id = require_script_id(character_id, label="character ID")
        character = self._characters.get(character_id)
        if character is not None:
            return character
        if include_vanilla and self.hoi4_install is not None:
            prefix = character_id.split("_", 1)[0]
            tag = (
                prefix
                if len(prefix) == 3 and prefix.isalnum() and prefix.upper() == prefix
                else None
            )
            self._load_vanilla_characters(tag)
            character = self._characters.get(character_id)
        if character is None:
            raise KeyError(f"Character '{character_id}' not found")
        return character

    def create_character(
        self,
        tag: str,
        character: Character,
        *,
        recruit: bool = True,
        overwrite: bool = False,
        path: str | Path | None = None,
    ) -> Character:
        tag = require_country_tag(tag)
        character_id = require_script_id(character.id, label="character ID")
        if recruit and not self._is_known_country_tag(tag):
            raise KeyError(
                f"Cannot recruit character '{character_id}': country '{tag}' "
                "is not defined. Create or load the country first, or pass "
                "recruit=False for an event-unlocked definition."
            )
        existing = self._characters.get(character_id)
        if existing is not None and not overwrite:
            raise ValueError(
                f"Character '{character_id}' already exists. "
                "Use overwrite=True only for deliberate replacement."
            )
        target = resolve_mod_output_path(
            self.mod_root,
            path
            or (
                existing.path
                if existing is not None
                and existing.path is not None
                and existing.path.resolve(strict=False).is_relative_to(self.mod_root)
                else Path("common") / "characters" / f"{tag}_characters.txt"
            ),
        )
        replacement = copy.deepcopy(character)
        replacement.id = character_id
        replacement.country_tag = tag
        replacement.path = target
        replacement.recruitment_expected = recruit
        if existing is not None:
            replacement.source_index = existing.source_index
            replacement.raw_block = existing.raw_block
            replacement.touched_fields.update(
                {"name", "portraits", "roles", "instances"}
            )
        else:
            replacement.source_index = -1
        self._prepare_character_roles(replacement, tag)
        self._characters[character_id] = replacement
        self._dirty_characters.add(character_id)
        self._dirty_character_files.add(target)
        self._dirty.add("characters")
        if recruit:
            self._set_character_recruited(tag, character_id, recruited=True)
        self._sync_character_loc(replacement)
        return replacement

    def update_character(self, character_id: str, **kwargs: object) -> bool:
        try:
            character = self.get_character(character_id)
        except (KeyError, ValueError):
            return False
        unknown = set(kwargs) - {"name", "portraits", "country_tag"}
        if unknown:
            raise TypeError(
                f"Unknown character fields: {sorted(unknown)}. "
                "Use the explicit instance and role mutation methods for nested content."
            )
        self._materialize_character_override(character)
        for key, value in kwargs.items():
            setattr(character, key, copy.deepcopy(value))
        character.touched_fields.update(kwargs)
        self._dirty_characters.add(character.id)
        if character.path is not None:
            self._dirty_character_files.add(character.path)
        self._dirty.add("characters")
        self._sync_character_loc(character)
        return True

    def delete_character(
        self,
        character_id: str,
        *,
        remove_recruitment: bool = True,
    ) -> bool:
        try:
            character = self.get_character(character_id, include_vanilla=False)
        except (KeyError, ValueError):
            return False
        self._materialize_character_override(character)
        self._characters.pop(character.id, None)
        if character.path is not None:
            self._dirty_character_files.add(character.path)
        self._dirty_characters.add(character.id)
        self._dirty.add("characters")
        if remove_recruitment and character.country_tag:
            self._set_character_recruited(
                character.country_tag,
                character.id,
                recruited=False,
            )
        self.delete_loc(character.id)
        self.delete_loc(f"{character.id}_desc")
        return True

    def add_character_instance(
        self,
        character_id: str,
        instance: CharacterInstance,
    ) -> CharacterInstance:
        character = self.get_character(character_id)
        self._materialize_character_override(character)
        new_instance = copy.deepcopy(instance)
        new_instance.source_occurrence = -1
        self._prepare_roles(
            new_instance.roles,
            character.country_tag,
            character.id,
        )
        character.instances.append(new_instance)
        character.touched_fields.add("instances")
        self._mark_character_dirty(character)
        return new_instance

    def update_character_instance(
        self,
        character_id: str,
        occurrence: int,
        **kwargs: object,
    ) -> bool:
        character = self.get_character(character_id)
        if occurrence < 0 or occurrence >= len(character.instances):
            return False
        unknown = set(kwargs) - {"allowed", "name", "portraits"}
        if unknown:
            raise TypeError(
                f"Unknown character instance fields: {sorted(unknown)}. "
                "Use role mutation methods for instance roles."
            )
        self._materialize_character_override(character)
        instance = character.instances[occurrence]
        for key, value in kwargs.items():
            setattr(instance, key, copy.deepcopy(value))
        instance.touched_fields.update(kwargs)
        character.touched_fields.add("instances")
        self._mark_character_dirty(character)
        return True

    def delete_character_instance(
        self,
        character_id: str,
        occurrence: int,
    ) -> bool:
        character = self.get_character(character_id)
        if occurrence < 0 or occurrence >= len(character.instances):
            return False
        self._materialize_character_override(character)
        character.instances.pop(occurrence)
        character.touched_fields.add("instances")
        self._mark_character_dirty(character)
        return True

    def add_character_role(
        self,
        character_id: str,
        role: CharacterRole,
        *,
        instance_occurrence: int | None = None,
    ) -> CharacterRole:
        character = self.get_character(character_id)
        self._materialize_character_override(character)
        roles, scope = self._character_role_scope(character, instance_occurrence)
        new_role = copy.deepcopy(role)
        new_role.source_occurrence = -1
        self._prepare_roles([new_role], character.country_tag, character.id)
        roles.append(new_role)
        scope.touched_fields.add("roles")
        if isinstance(scope, CharacterInstance):
            character.touched_fields.add("instances")
        self._mark_character_dirty(character)
        return new_role

    def update_character_role(
        self,
        character_id: str,
        role_type: str,
        *,
        occurrence: int | None = None,
        instance_occurrence: int | None = None,
        **kwargs: object,
    ) -> bool:
        if role_type not in {
            "country_leader",
            "advisor",
            "corps_commander",
            "field_marshal",
            "navy_leader",
        }:
            raise ValueError(f"Unsupported character role type: {role_type}")
        character = self.get_character(character_id)
        roles, scope = self._character_role_scope(character, instance_occurrence)
        matches = [role for role in roles if role_key(role) == role_type]
        if not matches:
            return False
        if occurrence is None:
            if len(matches) != 1:
                raise ValueError(
                    f"Character '{character_id}' has {len(matches)} '{role_type}' "
                    "roles; pass occurrence= to select one."
                )
            role = matches[0]
        else:
            if occurrence < 0 or occurrence >= len(matches):
                return False
            role = matches[occurrence]
        self._materialize_character_override(character)
        internal = {"raw_block", "touched_fields", "source_occurrence", "kind"}
        valid = {
            field.name for field in dataclasses.fields(role)
        } - internal
        unknown = set(kwargs) - valid
        if unknown:
            raise TypeError(
                f"Unknown {role_type} fields: {sorted(unknown)}. "
                f"Valid: {sorted(valid)}"
            )
        for key, value in kwargs.items():
            setattr(role, key, copy.deepcopy(value))
        role.touched_fields.update(kwargs)
        scope.touched_fields.add("roles")
        if isinstance(scope, CharacterInstance):
            character.touched_fields.add("instances")
        self._mark_character_dirty(character)
        return True

    def remove_character_role(
        self,
        character_id: str,
        role_type: str,
        *,
        occurrence: int | None = None,
        instance_occurrence: int | None = None,
    ) -> bool:
        character = self.get_character(character_id)
        roles, scope = self._character_role_scope(character, instance_occurrence)
        indexes = [
            index for index, role in enumerate(roles) if role_key(role) == role_type
        ]
        if not indexes:
            return False
        if occurrence is None:
            if len(indexes) != 1:
                raise ValueError(
                    f"Character '{character_id}' has {len(indexes)} '{role_type}' "
                    "roles; pass occurrence= to select one."
                )
            index = indexes[0]
        else:
            if occurrence < 0 or occurrence >= len(indexes):
                return False
            index = indexes[occurrence]
        self._materialize_character_override(character)
        roles.pop(index)
        scope.touched_fields.add("roles")
        if isinstance(scope, CharacterInstance):
            character.touched_fields.add("instances")
        self._mark_character_dirty(character)
        return True

    def _character_role_scope(
        self,
        character: Character,
        instance_occurrence: int | None,
    ) -> tuple[
        list[CharacterRole],
        Character | CharacterInstance,
    ]:
        if instance_occurrence is None:
            return character.roles, character
        if instance_occurrence < 0 or instance_occurrence >= len(character.instances):
            raise IndexError(
                f"Character '{character.id}' has no instance occurrence "
                f"{instance_occurrence}"
            )
        instance = character.instances[instance_occurrence]
        return instance.roles, instance

    def _prepare_character_roles(self, character: Character, tag: str) -> None:
        self._prepare_roles(character.roles, tag, character.id)
        for instance in character.instances:
            self._prepare_roles(instance.roles, tag, character.id)

    @staticmethod
    def _prepare_roles(
        roles: list[CharacterRole],
        tag: str,
        character_id: str = "",
    ) -> None:
        occurrences: dict[str, int] = {}
        for role in roles:
            if isinstance(role, AdvisorRole):
                if not role.idea_token:
                    role.idea_token = character_id
                if not role.allowed and tag:
                    role.allowed = f"original_tag = {tag}"
            key = role_key(role)
            if role.source_occurrence < 0 and role.raw_block:
                role.source_occurrence = occurrences.get(key, 0)
            occurrences[key] = occurrences.get(key, 0) + 1

    def _set_character_recruited(
        self,
        tag: str,
        character_id: str,
        *,
        recruited: bool,
    ) -> None:
        country = self.get_country(tag)
        self._materialize_country_override(country)
        values = list(country.recruited_characters)
        if country.leader and country.leader.character_id not in values:
            values.append(country.leader.character_id)
        if recruited and character_id not in values:
            values.append(character_id)
        if not recruited:
            values = [value for value in values if value != character_id]
        country.recruited_characters = values
        country.touched_fields.add("recruited_characters")
        self._dirty_countries.add(tag)
        self._dirty.add("countries")

    def _mark_character_dirty(self, character: Character) -> None:
        self._dirty_characters.add(character.id)
        if character.path is not None:
            self._dirty_character_files.add(character.path)
        self._dirty.add("characters")

    def _materialize_character_override(self, character: Character) -> None:
        source = character.path
        if source is None:
            target = self.mod_root / "common" / "characters" / (
                f"{character.country_tag or 'mod'}_characters.txt"
            )
            character.path = target
            self._dirty_character_files.add(target)
            return
        if source.resolve(strict=False).is_relative_to(self.mod_root):
            return
        if self.hoi4_install is None:
            raise ValueError(
                f"Cannot retarget character '{character.id}' without a HOI4 install"
            )
        relative = source.resolve(strict=False).relative_to(self.hoi4_install)
        target = resolve_mod_output_path(self.mod_root, relative)
        source_text = source.read_text(encoding="utf-8", errors="ignore")
        for item in load_characters_file(source, country_tag=character.country_tag):
            cached = self._characters.get(item.id)
            if cached is not None and cached.path == source:
                cached.path = target
        self._original_files.setdefault(
            target,
            self._read_current_text(target) or source_text,
        )
        character.path = target
        self._dirty_character_files.add(target)

    def _load_vanilla_characters(self, tag: str | None) -> None:
        if self.hoi4_install is None:
            return
        directory = self.hoi4_install / "common" / "characters"
        if not directory.is_dir():
            return
        if tag is None:
            paths = sorted(directory.glob("*.txt"))
        else:
            preferred = [
                directory / f"{tag}.txt",
                directory / f"{tag}_characters.txt",
            ]
            paths = [path for path in preferred if path.is_file()]
            if not paths:
                paths = sorted(directory.glob(f"{tag}*.txt"))
        for path in paths:
            cache_key = f"character_file:{path}"
            if cache_key in self._scan_cache:
                continue
            try:
                for character in load_characters_file(path, country_tag=tag or ""):
                    self._characters.setdefault(character.id, character)
            except (OSError, ValueError):
                pass
            self._scan_cache[cache_key] = set()

    # ── Land orders of battle ───────────────────────────────────

    def list_oobs(self, *, include_vanilla: bool = False) -> list[str]:
        if include_vanilla and self.hoi4_install is not None:
            directory = self.hoi4_install / "history" / "units"
            if directory.is_dir():
                return sorted(
                    set(self._oobs)
                    | {path.stem for path in directory.glob("*.txt")}
                )
        return sorted(self._oobs)

    def get_oob(
        self,
        name: str,
        *,
        include_vanilla: bool = True,
    ) -> OrderOfBattle:
        name = require_script_id(name, label="OOB name")
        cached = self._oobs.get(name)
        if cached is not None:
            return cached
        candidates = [self.mod_root / "history" / "units" / f"{name}.txt"]
        if include_vanilla and self.hoi4_install is not None:
            candidates.append(
                self.hoi4_install / "history" / "units" / f"{name}.txt"
            )
        country_tag = next(
            (
                country.tag
                for country in self._countries.values()
                if any(
                    reference.name == name
                    for reference in self._country_oob_references(country)
                )
            ),
            "",
        )
        reference = next(
            (
                reference
                for country in self._countries.values()
                for reference in self._country_oob_references(country)
                if reference.name == name
            ),
            None,
        )
        for path in candidates:
            if not path.is_file():
                continue
            oob = load_oob_file(
                path,
                name=name,
                country_tag=country_tag,
                kind=reference.kind if reference is not None else None,
                required_dlc=(reference.required_dlc if reference is not None else ()),
                excluded_dlc=(reference.excluded_dlc if reference is not None else ()),
            )
            self._oobs[name] = oob
            if path.resolve(strict=False).is_relative_to(self.mod_root):
                self._original_files.setdefault(
                    path,
                    path.read_text(encoding="utf-8-sig", errors="ignore"),
                )
            return oob
        raise KeyError(f"OOB '{name}' not found")

    def create_oob(
        self,
        name: str,
        country_tag: str,
        *,
        templates: Sequence[DivisionTemplate] = (),
        divisions: Sequence[DivisionUnit] = (),
        fleets: Sequence[Fleet] = (),
        air_wings: Sequence[AirWing] = (),
        kind: Literal["auto", "land", "naval", "air"] = "auto",
        required_dlc: Sequence[str] = (),
        excluded_dlc: Sequence[str] = (),
        assign: bool = True,
        overwrite: bool = False,
        path: str | Path | None = None,
    ) -> OrderOfBattle:
        name = require_script_id(name, label="OOB name")
        country_tag = require_country_tag(country_tag)
        existing = self._oobs.get(name)
        if existing is not None and not overwrite:
            raise ValueError(
                f"OOB '{name}' already exists. Use overwrite=True only for "
                "deliberate replacement."
            )
        target = resolve_mod_output_path(
            self.mod_root,
            path or Path("history") / "units" / f"{name}.txt",
        )
        inferred_kind = _oob_content_kind(
            templates,
            divisions,
            fleets,
            air_wings,
        )
        if inferred_kind == "mixed":
            raise ValueError(
                "New OOBs cannot mix land, naval, and air content. Split the "
                "content into separate create_oob() calls."
            )
        selected_kind: OOBKind = (
            inferred_kind or "land"
            if kind == "auto"
            else cast(OOBKind, kind)
        )
        if inferred_kind is not None and selected_kind != inferred_kind:
            raise ValueError(
                f"OOB kind '{selected_kind}' does not match its "
                f"{inferred_kind} content."
            )
        required = tuple(dict.fromkeys(value.strip() for value in required_dlc if value.strip()))
        excluded = tuple(dict.fromkeys(value.strip() for value in excluded_dlc if value.strip()))
        overlap = sorted(set(required) & set(excluded))
        if overlap:
            raise ValueError(f"DLC cannot be both required and excluded: {overlap}")
        oob = OrderOfBattle(
            name=name,
            country_tag=country_tag,
            templates=copy.deepcopy(list(templates)),
            divisions=copy.deepcopy(list(divisions)),
            fleets=copy.deepcopy(list(fleets)),
            air_wings=copy.deepcopy(list(air_wings)),
            kind=selected_kind,
            required_dlc=required,
            excluded_dlc=excluded,
            path=target,
            raw_text=existing.raw_text if existing is not None else "",
            touched_fields=(
                {"templates", "divisions", "fleets", "air_wings"}
                if existing is not None
                else set()
            ),
        )
        _default_oob_equipment_owners(oob)
        if existing is not None:
            for index, template in enumerate(oob.templates):
                if index < len(existing.templates):
                    template.source_index = existing.templates[index].source_index
                    template.raw_block = existing.templates[index].raw_block
                    template.touched_fields.update(
                        {
                            "name",
                            "battalions",
                            "support",
                            "division_names_group",
                            "priority",
                        }
                    )
            for index, division in enumerate(oob.divisions):
                if index < len(existing.divisions):
                    division.source_index = existing.divisions[index].source_index
                    division.raw_block = existing.divisions[index].raw_block
                    division.touched_fields.update(
                        {
                            "name",
                            "name_order",
                            "location",
                            "division_template",
                            "start_experience_factor",
                            "start_equipment_factor",
                        }
                    )
            _inherit_fleet_sources(oob.fleets, existing.fleets)
            for index, wing in enumerate(oob.air_wings):
                if index < len(existing.air_wings):
                    source = existing.air_wings[index]
                    wing.source_index = source.source_index
                    wing.source_location_index = source.source_location_index
                    wing.raw_block = source.raw_block
                    wing.touched_fields.update(
                        {"amount", "owner", "creator", "version_name"}
                    )
        self._oobs[name] = oob
        self._dirty_oobs.add(name)
        self._dirty_oob_files.add(target)
        self._dirty.add("oobs")
        if assign:
            assign_kind = cast(
                Literal["land", "naval", "air"],
                selected_kind,
            )
            self.assign_country_oob(
                country_tag,
                name,
                kind=assign_kind,
                required_dlc=required,
                excluded_dlc=excluded,
            )
        return oob

    def update_oob(
        self,
        name: str,
        *,
        templates: Sequence[DivisionTemplate] | None = None,
        divisions: Sequence[DivisionUnit] | None = None,
        fleets: Sequence[Fleet] | None = None,
        air_wings: Sequence[AirWing] | None = None,
        country_tag: str | None = None,
        kind: Literal["land", "naval", "air"] | None = None,
        required_dlc: Sequence[str] | None = None,
        excluded_dlc: Sequence[str] | None = None,
        assign: bool = False,
    ) -> bool:
        try:
            oob = self.get_oob(name)
        except (KeyError, ValueError):
            return False
        existing_content_kind = _oob_content_kind(
            oob.templates,
            oob.divisions,
            oob.fleets,
            oob.air_wings,
        )
        prospective_kind = _oob_content_kind(
            templates if templates is not None else oob.templates,
            divisions if divisions is not None else oob.divisions,
            fleets if fleets is not None else oob.fleets,
            air_wings if air_wings is not None else oob.air_wings,
        )
        selected_kind: OOBKind = kind or oob.kind
        if prospective_kind == "mixed" and existing_content_kind != "mixed":
            raise ValueError(
                "This update would mix land, naval, and air content. Split "
                "new content into separate OOB files."
            )
        if (
            prospective_kind not in {None, "mixed"}
            and selected_kind != prospective_kind
        ):
            raise ValueError(
                f"OOB kind '{selected_kind}' does not match its "
                f"{prospective_kind} content."
            )
        self._materialize_oob_override(oob)
        if templates is not None:
            template_replacements = copy.deepcopy(list(templates))
            for index, template in enumerate(template_replacements):
                if (
                    index < len(oob.templates)
                    and template.source_index < 0
                    and not template.raw_block
                ):
                    template.source_index = oob.templates[index].source_index
                    template.raw_block = oob.templates[index].raw_block
                    template.touched_fields.update(
                        {
                            "name",
                            "battalions",
                            "support",
                            "division_names_group",
                            "priority",
                        }
                    )
            oob.templates = template_replacements
            oob.touched_fields.add("templates")
        if divisions is not None:
            division_replacements = copy.deepcopy(list(divisions))
            for index, division in enumerate(division_replacements):
                if (
                    index < len(oob.divisions)
                    and division.source_index < 0
                    and not division.raw_block
                ):
                    division.source_index = oob.divisions[index].source_index
                    division.raw_block = oob.divisions[index].raw_block
                    division.touched_fields.update(
                        {
                            "name",
                            "name_order",
                            "location",
                            "division_template",
                            "start_experience_factor",
                            "start_equipment_factor",
                        }
                    )
            oob.divisions = division_replacements
            oob.touched_fields.add("divisions")
        if fleets is not None:
            fleet_replacements = copy.deepcopy(list(fleets))
            _inherit_fleet_sources(fleet_replacements, oob.fleets)
            oob.fleets = fleet_replacements
            oob.touched_fields.add("fleets")
        if air_wings is not None:
            wing_replacements = copy.deepcopy(list(air_wings))
            for index, wing in enumerate(wing_replacements):
                if (
                    index < len(oob.air_wings)
                    and wing.source_index < 0
                    and not wing.raw_block
                ):
                    source = oob.air_wings[index]
                    wing.source_index = source.source_index
                    wing.source_location_index = source.source_location_index
                    wing.raw_block = source.raw_block
                    wing.touched_fields.update(
                        {"amount", "owner", "creator", "version_name"}
                    )
            oob.air_wings = wing_replacements
            oob.touched_fields.add("air_wings")
        if country_tag is not None:
            oob.country_tag = require_country_tag(country_tag)
        if kind is not None:
            oob.kind = kind
        if required_dlc is not None:
            oob.required_dlc = tuple(
                dict.fromkeys(value.strip() for value in required_dlc if value.strip())
            )
        if excluded_dlc is not None:
            oob.excluded_dlc = tuple(
                dict.fromkeys(value.strip() for value in excluded_dlc if value.strip())
            )
        overlap = sorted(set(oob.required_dlc) & set(oob.excluded_dlc))
        if overlap:
            raise ValueError(f"DLC cannot be both required and excluded: {overlap}")
        _default_oob_equipment_owners(oob)
        if assign:
            if not oob.country_tag:
                raise ValueError("Assigning an OOB requires country_tag")
            if oob.kind == "mixed":
                raise ValueError(
                    "A mixed land/naval/air OOB cannot be assigned. Split it "
                    "into separate OOB files."
                )
            self.assign_country_oob(
                oob.country_tag,
                oob.name,
                kind=cast(Literal["land", "naval", "air"], oob.kind),
                required_dlc=oob.required_dlc,
                excluded_dlc=oob.excluded_dlc,
            )
        self._dirty_oobs.add(oob.name)
        if oob.path is not None:
            self._dirty_oob_files.add(oob.path)
        self._dirty.add("oobs")
        return True

    def delete_oob(self, name: str, *, unassign: bool = True) -> bool:
        try:
            oob = self.get_oob(name, include_vanilla=False)
        except (KeyError, ValueError):
            return False
        if oob.path is not None and not oob.path.resolve(strict=False).is_relative_to(
            self.mod_root
        ):
            raise ValueError(f"Cannot delete vanilla OOB '{name}'")
        self._oobs.pop(oob.name, None)
        self._dirty_oobs.add(oob.name)
        if oob.path is not None:
            self._dirty_oob_files.add(oob.path)
        self._dirty.add("oobs")
        if unassign:
            for country in self._countries.values():
                for reference in self._country_oob_references(country):
                    if reference.name == oob.name:
                        self.unassign_country_oob(
                            country.tag,
                            reference.name,
                            kind=reference.kind,
                            required_dlc=reference.required_dlc,
                            excluded_dlc=reference.excluded_dlc,
                            date=reference.date,
                        )
        return True

    def _assign_country_oob(self, tag: str, name: str) -> None:
        if name:
            self.assign_country_oob(tag, name, kind="land")
            return
        country = self.get_country(tag)
        for reference in self._country_oob_references(country):
            if reference.kind == "land":
                self.unassign_country_oob(
                    tag,
                    reference.name,
                    kind="land",
                    required_dlc=reference.required_dlc,
                    excluded_dlc=reference.excluded_dlc,
                )

    def assign_country_oob(
        self,
        tag: str,
        name: str,
        *,
        kind: Literal["land", "naval", "air"] = "land",
        required_dlc: Sequence[str] = (),
        excluded_dlc: Sequence[str] = (),
        date: str = "",
    ) -> None:
        """Assign an OOB with the engine's land/naval/air history effect."""

        tag = require_country_tag(tag)
        name = require_script_id(name, label="OOB name")
        country = self.get_country(tag)
        self._materialize_country_override(country)
        required = tuple(
            dict.fromkeys(value.strip() for value in required_dlc if value.strip())
        )
        excluded = tuple(
            dict.fromkeys(value.strip() for value in excluded_dlc if value.strip())
        )
        if date and re.fullmatch(r"\d{1,4}\.\d{1,2}\.\d{1,2}(?:\.\d{1,2})?", date) is None:
            raise ValueError(f"Invalid OOB assignment date: {date!r}")
        reference = OOBReference(name, kind, required, excluded, date)
        existing = self._country_oob_references(country)
        if reference in existing:
            return
        conflict = next(
            (
                item
                for item in existing
                if item.kind == kind
                and item.required_dlc == required
                and item.excluded_dlc == excluded
                and item.date == date
            ),
            None,
        )
        if conflict is not None:
            raise ValueError(
                f"Country '{tag}' already assigns {kind} OOB '{conflict.name}' "
                f"for date {date or '<scenario start>'}, required DLC {required}, "
                f"and excluded DLC {excluded}."
            )
        if kind == "land" and not required and not excluded and not date:
            country.oob = name
            country.touched_fields.add("oob")
        else:
            self._materialize_country_history(country)
            country.raw_history = append_assignment(
                country.raw_history,
                serialize_oob_assignment(reference),
            )
        self._dirty_countries.add(tag)
        self._dirty.add("countries")

    def unassign_country_oob(
        self,
        tag: str,
        name: str,
        *,
        kind: Literal["land", "naval", "air"] = "land",
        required_dlc: Sequence[str] = (),
        excluded_dlc: Sequence[str] = (),
        date: str = "",
    ) -> bool:
        """Remove one exact land, naval, or air OOB assignment."""

        tag = require_country_tag(tag)
        name = require_script_id(name, label="OOB name")
        country = self.get_country(tag)
        required = tuple(
            dict.fromkeys(value.strip() for value in required_dlc if value.strip())
        )
        excluded = tuple(
            dict.fromkeys(value.strip() for value in excluded_dlc if value.strip())
        )
        reference = OOBReference(name, kind, required, excluded, date)
        if reference not in self._country_oob_references(country):
            return False
        self._materialize_country_override(country)
        updated, removed = remove_oob_reference(country.raw_history, reference)
        if (
            kind == "land"
            and not required
            and not excluded
            and not date
            and country.oob == name
        ):
            country.oob = ""
            country.touched_fields.add("oob")
            removed = True
        if updated != country.raw_history:
            country.raw_history = updated
        if removed:
            self._dirty_countries.add(tag)
            self._dirty.add("countries")
        return removed

    def create_equipment_variant(
        self,
        country_tag: str,
        variant: EquipmentVariant,
    ) -> EquipmentVariant:
        """Append a validated equipment variant to country history."""

        country_tag = require_country_tag(country_tag)
        if not variant.name.strip():
            raise ValueError("Equipment variant name cannot be empty")
        if not variant.equipment_type.strip():
            raise ValueError("Equipment variant type cannot be empty")
        overlap = sorted(set(variant.required_dlc) & set(variant.excluded_dlc))
        if overlap:
            raise ValueError(f"DLC cannot be both required and excluded: {overlap}")
        known_equipment = self._known_equipment_ids()
        if known_equipment and variant.equipment_type not in known_equipment:
            raise ValueError(
                f"Unknown equipment type '{variant.equipment_type}' for variant "
                f"'{variant.name}'"
            )
        country = self.get_country(country_tag)
        unlocks = self._equipment_unlock_technologies().get(
            variant.equipment_type,
            (),
        )
        is_modular_chassis = (
            variant.equipment_type.startswith("ship_hull_")
            or "airframe" in variant.equipment_type
        )
        grants, _ = self._history_equipment_operations(country.raw_history)
        grants.extend(
            self._modeled_technology_grants_missing_from_history(country, grants)
        )
        if (
            is_modular_chassis
            and unlocks
            and not self._dlc_conditions_are_covered(
                variant.required_dlc,
                variant.excluded_dlc,
                [
                    (grant.required_dlc, grant.excluded_dlc)
                    for grant in grants
                    if grant.technology in unlocks
                ],
            )
        ):
            raise ValueError(
                f"Equipment variant '{variant.name}' uses modular chassis "
                f"'{variant.equipment_type}' before it is unlocked on every matching "
                "DLC path. Grant one of these technologies in the same or a broader "
                f"DLC branch first: {', '.join(unlocks)}. "
                "allow_without_tech does not create the required chassis variant."
            )
        self._materialize_country_override(country)
        self._materialize_country_history(country)
        self._synchronize_pending_country_technologies(country)
        for existing in find_equipment_variants(country.raw_history):
            if (
                existing.name == variant.name
                and existing.equipment_type == variant.equipment_type
                and existing.required_dlc == variant.required_dlc
                and existing.excluded_dlc == variant.excluded_dlc
            ):
                raise ValueError(
                    f"Equipment variant '{variant.name}' for "
                    f"'{variant.equipment_type}' already exists in {country_tag} history"
                )
        country.raw_history = append_assignment(
            country.raw_history,
            serialize_equipment_variant(variant),
        )
        self._dirty_countries.add(country_tag)
        self._dirty.add("countries")
        return variant

    def set_country_name_pool(
        self,
        tag: str,
        *,
        male_names: Sequence[str],
        surnames: Sequence[str],
        female_names: Sequence[str] = (),
        callsigns: Sequence[str] = (),
    ) -> None:
        """Define random character and ace names for one country tag."""

        tag = require_country_tag(tag)

        def values(items: Sequence[str], *, required: bool, label: str) -> str:
            normalized = tuple(
                dict.fromkeys(item.strip() for item in items if item.strip())
            )
            if required and not normalized:
                raise ValueError(
                    f"Country name pool requires at least one {label}"
                )
            return " ".join(pdx_string(item) for item in normalized)

        male = values(male_names, required=True, label="male name")
        family = values(surnames, required=True, label="surname")
        female = values(female_names, required=False, label="female name")
        call = values(callsigns, required=False, label="callsign")
        sections = [f"male = {{\n\tnames = {{ {male} }}\n}}"]
        if female:
            sections.append(f"female = {{\n\tnames = {{ {female} }}\n}}")
        sections.append(f"surnames = {{ {family} }}")
        if call:
            sections.append(f"callsigns = {{ {call} }}")
        self._country_name_pool_updates[tag] = "\n".join(sections)
        self._dirty.add("country_names")

    @staticmethod
    def _country_oob_references(country: Country) -> tuple[OOBReference, ...]:
        references = list(find_oob_references(country.raw_history))
        if country.oob and not any(
            reference.kind == "land" and reference.name == country.oob
            for reference in references
        ):
            references.append(OOBReference(country.oob, "land"))
        return tuple(references)

    def _materialize_country_history(self, country: Country) -> None:
        if country.raw_history:
            return
        files = serialize_country_files(self.mod_root, country)
        history_path = next(
            path for path in files if path.parent.name == "countries"
            and path.parent.parent.name == "history"
        )
        country.history_path = history_path
        country.raw_history = files[history_path]

    @staticmethod
    def _synchronize_pending_country_technologies(country: Country) -> None:
        """Place pending model technologies before subsequently appended effects."""

        if not ({"*", "technologies"} & country.touched_fields):
            return
        body = "\n".join(
            f"{technology} = {pdx_value(level)}"
            for technology, level in country.technologies.items()
        )
        country.raw_history = set_block(
            country.raw_history,
            "set_technology",
            body or None,
        )

    def _materialize_oob_override(self, oob: OrderOfBattle) -> None:
        source = oob.path
        if source is None:
            source = self.mod_root / "history" / "units" / f"{oob.name}.txt"
            oob.path = source
            self._dirty_oob_files.add(source)
            return
        if source.resolve(strict=False).is_relative_to(self.mod_root):
            return
        if self.hoi4_install is None:
            raise ValueError(f"Cannot retarget OOB '{oob.name}' without a HOI4 install")
        target = resolve_mod_output_path(
            self.mod_root,
            source.resolve(strict=False).relative_to(self.hoi4_install),
        )
        self._original_files.setdefault(
            target,
            self._read_current_text(target)
            or source.read_text(encoding="utf-8-sig", errors="ignore"),
        )
        oob.path = target
        self._dirty_oob_files.add(target)

    def delete_country(self, tag: str) -> bool:
        try:
            tag = require_country_tag(tag)
        except ValueError:
            return False
        if tag not in self._countries:
            return False
        country = self._countries.pop(tag)
        if any(
            path is not None and not path.resolve(strict=False).is_relative_to(self.mod_root)
            for path in (country.definition_path, country.history_path, country.character_path)
        ):
            self._countries[tag] = country
            raise ValueError(
                f"Cannot delete vanilla country '{tag}' without an explicit total-conversion "
                "tag replacement strategy"
            )
        self._deleted_countries[tag] = country
        self._created_country_tags.discard(tag)
        loc_keys: list[str] = []
        for suffix in _country_localisation_suffixes(country):
            key = f"{tag}{suffix}"
            loc_keys.extend((key, f"{key}_DEF"))
        loc_keys.append(f"{tag}_ADJ")
        if country.leader:
            loc_keys.extend((country.leader.character_id, f"{country.leader.character_id}_desc"))
        for key in set(loc_keys):
            self.delete_loc(key)
        self._dirty.add("countries")
        self._dirty_countries.add(tag)
        return True

    def _materialize_country_override(self, country: Country) -> None:
        """Retarget vanilla-backed country files into equivalent mod paths."""

        path_fields = (
            ("definition_path", Path("common/countries") / f"{country.tag}.txt"),
            (
                "history_path",
                Path("history/countries")
                / f"{country.tag} - {country.name or country.tag}.txt",
            ),
            ("character_path", Path("common/characters") / f"{country.tag}_characters.txt"),
        )
        for field_name, fallback in path_fields:
            source = getattr(country, field_name)
            if source is None or source.resolve(strict=False).is_relative_to(self.mod_root):
                continue
            relative = fallback
            if self.hoi4_install is not None:
                try:
                    relative = source.resolve(strict=False).relative_to(self.hoi4_install)
                except ValueError:
                    pass
            target = resolve_mod_output_path(self.mod_root, relative)
            setattr(country, field_name, target)
            self._original_files.setdefault(target, self._read_current_text(target))

    # ── States ──────────────────────────────────────────────────

    def list_states(self) -> list[int]:
        all_ids = set(self._state_ids) | set(self._states.keys())
        return sorted(all_ids)

    def state_index(self, include_vanilla: bool = True) -> list[dict]:
        cached = self._state_index_cache.get(include_vanilla)
        if cached is not None:
            return [dict(entry) for entry in cached]
        state_localization = self._state_localization_entries()
        entries: list[dict] = []
        for source, base in [("mod", self.mod_root), ("vanilla", self.hoi4_install)]:
            if base is None:
                continue
            if source == "vanilla" and not include_vanilla:
                continue
            states_dir = base / "history" / "states"
            for entry in build_state_index(states_dir):
                item = dict(entry)
                item["source"] = source
                loc_key = str(item.get("name", ""))
                localized_name = state_localization.get(loc_key)
                item["localized"] = localized_name is not None
                if localized_name is not None:
                    item["display_name"] = localized_name
                entries.append(item)
        by_id: dict[int, dict] = {}
        for entry in entries:
            existing = by_id.get(entry["id"])
            if existing is None or (
                existing["source"] == "vanilla" and entry["source"] == "mod"
            ):
                by_id[entry["id"]] = entry
        result = sorted(by_id.values(), key=lambda item: item["id"])
        self._state_index_cache[include_vanilla] = [dict(entry) for entry in result]
        return result

    def get_state_name_map(self, include_vanilla: bool = True) -> dict[int, str]:
        return {
            entry["id"]: entry.get("display_name") or entry.get("name") or str(entry["id"])
            for entry in self.state_index(include_vanilla=include_vanilla)
        }

    def find_state(self, query: str, include_vanilla: bool = True, limit: int = 10) -> list[dict]:
        needle = _fold_search_text(query)
        if not needle:
            return []
        scored: list[tuple[float, dict, str]] = []
        for entry in self.state_index(include_vanilla=include_vanilla):
            haystacks = [
                ("id", str(entry.get("id", "")), 1.0),
                ("name", str(entry.get("name", "")), 1.0),
                ("display_name", str(entry.get("display_name", "")), 1.0),
            ]
            best = 0.0
            matched = ""
            for field_name, haystack, weight in haystacks:
                text = _fold_search_text(haystack)
                if text == needle:
                    score = 1.0 * weight
                elif needle in text:
                    score = 0.85 * weight
                else:
                    score = SequenceMatcher(None, needle, text).ratio() * weight
                if score > best:
                    best = score
                    matched = field_name
            file_name = str(entry.get("file_name", ""))
            if file_name:
                file_weight = 0.7 if entry.get("localized") else 1.0
                text = _fold_search_text(file_name)
                if text == needle:
                    score = 1.0 * file_weight
                elif needle in text:
                    score = 0.85 * file_weight
                else:
                    score = SequenceMatcher(None, needle, text).ratio() * file_weight
                if score > best:
                    best = score
                    matched = "file_name"
            if best >= 0.45:
                scored.append((best, entry, matched))
        scored.sort(key=lambda item: (-item[0], item[1]["id"]))
        results: list[dict] = []
        for _, entry, matched in scored[:limit]:
            result = dict(entry)
            result["matched"] = matched
            results.append(result)
        return results

    def _state_localization_entries(self) -> dict[str, str]:
        if self._vanilla_state_loc_entries is None:
            vanilla_entries: dict[str, str] = {}
            if self.hoi4_install is not None:
                loaded, _ = parse_localization_dir(
                    self.hoi4_install / "localisation" / "english"
                )
                vanilla_entries = {
                    key: value for key, value in loaded.items() if key.startswith("STATE_")
                }
            self._vanilla_state_loc_entries = vanilla_entries
        entries = dict(self._vanilla_state_loc_entries)
        entries.update(
            {
                key: value
                for key, value in self._loc_entries.items()
                if key.startswith("STATE_")
            }
        )
        return entries

    def get_state(self, state_id: int) -> State:
        """Load a state without changing the mod directory.

        Vanilla fallback states remain backed by their vanilla source until a
        mutating state API explicitly materializes an override under the mod.
        """
        if state_id in self._states:
            return self._states[state_id]
        states_dir = self.mod_root / "history" / "states"
        f = self._state_source_paths.get(state_id)
        if f is not None and not f.exists():
            f = None
        if f is None:
            f = find_state_file(states_dir, state_id)
        if not f and self.hoi4_install:
            f = find_state_file(self.hoi4_install / "history" / "states", state_id)
        if f:
            state = read_state(f)
            self._states[state_id] = state
            self._state_source_paths.setdefault(state_id, f)
            self._original_files.setdefault(f, f.read_text(encoding="utf-8", errors="ignore"))
            self._source_baseline.setdefault(f.resolve(strict=False), f.read_bytes())
            return state
        raise KeyError(f"State {state_id} not found")

    def _materialize_state_override(self, state: State) -> bool:
        """Retarget a vanilla-backed state to its equivalent in-mod path.

        This only changes queued in-memory state. The source file is copied by
        the normal preview/save rendering path, so reads and validation never
        create files as a side effect.
        """

        source = state.path or state.source_path or self._state_source_paths.get(state.id)
        if source is None:
            relative = Path("history") / "states" / f"{state.id} - {state.name or state.id}.txt"
        elif source.resolve(strict=False).is_relative_to(self.mod_root):
            return False
        else:
            relative = Path("history") / "states" / source.name
            if self.hoi4_install is not None:
                try:
                    relative = source.resolve(strict=False).relative_to(self.hoi4_install)
                except ValueError:
                    pass
        target = resolve_mod_output_path(self.mod_root, relative)
        state.path = target
        self._state_source_paths[state.id] = target
        self._original_files.setdefault(target, self._read_current_text(target))
        return True

    def set_state_owner(self, state_id: int, tag: str, add_core: bool = True) -> State:
        tag = require_country_tag(tag)
        state = self.get_state(state_id)
        self._materialize_state_override(state)
        state.owner = tag
        if add_core and tag not in state.cores:
            state.cores.append(tag)
        self._dirty.add("states")
        self._dirty_states.add(state_id)
        return state

    def set_state_properties(self, state_id: int, **kwargs) -> bool:
        state = self.get_state(state_id)
        self._materialize_state_override(state)
        for list_key in ("cores", "provinces"):
            if list_key in kwargs and isinstance(kwargs[list_key], list):
                kwargs[list_key] = _append_unique(getattr(state, list_key), kwargs[list_key])
        _set_fields(state, kwargs)
        self._dirty.add("states")
        self._dirty_states.add(state_id)
        return True

    def add_state_core(self, state_id: int, tag: str) -> State:
        state = self.get_state(state_id)
        self._materialize_state_override(state)
        tag = require_country_tag(tag)
        if tag not in state.cores:
            state.cores.append(tag)
        self._dirty.add("states")
        self._dirty_states.add(state_id)
        return state

    def remove_state_core(self, state_id: int, tag: str) -> State:
        state = self.get_state(state_id)
        self._materialize_state_override(state)
        tag = require_country_tag(tag)
        state.cores = [core for core in state.cores if core != tag]
        self._dirty.add("states")
        self._dirty_states.add(state_id)
        return state

    def patch_state_history(
        self,
        state_id: int,
        *,
        owner: str | None = None,
        add_cores: list[str] | None = None,
        remove_cores: list[str] | None = None,
    ) -> State:
        """Queue a minimal owner/core history patch for the next ``save()``."""
        state = self.get_state(state_id)
        self._materialize_state_override(state)
        removed = {tag.upper() for tag in (remove_cores or [])}
        state.cores = [core for core in state.cores if core not in removed]
        for core in add_cores or []:
            normalized = require_country_tag(core)
            if normalized not in state.cores:
                state.cores.append(normalized)
        if owner is not None:
            state.owner = require_country_tag(owner)
        self._state_history_patches[state_id] = {
            "owner": state.owner if owner is not None else None,
            "add_cores": list(add_cores or []),
            "remove_cores": list(remove_cores or []),
        }
        self._dirty.add("states")
        self._dirty_states.add(state_id)
        return state

    def batch_set_owner(self, state_ids: list[int], tag: str, add_core: bool = True) -> list[State]:
        results = []
        for sid in state_ids:
            results.append(self.set_state_owner(sid, tag, add_core))
        return results

    # ── Events ──────────────────────────────────────────────────

    def list_events(self) -> list[str]:
        return sorted(self._events.keys())

    def get_event(self, event_id: str) -> Event:
        if event_id not in self._events:
            raise KeyError(f"Event '{event_id}' not found. Available: {self.list_events()}")
        return self._events[event_id]

    def create_event(
        self,
        event_id: str,
        title: str = "",
        description: str = "",
        event_type: str = "country_event",
        picture: str = "GFX_report_event_generic",
        is_triggered_only: bool = False,
        fire_only_once: bool | None = None,
        trigger: str = "",
        immediate: str = "",
        mean_time_to_happen: str = "",
        options: list[EventOption] | None = None,
        overwrite: bool = False,
    ) -> Event:
        event_id = require_script_id(event_id, label="event ID")
        existing = self._events.get(event_id)
        if existing is not None and not overwrite:
            raise ValueError(
                f"Event '{event_id}' already exists. Use overwrite=True to replace it, "
                "or update_event()/update_event_option() to patch it."
            )
        existing_namespace = self._event_namespaces.get(event_id)
        event = Event(
            id=event_id,
            title=title or f"{event_id}.t",
            description=description or f"{event_id}.d",
            event_type=event_type,
            picture=picture,
            is_triggered_only=is_triggered_only,
            fire_only_once=fire_only_once,
            trigger=normalize_block_body(trigger),
            immediate=normalize_block_body(immediate),
            mean_time_to_happen=normalize_block_body(mean_time_to_happen),
            options=[
                _normalize_event_option(option)
                for option in (options or [EventOption(name=f"{event_id}.a", effect="")])
            ],
            path=existing.path if existing is not None else None,
            touched=True,
        )
        self._events[event_id] = event
        self._event_namespaces[event_id] = (
            existing_namespace
            if existing_namespace is not None
            else _infer_event_namespace(event_id)
        )
        self._dirty.add("events")
        self._dirty_events.add(event_id)
        return event

    def ensure_event(self, event_id: str, **kwargs) -> Event:
        """Create an event if missing, otherwise patch the existing event."""
        if event_id in self._events:
            self.update_event(event_id, **kwargs)
            return self._events[event_id]
        return self.create_event(event_id, **kwargs)

    def update_event(self, event_id: str, **kwargs) -> bool:
        event = self._events.get(event_id)
        if event is None:
            return False
        if "id" in kwargs:
            raise ValueError("Event IDs are immutable; create a new event instead")
        old_path = (
            event.path
            or self.mod_root
            / "events"
            / f"{self._event_namespaces.get(event_id) or 'mod'}_events.txt"
        )
        if "path" in kwargs:
            kwargs["path"] = resolve_mod_output_path(self.mod_root, kwargs["path"])
        for key in ("trigger", "immediate", "mean_time_to_happen"):
            if key in kwargs and isinstance(kwargs[key], str):
                kwargs[key] = normalize_block_body(kwargs[key])
        if "options" in kwargs and isinstance(kwargs["options"], list):
            kwargs["options"] = [_normalize_event_option(option) for option in kwargs["options"]]
        _set_fields(event, kwargs, allow_path=True)
        event.touched = True
        self._dirty_event_files.add(old_path)
        self._dirty_event_files.add(
            event.path
            or self.mod_root
            / "events"
            / f"{self._event_namespaces.get(event_id) or 'mod'}_events.txt"
        )
        self._dirty.add("events")
        self._dirty_events.add(event_id)
        return True

    def delete_event(self, event_id: str) -> bool:
        event = self._events.get(event_id)
        if event is None:
            return False
        old_path = (
            event.path
            or self.mod_root
            / "events"
            / f"{self._event_namespaces.get(event_id) or 'mod'}_events.txt"
        )
        self._dirty_event_files.add(old_path)
        del self._events[event_id]
        self._event_namespaces.pop(event_id, None)
        self._dirty.add("events")
        self._dirty_events.discard(event_id)
        return True

    def set_event_namespace(self, event_id: str, namespace: str) -> None:
        if event_id in self._events:
            namespace = require_event_namespace(namespace)
            event = self._events[event_id]
            old_path = (
                event.path
                or self.mod_root
                / "events"
                / f"{self._event_namespaces.get(event_id) or 'mod'}_events.txt"
            )
            event.path = self.mod_root / "events" / f"{namespace}_events.txt"
            event.touched = True
            self._dirty_event_files.update({old_path, event.path})
            self._event_namespaces[event_id] = namespace
            self._dirty.add("events")
            self._dirty_events.add(event_id)

    def add_event_option(self, event_id: str, option: EventOption) -> bool:
        event = self._events.get(event_id)
        if event is None:
            return False
        event.raw_block = ""
        event.options.append(_normalize_event_option(option))
        event.options[-1].touched = True
        event.touched = True
        self._dirty.add("events")
        self._dirty_events.add(event_id)
        return True

    def update_event_option(self, event_id: str, option: int | str, **kwargs) -> bool:
        event = self._events.get(event_id)
        if event is None:
            return False
        event_option: EventOption | None
        if isinstance(option, int):
            if option < 0 or option >= len(event.options):
                return False
            event_option = event.options[option]
        else:
            event_option = next(
                (candidate for candidate in event.options if candidate.name == option), None
            )
            if event_option is None:
                return False
        for key in ("trigger", "ai_chance"):
            if key in kwargs and isinstance(kwargs[key], str):
                kwargs[key] = normalize_block_body(kwargs[key])
        assert event_option is not None
        _set_fields(event_option, kwargs)
        _normalize_event_option(event_option)
        event_option.touched = True
        event.touched = True
        self._dirty.add("events")
        self._dirty_events.add(event_id)
        return True

    # ── On Actions ───────────────────────────────────────────────

    def list_on_actions(self) -> list[str]:
        return sorted(self._on_actions.keys())

    def get_on_action_occurrences(self, action_id: str) -> tuple[OnAction, ...]:
        """Return every compositional occurrence of an on-action hook."""

        occurrences = self._on_action_occurrences.get(action_id)
        if not occurrences:
            raise KeyError(
                f"On-action '{action_id}' not found. Available: {self.list_on_actions()}"
            )
        return tuple(occurrences)

    def _select_on_action_occurrence(
        self,
        action_id: str,
        *,
        occurrence: int | None,
        source_path: str | Path | None,
        require_unique: bool,
    ) -> tuple[int, OnAction]:
        actions = self._on_action_occurrences.get(action_id)
        if not actions:
            raise KeyError(
                f"On-action '{action_id}' not found. Available: {self.list_on_actions()}"
            )
        candidates = list(enumerate(actions))
        if source_path is not None:
            target = resolve_mod_output_path(self.mod_root, source_path)
            candidates = [
                (index, action)
                for index, action in candidates
                if (action.path or self.mod_root / "common/on_actions/mod_on_actions.txt")
                .resolve(strict=False)
                == target
            ]
            if not candidates:
                raise KeyError(
                    f"On-action '{action_id}' has no occurrence in {target}"
                )
        if occurrence is None:
            if require_unique and len(candidates) != 1:
                locations = ", ".join(
                    f"{index}: {action.path or '<generated>'}"
                    for index, action in candidates
                )
                raise ValueError(
                    f"On-action '{action_id}' has {len(candidates)} compositional "
                    f"occurrences ({locations}). Specify occurrence= or source_path= "
                    "to choose one safely."
                )
            occurrence = 0
        if occurrence < 0 or occurrence >= len(candidates):
            raise IndexError(
                f"On-action '{action_id}' occurrence {occurrence} is out of range "
                f"for {len(candidates)} matching occurrence(s)"
            )
        return candidates[occurrence]

    def get_on_action(
        self,
        action_id: str,
        *,
        occurrence: int = 0,
        source_path: str | Path | None = None,
    ) -> OnAction:
        """Return one hook occurrence, defaulting to the first in load order."""

        _, action = self._select_on_action_occurrence(
            action_id,
            occurrence=occurrence,
            source_path=source_path,
            require_unique=False,
        )
        return action

    def create_on_action(
        self,
        action_id: str,
        *,
        effect: str = "",
        events: list[str] | None = None,
        random_events: list[str] | None = None,
        path: str | Path | None = None,
        overwrite: bool = False,
    ) -> OnAction:
        action_id = require_script_id(action_id, label="on-action ID")
        existing = self._on_action_occurrences.get(action_id, [])
        default_path = self.mod_root / "common" / "on_actions" / "mod_on_actions.txt"
        if path is None and overwrite and len(existing) > 1:
            raise ValueError(
                f"On-action '{action_id}' has multiple compositional occurrences; "
                "specify path= to choose which file to overwrite"
            )
        target = (
            resolve_mod_output_path(self.mod_root, path)
            if path is not None
            else (
                existing[0].path
                if overwrite and len(existing) == 1 and existing[0].path is not None
                else default_path
            )
        )
        target_existing = [
            action
            for action in existing
            if (action.path or default_path).resolve(strict=False) == target
        ]
        if target_existing and not overwrite:
            raise ValueError(
                f"On-action '{action_id}' already has an occurrence in {target}. "
                "Use overwrite=True to replace that occurrence, update_on_action() "
                "to patch it, or choose another path for an additive extension."
            )
        if len(target_existing) > 1:
            raise ValueError(
                f"On-action '{action_id}' occurs multiple times in {target}; "
                "create_on_action(overwrite=True) cannot choose one safely. Use "
                "update_on_action(..., source_path=..., occurrence=...)."
            )
        replaced = target_existing[0] if target_existing else None
        if replaced is not None and replaced.path is not None:
            self._dirty_on_action_files.add(replaced.path)
        action = OnAction(
            id=action_id,
            effect=normalize_block_body(effect),
            events=list(events or ()),
            random_events=list(random_events or ()),
            path=target,
            touched=True,
            source_path=replaced.source_path if replaced is not None else None,
            source_occurrence=(
                replaced.source_occurrence if replaced is not None else -1
            ),
        )
        if replaced is None:
            existing.append(action)
            self._on_action_occurrences[action_id] = existing
            self._on_actions.setdefault(action_id, action)
        else:
            index = existing.index(replaced)
            existing[index] = action
            if self._on_actions.get(action_id) is replaced:
                self._on_actions[action_id] = action
        self._dirty.add("on_actions")
        self._dirty_on_actions.add(action_id)
        self._dirty_on_action_files.add(target)
        return action

    def ensure_on_action(self, action_id: str, **kwargs) -> OnAction:
        """Ensure one occurrence, using the requested/default file as its identity."""

        occurrences = self._on_action_occurrences.get(action_id, [])
        if len(occurrences) == 1:
            self.update_on_action(action_id, occurrence=0, **kwargs)
            return occurrences[0]
        if occurrences:
            path_arg = kwargs.get("path")
            target = (
                resolve_mod_output_path(self.mod_root, path_arg)
                if path_arg is not None
                else self.mod_root / "common" / "on_actions" / "mod_on_actions.txt"
            )
            matching = [
                index
                for index, action in enumerate(occurrences)
                if (
                    action.path
                    or self.mod_root / "common" / "on_actions" / "mod_on_actions.txt"
                ).resolve(strict=False)
                == target
            ]
            if len(matching) == 1:
                self.update_on_action(action_id, occurrence=matching[0], **kwargs)
                return occurrences[matching[0]]
            if len(matching) > 1:
                raise ValueError(
                    f"On-action '{action_id}' occurs multiple times in {target}; "
                    "ensure_on_action() cannot choose one safely"
                )
        return self.create_on_action(action_id, **kwargs)

    def update_on_action(
        self,
        action_id: str,
        *,
        occurrence: int | None = None,
        source_path: str | Path | None = None,
        **kwargs,
    ) -> bool:
        if action_id not in self._on_action_occurrences:
            return False
        _, action = self._select_on_action_occurrence(
            action_id,
            occurrence=occurrence,
            source_path=source_path,
            require_unique=True,
        )
        old_path = action.path
        if "effect" in kwargs and isinstance(kwargs["effect"], str):
            kwargs["effect"] = normalize_block_body(kwargs["effect"])
        if "id" in kwargs:
            raise ValueError("On-action IDs are immutable; create a new on-action instead")
        if "path" in kwargs:
            kwargs["path"] = resolve_mod_output_path(self.mod_root, kwargs["path"])
        _set_fields(action, kwargs, allow_path=True)
        action.touched = True
        self._dirty.add("on_actions")
        self._dirty_on_actions.add(action_id)
        if old_path is not None:
            self._dirty_on_action_files.add(old_path)
        self._dirty_on_action_files.add(
            action.path or self.mod_root / "common" / "on_actions" / "mod_on_actions.txt"
        )
        return True

    def delete_on_action(
        self,
        action_id: str,
        *,
        occurrence: int | None = None,
        source_path: str | Path | None = None,
    ) -> bool:
        actions = self._on_action_occurrences.get(action_id)
        if not actions:
            return False
        index, action = self._select_on_action_occurrence(
            action_id,
            occurrence=occurrence,
            source_path=source_path,
            require_unique=True,
        )
        del actions[index]
        if actions:
            self._on_actions[action_id] = actions[0]
        else:
            del self._on_action_occurrences[action_id]
            del self._on_actions[action_id]
        self._dirty.add("on_actions")
        self._dirty_on_actions.discard(action_id)
        self._dirty_on_action_files.add(
            action.path or self.mod_root / "common" / "on_actions" / "mod_on_actions.txt"
        )
        return True

    # ── Decisions ────────────────────────────────────────────────

    def list_decision_categories(self) -> list[str]:
        return sorted(self._decision_categories.keys())

    def list_decisions(self) -> list[str]:
        return sorted(self._decisions.keys())

    def get_decision(self, decision_id: str) -> Decision:
        if decision_id not in self._decisions:
            raise KeyError(
                f"Decision '{decision_id}' not found. Available: {self.list_decisions()}"
            )
        return self._decisions[decision_id]

    def get_decision_category(self, category_id: str) -> DecisionCategory:
        if category_id not in self._decision_categories:
            raise KeyError(
                f"Decision category '{category_id}' not found. Available: {self.list_decision_categories()}"
            )
        return self._decision_categories[category_id]

    def create_decision_category(
        self,
        category_id: str,
        *,
        icon: str = "",
        allowed: str = "",
        visible: str = "",
        path: str | Path | None = None,
        category_path: str | Path | None = None,
        overwrite: bool = False,
    ) -> DecisionCategory:
        category_id = require_script_id(category_id, label="decision category ID")
        existing = self._decision_categories.get(category_id)
        if existing is not None and not overwrite:
            raise ValueError(
                f"Decision category '{category_id}' already exists. "
                "Use overwrite=True to replace it or update existing decisions."
            )
        existing_decisions = [
            decision
            for decision in self._decisions.values()
            if decision.category == category_id
        ]
        if existing is not None:
            canonical_path = existing.path
            extended_paths = {
                decision.path.resolve(strict=False)
                for decision in existing_decisions
                if decision.path is not None
                and (
                    canonical_path is None
                    or decision.path.resolve(strict=False)
                    != canonical_path.resolve(strict=False)
                )
            }
            if extended_paths:
                raise RuntimeError(
                    f"Decision category '{category_id}' is extended across multiple files; "
                    "cannot safely overwrite the whole category. Consolidate it first."
                )
        target = (
            resolve_mod_output_path(self.mod_root, path)
            if path is not None
            else (
                existing.path
                if existing is not None and existing.path is not None
                else self.mod_root / "common" / "decisions" / "mod_decisions.txt"
            )
        )
        definition_target = (
            resolve_mod_output_path(self.mod_root, category_path)
            if category_path is not None
            else (
                existing.definition_path
                if existing is not None and existing.definition_path is not None
                else _decision_category_definition_path(self.mod_root, target)
            )
        )
        expected_categories_dir = self.mod_root / "common" / "decisions" / "categories"
        if definition_target.parent != expected_categories_dir:
            raise ValueError(
                "Decision category_path must point inside common/decisions/categories"
            )
        if existing is not None:
            if existing.path is not None:
                self._dirty_decision_files.add(existing.path)
            if existing.definition_path is not None:
                self._dirty_decision_category_files.add(existing.definition_path)
            for decision in existing_decisions:
                self._decisions.pop(decision.id, None)
        category = DecisionCategory(
            id=category_id,
            icon=icon,
            allowed=allowed,
            visible=visible,
            path=target,
            definition_path=definition_target,
        )
        category.touched = True
        self._decision_categories[category_id] = category
        self._dirty.add("decisions")
        self._dirty_decision_categories.add(category_id)
        self._dirty_decision_files.add(target)
        self._dirty_decision_category_files.add(definition_target)
        return category

    def ensure_decision_category(self, category_id: str, **kwargs) -> DecisionCategory:
        if category_id in self._decision_categories:
            self.update_decision_category(category_id, **kwargs)
            return self._decision_categories[category_id]
        return self.create_decision_category(category_id, **kwargs)

    def update_decision_category(self, category_id: str, **kwargs) -> bool:
        category = self._decision_categories.get(category_id)
        if category is None:
            return False
        if "id" in kwargs:
            raise ValueError("Decision category IDs are immutable")
        old_path = category.path
        old_definition_path = category.definition_path
        category_path = kwargs.pop("category_path", None)
        if "path" in kwargs:
            kwargs["path"] = resolve_mod_output_path(self.mod_root, kwargs["path"])
        if category_path is not None:
            category.definition_path = resolve_mod_output_path(
                self.mod_root, category_path
            )
        elif "path" in kwargs and category.definition_path is None:
            category.definition_path = _decision_category_definition_path(
                self.mod_root, cast(Path, kwargs["path"])
            )
        if (
            category.definition_path is not None
            and category.definition_path.parent
            != self.mod_root / "common" / "decisions" / "categories"
        ):
            raise ValueError(
                "Decision category_path must point inside common/decisions/categories"
            )
        _set_fields(category, kwargs, allow_path=True)
        category.touched = True
        if old_path is not None:
            self._dirty_decision_files.add(old_path)
        if category.path is not None:
            self._dirty_decision_files.add(category.path)
        if old_definition_path is not None:
            self._dirty_decision_category_files.add(old_definition_path)
        if category.definition_path is not None:
            self._dirty_decision_category_files.add(category.definition_path)
        self._dirty.add("decisions")
        self._dirty_decision_categories.add(category_id)
        return True

    def create_decision(
        self,
        category_id: str,
        decision_id: str,
        *,
        icon: str = "",
        cost: int | None = None,
        days_remove: int | None = None,
        fire_only_once: bool | None = None,
        available: str = "",
        visible: str = "",
        complete_effect: str = "",
        remove_effect: str = "",
        ai_will_do: str = "",
        path: str | Path | None = None,
        overwrite: bool = False,
    ) -> Decision:
        category_id = require_script_id(category_id, label="decision category ID")
        decision_id = require_script_id(decision_id, label="decision ID")
        existing = self._decisions.get(decision_id)
        if existing is not None and not overwrite:
            raise ValueError(
                f"Decision '{decision_id}' already exists. "
                "Use overwrite=True to replace it or update_decision() to patch it."
            )
        if existing is not None:
            self._assert_decision_occurrence_is_editable(existing)
        category = self._decision_categories.get(category_id)
        if category is None:
            category = self.create_decision_category(category_id, path=path)
        elif path is not None:
            old_path = category.path
            category.path = resolve_mod_output_path(self.mod_root, path)
            category.touched = True
            if old_path is not None:
                self._dirty_decision_files.add(old_path)
            self._dirty_decision_files.add(category.path)
        if existing is not None:
            old_category = self._decision_categories.get(existing.category)
            if old_category is not None:
                old_category.decisions = [
                    candidate for candidate in old_category.decisions if candidate.id != decision_id
                ]
                self._dirty_decision_categories.add(old_category.id)
        decision = Decision(
            id=decision_id,
            category=category_id,
            icon=icon,
            cost=cost,
            days_remove=days_remove,
            fire_only_once=fire_only_once,
            available=available,
            visible=visible,
            complete_effect=complete_effect,
            remove_effect=remove_effect,
            ai_will_do=ai_will_do,
            path=category.path,
            touched=True,
        )
        category.decisions.append(decision)
        self._decisions[decision_id] = decision
        self._dirty.add("decisions")
        self._dirty_decision_categories.add(category_id)
        if category.path is not None:
            self._dirty_decision_files.add(category.path)
        if (
            category.definition_path is not None
            and (category.touched or not category.definition_raw_block)
        ):
            self._dirty_decision_category_files.add(category.definition_path)
        return decision

    def ensure_decision(self, category_id: str, decision_id: str, **kwargs) -> Decision:
        if decision_id in self._decisions:
            self.update_decision(decision_id, **kwargs)
            return self._decisions[decision_id]
        return self.create_decision(category_id, decision_id, **kwargs)

    def update_decision(self, decision_id: str, **kwargs) -> bool:
        decision = self._decisions.get(decision_id)
        if decision is None:
            return False
        self._assert_decision_occurrence_is_editable(decision)
        if "id" in kwargs or "category" in kwargs:
            raise ValueError(
                "Decision identity/category are immutable; recreate the decision to move it"
            )
        kwargs.pop("path", None)
        _set_fields(decision, kwargs)
        decision.touched = True
        self._dirty.add("decisions")
        self._dirty_decision_categories.add(decision.category)
        category = self._decision_categories.get(decision.category)
        if category is not None:
            if category.path is not None:
                self._dirty_decision_files.add(category.path)
            if (
                category.definition_path is not None
                and not category.definition_raw_block
            ):
                self._dirty_decision_category_files.add(category.definition_path)
        return True

    def delete_decision(self, decision_id: str) -> bool:
        decision = self._decisions.get(decision_id)
        if decision is None:
            return False
        self._assert_decision_occurrence_is_editable(decision)
        del self._decisions[decision_id]
        category = self._decision_categories.get(decision.category)
        if category:
            category.decisions = [
                candidate for candidate in category.decisions if candidate.id != decision_id
            ]
            self._dirty_decision_categories.add(category.id)
            if category.path is not None:
                self._dirty_decision_files.add(category.path)
        self._dirty.add("decisions")
        return True

    def _assert_decision_occurrence_is_editable(self, decision: Decision) -> None:
        category = self._decision_categories.get(decision.category)
        if (
            category is None
            or decision.path is None
            or category.path is None
            or decision.path.resolve(strict=False) == category.path.resolve(strict=False)
        ):
            return
        raise RuntimeError(
            f"Decision category '{decision.category}' is extended across multiple files; "
            f"cannot safely mutate '{decision.id}' in {decision.path}. Consolidate the "
            "category or edit that source file directly."
        )

    def create_decision_chain(
        self,
        category_id: str,
        steps: list[dict | str],
        *,
        prefix: str | None = None,
        icon: str = "",
        category_icon: str = "",
        path: str | Path | None = None,
        final_event: str | None = None,
        overwrite: bool = False,
    ) -> list[Decision]:
        """Create a staged decision chain with generated completion flags.

        Each step is a dict with at least ``id`` unless ``prefix`` is supplied.
        Common keys are ``icon``, ``cost``, ``days_remove``, ``visible``,
        ``available``, ``complete_effect``, ``event``, ``loc_name``, and
        ``loc_desc``. A string step is treated as ``{"id": step}``.
        """
        if not steps:
            raise ValueError("create_decision_chain() requires at least one step")

        category_kwargs: dict[str, object] = {}
        if category_icon:
            category_kwargs["icon"] = category_icon
        if path is not None:
            category_kwargs["path"] = path
        self.ensure_decision_category(category_id, **category_kwargs)
        decisions: list[Decision] = []
        previous_flag = ""
        for index, raw_step in enumerate(steps, start=1):
            step = {"id": raw_step} if isinstance(raw_step, str) else dict(raw_step)
            decision_id = str(step.get("id") or (f"{prefix}_{index}" if prefix else ""))
            if not decision_id:
                raise ValueError("Decision chain steps need an id or a prefix")
            flag = str(step.get("flag") or f"{decision_id}_done")
            visible = _join_script_parts(
                [
                    str(step.get("visible") or ""),
                    f"has_country_flag = {previous_flag}" if previous_flag else "",
                    f"NOT = {{ has_country_flag = {flag} }}",
                ]
            )
            available = _join_script_parts(
                [
                    str(step.get("available") or ""),
                    f"has_country_flag = {previous_flag}" if previous_flag else "",
                ]
            )
            event_id = step.get("event")
            effect_parts = [
                str(step.get("complete_effect") or ""),
                self.effect_schedule_country_event(str(event_id), int(step.get("event_days") or 0))
                if event_id
                else "",
                self.effect_schedule_country_event(final_event)
                if final_event and index == len(steps)
                else "",
                f"set_country_flag = {flag}",
            ]
            clear_flags = step.get("clear_flags") or []
            effect_parts.extend(f"clr_country_flag = {item}" for item in clear_flags)
            create_kwargs: dict[str, Any] = {
                "icon": str(step.get("icon") or icon),
                "cost": step.get("cost", 0),
                "days_remove": step.get("days_remove"),
                "fire_only_once": step.get("fire_only_once", True),
                "visible": visible,
                "available": available,
                "complete_effect": _join_script_parts(effect_parts),
                "remove_effect": str(step.get("remove_effect") or ""),
                "ai_will_do": str(step.get("ai_will_do") or ""),
                "overwrite": overwrite,
            }
            if path is not None:
                create_kwargs["path"] = path
            if overwrite:
                decision = self.create_decision(category_id, decision_id, **create_kwargs)
            else:
                create_kwargs.pop("overwrite")
                decision = self.ensure_decision(category_id, decision_id, **create_kwargs)
            if step.get("loc_name"):
                self.set_loc(decision_id, str(step["loc_name"]))
            if step.get("loc_desc"):
                self.set_loc(f"{decision_id}_desc", str(step["loc_desc"]))
            decisions.append(decision)
            previous_flag = flag
        return decisions

    def create_recovery_decision(
        self,
        category_id: str,
        decision_id: str,
        *,
        effect: str,
        visible: str = "",
        available: str = "",
        hidden: bool = False,
        icon: str = "",
        cost: int = 0,
        days_remove: int | None = None,
        fire_only_once: bool = True,
        loc_name: str = "",
        loc_desc: str = "",
        path: str | Path | None = None,
        overwrite: bool = False,
    ) -> Decision:
        """Create a repair/migration decision for already-started saves."""
        if path is not None:
            self.ensure_decision_category(category_id, path=path)
        elif category_id not in self._decision_categories:
            self.create_decision_category(category_id)
        visible_body = visible or ("always = no" if hidden else "")
        decision = self.create_decision(
            category_id,
            decision_id,
            icon=icon,
            cost=cost,
            days_remove=days_remove,
            fire_only_once=fire_only_once,
            visible=visible_body,
            available=available,
            complete_effect=effect,
            path=path,
            overwrite=overwrite,
        )
        if loc_name:
            self.set_loc(decision_id, loc_name)
        if loc_desc:
            self.set_loc(f"{decision_id}_desc", loc_desc)
        return decision

    # ── Ideas ───────────────────────────────────────────────────

    def list_ideas(self) -> list[str]:
        return sorted(self._ideas.keys())

    def get_idea(self, idea_id: str) -> Idea:
        if idea_id not in self._ideas:
            raise KeyError(f"Idea '{idea_id}' not found. Available: {self.list_ideas()}")
        return self._ideas[idea_id]

    def create_idea(
        self,
        idea_id: str,
        icon: str = DEFAULT_IDEA_ICON,
        modifier: dict[str, str | int | float | bool] | None = None,
        category: str = "country",
        path: str | Path | None = None,
        overwrite: bool = False,
        *,
        desc: str = "",
        removal_cost: str | int | float | None = None,
    ) -> Idea:
        idea_id = require_script_id(idea_id, label="idea ID")
        expected_desc = f"{idea_id}_desc"
        if desc and desc != expected_desc:
            raise ValueError(
                "HOI4 idea descriptions use the fixed localization key "
                f"'{expected_desc}'; call set_loc('{expected_desc}', text) instead"
            )
        existing = self._ideas.get(idea_id)
        if existing is not None and not overwrite:
            raise ValueError(
                f"Idea '{idea_id}' already exists. Use overwrite=True to replace it or update_idea() to patch it."
            )
        idea = Idea(
            id=idea_id,
            icon=normalize_idea_icon(icon),
            desc=desc,
            removal_cost=removal_cost,
            modifier=dict(modifier or {}),
            category=category,
            touched=True,
        )
        if existing is not None and existing.path is not None:
            self._dirty_idea_files.add(existing.path)
        if path is not None:
            idea.path = resolve_mod_output_path(self.mod_root, path)
        elif existing is not None and existing.path is not None:
            idea.path = existing.path
        else:
            prefix = idea_id.split("_")[0] if "_" in idea_id else idea_id
            if prefix in self._cached_idea_file:
                idea.path = self._cached_idea_file[prefix]
            elif len(prefix) == 3 and prefix.isupper():
                idea.path = self.mod_root / "common" / "ideas" / f"{prefix}_ideas.txt"
            else:
                idea.path = self.mod_root / "common" / "ideas" / "mod_ideas.txt"
        if idea.path is not None and idea.path.parent.name == "ideas":
            self._idea_file_containers.setdefault(idea.path, "ideas")
        self._ideas[idea_id] = idea
        self._dirty.add("ideas")
        self._dirty_ideas.add(idea_id)
        return idea

    def ensure_idea(self, idea_id: str, *, merge_modifier: bool = False, **kwargs) -> Idea:
        """Create an idea if missing, otherwise update the existing idea.

        Existing modifiers are replaced by default. Pass
        ``merge_modifier=True`` to merge supplied keys instead.
        """
        if idea_id in self._ideas:
            self.update_idea(idea_id, merge_modifier=merge_modifier, **kwargs)
            return self._ideas[idea_id]
        return self.create_idea(idea_id, **kwargs)

    def update_idea(self, idea_id: str, *, merge_modifier: bool = False, **kwargs) -> bool:
        """Update an idea, replacing its modifier mapping by default.

        ``modifier={}`` therefore clears the block. Pass
        ``merge_modifier=True`` for the legacy key-by-key merge behavior.
        """
        idea = self._ideas.get(idea_id)
        if idea is None:
            return False
        if "id" in kwargs:
            raise ValueError("Idea IDs are immutable; create a new idea instead")
        if "desc" in kwargs:
            expected_desc = f"{idea_id}_desc"
            desc = kwargs["desc"]
            if desc and desc != expected_desc:
                raise ValueError(
                    "HOI4 idea descriptions use the fixed localization key "
                    f"'{expected_desc}'; call set_loc('{expected_desc}', text) instead"
                )
        if "icon" in kwargs:
            kwargs["icon"] = normalize_idea_icon(kwargs["icon"])
        touched_fields = set(kwargs) - {"path"}
        old_path = idea.path
        if "path" in kwargs:
            kwargs["path"] = resolve_mod_output_path(self.mod_root, kwargs["path"])
        modifier_supplied = "modifier" in kwargs
        modifier_val = kwargs.pop("modifier", None)
        if modifier_supplied and modifier_val is not None:
            if not isinstance(modifier_val, dict):
                raise TypeError(f"modifier must be a dict, got {type(modifier_val).__name__}")
            if merge_modifier:
                idea.modifier.update(modifier_val)
            else:
                idea.modifier = dict(modifier_val)
            idea.modifier_merge = merge_modifier
            touched_fields.add("modifier")
        if kwargs:
            _set_fields(idea, kwargs, allow_path=True)
        idea.touched = True
        idea.touched_fields.update(touched_fields)
        if old_path is not None:
            self._dirty_idea_files.add(old_path)
        if idea.path is not None:
            self._dirty_idea_files.add(idea.path)
        self._dirty.add("ideas")
        self._dirty_ideas.add(idea_id)
        return True

    def delete_idea(self, idea_id: str) -> bool:
        idea = self._ideas.get(idea_id)
        if idea is None:
            return False
        if idea.path is not None:
            self._dirty_idea_files.add(idea.path)
        del self._ideas[idea_id]
        self._dirty.add("ideas")
        self._dirty_ideas.discard(idea_id)
        return True

    def set_idea_path(self, tag: str, path: str | Path) -> None:
        tag = require_country_tag(tag)
        self._cached_idea_file[tag] = resolve_mod_output_path(self.mod_root, path)

    # ── Ideologies ───────────────────────────────────────────────

    def list_ideologies(self, *, include_vanilla: bool = True) -> list[str]:
        ids = set(self._ideologies)
        if include_vanilla:
            ids.update(self._vanilla_ideologies)
        return sorted(ids)

    def get_ideology(self, ideology_id: str, *, include_vanilla: bool = True) -> Ideology:
        ideology = self._ideologies.get(ideology_id)
        if ideology is None and include_vanilla:
            ideology = self._vanilla_ideologies.get(ideology_id)
        if ideology is None:
            raise KeyError(
                f"Ideology '{ideology_id}' not found. Available: {self.list_ideologies(include_vanilla=include_vanilla)}"
            )
        return ideology

    def create_ideology(
        self,
        ideology_id: str,
        *,
        color: tuple[int, int, int] = (128, 128, 128),
        types: list[SubIdeology] | None = None,
        rules: dict[str, str] | None = None,
        modifiers: dict[str, str] | None = None,
        hidden_modifiers: dict[str, str] | None = None,
        faction_modifiers: dict[str, str] | None = None,
        dynamic_faction_names: list[str] | None = None,
        ai_behavior: str = "",
        can_host_government_in_exile: bool = False,
        can_collaborate: bool = False,
        effects: list[str] | None = None,
        path: str | Path | None = None,
        overwrite: bool = False,
    ) -> Ideology:
        ideology_id = require_script_id(ideology_id, label="ideology ID")
        existing = self._ideologies.get(ideology_id)
        if existing is not None and not overwrite:
            raise ValueError(
                f"Ideology '{ideology_id}' already exists. Use overwrite=True or update_ideology()."
            )
        target = (
            resolve_mod_output_path(self.mod_root, path)
            if path is not None
            else self.mod_root / "common" / "ideologies" / "00_mod_ideologies.txt"
        )
        ideology = Ideology(
            id=ideology_id,
            color=color,
            types=list(types or []),
            rules=dict(rules or {}),
            modifiers=dict(modifiers or {}),
            hidden_modifiers=dict(hidden_modifiers or {}),
            faction_modifiers=dict(faction_modifiers or {}),
            dynamic_faction_names=list(dynamic_faction_names or []),
            ai_behavior=ai_behavior,
            can_host_government_in_exile=can_host_government_in_exile,
            can_collaborate=can_collaborate,
            effects=list(effects or []),
            path=target,
        )
        if existing is not None and existing.path is not None:
            self._dirty_ideology_files.add(existing.path)
        self._ideologies[ideology_id] = ideology
        self._script_vocabulary_cache = None
        self._dirty_ideologies.add(ideology_id)
        self._dirty_ideology_files.add(target)
        self._dirty.add("ideologies")
        return ideology

    def update_ideology(self, ideology_id: str, **kwargs) -> bool:
        ideology = self._ideologies.get(ideology_id)
        if ideology is None:
            vanilla = self._vanilla_ideologies.get(ideology_id)
            if vanilla is None:
                return False
            ideology = copy.deepcopy(vanilla)
            ideology.is_vanilla = False
            ideology.path = (
                self.mod_root / "common" / "ideologies" / "00_mod_ideologies.txt"
            )
            self._ideologies[ideology_id] = ideology
        forbidden = {"id", "raw_block", "touched_fields", "is_vanilla"} & set(kwargs)
        if forbidden:
            raise ValueError(f"Immutable/internal ideology fields: {sorted(forbidden)}")
        old_path = ideology.path
        if "path" in kwargs:
            kwargs["path"] = resolve_mod_output_path(self.mod_root, kwargs["path"])
        _set_fields(ideology, kwargs, allow_path=True)
        ideology.touched_fields.update(key for key in kwargs if key != "path")
        if old_path is not None:
            self._dirty_ideology_files.add(old_path)
        if ideology.path is not None:
            self._dirty_ideology_files.add(ideology.path)
        self._dirty_ideologies.add(ideology_id)
        self._dirty.add("ideologies")
        return True

    def delete_ideology(self, ideology_id: str) -> bool:
        ideology = self._ideologies.pop(ideology_id, None)
        if ideology is None:
            return False
        if ideology.path is not None:
            self._dirty_ideology_files.add(ideology.path)
        self._dirty_ideologies.discard(ideology_id)
        self._script_vocabulary_cache = None
        self._dirty.add("ideologies")
        return True

    # ── Dynamic Modifiers ────────────────────────────────────────

    def list_dynamic_modifiers(self) -> list[str]:
        return sorted(self._dynamic_modifiers)

    def get_dynamic_modifier(self, modifier_id: str) -> DynamicModifier:
        try:
            return self._dynamic_modifiers[modifier_id]
        except KeyError as error:
            raise KeyError(
                f"Dynamic modifier '{modifier_id}' not found. "
                f"Available: {self.list_dynamic_modifiers()}"
            ) from error

    def create_dynamic_modifier(
        self,
        modifier_id: str,
        *,
        icon: str = "",
        enable: str = "",
        remove_trigger: str = "",
        attacker_modifier: bool | None = None,
        modifier: dict[str, str | int | float | bool] | None = None,
        path: str | Path | None = None,
        overwrite: bool = False,
    ) -> DynamicModifier:
        modifier_id = require_script_id(
            modifier_id,
            label="dynamic modifier ID",
        )
        existing = self._dynamic_modifiers.get(modifier_id)
        if existing is not None and not overwrite:
            raise ValueError(f"Dynamic modifier '{modifier_id}' already exists")
        target = (
            resolve_mod_output_path(self.mod_root, path)
            if path is not None
            else self.mod_root
            / "common"
            / "dynamic_modifiers"
            / f"{safe_file_stem(modifier_id)}.txt"
        )
        if existing is not None and existing.path is not None:
            self._dirty_dynamic_modifier_files.add(existing.path)
        dynamic_modifier = DynamicModifier(
            id=modifier_id,
            icon=icon,
            enable=normalize_block_body(enable),
            remove_trigger=normalize_block_body(remove_trigger),
            attacker_modifier=attacker_modifier,
            modifier=dict(modifier or {}),
            path=target,
        )
        self._dynamic_modifiers[modifier_id] = dynamic_modifier
        self._dynamic_modifier_sources[modifier_id] = target
        self._dirty_dynamic_modifiers.add(modifier_id)
        self._dirty_dynamic_modifier_files.add(target)
        self._dirty.add("dynamic_modifiers")
        return dynamic_modifier

    def update_dynamic_modifier(self, modifier_id: str, **kwargs: object) -> bool:
        dynamic_modifier = self._dynamic_modifiers.get(modifier_id)
        if dynamic_modifier is None:
            return False
        forbidden = {"id", "path", "raw_block", "touched_fields"} & set(kwargs)
        if forbidden:
            raise ValueError(
                f"Immutable/internal dynamic modifier fields: {sorted(forbidden)}"
            )
        for key in ("enable", "remove_trigger"):
            if key in kwargs:
                kwargs[key] = normalize_block_body(str(kwargs[key]))
        _set_fields(dynamic_modifier, dict(kwargs))
        dynamic_modifier.touched_fields.update(kwargs)
        if dynamic_modifier.path is not None:
            self._dirty_dynamic_modifier_files.add(dynamic_modifier.path)
        self._dirty_dynamic_modifiers.add(modifier_id)
        self._dirty.add("dynamic_modifiers")
        return True

    def delete_dynamic_modifier(self, modifier_id: str) -> bool:
        dynamic_modifier = self._dynamic_modifiers.pop(modifier_id, None)
        if dynamic_modifier is None:
            return False
        self._dynamic_modifier_sources.pop(modifier_id, None)
        if dynamic_modifier.path is not None:
            self._dirty_dynamic_modifier_files.add(dynamic_modifier.path)
        self._dirty_dynamic_modifiers.add(modifier_id)
        self._dirty.add("dynamic_modifiers")
        return True

    # ── Bookmarks ────────────────────────────────────────────────

    def list_bookmarks(self) -> list[str]:
        return [bookmark.name for bookmark in self._bookmarks]

    def get_bookmark(self, name: str) -> Bookmark:
        bookmark = next((item for item in self._bookmarks if item.name == name), None)
        if bookmark is None:
            raise KeyError(f"Bookmark '{name}' not found. Available: {self.list_bookmarks()}")
        return bookmark

    def create_bookmark(
        self,
        name: str,
        *,
        description: str = "",
        date: str = "1936.1.1.12",
        picture: str = "GFX_select_date_1936",
        default_country: str = "",
        default: bool | None = None,
        filters: str = "",
        effect: str = DEFAULT_BOOKMARK_EFFECT,
        countries: list[BookmarkCountry] | None = None,
        path: str | Path | None = None,
        overwrite: bool = False,
    ) -> Bookmark:
        existing = next((item for item in self._bookmarks if item.name == name), None)
        if existing is not None and not overwrite:
            raise ValueError(f"Bookmark '{name}' already exists")
        if existing is not None:
            self.delete_bookmark(name)
        target = (
            resolve_mod_output_path(self.mod_root, path)
            if path is not None
            else self.mod_root
            / "common"
            / "bookmarks"
            / f"{safe_file_stem(name, fallback='bookmark')}.txt"
        )
        selected_countries = list(countries or [])
        for country in selected_countries:
            country.tag = require_bookmark_country_key(country.tag)
            country.required_dlc = normalize_required_dlc(country.required_dlc)
        bookmark = Bookmark(
            name=name,
            description=description,
            date=date,
            picture=picture,
            default_country=default_country,
            default=default,
            filters=normalize_block_body(filters),
            effect=normalize_block_body(effect),
            countries=selected_countries,
            path=target,
        )
        self._bookmarks.append(bookmark)
        self._dirty_bookmarks.add((target, None))
        self._dirty_bookmark_files.add(target)
        self._dirty.add("bookmarks")
        return bookmark

    def update_bookmark(self, name: str, **kwargs) -> bool:
        try:
            bookmark = self.get_bookmark(name)
        except KeyError:
            return False
        forbidden = {"raw_block", "source_index", "touched_fields"} & set(kwargs)
        if forbidden:
            raise ValueError(f"Internal bookmark fields: {sorted(forbidden)}")
        old_path = bookmark.path
        if "path" in kwargs:
            kwargs["path"] = resolve_mod_output_path(self.mod_root, kwargs["path"])
        if "filters" in kwargs:
            kwargs["filters"] = normalize_block_body(kwargs["filters"])
        if "effect" in kwargs:
            kwargs["effect"] = normalize_block_body(kwargs["effect"])
        _set_fields(bookmark, kwargs, allow_path=True)
        bookmark.touched_fields.update(key for key in kwargs if key != "path")
        if old_path is not None:
            self._dirty_bookmark_files.add(old_path)
        if bookmark.path is not None:
            self._dirty_bookmark_files.add(bookmark.path)
            self._dirty_bookmarks.add((bookmark.path, bookmark.source_index))
        self._dirty.add("bookmarks")
        return True

    def add_bookmark_country(self, bookmark_name: str, country: BookmarkCountry) -> None:
        bookmark = self.get_bookmark(bookmark_name)
        country.tag = require_bookmark_country_key(country.tag)
        country.required_dlc = normalize_required_dlc(country.required_dlc)
        bookmark.countries.append(country)
        if bookmark.path is not None:
            self._dirty_bookmark_files.add(bookmark.path)
            self._dirty_bookmarks.add((bookmark.path, bookmark.source_index))
        self._dirty.add("bookmarks")

    def update_bookmark_country(
        self,
        bookmark_name: str,
        tag: str,
        *,
        occurrence: int = 0,
        **kwargs,
    ) -> bool:
        bookmark = self.get_bookmark(bookmark_name)
        tag = require_bookmark_country_key(tag)
        matches = [country for country in bookmark.countries if country.tag == tag]
        if occurrence < 0 or occurrence >= len(matches):
            return False
        country = matches[occurrence]
        forbidden = {"tag", "raw_block", "source_index", "touched_fields"} & set(kwargs)
        if forbidden:
            raise ValueError(f"Immutable/internal bookmark country fields: {sorted(forbidden)}")
        if "available" in kwargs:
            kwargs["available"] = normalize_block_body(kwargs["available"])
        if "required_dlc" in kwargs:
            kwargs["required_dlc"] = normalize_required_dlc(kwargs["required_dlc"])
        _set_fields(country, kwargs)
        country.touched_fields.update(kwargs)
        if bookmark.path is not None:
            self._dirty_bookmark_files.add(bookmark.path)
            self._dirty_bookmarks.add((bookmark.path, bookmark.source_index))
        self._dirty.add("bookmarks")
        return True

    def delete_bookmark_country(
        self,
        bookmark_name: str,
        tag: str,
        *,
        occurrence: int = 0,
    ) -> bool:
        bookmark = self.get_bookmark(bookmark_name)
        tag = require_bookmark_country_key(tag)
        indices = [index for index, country in enumerate(bookmark.countries) if country.tag == tag]
        if occurrence < 0 or occurrence >= len(indices):
            return False
        del bookmark.countries[indices[occurrence]]
        if bookmark.path is not None:
            self._dirty_bookmark_files.add(bookmark.path)
            self._dirty_bookmarks.add((bookmark.path, bookmark.source_index))
        self._dirty.add("bookmarks")
        return True

    def delete_bookmark(self, name: str) -> bool:
        bookmark = next((item for item in self._bookmarks if item.name == name), None)
        if bookmark is None:
            return False
        self._bookmarks.remove(bookmark)
        if bookmark.path is not None:
            self._dirty_bookmark_files.add(bookmark.path)
        self._dirty.add("bookmarks")
        return True

    def set_bookmark_date_range(
        self,
        start_date: str,
        end_date: str,
        *,
        path: str | Path = "common/defines/zz_bookmark_dates.lua",
    ) -> Path:
        """Stage START_DATE/END_DATE defines in the normal preview/save transaction."""

        start = require_bookmark_date(start_date, label="bookmark start date")
        end = require_bookmark_date(end_date, label="bookmark end date")
        target = resolve_mod_output_path(self.mod_root, path)
        self._original_files.setdefault(target, self._read_current_text(target))
        self._bookmark_date_defines = (target, start, end)
        self._dirty.add("bookmark_dates")
        return target

    # ── Focus Trees ──────────────────────────────────────────────

    def list_focus_trees(self) -> list[str]:
        return sorted(self._focus_trees.keys())

    def get_focus_tree(self, tree_id: str) -> FocusTree:
        if tree_id not in self._focus_trees:
            raise KeyError(
                f"Focus tree '{tree_id}' not found. Available: {self.list_focus_trees()}"
            )
        return self._focus_trees[tree_id]

    def create_focus_tree(
        self, tree_id: str, country_tag: str, overwrite: bool = False
    ) -> FocusTree:
        tree_id = require_script_id(tree_id, label="focus tree ID")
        existing = self._focus_trees.get(tree_id)
        if existing is not None and not overwrite:
            raise ValueError(
                f"Focus tree '{tree_id}' already exists. "
                "Use overwrite=True to replace it or update_focus_tree()/add_focus() to patch it."
            )
        tag = require_country_tag(country_tag)
        path = (
            existing.path
            if existing is not None and existing.path is not None
            else self.mod_root / "common" / "national_focus" / f"{tag}_focus.txt"
        )
        tree = FocusTree(id=tree_id, country_tag=tag, path=path, touched=True)
        self._focus_trees[tree_id] = tree
        self._original_files.setdefault(path, "")
        self._dirty.add("focus")
        self._dirty_focus_trees.add(tree_id)
        return tree

    def ensure_focus_tree(self, tree_id: str, country_tag: str, **kwargs) -> FocusTree:
        """Create a focus tree if missing, otherwise patch tree-level fields."""
        if tree_id in self._focus_trees:
            if kwargs:
                self.update_focus_tree(tree_id, **kwargs)
            return self._focus_trees[tree_id]
        tree = self.create_focus_tree(tree_id, country_tag)
        if kwargs:
            self.update_focus_tree(tree_id, **kwargs)
        return tree

    def delete_focus_tree(self, tree_id: str) -> bool:
        if tree_id not in self._focus_trees:
            return False
        tree = self._focus_trees.pop(tree_id)
        if tree.path:
            self._dirty_focus_files.add(tree.path)
        self._dirty.add("focus")
        self._dirty_focus_trees.discard(tree_id)
        return True

    def update_focus_tree(self, tree_id: str, **kwargs) -> bool:
        tree = self._focus_trees.get(tree_id)
        if tree is None:
            return False
        if "id" in kwargs:
            raise ValueError("Focus tree IDs are immutable; create a new tree instead")
        old_path = tree.path
        if "path" in kwargs:
            kwargs["path"] = resolve_mod_output_path(self.mod_root, kwargs["path"])
        _set_fields(tree, kwargs, allow_path=True)
        tree.touched = True
        if old_path is not None:
            self._dirty_focus_files.add(old_path)
        if tree.path is not None:
            self._dirty_focus_files.add(tree.path)
        self._dirty.add("focus")
        self._dirty_focus_trees.add(tree_id)
        return True

    # ── Focuses ──────────────────────────────────────────────────

    def add_focus(self, tree_id: str, focus: Focus) -> None:
        tree = self.get_focus_tree(tree_id)
        focus.touched = True
        tree.focuses.append(focus)
        tree.touched = True
        self._dirty.add("focus")
        self._dirty_focus_trees.add(tree_id)

    def remove_focus(self, tree_id: str, focus_id: str) -> bool:
        tree = self.get_focus_tree(tree_id)
        for i, f in enumerate(tree.focuses):
            if f.id == focus_id:
                tree.focuses.pop(i)
                tree.touched = True
                self._dirty.add("focus")
                self._dirty_focus_trees.add(tree_id)
                return True
        return False

    def get_focus(self, tree_id: str, focus_id: str) -> Optional[Focus]:
        tree = self.get_focus_tree(tree_id)
        for f in tree.focuses:
            if f.id == focus_id:
                return f
        return None

    def update_focus(self, tree_id: str, focus_id: str, **kwargs) -> bool:
        focus = self.get_focus(tree_id, focus_id)
        if focus is None:
            return False
        if "id" in kwargs:
            raise ValueError("Focus IDs are immutable; create a new focus instead")
        _set_fields(focus, kwargs)
        focus.touched = True
        tree = self.get_focus_tree(tree_id)
        tree.touched = True
        self._dirty.add("focus")
        self._dirty_focus_trees.add(tree_id)
        return True

    def upsert_focus(self, tree_id: str, focus: Focus) -> Focus:
        """Add a focus if missing, otherwise replace its modeled fields."""
        existing = self.get_focus(tree_id, focus.id)
        if existing is None:
            self.add_focus(tree_id, focus)
            return focus
        updates = {
            field.name: copy.deepcopy(getattr(focus, field.name))
            for field in dataclasses.fields(Focus)
            if field.name not in {"id", "raw_block", "touched"}
        }
        self.update_focus(tree_id, focus.id, **updates)
        return self.get_focus(tree_id, focus.id) or focus

    def focus_tree_bounds(self, tree_id: str) -> dict[str, int]:
        tree = self.get_focus_tree(tree_id)
        if not tree.focuses:
            return {"min_x": 0, "max_x": 0, "min_y": 0, "max_y": 0, "width": 0, "height": 0}
        xs = [focus.x for focus in tree.focuses]
        ys = [focus.y for focus in tree.focuses]
        return {
            "min_x": min(xs),
            "max_x": max(xs),
            "min_y": min(ys),
            "max_y": max(ys),
            "width": max(xs) - min(xs) + 1,
            "height": max(ys) - min(ys) + 1,
        }

    def place_continuous_focus_below_tree(
        self, tree_id: str, padding: int = 400, x: int = 50
    ) -> str:
        bounds = self.focus_tree_bounds(tree_id)
        y = (bounds["max_y"] + 1) * 100 + padding
        position = f"x = {x} y = {y}"
        self.update_focus_tree(tree_id, continuous_focus_position=position)
        return position

    def assert_no_visual_overlap(self, tree_id: str, *, min_continuous_padding: int = 100) -> bool:
        tree = self.get_focus_tree(tree_id)
        positions: dict[tuple[int, int], str] = {}
        issues: list[str] = []
        for focus in tree.focuses:
            pos = (focus.x, focus.y)
            if pos in positions:
                issues.append(f"{focus.id} overlaps {positions[pos]} at x={focus.x}, y={focus.y}")
            else:
                positions[pos] = focus.id
        if tree.continuous_focus_position:
            match = re.search(r"\by\s*=\s*(-?\d+)", tree.continuous_focus_position)
            if match:
                min_y = (
                    self.focus_tree_bounds(tree_id)["max_y"] + 1
                ) * 100 + min_continuous_padding
                y = int(match.group(1))
                if y < min_y:
                    issues.append(
                        f"continuous_focus_position y={y} is above recommended minimum y={min_y}"
                    )
        if issues:
            raise ValueError("; ".join(issues))
        return True

    def auto_layout_branch(
        self,
        tree_id: str,
        focuses: list[Focus],
        *,
        anchor_focus_id: str | None = None,
        x: int | None = None,
        y_start: int | None = None,
        spacing_y: int = 1,
        chain_prerequisites: bool = True,
    ) -> list[Focus]:
        tree = self.get_focus_tree(tree_id)
        if not focuses:
            return []
        if x is None:
            if anchor_focus_id:
                anchor = self.get_focus(tree_id, anchor_focus_id)
                x = anchor.x if anchor else 0
            else:
                x = max((focus.x for focus in tree.focuses), default=0) + 2
        if y_start is None:
            if anchor_focus_id:
                anchor = self.get_focus(tree_id, anchor_focus_id)
                y_start = (
                    (anchor.y + spacing_y)
                    if anchor
                    else max((focus.y for focus in tree.focuses), default=-1) + spacing_y
                )
            else:
                y_start = max((focus.y for focus in tree.focuses), default=-1) + spacing_y
        previous = anchor_focus_id
        for index, focus in enumerate(focuses):
            focus.x = x
            focus.y = y_start + index * spacing_y
            if chain_prerequisites and previous and not focus.prerequisites:
                focus.prerequisites = [[previous]]
            previous = focus.id
        return focuses

    def insert_focus_after(
        self,
        tree_id: str,
        anchor_focus_id: str,
        focus: Focus,
        *,
        add_prerequisite: bool = True,
        relative_position: bool = True,
    ) -> None:
        tree = self.get_focus_tree(tree_id)
        for idx, existing in enumerate(tree.focuses):
            if existing.id == anchor_focus_id:
                if add_prerequisite and not focus.prerequisites:
                    focus.prerequisites = [[anchor_focus_id]]
                if relative_position and not focus.relative_position_id:
                    focus.relative_position_id = anchor_focus_id
                focus.touched = True
                tree.focuses.insert(idx + 1, focus)
                self._dirty.add("focus")
                self._dirty_focus_trees.add(tree_id)
                return
        raise KeyError(f"Focus '{anchor_focus_id}' not found in tree '{tree_id}'")

    def insert_branch(
        self,
        tree_id: str,
        anchor_focus_id: str,
        focuses: list[Focus],
        *,
        chain_prerequisites: bool = True,
    ) -> None:
        previous = anchor_focus_id
        for focus in focuses:
            if chain_prerequisites and not focus.prerequisites:
                focus.prerequisites = [[previous]]
            if not focus.relative_position_id:
                focus.relative_position_id = previous
            self.insert_focus_after(tree_id, previous, focus, add_prerequisite=False)
            previous = focus.id

    def append_to_focus_reward(self, tree_id: str, focus_id: str, effect: str) -> bool:
        focus = self.get_focus(tree_id, focus_id)
        if focus is None:
            return False
        focus.completion_reward = "\n".join(
            part for part in [focus.completion_reward.strip(), effect.strip()] if part
        )
        focus.touched = True
        self._dirty.add("focus")
        self._dirty_focus_trees.add(tree_id)
        return True

    def set_focuses_mutually_exclusive(
        self, tree_id: str, focus_a_id: str, focus_b_id: str
    ) -> bool:
        focus_a = self.get_focus(tree_id, focus_a_id)
        focus_b = self.get_focus(tree_id, focus_b_id)
        if focus_a is None or focus_b is None:
            return False
        if [focus_b_id] not in focus_a.mutually_exclusive:
            focus_a.mutually_exclusive.append([focus_b_id])
        if [focus_a_id] not in focus_b.mutually_exclusive:
            focus_b.mutually_exclusive.append([focus_a_id])
        focus_a.touched = True
        focus_b.touched = True
        self._dirty.add("focus")
        self._dirty_focus_trees.add(tree_id)
        return True

    def set_focus_loc(
        self,
        focus_id: str,
        name: str,
        description: str,
        file_path: str | Path | None = None,
    ) -> None:
        self.set_loc(focus_id, name, file_path=file_path)
        self.set_loc(f"{focus_id}_desc", description, file_path=file_path)

    @staticmethod
    def effect_add_building(state_id: int, building_type: str, level: int = 1) -> str:
        return (
            f"{state_id} = {{ "
            f"add_extra_state_shared_building_slots = {level} "
            "add_building_construction = { "
            f"type = {building_type} level = {level} instant_build = yes "
            "} "
            "}"
        )

    @staticmethod
    def effect_block(name: str, fields: dict[str, object] | None = None, **kwargs: object) -> str:
        return effect_block(name, fields, **kwargs)

    @staticmethod
    def scope_block(scope: str | int, *effects: str) -> str:
        return scope_block(scope, *effects)

    @staticmethod
    def effect_add_state_building(
        state_id: int,
        building_type: str,
        level: int = 1,
        *,
        add_slots: int = 0,
        province: int | None = None,
    ) -> str:
        fields: dict[str, object] = {
            "type": building_type,
            "level": level,
            "instant_build": True,
        }
        if province is not None:
            fields["province"] = province
        effects: list[str] = []
        if add_slots:
            effects.append(f"add_extra_state_shared_building_slots = {add_slots}")
        effects.append(effect_block("add_building_construction", fields))
        return scope_block(state_id, *effects)

    @classmethod
    def effect_add_civilian_factory(cls, state_id: int, level: int = 1) -> str:
        return cls.effect_add_building(state_id, "industrial_complex", level)

    @classmethod
    def effect_add_military_factory(cls, state_id: int, level: int = 1) -> str:
        return cls.effect_add_building(state_id, "arms_factory", level)

    @staticmethod
    def effect_add_infrastructure(state_id: int, level: int = 1) -> str:
        return (
            f"{state_id} = {{ "
            "add_building_construction = { "
            f"type = infrastructure level = {level} instant_build = yes "
            "} "
            "}"
        )

    @classmethod
    def effect_add_bunker(cls, state_id: int, level: int = 1, province: int | None = None) -> str:
        return cls.effect_add_state_building(state_id, "bunker", level, province=province)

    @classmethod
    def effect_add_coastal_bunker(
        cls, state_id: int, level: int = 1, province: int | None = None
    ) -> str:
        return cls.effect_add_state_building(state_id, "coastal_bunker", level, province=province)

    @staticmethod
    def effect_add_tech_bonus(
        name: str,
        category: str = "industry",
        uses: int = 1,
        bonus: float = 0.5,
    ) -> str:
        if category not in TECHNOLOGY_CATEGORIES:
            examples = ", ".join(
                (
                    "industry",
                    "infantry_weapons",
                    "artillery",
                    "armor",
                    "electronics",
                    "land_doctrine",
                )
            )
            raise ValueError(
                f"Unknown technology category '{category}'. Common valid categories: {examples}"
            )
        return f"add_tech_bonus = {{ name = {name} bonus = {bonus} uses = {uses} category = {category} }}"

    @classmethod
    def effect_add_industry_bonus(cls, name: str, uses: int = 1, bonus: float = 0.5) -> str:
        return cls.effect_add_tech_bonus(name, category="industry", uses=uses, bonus=bonus)

    @staticmethod
    def effect_add_state_core(state_id: int, tag: str) -> str:
        return f"{state_id} = {{ add_core_of = {require_country_tag(tag)} }}"

    @staticmethod
    def effect_remove_state_core(state_id: int, tag: str) -> str:
        return f"{state_id} = {{ remove_core_of = {require_country_tag(tag)} }}"

    @staticmethod
    def effect_transfer_state(state_id: int, target: str) -> str:
        return f"{require_country_tag(target)} = {{ transfer_state = {state_id} }}"

    @classmethod
    def effect_transfer_state_with_core(cls, state_id: int, target: str) -> str:
        return "\n".join(
            [
                cls.effect_transfer_state(state_id, target),
                cls.effect_add_state_core(state_id, target),
            ]
        )

    @staticmethod
    def effect_add_political_power(amount: int) -> str:
        return f"add_political_power = {amount}"

    @staticmethod
    def effect_add_war_support(amount: float) -> str:
        return f"add_war_support = {amount}"

    @staticmethod
    def effect_add_stability(amount: float) -> str:
        return f"add_stability = {amount}"

    @staticmethod
    def effect_add_manpower(amount: int) -> str:
        return f"add_manpower = {amount}"

    @staticmethod
    def effect_add_army_experience(amount: int | float) -> str:
        return f"army_experience = {amount}"

    @staticmethod
    def effect_add_navy_experience(amount: int | float) -> str:
        return f"navy_experience = {amount}"

    @staticmethod
    def effect_add_air_experience(amount: int | float) -> str:
        return f"air_experience = {amount}"

    @staticmethod
    def effect_add_equipment(
        equipment_type: str,
        amount: int,
        producer: str | None = None,
        variant_name: str | None = None,
    ) -> str:
        fields: dict[str, object] = {"type": equipment_type, "amount": amount}
        if producer:
            fields["producer"] = require_country_tag(producer)
        if variant_name:
            fields["variant_name"] = variant_name
        return effect_block("add_equipment_to_stockpile", fields)

    @staticmethod
    def effect_set_technology(technology: str, level: int = 1, popup: bool | None = None) -> str:
        technology = require_script_id(technology, label="technology ID")
        fields: dict[str, object] = {technology: level}
        if popup is not None:
            fields["popup"] = popup
        return effect_block("set_technology", fields)

    @classmethod
    def effect_set_technologies(cls, technologies: dict[str, int]) -> str:
        return effect_block(
            "set_technology",
            {
                require_script_id(technology, label="technology ID"): level
                for technology, level in technologies.items()
            },
        )

    @staticmethod
    def effect_add_timed_idea(idea_id: str, days: int) -> str:
        idea_id = require_script_id(idea_id, label="idea ID")
        return f"add_timed_idea = {{ idea = {idea_id} days = {days} }}"

    @staticmethod
    def effect_add_dynamic_modifier(
        modifier_id: str,
        *,
        days: int | str | None = None,
        scope: str | int | None = None,
    ) -> str:
        fields: dict[str, object] = {
            "modifier": require_script_id(
                modifier_id,
                label="dynamic modifier ID",
            )
        }
        if days is not None:
            fields["days"] = days
        if scope is not None:
            fields["scope"] = scope
        return effect_block("add_dynamic_modifier", fields)

    @staticmethod
    def effect_remove_dynamic_modifier(
        modifier_id: str,
        *,
        scope: str | int | None = None,
    ) -> str:
        fields: dict[str, object] = {
            "modifier": require_script_id(
                modifier_id,
                label="dynamic modifier ID",
            )
        }
        if scope is not None:
            fields["scope"] = scope
        return effect_block("remove_dynamic_modifier", fields)

    @staticmethod
    def effect_force_update_dynamic_modifier() -> str:
        return "force_update_dynamic_modifier = yes"

    @staticmethod
    def effect_swap_idea(old: str, new: str, target: str | None = None) -> str:
        old = require_script_id(old, label="idea ID")
        new = require_script_id(new, label="idea ID")
        effect = f"swap_ideas = {{ remove_idea = {old} add_idea = {new} }}"
        return scope_block(require_country_tag(target), effect) if target else effect

    @classmethod
    def effect_upgrade_idea_chain(cls, ideas: list[str], target: str | None = None) -> str:
        if len(ideas) < 2:
            raise ValueError("effect_upgrade_idea_chain() requires at least two idea IDs")
        ideas = [require_script_id(idea, label="idea ID") for idea in ideas]
        effects: list[str] = []
        for old, new in reversed(list(zip(ideas, ideas[1:]))):
            effects.append(
                f"if = {{ limit = {{ has_idea = {old} }} {cls.effect_swap_idea(old, new)} }}"
            )
        missing_checks = " ".join(f"NOT = {{ has_idea = {idea} }}" for idea in ideas)
        effects.append(f"if = {{ limit = {{ {missing_checks} }} add_ideas = {ideas[0]} }}")
        body = "\n".join(effects)
        return scope_block(require_country_tag(target), body) if target else body

    @staticmethod
    def effect_create_wargoal(target: str, war_goal_type: str = "annex_everything") -> str:
        target = require_country_tag(target)
        war_goal_type = require_script_id(war_goal_type, label="war goal type")
        return f"create_wargoal = {{ type = {war_goal_type} target = {target} }}"

    @staticmethod
    def effect_declare_war(target: str, war_goal_type: str = "annex_everything") -> str:
        target = require_country_tag(target)
        war_goal_type = require_script_id(war_goal_type, label="war goal type")
        return f"declare_war_on = {{ type = {war_goal_type} target = {target} }}"

    @classmethod
    def effect_declare_war_from(
        cls,
        actor: str,
        target: str,
        war_goal_type: str = "annex_everything",
    ) -> str:
        return cls.scope_block(
            require_country_tag(actor), cls.effect_declare_war(target, war_goal_type)
        )

    @staticmethod
    def effect_start_civil_war(
        ideology: str,
        size: float = 0.5,
        capital: int | None = None,
        *,
        effects: Sequence[str] | None = None,
    ) -> str:
        ideology = require_script_id(ideology, label="ideology ID")
        parts = [f"ideology = {ideology}", f"size = {size}"]
        if capital is not None:
            if not isinstance(capital, int) or isinstance(capital, bool) or capital <= 0:
                raise ValueError("Civil-war capital must be a positive state ID")
            parts.append(f"capital = {capital}")
        nested = [effect.strip() for effect in effects or () if effect and effect.strip()]
        if not nested:
            return f"start_civil_war = {{ {' '.join(parts)} }}"
        body = "\n".join(
            [
                *(f"\t{part}" for part in parts),
                *(
                    "\n".join(f"\t{line}" for line in effect.splitlines())
                    for effect in nested
                ),
            ]
        )
        return f"start_civil_war = {{\n{body}\n}}"

    @staticmethod
    def effect_load_focus_tree(
        tree_id: str,
        *,
        keep_completed: bool = False,
        copy_completed_from: str | None = None,
        mark_layout_dirty: bool = True,
    ) -> str:
        tree_id = require_script_id(tree_id, label="focus tree ID")
        fields: dict[str, object] = {"tree": tree_id, "keep_completed": keep_completed}
        if copy_completed_from:
            fields["copy_completed_from"] = require_country_tag(copy_completed_from)
        effects = [effect_block("load_focus_tree", fields)]
        if mark_layout_dirty:
            effects.append("mark_focus_tree_layout_dirty = yes")
        return "\n".join(effects)

    @classmethod
    def effect_spawn_civil_war_with_focus_tree(
        cls,
        ideology: str,
        tree_id: str,
        *,
        size: float = 0.5,
        capital: int | None = None,
        keep_completed: bool = False,
        copy_completed_from: str | None = None,
        mark_layout_dirty: bool = True,
    ) -> str:
        load_tree = cls.effect_load_focus_tree(
            tree_id,
            keep_completed=keep_completed,
            copy_completed_from=copy_completed_from,
            mark_layout_dirty=mark_layout_dirty,
        )
        return cls.effect_start_civil_war(
            ideology,
            size=size,
            capital=capital,
            effects=[load_tree],
        )

    @staticmethod
    def effect_set_politics(
        ruling_party: str,
        *,
        elections_allowed: bool | None = None,
        elections_frequency: int | None = None,
    ) -> str:
        ruling_party = require_script_id(ruling_party, label="ruling party")
        if elections_frequency is not None:
            raise ValueError(
                "HOI4's set_politics effect does not support elections_frequency; "
                "remove this argument"
            )
        fields: dict[str, object] = {"ruling_party": ruling_party}
        if elections_allowed is not None:
            fields["elections_allowed"] = elections_allowed
        return effect_block("set_politics", fields)

    @staticmethod
    def effect_create_faction(name: str) -> str:
        return f"create_faction = {pdx_string(name)}"

    @staticmethod
    def effect_add_to_faction(target: str) -> str:
        return f"add_to_faction = {require_country_tag(target)}"

    @classmethod
    def effect_add_target_to_faction(cls, faction_leader: str, target: str) -> str:
        """Add ``target`` to ``faction_leader``'s faction with explicit scope."""
        faction_leader = require_country_tag(faction_leader)
        target = require_country_tag(target)
        return cls.scope_block(faction_leader, f"add_to_faction = {target}")

    @classmethod
    def effect_join_faction(cls, actor: str, faction_leader: str) -> str:
        """Make ``actor`` join ``faction_leader``'s faction with explicit direction."""
        return cls.effect_add_target_to_faction(faction_leader, actor)

    @staticmethod
    def effect_white_peace(target: str = "all") -> str:
        return f"white_peace = {'all' if target == 'all' else require_country_tag(target)}"

    @staticmethod
    def effect_release(tag: str) -> str:
        return f"release = {require_country_tag(tag)}"

    @staticmethod
    def effect_release_puppet(tag: str) -> str:
        return f"release_puppet = {require_country_tag(tag)}"

    @classmethod
    def effect_end_puppet(cls, puppet: str, overlord: str | None = None) -> str:
        effect = f"end_puppet = {require_country_tag(puppet)}"
        return cls.scope_block(require_country_tag(overlord), effect) if overlord else effect

    @staticmethod
    def effect_set_autonomy(
        target: str,
        autonomy_state: str,
        *,
        freedom_level: float | int | None = None,
    ) -> str:
        fields: dict[str, object] = {
            "target": require_country_tag(target),
            "autonomy_state": require_script_id(autonomy_state, label="autonomy state"),
        }
        if freedom_level is not None:
            fields["freedom_level"] = freedom_level
        return effect_block("set_autonomy", fields)

    @classmethod
    def effect_convert_puppet_to_ally(
        cls,
        puppet: str,
        overlord: str,
        *,
        faction_leader: str | None = None,
    ) -> str:
        effects = [cls.effect_end_puppet(puppet, overlord)]
        if faction_leader:
            effects.append(cls.effect_join_faction(puppet, faction_leader))
        return "\n".join(effects)

    @staticmethod
    def effect_set_rule(rule: str, value: bool | str = True) -> str:
        return effect_block("set_rule", {require_script_id(rule, label="rule ID"): value})

    @staticmethod
    def effect_schedule_country_event(
        event_id: str, days: int = 0, target: str | None = None
    ) -> str:
        fields: dict[str, object] = {"id": require_script_id(event_id, label="event ID")}
        if days:
            fields["days"] = days
        effect = effect_block("country_event", fields)
        return scope_block(require_country_tag(target), effect) if target else effect

    @staticmethod
    def effect_division_template(
        name: str,
        regiments: str,
        support: str = "",
        division_names_group: str = "",
    ) -> str:
        fields = [
            f"name = {pdx_string(name)}",
            f"regiments = {{ {normalize_block_body(regiments)} }}",
        ]
        if support:
            fields.append(f"support = {{ {normalize_block_body(support)} }}")
        if division_names_group:
            fields.append(f"division_names_group = {pdx_string(division_names_group)}")
        return f"division_template = {{ {' '.join(fields)} }}"

    @staticmethod
    def effect_create_unit(
        division: str,
        owner: str | None = None,
        start_experience_factor: float | None = None,
    ) -> str:
        fields = [f"division = {pdx_string(division)}"]
        if owner:
            fields.append(f"owner = {require_country_tag(owner)}")
        if start_experience_factor is not None:
            fields.append(f"start_experience_factor = {start_experience_factor}")
        return f"create_unit = {{ {' '.join(fields)} }}"

    @classmethod
    def _effect_revolt_payload(
        cls,
        tag: str,
        *,
        manpower: int,
        equipment: dict[str, int] | None,
        technologies: dict[str, int] | None,
        division_template: str,
        units: list[str] | None,
    ) -> list[str]:
        scoped: list[str] = []
        if manpower:
            scoped.append(cls.effect_add_manpower(manpower))
        if technologies:
            scoped.append(cls.effect_set_technologies(technologies))
        for equipment_type, amount in (equipment or {}).items():
            scoped.append(cls.effect_add_equipment(equipment_type, amount))
        if division_template:
            scoped.append(division_template)
        for unit in units or []:
            scoped.append(cls.effect_create_unit(unit))
        return [cls.scope_block(tag, "\n".join(scoped))] if scoped else []

    @classmethod
    def effect_spawn_revolution(
        cls,
        tag: str,
        state_ids: list[int],
        *,
        overlord: str | None = None,
        manpower: int = 0,
        equipment: dict[str, int] | None = None,
        technologies: dict[str, int] | None = None,
        division_template: str = "",
        units: list[str] | None = None,
        faction_leader: str | None = None,
        war_goal_type: str = "annex_everything",
    ) -> str:
        tag = require_country_tag(tag)
        if not state_ids:
            raise ValueError("effect_spawn_revolution() requires at least one state ID")
        if not any([manpower, equipment, technologies, division_template, units]):
            warnings.warn(
                "effect_spawn_revolution() called without manpower, equipment, technologies, templates, or units; "
                "the revolt may spawn as an unplayable shell.",
                RuntimeWarning,
                stacklevel=2,
            )
        effects: list[str] = []
        for state_id in state_ids:
            effects.append(cls.effect_transfer_state_with_core(state_id, tag))
        effects.extend(
            cls._effect_revolt_payload(
                tag,
                manpower=manpower,
                equipment=equipment,
                technologies=technologies,
                division_template=division_template,
                units=units,
            )
        )
        if faction_leader:
            effects.append(cls.effect_join_faction(tag, faction_leader))
        if overlord:
            effects.append(cls.effect_declare_war_from(tag, overlord, war_goal_type))
        return "\n".join(effects)

    @classmethod
    def effect_convert_existing_or_spawn_revolt(
        cls,
        tag: str,
        state_ids: list[int],
        *,
        overlord: str | None = None,
        manpower: int = 0,
        equipment: dict[str, int] | None = None,
        technologies: dict[str, int] | None = None,
        division_template: str = "",
        units: list[str] | None = None,
        faction_leader: str | None = None,
        war_goal_type: str = "annex_everything",
    ) -> str:
        tag = require_country_tag(tag)
        if not state_ids:
            raise ValueError(
                "effect_convert_existing_or_spawn_revolt() requires at least one state ID"
            )
        existing_effects: list[str] = []
        if overlord:
            existing_effects.append(cls.effect_end_puppet(tag, overlord))
        for state_id in state_ids:
            existing_effects.append(cls.effect_transfer_state_with_core(state_id, tag))
        existing_effects.extend(
            cls._effect_revolt_payload(
                tag,
                manpower=manpower,
                equipment=equipment,
                technologies=technologies,
                division_template=division_template,
                units=units,
            )
        )
        if faction_leader:
            existing_effects.append(cls.effect_join_faction(tag, faction_leader))
        if overlord:
            existing_effects.append(cls.effect_declare_war_from(tag, overlord, war_goal_type))

        spawned = cls.effect_spawn_revolution(
            tag,
            state_ids,
            overlord=overlord,
            manpower=manpower,
            equipment=equipment,
            technologies=technologies,
            division_template=division_template,
            units=units,
            faction_leader=faction_leader,
            war_goal_type=war_goal_type,
        )
        return "\n".join(
            [
                f"if = {{ limit = {{ {tag} = {{ exists = yes }} }} {normalize_block_body(_join_script_parts(existing_effects))} }}",
                f"else = {{ {normalize_block_body(spawned)} }}",
            ]
        )

    @staticmethod
    def default_leader_ideology(ruling_party: str) -> str:
        return LEADER_IDEOLOGIES_BY_PARTY.get(
            ruling_party, LEADER_IDEOLOGIES_BY_PARTY["democratic"]
        )[0]

    @staticmethod
    def leader_ideologies_for_party(ruling_party: str) -> tuple[str, ...]:
        return LEADER_IDEOLOGIES_BY_PARTY.get(ruling_party, ())

    @staticmethod
    def ruling_parties() -> tuple[str, ...]:
        return RULING_PARTIES

    def available_ruling_parties(self, *, include_vanilla: bool = True) -> tuple[str, ...]:
        """Return ideology groups available to this mod, including custom groups."""

        parties = set(RULING_PARTIES) | set(self._ideologies)
        if include_vanilla:
            parties.update(self._vanilla_ideologies)
        return tuple(sorted(parties))

    def available_leader_ideologies(
        self, ruling_party: str, *, include_vanilla: bool = True
    ) -> tuple[str, ...]:
        """Return subtype IDs for a vanilla or custom ideology group."""

        ideology = self._ideologies.get(ruling_party)
        if ideology is None and include_vanilla:
            ideology = self._vanilla_ideologies.get(ruling_party)
        if ideology is not None:
            return tuple(subtype.name for subtype in ideology.types)
        return self.leader_ideologies_for_party(ruling_party)

    def _default_leader_ideology_for_party(self, ruling_party: str) -> str:
        available = self.available_leader_ideologies(ruling_party)
        if available:
            return available[0]
        return self.default_leader_ideology(ruling_party)

    def create_wargoal(self, target: str, war_goal_type: str = "annex_everything") -> str:
        warnings.warn(
            "create_wargoal() only builds script; use effect_create_wargoal()",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.effect_create_wargoal(target, war_goal_type)

    def declare_war(self, target: str, war_goal_type: str = "annex_everything") -> str:
        warnings.warn(
            "declare_war() only builds script; use effect_declare_war()",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.effect_declare_war(target, war_goal_type)

    def start_civil_war(self, ideology: str, size: float = 0.5, capital: int | None = None) -> str:
        warnings.warn(
            "start_civil_war() only builds script; use effect_start_civil_war()",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.effect_start_civil_war(ideology, size, capital)

    def validate_effect(
        self,
        script: str,
        suppress_warnings: list[str] | tuple[str, ...] | set[str] | None = None,
        *,
        script_token_allowlist: Sequence[str] = (),
        scope: str | None = "COUNTRY",
    ) -> list[ValidationError]:
        known_tags = set(load_all_tags(self.hoi4_install, self.mod_root))
        known_tags.update(self._countries.keys())
        known_ideas = (
            self._known_idea_ids()
            if (_IDEA_EFFECT_RE.search(script) or _HAS_IDEA_RE.search(script))
            else set(self._ideas)
        )
        known_events = (
            self._known_event_ids() if _EVENT_REF_RE.search(script) else set(self._events)
        )
        known_technologies = (
            self._known_technology_ids() if _TECH_BLOCK_RE.search(script) else set()
        )
        known_equipment = (
            self._known_equipment_ids() if _EQUIPMENT_STOCKPILE_RE.search(script) else set()
        )
        known_focus_trees = set(self._focus_trees)
        probe = Event(
            id="effect_probe.1",
            title="Effect Probe",
            description="Effect Probe",
            options=[
                EventOption(name="effect_probe.1.a", effect=script),
            ],
        )
        errors = validate_event(probe, namespace="effect_probe", known_tags=known_tags)
        entries: list[tuple[str, str, str | None, str | None]] = [
            (script, "effect", "effect_probe", None)
        ]
        errors.extend(
            self._validate_script_references(
                known_ideas=known_ideas,
                known_events=known_events,
                known_technologies=known_technologies,
                known_equipment=known_equipment,
                known_focus_trees=known_focus_trees,
                entries=entries,
            )
        )
        errors.extend(self._validate_state_effect_assumptions(entries=entries))
        if self.hoi4_install is not None:
            errors.extend(
                validate_script_sources(
                    [
                        ScriptSource(
                            script,
                            "effect",
                            "effect",
                            "effect_probe",
                            scope=scope,
                        )
                    ],
                    self.game_script_vocabulary(),
                    mod_root=self.mod_root,
                    allowlist=script_token_allowlist,
                )
            )
        return _filter_validation_errors(
            errors,
            suppress_warnings=suppress_warnings,
        )

    def create_industrial_branch(
        self,
        tree_id: str,
        tag: str,
        *,
        anchor_focus_id: str | None = None,
        state_id: int | None = None,
        grounded: bool = True,
    ) -> list[Focus]:
        tag = tag.upper()
        tree = (
            self.get_focus_tree(tree_id)
            if tree_id in self._focus_trees
            else self.create_focus_tree(tree_id, tag)
        )
        context = self.get_country_context(tag)
        target_state = (
            state_id or context.get("capital") or (context.get("states") or [{}])[0].get("id") or 1
        )
        y_base = max((focus.y for focus in tree.focuses), default=-1) + 1
        x_base = 0 if not tree.focuses else max(focus.x for focus in tree.focuses) + 2
        prefix = tag
        focuses = [
            Focus(
                id=f"{prefix}_map_domestic_industry",
                icon="GFX_goal_generic_construct_civ_factory",
                x=x_base,
                y=y_base,
                cost=5 if grounded else 10,
                search_filters=["FOCUS_FILTER_INDUSTRY"],
                completion_reward="add_political_power = 50",
            ),
            Focus(
                id=f"{prefix}_modernize_transport_links",
                icon="GFX_goal_generic_construct_infrastructure",
                x=0,
                y=1,
                cost=10,
                search_filters=["FOCUS_FILTER_INDUSTRY"],
                completion_reward=self.effect_add_infrastructure(int(target_state), 1),
            ),
            Focus(
                id=f"{prefix}_support_domestic_steel",
                icon="GFX_goal_generic_production",
                x=0,
                y=1,
                cost=10,
                search_filters=["FOCUS_FILTER_INDUSTRY"],
                completion_reward="\n".join(
                    [
                        self.effect_add_civilian_factory(int(target_state), 1),
                        self.effect_add_industry_bonus(
                            f"{prefix}_steel_industry_bonus", uses=1, bonus=0.5
                        ),
                    ]
                ),
            ),
            Focus(
                id=f"{prefix}_precision_workshops",
                icon="GFX_goal_generic_construct_mil_factory",
                x=0,
                y=1,
                cost=10,
                search_filters=["FOCUS_FILTER_INDUSTRY", "FOCUS_FILTER_RESEARCH"],
                completion_reward="\n".join(
                    [
                        self.effect_add_military_factory(int(target_state), 1),
                        self.effect_add_industry_bonus(
                            f"{prefix}_precision_tools_bonus", uses=1, bonus=0.5
                        ),
                    ]
                ),
            ),
        ]
        loc_text = {
            focuses[0].id: (
                "Map Domestic Industry",
                "A careful survey of local workshops, transport bottlenecks, and available capital will let us expand without pretending we are a great power.",
            ),
            focuses[1].id: (
                "Modernize Transport Links",
                "Industry depends on reliable roads and rail connections. Targeted improvements can make our small economy more resilient.",
            ),
            focuses[2].id: (
                "Support Domestic Steel",
                "Steel remains the backbone of our heavy industry. State support can help established firms modernize production.",
            ),
            focuses[3].id: (
                "Precision Workshops",
                "Small states must compete through skilled labor and precision tools rather than sheer scale.",
            ),
        }
        for focus in focuses:
            name, desc = loc_text[focus.id]
            self.set_focus_loc(focus.id, name, desc)

        if anchor_focus_id:
            self.insert_branch(tree_id, anchor_focus_id, focuses)
        else:
            for offset, focus in enumerate(focuses):
                focus.x = x_base
                focus.y = y_base + offset
                if offset and not focus.prerequisites:
                    focus.prerequisites = [[focuses[offset - 1].id]]
                self.add_focus(tree_id, focus)
        return focuses

    # ── Localization ─────────────────────────────────────────────

    def get_loc(self, key: str) -> Optional[str]:
        return self._loc_entries.get(self._normalize_loc_key(key))

    @staticmethod
    def _normalize_loc_key(key: str) -> str:
        return normalize_localization_key(key)

    def set_loc(self, key: str, value: str, file_path: str | Path | None = None) -> None:
        key = self._normalize_loc_key(key)
        old_source = self._loc_sources.get(key)
        self._loc_entries[key] = value
        if key.startswith("STATE_"):
            self._state_index_cache.clear()
        if file_path is not None:
            self._loc_sources[key] = resolve_mod_output_path(self.mod_root, file_path)
        elif key not in self._loc_sources:
            self._loc_sources[key] = self.default_loc_file
        self._dirty.add("localization")
        self._dirty_loc_keys.add(key)
        if old_source is not None:
            self._dirty_loc_files.add(old_source)
        self._dirty_loc_files.add(self._loc_sources[key])

    def delete_loc(self, key: str) -> bool:
        key = self._normalize_loc_key(key)
        if key in self._loc_entries:
            source = self._loc_sources.get(key, self.default_loc_file)
            del self._loc_entries[key]
            self._loc_sources.pop(key, None)
            if key.startswith("STATE_"):
                self._state_index_cache.clear()
            self._dirty.add("localization")
            self._dirty_loc_keys.add(key)
            self._dirty_loc_files.add(source)
            return True
        return False

    def search_loc(self, query: str) -> dict[str, str]:
        q = query.lower()
        return {k: v for k, v in self._loc_entries.items() if q in k.lower() or q in v.lower()}

    def all_loc(self) -> dict[str, str]:
        return dict(self._loc_entries)

    def get_country_context(self, tag: str, *, copy_states: bool = False) -> dict:
        tag = require_country_tag(tag)
        if not copy_states and not self._dirty and tag in self._country_context_cache:
            return copy.deepcopy(self._country_context_cache[tag])
        country = self.get_country(tag)
        context: dict = {
            "tag": tag,
            "name": country.name,
            "adjective": country.adjective,
            "capital": country.capital,
            "ruling_party": country.ruling_party,
            "popularities": dict(country.popularities),
            "leader": dataclasses.asdict(country.leader) if country.leader else None,
            "states": [],
            "ideas": [],
            "localization": {},
            "focus_trees": [],
        }

        for base in [self.hoi4_install, self.mod_root]:
            if base is None:
                continue
            states_dir = base / "history" / "states"
            if states_dir.exists():
                for state_path in sorted(states_dir.glob("*.txt")):
                    try:
                        state_text = state_path.read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        continue
                    if (
                        f"owner = {tag}" not in state_text
                        and f"add_core_of = {tag}" not in state_text
                    ):
                        continue
                    try:
                        state = read_state(state_path, text=state_text)
                    except Exception:
                        continue
                    if state.owner == tag or tag in state.cores:
                        if copy_states:
                            try:
                                state = self.get_state(state.id)
                                if self._materialize_state_override(state):
                                    self._dirty.add("states")
                                    self._dirty_states.add(state.id)
                            except KeyError:
                                pass
                        context["states"].append(dataclasses.asdict(state))

            for ideas_dir in [base / "common" / "ideas", base / "common" / "national_ideas"]:
                if not ideas_dir.exists():
                    continue
                candidates = [ideas_dir / f"{tag}.txt"]
                if country.name:
                    candidates.append(ideas_dir / f"{country.name.lower().replace(' ', '_')}.txt")
                for path in candidates:
                    if not path.exists():
                        continue
                    try:
                        ideas, _ = read_ideas_file(path)
                    except Exception:
                        continue
                    for idea in ideas:
                        context["ideas"].append(dataclasses.asdict(idea))

            focus_dir = base / "common" / "national_focus"
            if focus_dir.exists():
                for path in sorted(focus_dir.glob("*.txt")):
                    try:
                        raw_focus_text = path.read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        continue
                    focus_header = raw_focus_text.split("focus = {", 1)[0]
                    if (
                        f"tag = {tag}" not in focus_header
                        and f"original_tag = {tag}" not in focus_header
                    ):
                        continue
                    try:
                        tree = load_focus_tree(path, text=raw_focus_text)
                    except Exception:
                        continue
                    if tree and tree.country_tag == tag:
                        context["focus_trees"].append(
                            {
                                "id": tree.id,
                                "path": str(path),
                                "focus_ids": [focus.id for focus in tree.focuses],
                            }
                        )

        for key, value in self._loc_entries.items():
            if key == tag or key.startswith(f"{tag}_"):
                context["localization"][key] = value

        # Deduplicate repeated vanilla/mod fallback entries by stable IDs.
        context["states"] = list({state["id"]: state for state in context["states"]}.values())
        context["ideas"] = list({idea["id"]: idea for idea in context["ideas"]}.values())
        if not copy_states and not self._dirty:
            self._country_context_cache[tag] = copy.deepcopy(context)
        return context

    def ensure_country_states_in_mod(self, tag: str) -> list[State]:
        context = self.get_country_context(tag, copy_states=True)
        return [self.get_state(state["id"]) for state in context["states"]]

    def suggest_focus_icons(self, query: str, count: int = 5) -> list[str]:
        icons = sorted(self._known_focus_icons())
        if not icons:
            icons = sorted(COMMON_FOCUS_ICONS)
        return self._rank_icons(query, icons, count)

    def suggest_idea_icons(self, query: str, count: int = 5) -> list[str]:
        icons = sorted(
            icon.removeprefix(IDEA_SPRITE_PREFIX)
            for icon in self._known_focus_icons()
            if icon.startswith(IDEA_SPRITE_PREFIX)
        )
        return self._rank_icons(normalize_idea_icon(query), icons, count)

    @staticmethod
    def _rank_icons(query: str, icons: Sequence[str], count: int) -> list[str]:
        needle = query.lower().replace(" ", "_")

        needle_parts = {part for part in needle.split("_") if part}

        def score(icon: str) -> tuple[float, str]:
            low = icon.lower()
            low_parts = set(low.split("_"))
            substring = 2.0 if needle and needle in low else 0.0
            token_overlap = len(needle_parts & low_parts) / max(1, len(needle_parts))
            prefix = (
                len(os.path.commonprefix((needle, low))) / max(1, len(needle)) if needle else 0.0
            )
            return (substring + token_overlap + prefix, icon)

        return [icon for _, icon in nlargest(count, (score(icon) for icon in icons))]

    def suggest_focus_icon(self, query: str) -> str:
        suggestions = self.suggest_focus_icons(query, count=1)
        if not suggestions:
            raise ValueError(
                "No focus icons available from mod, vanilla install, or built-in fallback list"
            )
        return suggestions[0]

    def suggest_idea_icon(self, query: str) -> str:
        suggestions = self.suggest_idea_icons(query, count=1)
        if not suggestions:
            raise ValueError(
                "No idea icons available from mod or configured vanilla install"
            )
        return suggestions[0]

    # ── Complete country packages and geography ─────────────────

    def find_disconnected_states(
        self,
        tag: str,
        *,
        minimum_land_provinces: int = 2,
        allowed_state_ids: Sequence[int] = (),
    ) -> tuple[TerritoryComponent, ...]:
        """Find significant owned territory disconnected from the capital.

        This method imports NumPy and Pillow lazily and therefore requires the
        optional ``map`` extra only when it is called.
        """

        tag = require_country_tag(tag)
        if not self._is_known_country_tag(tag):
            raise KeyError(f"Country '{tag}' is not defined")
        country = self.get_country(tag)
        return find_country_territory_components(
            self.mod_root,
            self.hoi4_install,
            self._effective_states(),
            country_tag=tag,
            capital_state_id=country.capital,
            minimum_land_provinces=minimum_land_provinces,
            allowed_state_ids=allowed_state_ids,
        )

    def find_enclosed_foreign_states(
        self,
        tag: str,
        *,
        minimum_land_provinces: int = 1,
        allowed_state_ids: Sequence[int] = (),
    ) -> tuple[TerritoryComponent, ...]:
        """Find foreign land components entirely enclosed by ``tag``."""

        tag = require_country_tag(tag)
        if not self._is_known_country_tag(tag):
            raise KeyError(f"Country '{tag}' is not defined")
        return find_enclosed_foreign_components(
            self.mod_root,
            self.hoi4_install,
            self._effective_states(),
            country_tag=tag,
            minimum_land_provinces=minimum_land_provinces,
            allowed_state_ids=allowed_state_ids,
        )

    def _country_runtime_activation_evidence(
        self,
        tag: str,
    ) -> tuple[set[int], set[int], set[str], set[str]]:
        """Return runtime state transfers, cores, activators, and source labels."""

        scripts: list[tuple[str, str]] = []

        def add_script(label: str, body: str, path: Path | None) -> None:
            if not body.strip():
                return
            source = f"{self._display_path(path)}:{label}" if path else label
            scripts.append((source, body))

        for tree in self._focus_trees.values():
            for focus in tree.focuses:
                add_script(
                    f"focus {focus.id} completion_reward",
                    focus.completion_reward,
                    tree.path,
                )
                add_script(
                    f"focus {focus.id} select_effect",
                    focus.select_effect,
                    tree.path,
                )
        for event in self._events.values():
            add_script(f"event {event.id} immediate", event.immediate, event.path)
            for index, option in enumerate(event.options):
                add_script(
                    f"event {event.id} option {index}",
                    option.effect,
                    event.path,
                )
        for decision in self._decisions.values():
            add_script(
                f"decision {decision.id} complete_effect",
                decision.complete_effect,
                decision.path,
            )
            add_script(
                f"decision {decision.id} remove_effect",
                decision.remove_effect,
                decision.path,
            )
        for occurrences in self._on_action_occurrences.values():
            for action in occurrences:
                add_script(
                    f"on_action {action.id} effect",
                    action.effect,
                    action.path,
                )

        transferred_states: set[int] = set()
        cored_states: set[int] = set()
        activators: set[str] = set()
        sources: set[str] = set()

        def scalar_int(node: PdxNode) -> int | None:
            if node.value is None:
                return None
            try:
                return int(node.value)
            except ValueError:
                return None

        def walk(
            node: PdxNode,
            *,
            in_country_scope: bool = False,
            state_scope: int | None = None,
        ) -> None:
            country_scope = in_country_scope
            nested_state_scope = state_scope
            if node.is_block():
                if node.key == tag:
                    country_scope = True
                    nested_state_scope = None
                elif node.key and node.key.isdigit():
                    nested_state_scope = int(node.key)
                    country_scope = False
            if country_scope and node.key == "transfer_state":
                state_id = scalar_int(node)
                if state_id is not None:
                    transferred_states.add(state_id)
            if (
                nested_state_scope is not None
                and node.key == "add_core_of"
                and node.value == tag
            ):
                cored_states.add(nested_state_scope)
            if node.key in {"release", "release_puppet", "puppet"} and node.value == tag:
                activators.add(node.key)
            if node.key == "create_subject" and node.is_block():
                if any(
                    child.key in {"subject", "target"} and child.value == tag
                    for child in node.children
                ):
                    activators.add("create_subject")
            for child in node.children:
                walk(
                    child,
                    in_country_scope=country_scope,
                    state_scope=nested_state_scope,
                )

        for source, body in scripts:
            before = (
                len(transferred_states),
                len(cored_states),
                len(activators),
            )
            try:
                root = parse_pdx(body)
            except ParseError:
                # General script validation owns syntax diagnostics. Lifecycle
                # inference must never conceal or duplicate those findings.
                continue
            walk(root)
            after = (
                len(transferred_states),
                len(cored_states),
                len(activators),
            )
            if after != before:
                sources.add(source)

        return transferred_states, cored_states, activators, sources

    def validate_country_package(
        self,
        tag: str,
        *,
        minimum_land_provinces: int = 2,
        allowed_state_ids: Sequence[int] = (),
        check_geography: bool = True,
        lifecycle: Literal["auto", "starting", "runtime"] = "auto",
    ) -> CountryPackageReport:
        """Validate one complete country package with lifecycle awareness.

        ``auto`` treats a country owning scenario-start states as ``starting``.
        A country with no starting territory is ``runtime`` only when loaded
        focus, event, decision, or on-action effects provide activation
        evidence. Otherwise its lifecycle remains unresolved and validation
        fails with a targeted remediation.

        A complete report proves structural authoring completeness only. It
        does not prove that runtime popularity thresholds or other dynamic
        trigger arithmetic are achievable in play.
        """

        tag = require_country_tag(tag)
        if not self._is_known_country_tag(tag):
            raise KeyError(f"Country '{tag}' is not defined")
        if lifecycle not in {"auto", "starting", "runtime"}:
            raise ValueError(
                "lifecycle must be 'auto', 'starting', or 'runtime'"
            )
        country = self.get_country(tag)
        findings: list[ValidationError] = []
        recruited = set(country.recruited_characters)
        roster = {
            character.id: character
            for character in self._characters.values()
            if character.country_tag == tag or character.id in recruited
        }

        def issue(
            code: str,
            message: str,
            *,
            file_path: str | None = None,
            state_id: int | None = None,
            severity: str = "error",
        ) -> None:
            findings.append(
                ValidationError(
                    message=message,
                    severity=severity,
                    code=code,
                    country_tag=tag,
                    file_path=file_path,
                    state_id=state_id,
                )
            )

        flag_paths = (
            self.mod_root / "gfx" / "flags" / f"{tag}.tga",
            self.mod_root / "gfx" / "flags" / "medium" / f"{tag}.tga",
            self.mod_root / "gfx" / "flags" / "small" / f"{tag}.tga",
        )
        missing_flags = [
            path.relative_to(self.mod_root).as_posix()
            for path in flag_paths
            if not path.is_file() and path not in self._pending_asset_writes
        ]
        if missing_flags:
            issue(
                "missing_country_flag",
                (
                    f"Country '{tag}' is missing required flag size(s): "
                    f"{', '.join(missing_flags)}. Import a source image with "
                    "import_flag_to_mod()."
                ),
            )

        requires_generated_names = False
        for reference in self._country_oob_references(country):
            try:
                if self.get_oob(reference.name).air_wings:
                    requires_generated_names = True
                    break
            except (KeyError, ValueError):
                continue
        if requires_generated_names and tag not in self._known_country_name_pool_tags():
            issue(
                "missing_country_name_pool",
                (
                    f"Country '{tag}' has no common/names entry. HOI4 will fail "
                    "to generate names for aces and other dynamic characters. "
                    "Call set_country_name_pool() with culturally appropriate "
                    "given names and surnames."
                ),
            )

        undefined = sorted(recruited - set(roster))
        for character_id in undefined:
            issue(
                "undefined_recruited_character",
                (
                    f"Country '{tag}' recruits undefined character "
                    f"'{character_id}'. Create it or remove the recruitment entry."
                ),
            )

        for character in roster.values():
            if character.recruitment_expected is True and character.id not in recruited:
                issue(
                    "unrecruited_character",
                    (
                        f"Character '{character.id}' was created for immediate "
                        f"recruitment but is not recruited by country '{tag}'."
                    ),
                    file_path=str(character.path) if character.path else None,
                )

        recruited_characters = [
            character
            for character_id, character in roster.items()
            if character_id in recruited
        ]
        leader_ids = {
            character.id
            for character in recruited_characters
            if any(
                isinstance(role, CountryLeaderRole)
                for role in self._all_character_roles(character)
            )
        }
        if not leader_ids:
            issue(
                "missing_country_leader",
                (
                    f"Country '{tag}' has no recruited character with a "
                    "country_leader role."
                ),
            )

        advisor_ids = {
            character.id
            for character in recruited_characters
            if any(
                isinstance(role, AdvisorRole)
                and role.slot == "political_advisor"
                for role in self._all_character_roles(character)
            )
        }
        commander_ids = {
            character.id
            for character in recruited_characters
            if any(
                isinstance(role, ArmyCommanderRole)
                for role in self._all_character_roles(character)
            )
        }
        if len(advisor_ids) < 2:
            issue(
                "insufficient_political_advisors",
                (
                    f"Country '{tag}' has {len(advisor_ids)} recruited political "
                    "advisor(s); create and recruit at least 2."
                ),
            )
        if len(commander_ids) < 2:
            issue(
                "insufficient_military_commanders",
                (
                    f"Country '{tag}' has {len(commander_ids)} recruited army "
                    "commander(s); create and recruit at least 2."
                ),
            )

        sprite_textures = self._known_sprite_textures()
        visible_characters = [
            character
            for character in roster.values()
            if self._all_character_roles(character)
        ]
        for character in visible_characters:
            portraits = list(character.portraits)
            portraits.extend(
                portrait
                for instance in character.instances
                if instance.roles
                for portrait in instance.portraits
            )
            sprite_names = {
                sprite
                for portrait in portraits
                for sprite in (portrait.large, portrait.small)
                if sprite
            }
            roles = self._all_character_roles(character)
            if any(isinstance(role, AdvisorRole) for role in roles) and not any(
                portrait.channel == "civilian" and portrait.small
                for portrait in portraits
            ):
                issue(
                    "missing_character_small_portrait",
                    (
                        f"Advisor character '{character.id}' has no civilian small "
                        "portrait. Advisor cards require portraits.civilian.small "
                        "and a resolvable interface/*.gfx sprite."
                    ),
                    file_path=str(character.path) if character.path else None,
                )
            if not sprite_names:
                issue(
                    "missing_character_portrait",
                    (
                        f"Visible character '{character.id}' has no portrait. "
                        "Import one and declare its sprite with write_portrait_gfx()."
                    ),
                    file_path=str(character.path) if character.path else None,
                )
            for sprite_name in sorted(sprite_names):
                texture = sprite_textures.get(sprite_name)
                if texture is None:
                    issue(
                        "missing_character_portrait_gfx",
                        (
                            f"Character '{character.id}' portrait sprite "
                            f"'{sprite_name}' has no interface/*.gfx declaration."
                        ),
                        file_path=str(character.path) if character.path else None,
                    )
                    continue
                if not self._texture_exists(texture):
                    issue(
                        "missing_character_portrait_texture",
                        (
                            f"Character '{character.id}' portrait sprite "
                            f"'{sprite_name}' points to missing texture '{texture}'."
                        ),
                        file_path=str(character.path) if character.path else None,
                    )
            if character.id not in self._loc_entries:
                issue(
                    "missing_character_localization",
                    (
                        f"Character '{character.id}' is missing name "
                        f"localization key '{character.id}'."
                    ),
                    file_path=str(character.path) if character.path else None,
                )

        owned_states = [
            state for state in self._effective_states() if state.owner == tag
        ]
        owned_by_id = {state.id: state for state in owned_states}
        (
            runtime_state_ids,
            runtime_core_ids,
            runtime_activators,
            activation_sources,
        ) = self._country_runtime_activation_evidence(tag)
        resolved_lifecycle: str = lifecycle
        if lifecycle == "auto":
            if owned_states:
                resolved_lifecycle = "starting"
            elif runtime_state_ids or runtime_activators:
                resolved_lifecycle = "runtime"
            else:
                resolved_lifecycle = "unresolved"

        if resolved_lifecycle == "unresolved" or (
            resolved_lifecycle == "runtime"
            and not runtime_state_ids
            and not runtime_activators
        ):
            issue(
                "missing_country_activation",
                (
                    f"Country '{tag}' owns no scenario-start states and has no "
                    "runtime activation path. Assign starting territory and an "
                    "OOB, or add a focus/event/decision/on-action release path "
                    "that gives the country territory."
                ),
            )
        elif resolved_lifecycle == "runtime" and runtime_state_ids:
            if country.capital not in runtime_state_ids:
                issue(
                    "runtime_capital_not_assigned",
                    (
                        f"Runtime-created country '{tag}' receives states "
                        f"{sorted(runtime_state_ids)}, but not its declared "
                        f"capital state {country.capital}."
                    ),
                    state_id=country.capital,
                )
            elif (
                country.capital in runtime_state_ids
                and country.capital not in runtime_core_ids
            ):
                issue(
                    "runtime_capital_not_cored",
                    (
                        f"Runtime-created country '{tag}' receives capital state "
                        f"{country.capital}, but no runtime add_core_of = {tag} "
                        "was found for that state."
                    ),
                    state_id=country.capital,
                )

        matching_oobs = [
            oob
            for oob in self._oobs.values()
            if oob.country_tag == tag and oob.kind == "land"
        ]
        land_references = [
            reference
            for reference in self._country_oob_references(country)
            if reference.kind == "land"
        ]
        package_oob: OrderOfBattle | None = None
        if resolved_lifecycle == "starting":
            if land_references:
                try:
                    package_oob = self.get_oob(land_references[0].name)
                except KeyError:
                    issue(
                        "missing_oob_reference",
                        (
                            f"Country '{tag}' references missing OOB "
                            f"'{land_references[0].name}'."
                        ),
                    )
            elif len(matching_oobs) == 1:
                # Some scenarios deliberately load their sole tag-prefixed OOB
                # from an on-action rather than the country-history ``oob`` key.
                package_oob = matching_oobs[0]
            else:
                issue(
                    "missing_land_oob",
                    (
                        f"Starting country '{tag}' has no unambiguous land OOB. "
                        "Use create_oob(..., assign=True), or explicitly load one "
                        "tag-owned OOB from scenario script."
                    ),
                )
        if package_oob is not None:
            if not package_oob.templates or not package_oob.divisions:
                issue(
                    "empty_land_oob",
                    (
                        f"Country '{tag}' OOB '{package_oob.name}' must contain at "
                        "least one land template and one starting division."
                    ),
                    file_path=(
                        str(package_oob.path)
                        if package_oob.path
                        else None
                    ),
                )
            findings.extend(validate_oob(package_oob))
            findings.extend(self._validate_oob_context(package_oob, tag))

        capital = owned_by_id.get(country.capital)
        if resolved_lifecycle == "starting":
            if not owned_states:
                issue(
                    "no_owned_territory",
                    (
                        f"Starting country '{tag}' owns no states. Assign "
                        "scenario-start territory before treating the package "
                        "as complete."
                    ),
                )
            if capital is None:
                issue(
                    "capital_not_owned",
                    (
                        f"Starting country '{tag}' capital state "
                        f"{country.capital} is not owned by {tag}."
                    ),
                    state_id=country.capital,
                )
            elif tag not in capital.cores:
                issue(
                    "capital_not_cored",
                    (
                        f"Starting country '{tag}' capital state "
                        f"{country.capital} is not cored by {tag}."
                    ),
                    state_id=country.capital,
                )

        if (
            resolved_lifecycle == "starting"
            and check_geography
            and capital is not None
        ):
            try:
                components = self.find_disconnected_states(
                    tag,
                    minimum_land_provinces=minimum_land_provinces,
                    allowed_state_ids=allowed_state_ids,
                )
            except (FileNotFoundError, RuntimeError):
                # Topology is intentionally optional; callers who require it
                # can invoke find_disconnected_states() directly.
                components = ()
            for component in components:
                issue(
                    "disconnected_country_territory",
                    (
                        f"Country '{tag}' owns a component disconnected from "
                        f"capital state {country.capital}: states "
                        f"{list(component.state_ids)}, "
                        f"{component.land_province_count} land province(s). "
                        "Fix the border or pass allowed_state_ids for a deliberate "
                        "island/overseas component."
                    ),
                    state_id=component.state_ids[0],
                    severity="warning",
                )
            try:
                enclosed = self.find_enclosed_foreign_states(
                    tag,
                    allowed_state_ids=allowed_state_ids,
                )
            except (FileNotFoundError, RuntimeError):
                enclosed = ()
            for component in enclosed:
                issue(
                    "enclosed_foreign_territory",
                    (
                        f"Country '{tag}' completely encloses foreign states "
                        f"{list(component.state_ids)} containing "
                        f"{component.land_province_count} land province(s). "
                        "This often means a state was missed during a border "
                        "transfer; fix it or explicitly allow the state."
                    ),
                    state_id=component.state_ids[0],
                    severity="warning",
                )

        return CountryPackageReport(
            tag=tag,
            findings=tuple(findings),
            lifecycle=resolved_lifecycle,
            advisor_count=len(advisor_ids),
            commander_count=len(commander_ids),
            character_count=len(roster),
            owned_state_count=len(owned_states),
            runtime_state_ids=tuple(sorted(runtime_state_ids)),
            activation_sources=tuple(sorted(activation_sources)),
        )

    @staticmethod
    def _all_character_roles(character: Character) -> tuple[CharacterRole, ...]:
        return tuple(
            [
                *character.roles,
                *(
                    role
                    for instance in character.instances
                    for role in instance.roles
                ),
            ]
        )

    def _effective_states(self) -> list[State]:
        result: list[State] = []
        for state_id in sorted(set(self._state_ids) | set(self._states)):
            try:
                result.append(self.get_state(state_id))
            except (KeyError, OSError, ValueError):
                continue
        return result

    def _is_known_country_tag(self, tag: str) -> bool:
        return (
            tag in self._countries
            or tag in self._vanilla_tags
            or any(
                tag in mapping
                for mapping in self._country_tag_mappings.values()
            )
        )

    def _validate_country_colors_shadow(self) -> list[ValidationError]:
        if self.hoi4_install is None:
            return []
        mod_colors = self.mod_root / "common" / "countries" / "colors.txt"
        vanilla_colors = self.hoi4_install / "common" / "countries" / "colors.txt"
        if not mod_colors.is_file() or not vanilla_colors.is_file():
            return []
        mod_tags = country_color_tags(
            mod_colors.read_text(encoding="utf-8", errors="ignore")
        )
        vanilla_tags = country_color_tags(
            vanilla_colors.read_text(encoding="utf-8", errors="ignore")
        )
        missing = sorted(vanilla_tags - mod_tags)
        if not missing:
            return []
        sample = ", ".join(missing[:8])
        if len(missing) > 8:
            sample += ", …"
        return [
            ValidationError(
                message=(
                    f"{mod_colors} defines {len(mod_tags)} country color entries but "
                    f"the configured HOI4 install defines {len(vanilla_tags)}. HOI4 "
                    f"shadows this file wholesale; {len(missing)} vanilla entries are "
                    f"missing ({sample}). Remove colors.txt for mod-only tags or seed it "
                    "from vanilla before overriding an existing tag."
                ),
                severity="warning",
                code="country_colors_shadow_vanilla",
                file_path=str(mod_colors),
            )
        ]

    # ── Validation ───────────────────────────────────────────────

    def validate(
        self,
        suppress_warnings: list[str] | tuple[str, ...] | set[str] | None = None,
        validate_icons: bool = False,
        strict_localization: bool = False,
        stage: ValidationStage = "package",
        script_token_allowlist: Sequence[str] = (),
        liveness_flag_allowlist: Sequence[str] = (),
        liveness_localization_allowlist: Sequence[str] = (),
        progress: ProgressCallback | None = None,
        cancelled: CancelCallback | None = None,
    ) -> list[ValidationError]:
        stage = require_validation_stage(stage)
        errors: list[ValidationError] = []
        phase_total = 12 + (1 if self.hoi4_install is not None else 0)
        if stage == "build":
            phase_total -= 1
        elif stage == "release":
            phase_total += 1
        phase_current = 0

        def begin_phase(phase: str, message: str) -> None:
            nonlocal phase_current
            check_cancelled(cancelled, operation="validation")
            report_progress(
                progress,
                operation="validation",
                phase=phase,
                current=phase_current,
                total=phase_total,
                message=message,
            )
            phase_current += 1

        begin_phase("load_diagnostics", "Checking load and duplicate diagnostics")

        duplicate_codes = {
            "country_tag": "duplicate_country_tag",
            "character": "duplicate_character_id",
            "state": "duplicate_state_id",
            "focus_tree": "duplicate_focus_tree_id",
            "focus": "duplicate_focus_id",
            "event": "duplicate_event_id",
            "decision_category": "duplicate_decision_category_id",
            "decision": "duplicate_decision_id",
            "idea": "duplicate_idea_id",
            "ideology": "duplicate_ideology_id",
            "dynamic_modifier": "duplicate_dynamic_modifier_id",
            "bookmark": "duplicate_bookmark_name",
        }
        for diagnostic in self._load_diagnostics:
            if diagnostic.error_type != "DuplicateIdentifierError":
                errors.append(
                    ValidationError(
                        message=(
                            f"Failed to load {diagnostic.section} file "
                            f"{diagnostic.path}: {diagnostic.error_type}: "
                            f"{diagnostic.message}"
                        ),
                        severity="error",
                        code="load_failure",
                        file_path=str(diagnostic.path),
                    )
                )
                continue
            errors.append(
                ValidationError(
                    message=diagnostic.message,
                    severity="error",
                    code=duplicate_codes.get(
                        diagnostic.section, "duplicate_identifier"
                    ),
                    file_path=str(diagnostic.path),
                    related_file_path=(
                        str(diagnostic.related_path)
                        if diagnostic.related_path is not None
                        else None
                    ),
                    country_tag=(
                        diagnostic.identifier
                        if diagnostic.section == "country_tag"
                        else None
                    ),
                    state_id=(
                        int(diagnostic.identifier)
                        if diagnostic.section == "state" and diagnostic.identifier
                        else None
                    ),
                    event_id=(
                        diagnostic.identifier
                        if diagnostic.section == "event"
                        else None
                    ),
                    idea_id=(
                        diagnostic.identifier
                        if diagnostic.section == "idea"
                        else None
                    ),
                    decision_id=(
                        diagnostic.identifier
                        if diagnostic.section == "decision"
                        else None
                    ),
                    focus_tree_id=(
                        diagnostic.identifier
                        if diagnostic.section == "focus_tree"
                        else None
                    ),
                    ideology_id=(
                        diagnostic.identifier
                        if diagnostic.section == "ideology"
                        else None
                    ),
                    dynamic_modifier_id=(
                        diagnostic.identifier
                        if diagnostic.section == "dynamic_modifier"
                        else None
                    ),
                    bookmark_name=(
                        diagnostic.identifier
                        if diagnostic.section == "bookmark"
                        else None
                    ),
                )
            )

        begin_phase("source_files", "Checking all script and localization source files")
        errors.extend(_validate_source_tree(self.mod_root))

        begin_phase("catalogs", "Resolving referenced game and mod catalogs")
        known_tags = set(load_all_tags(self.hoi4_install, self.mod_root))
        known_tags.update(self._countries.keys())
        script_entries = self._script_entries()
        scripts = "\n".join(entry[0] for entry in script_entries)
        need_ideas = any(country.ideas for country in self._countries.values()) or bool(
            _IDEA_EFFECT_RE.search(scripts) or _HAS_IDEA_RE.search(scripts)
        )
        known_ideas = self._known_idea_ids() if need_ideas else set(self._ideas)
        known_events = (
            self._known_event_ids() if _EVENT_REF_RE.search(scripts) else set(self._events)
        )
        known_technologies = (
            self._known_technology_ids() if _TECH_BLOCK_RE.search(scripts) else set()
        )
        known_equipment = (
            self._known_equipment_ids() if _EQUIPMENT_STOCKPILE_RE.search(scripts) else set()
        )
        known_focus_trees = set(self._focus_trees)
        known_focus_icons = (
            self._known_focus_icons()
            if validate_icons and (self._focus_trees or self._ideas)
            else set()
        )
        known_state_ids = {entry["id"] for entry in self.state_index()}
        known_character_ids = self._known_character_ids()
        errors.extend(validate_tag_definition_targets(self.mod_root))
        errors.extend(
            validate_country_history_references(
                self.mod_root,
                known_state_ids=known_state_ids,
                known_character_ids=known_character_ids,
            )
        )

        begin_phase("countries", "Validating countries")
        errors.extend(self._validate_country_colors_shadow())
        known_parties = set(self._vanilla_ideologies) | set(self._ideologies)
        for country in list(self._countries.values()):
            errors.extend(validate_country(country, known_parties=known_parties))
            errors.extend(self._validate_equipment_variant_unlocks(country))
        begin_phase("oobs", "Validating land orders of battle")
        validated_oobs: set[str] = set()
        for country in list(self._countries.values()):
            for reference in self._country_oob_references(country):
                try:
                    oob = self.get_oob(reference.name)
                except KeyError:
                    errors.append(
                        ValidationError(
                            message=(
                                f"Country '{country.tag}' references missing "
                                f"{reference.kind} OOB '{reference.name}'"
                            ),
                            severity="error",
                            code="missing_oob_reference",
                            country_tag=country.tag,
                        )
                    )
                    continue
                oob.country_tag = country.tag
                oob.kind = reference.kind
                oob.required_dlc = reference.required_dlc
                oob.excluded_dlc = reference.excluded_dlc
                if oob.name not in validated_oobs:
                    errors.extend(validate_oob(oob))
                    validated_oobs.add(oob.name)
                errors.extend(self._validate_oob_context(oob, country.tag))
        for name, oob in self._oobs.items():
            if name not in validated_oobs:
                errors.extend(validate_oob(oob))
                if oob.country_tag:
                    errors.extend(
                        self._validate_oob_context(oob, oob.country_tag)
                    )
        begin_phase("states", "Validating states")
        for state_id in sorted(set(self._state_ids) | set(self._states)):
            check_cancelled(cancelled, operation="validation")
            try:
                state = self.get_state(state_id)
            except (KeyError, OSError, ValueError) as error:
                errors.append(
                    ValidationError(
                        message=f"Could not validate state {state_id}: {error}",
                        severity="error",
                        code="state_load_error",
                        state_id=state_id,
                        file_path=(
                            str(self._state_source_paths[state_id])
                            if state_id in self._state_source_paths
                            else None
                        ),
                    )
                )
                continue
            errors.extend(validate_state(state, known_tags))

        if stage != "build":
            begin_phase("country_packages", "Validating complete SDK-created countries")
            existing_findings = {
                (
                    finding.code,
                    finding.message,
                    finding.file_path,
                    finding.country_tag,
                    finding.state_id,
                )
                for finding in errors
            }
            for tag in sorted(self._created_country_tags & set(self._countries)):
                package = self.validate_country_package(tag)
                for finding in package.findings:
                    identity = (
                        finding.code,
                        finding.message,
                        finding.file_path,
                        finding.country_tag,
                        finding.state_id,
                    )
                    if identity in existing_findings:
                        continue
                    errors.append(finding)
                    existing_findings.add(identity)

        begin_phase("events", "Validating events and on-actions")
        for event_id, event in self._events.items():
            errors.extend(
                validate_event(
                    event, namespace=self._event_namespaces.get(event_id), known_tags=known_tags
                )
            )

        for occurrences in self._on_action_occurrences.values():
            for action in occurrences:
                if action.effect:
                    probe = Event(
                        id=f"on_action.{action.id}",
                        title=action.id,
                        description=action.id,
                        options=[
                            EventOption(name=f"on_action.{action.id}.a", effect=action.effect),
                        ],
                    )
                    errors.extend(
                        validate_event(probe, namespace="on_action", known_tags=known_tags)
                    )

        begin_phase("ideas", "Validating ideas and assignments")
        for idea in self._ideas.values():
            errors.extend(validate_idea(idea))
            if (
                validate_icons
                and known_focus_icons
                and resolve_idea_sprite(idea.icon) not in known_focus_icons
            ):
                suggestions = self.suggest_idea_icons(idea.icon, count=1)
                suggestion = (
                    f" Suggested close match: {suggestions[0]}"
                    if suggestions
                    else ""
                )
                errors.append(
                    ValidationError(
                        message=(
                            f"Idea '{idea.id}' picture stem '{idea.icon}' did not "
                            f"resolve to sprite '{resolve_idea_sprite(idea.icon)}'."
                            f"{suggestion}"
                        ),
                        severity="warning",
                        code="unknown_idea_icon",
                        idea_id=idea.id,
                        file_path=str(idea.path) if idea.path else None,
                    )
                )

        for ideology in self._ideologies.values():
            errors.extend(validate_ideology(ideology))
        for dynamic_modifier in self._dynamic_modifiers.values():
            errors.extend(validate_dynamic_modifier(dynamic_modifier))
        for bookmark in self._bookmarks:
            errors.extend(validate_bookmark(bookmark))

        for country in self._countries.values():
            for idea_id in country.ideas:
                assigned_idea = self._ideas.get(idea_id)
                if assigned_idea is None and idea_id not in known_ideas:
                    errors.append(
                        ValidationError(
                            message=f"Country '{country.tag}' assigns unknown idea '{idea_id}'",
                            severity="warning",
                            code="unknown_assigned_idea",
                            country_tag=country.tag,
                            idea_id=idea_id,
                        )
                    )
                elif assigned_idea is not None and assigned_idea.category != "country":
                    errors.append(
                        ValidationError(
                            message=(
                                f"Country '{country.tag}' assigns idea '{idea_id}', but that idea is in category "
                                f"'{assigned_idea.category or '<none>'}', not 'country'."
                            ),
                            severity="warning",
                            code="assigned_idea_not_country_category",
                            country_tag=country.tag,
                            idea_id=idea_id,
                        )
                    )

        begin_phase("focus_catalog", "Preparing focus validation context")
        all_focus_ids: set[str] = set()
        begin_phase("focus_trees", "Validating focus trees")
        for tree in self._focus_trees.values():
            check_cancelled(cancelled, operation="validation")
            all_focus_ids.update(f.id for f in tree.focuses)

        icon_suggestions: dict[str, str] = {}
        if validate_icons and known_focus_icons:
            for icon in {
                focus.icon
                for tree in self._focus_trees.values()
                for focus in tree.focuses
                if focus.icon not in known_focus_icons
            }:
                icon_suggestions[icon] = self.suggest_focus_icon(icon)

        for tree in self._focus_trees.values():
            tree_errors = validate_focus_tree(
                tree,
                known_focus_ids=all_focus_ids,
                known_state_ids=known_state_ids if known_state_ids else None,
                known_tags=known_tags,
            )
            errors.extend(tree_errors)
            try:
                self.assert_no_visual_overlap(tree.id)
            except ValueError as exc:
                errors.append(
                    ValidationError(
                        message=f"Focus tree '{tree.id}' visual overlap risk: {exc}",
                        severity="warning",
                        code="visual_overlap",
                        file_path=str(tree.path) if tree.path else None,
                    )
                )

        for tree in self._focus_trees.values():
            for focus in tree.focuses:
                loc_key = f"{focus.id}:0"
                if loc_key not in self._loc_entries and focus.id not in self._loc_entries:
                    errors.append(
                        ValidationError(
                            message=f"No localization found for focus '{focus.id}'",
                            severity="warning",
                            code="missing_localization",
                            focus_id=focus.id,
                            file_path=str(tree.path) if tree.path else None,
                        )
                    )
                desc_key = f"{focus.id}_desc"
                desc_colon_key = f"{focus.id}_desc:0"
                if desc_key not in self._loc_entries and desc_colon_key not in self._loc_entries:
                    errors.append(
                        ValidationError(
                            message=f"No description localization found for focus '{focus.id}'",
                            severity="warning",
                            code="missing_localization",
                            focus_id=focus.id,
                            file_path=str(tree.path) if tree.path else None,
                        )
                    )
                if validate_icons and known_focus_icons and focus.icon not in known_focus_icons:
                    suggestion = icon_suggestions[focus.icon]
                    errors.append(
                        ValidationError(
                            message=f"Focus '{focus.id}' icon '{focus.icon}' was not found. Suggested close match: {suggestion}",
                            severity="warning",
                            code="unknown_focus_icon",
                            focus_id=focus.id,
                            file_path=str(tree.path) if tree.path else None,
                        )
                    )

        begin_phase("references", "Validating cross-content references")
        errors.extend(
            self._validate_script_references(
                known_ideas=known_ideas,
                known_events=known_events,
                known_technologies=known_technologies,
                known_equipment=known_equipment,
                known_focus_trees=known_focus_trees,
            )
        )
        errors.extend(self._validate_state_effect_assumptions())
        errors.extend(self._validate_idea_mutation_collisions())
        if self.hoi4_install is not None:
            begin_phase(
                "script_vocabulary",
                "Checking effect, trigger, and modifier names",
            )
            errors.extend(
                validate_script_sources(
                    self._semantic_script_sources(),
                    self.game_script_vocabulary(),
                    mod_root=self.mod_root,
                    allowlist=script_token_allowlist,
                )
            )
        if strict_localization:
            errors.extend(self._validate_localization_references())
        if stage == "release":
            begin_phase("content_liveness", "Analyzing semantic content liveness")
            errors.extend(
                self.analyze_content_liveness(
                    flag_allowlist=liveness_flag_allowlist,
                    localization_allowlist=liveness_localization_allowlist,
                ).findings
            )

        check_cancelled(cancelled, operation="validation")
        report_progress(
            progress,
            operation="validation",
            phase="done",
            current=phase_total,
            total=phase_total,
            message=f"Validation complete with {len(errors)} issue(s)",
        )
        return _filter_validation_errors(errors, suppress_warnings=suppress_warnings)

    def analyze_content_liveness(
        self,
        *,
        flag_allowlist: Sequence[str] = (),
        localization_allowlist: Sequence[str] = (),
    ) -> ContentLivenessReport:
        """Return focus/event/idea/flag/localization liveness diagnostics."""

        return analyze_content_liveness(
            self,
            flag_allowlist=flag_allowlist,
            localization_allowlist=localization_allowlist,
        )

    def validate_game_log(
        self,
        log_path: str | Path,
        *,
        since: datetime | None = None,
        start_offset: int = 0,
        require_fresh: bool = False,
    ) -> list[ValidationError]:
        """Return HOI4 engine errors attributable to files in this mod."""

        from .game_log import parse_hoi4_error_log

        fresh_after: datetime | None = None
        if require_fresh:
            mtimes = [
                path.stat().st_mtime
                for path in self.mod_root.rglob("*")
                if path.is_file()
            ]
            if mtimes:
                fresh_after = datetime.fromtimestamp(max(mtimes)).astimezone()
        report = parse_hoi4_error_log(
            log_path,
            self.mod_root,
            since=since,
            start_offset=start_offset,
            fresh_after=fresh_after,
        )
        return list(report.validation_errors)

    # ── Transactional image assets ─────────────────────────────

    def _stage_asset_files(
        self,
        files: dict[Path, bytes],
        *,
        overwrite: bool,
    ) -> None:
        """Queue binary assets for preview/save with transaction rollback."""

        normalized: dict[Path, bytes] = {}
        for raw_path, content in files.items():
            path = resolve_mod_output_path(self.mod_root, raw_path)
            if path.is_dir() and not path.is_symlink():
                raise IsADirectoryError(f"Asset target is a directory: {path}")
            if not overwrite and (
                path.exists() or path in self._pending_asset_writes
            ):
                raise FileExistsError(f"Refusing to overwrite asset: {path}")
            normalized[path] = content
        for path, content in normalized.items():
            self._pending_asset_baselines.setdefault(
                path,
                path.read_bytes() if path.is_file() else None,
            )
            self._pending_asset_writes[path] = content
        if normalized:
            self._dirty.add("assets")

    def import_flag_to_mod(
        self,
        tag: str,
        src_image: str | Path,
        vanilla_override: bool = False,
        *,
        ideologies: Sequence[str | None] | None = None,
        resize_mode: Literal["stretch", "cover", "contain"] = "stretch",
        overwrite: bool = True,
    ) -> tuple[FlagAssetSet, ...]:
        """Stage all three HOI4 flag sizes for transactional save."""

        from .assets import import_flag_to_mod

        with tempfile.TemporaryDirectory(prefix="hoi4-sdk-flag-") as temporary:
            temporary_root = Path(temporary)
            generated = import_flag_to_mod(
                temporary_root,
                tag,
                src_image,
                vanilla_override,
                ideologies=ideologies,
                resize_mode=resize_mode,
                overwrite=True,
            )
            staged: dict[Path, bytes] = {}
            results: list[FlagAssetSet] = []
            for asset_set in generated:
                targets: dict[str, Path] = {}
                for size_name in ("large", "medium", "small"):
                    source = getattr(asset_set, size_name)
                    target = resolve_mod_output_path(
                        self.mod_root,
                        source.relative_to(temporary_root),
                    )
                    staged[target] = source.read_bytes()
                    targets[size_name] = target
                results.append(
                    FlagAssetSet(
                        tag=asset_set.tag,
                        ideology=asset_set.ideology,
                        large=targets["large"],
                        medium=targets["medium"],
                        small=targets["small"],
                    )
                )
        self._stage_asset_files(staged, overwrite=overwrite)
        return tuple(results)

    def import_portrait_to_mod(
        self,
        tag: str,
        name_slug: str,
        src_image: str | Path,
        *,
        output_format: Literal["dds", "tga"] = "dds",
        size: tuple[int, int] = (156, 210),
        resize_mode: Literal["stretch", "cover", "contain"] = "cover",
        dds_compression: Literal["DXT1", "DXT3", "DXT5"] = "DXT5",
        overwrite: bool = True,
    ) -> Path:
        """Stage one converted portrait for transactional save."""

        from .assets import import_portrait_to_mod

        with tempfile.TemporaryDirectory(prefix="hoi4-sdk-portrait-") as temporary:
            temporary_root = Path(temporary)
            generated = import_portrait_to_mod(
                temporary_root,
                tag,
                name_slug,
                src_image,
                output_format=output_format,
                size=size,
                resize_mode=resize_mode,
                dds_compression=dds_compression,
                overwrite=True,
            )
            target = resolve_mod_output_path(
                self.mod_root,
                generated.relative_to(temporary_root),
            )
            content = generated.read_bytes()
        self._stage_asset_files({target: content}, overwrite=overwrite)
        return target

    def write_portrait_gfx(
        self,
        tag: str,
        portrait_slug: str,
        *,
        portrait_path: str | Path | None = None,
        sprite_name: str | None = None,
        overwrite: bool = True,
    ) -> Path:
        """Stage a portrait sprite declaration for transactional save."""

        from .assets import _require_asset_stem

        normalized_tag = require_country_tag(tag)
        normalized_slug = _require_asset_stem(
            portrait_slug,
            label="portrait slug",
        )
        if portrait_path is None:
            selected = next(
                (
                    path
                    for extension in ("dds", "tga", "png")
                    if (
                        path := resolve_mod_output_path(
                            self.mod_root,
                            f"gfx/leaders/{normalized_tag}/{normalized_slug}.{extension}",
                        )
                    ).is_file()
                    or path in self._pending_asset_writes
                ),
                None,
            )
            if selected is None:
                raise FileNotFoundError(
                    f"No portrait found for {normalized_tag}/{normalized_slug}"
                )
        else:
            selected = Path(portrait_path)
            if not selected.is_absolute():
                selected = self.mod_root / selected
            selected = selected.resolve(strict=False)
            if not selected.is_relative_to(self.mod_root):
                raise ValueError("Portrait texture path escapes the mod root")
            if not selected.is_file() and selected not in self._pending_asset_writes:
                raise FileNotFoundError(
                    f"Portrait texture does not exist: {selected}"
                )
        resolved_sprite = require_script_id(
            sprite_name or f"GFX_portrait_{normalized_tag}_{normalized_slug}",
            label="portrait sprite name",
        )
        texture = selected.relative_to(self.mod_root).as_posix()
        output = resolve_mod_output_path(
            self.mod_root,
            f"interface/{normalized_tag}_{normalized_slug}_portrait.gfx",
        )
        content = (
            "spriteTypes = {\n"
            "\tspriteType = {\n"
            f"\t\tname = {pdx_string(resolved_sprite)}\n"
            f"\t\ttexturefile = {pdx_string(texture)}\n"
            "\t}\n"
            "}\n"
        ).encode("utf-8")
        self._stage_asset_files({output: content}, overwrite=overwrite)
        return output

    # ── Preview & Save ───────────────────────────────────────────

    def preview_summary(self) -> str:
        lines = self._semantic_preview_lines()
        if not lines:
            return "No semantic changes detected"
        return "Changed:\n" + "\n".join(f"- {line}" for line in lines)

    def preview(self) -> str:
        diffs: list[str] = []
        rendered = self._render_dirty_files()
        self._assert_no_external_modifications(rendered)
        for path, current in rendered.items():
            original = self._read_current_text(path)
            rel = str(path.relative_to(self.mod_root))
            if current is None:
                current = ""
            diff = unified_diff(original, current, rel)
            if diff:
                diffs.append(diff)
        for path, content in sorted(self._pending_asset_writes.items()):
            baseline = self._pending_asset_baselines[path]
            if baseline == content:
                continue
            action = "create" if baseline is None else "replace"
            rel = path.relative_to(self.mod_root).as_posix()
            diffs.append(f"Binary asset {action}: {rel} ({len(content)} bytes)")
        return "\n".join(diffs)

    def save(self, require_changes: bool = False) -> SaveResult:
        if not self._dirty:
            message = "No changes written: save() was called with no dirty changes"
            warnings.warn(message, RuntimeWarning, stacklevel=2)
            if require_changes:
                raise RuntimeError(message)
            return SaveResult(written_files=[], dirty_sections=[], no_changes=True, message=message)

        dirty_sections = sorted(self._dirty)
        rendered = self._render_dirty_files()
        self._assert_no_external_modifications(rendered)
        changed = {
            path: content
            for path, content in rendered.items()
            if (content is None and path.exists())
            or (content is not None and self._read_current_text(path) != content)
        }
        changed_assets = {
            path: content
            for path, content in self._pending_asset_writes.items()
            if self._pending_asset_baselines[path] != content
        }
        if not changed and not changed_assets:
            message = "No files written after processing dirty sections"
            if require_changes:
                raise RuntimeError(message)
            return SaveResult(dirty_sections=dirty_sections, no_changes=True, message=message)
        self._commit_rendered_files(changed, changed_assets)
        written_files = sorted((*changed, *changed_assets))
        self.discard()
        result = SaveResult(
            written_files=written_files,
            dirty_sections=dirty_sections,
            no_changes=False,
            message=f"Saved {len(written_files)} file(s)",
        )
        return result

    def _render_dirty_files(self) -> dict[Path, str | None]:
        rendered: dict[Path, str | None] = {}
        if "country_names" in self._dirty:
            path = self.mod_root / "common" / "names" / "00_generated_names.txt"
            text = self._read_current_text(path)
            for tag, body in sorted(self._country_name_pool_updates.items()):
                text = set_block(text, tag, body)
            rendered[path] = text or None
        if "countries" in self._dirty:
            tag_path = self.mod_root / "common" / "country_tags" / "00_generated_tags.txt"
            tag_text = self._read_current_text(tag_path)
            for tag in sorted(self._deleted_countries):
                tag_text = re.sub(rf"(?m)^\s*{re.escape(tag)}\s*=.*(?:\n|$)", "", tag_text)
            for tag in sorted(self._dirty_countries):
                if tag not in self._countries:
                    continue
                line = f'{tag} = "countries/{tag}.txt"'
                declared_in_mod = tag in self._country_tag_mappings.get(self.mod_root, {})
                declared_by_vanilla = tag in self._vanilla_tags
                if not declared_in_mod and not declared_by_vanilla and not re.search(
                    rf"(?m)^\s*{re.escape(tag)}\s*=", tag_text
                ):
                    tag_text = tag_text.rstrip() + ("\n" if tag_text.strip() else "") + line + "\n"
            tag_text = sort_generated_country_tags(tag_text)
            rendered[tag_path] = tag_text or None
            tags_dir = self.mod_root / "common" / "country_tags"
            if self._deleted_countries and tags_dir.is_dir():
                for source_path in sorted(tags_dir.glob("*.txt")):
                    if source_path == tag_path:
                        continue
                    source_text = self._read_current_text(source_path)
                    updated_text = source_text
                    for tag in sorted(self._deleted_countries):
                        updated_text = re.sub(
                            rf"(?m)^\s*{re.escape(tag)}\s*=.*(?:\n|$)",
                            "",
                            updated_text,
                        )
                    if updated_text != source_text:
                        rendered[source_path] = updated_text or None
            for deleted_country in self._deleted_countries.values():
                for path in country_file_paths(self.mod_root, deleted_country):
                    rendered[resolve_mod_output_path(self.mod_root, path)] = None
            for tag in sorted(self._dirty_countries):
                dirty_country = self._countries.get(tag)
                if dirty_country is None:
                    continue
                country_files = serialize_country_files(self.mod_root, dirty_country)
                rendered.update(
                    {
                        path: content
                        for path, content in country_files.items()
                        if path not in self._dirty_character_files
                    }
                )
                if (
                    dirty_country.history_path is not None
                    and dirty_country.history_path not in country_files
                ):
                    rendered[dirty_country.history_path] = None
            colors_path = self.mod_root / "common" / "countries" / "colors.txt"
            colors_original = self._read_current_text(colors_path)
            existing_color_tags = country_color_tags(colors_original)
            color_updates = {
                tag: country.color
                for tag in self._dirty_countries
                if (country := self._countries.get(tag)) is not None
                and ("*" in country.touched_fields or "color" in country.touched_fields)
                and (tag in self._vanilla_tags or tag in existing_color_tags)
            }
            deleted_color_tags = set(self._deleted_countries) & existing_color_tags
            colors_base = colors_original
            if (color_updates or deleted_color_tags) and self.hoi4_install is not None:
                vanilla_colors_path = (
                    self.hoi4_install / "common" / "countries" / "colors.txt"
                )
                if vanilla_colors_path.is_file():
                    colors_base = seed_country_colors_file(
                        colors_base,
                        vanilla_colors_path.read_text(
                            encoding="utf-8", errors="ignore"
                        ),
                    )
                elif set(color_updates) & self._vanilla_tags:
                    raise RuntimeError(
                        "Cannot safely override a vanilla country color because "
                        f"{vanilla_colors_path} is missing. HOI4 replaces colors.txt "
                        "wholesale, so writing a partial table would remove other "
                        "countries' map colors."
                    )
            colors_text = serialize_country_colors_file(
                colors_base,
                color_updates,
                deleted_tags=deleted_color_tags,
            )
            if colors_text != colors_original:
                rendered[colors_path] = colors_text or None

        if "characters" in self._dirty:
            for path, characters in self._group_characters_by_file(
                dirty_only=True
            ).items():
                self._assert_no_unmodeled_duplicates_in_file(
                    path,
                    sections=frozenset({"character"}),
                )
                rendered[path] = serialize_characters_file(
                    characters,
                    self._original_files.get(path, ""),
                )

        if "oobs" in self._dirty:
            for path in self._dirty_oob_files:
                matching = next(
                    (
                        oob
                        for oob in self._oobs.values()
                        if oob.path == path
                    ),
                    None,
                )
                rendered[path] = serialize_oob(matching) if matching is not None else None

        if "states" in self._dirty:
            for state_id in self._dirty_states:
                state = self._states.get(state_id)
                if state is None:
                    continue
                path = resolve_mod_output_path(
                    self.mod_root,
                    state.path or Path("history") / "states" / f"{state_id}-STATE.txt",
                )
                patch = self._state_history_patches.get(state_id)
                if patch is None:
                    rendered[path] = serialize_state(state)
                else:
                    rendered[path] = patch_state_history_owner_cores_text(
                        state.raw_text,
                        owner=patch["owner"],
                        add_cores=patch["add_cores"],
                        remove_cores=patch["remove_cores"],
                    )

        if "events" in self._dirty:
            event_files, namespaces = self._group_events_by_file(dirty_only=True)
            for path, events in event_files.items():
                self._assert_no_unmodeled_duplicates_in_file(
                    path,
                    sections=frozenset({"event"}),
                )
                rendered[path] = serialize_events_file(
                    namespaces.get(path), events, self._original_files.get(path, "")
                )

        if "on_actions" in self._dirty:
            for path, actions in self._group_on_actions_by_file(dirty_only=True).items():
                original = self._original_files.get(path)
                if not actions and original is None:
                    # A generated occurrence created and deleted before its
                    # first save should not leave an empty file behind.
                    rendered[path] = None
                else:
                    # Existing files may contain comments, scalar metadata, or
                    # intentionally empty hooks.  Remove only the selected
                    # modeled occurrence and retain the file itself.
                    rendered[path] = serialize_on_actions_file(
                        actions, original or ""
                    )

        if "decisions" in self._dirty:
            for path, categories in self._group_decisions_by_file(dirty_only=True).items():
                self._assert_no_unmodeled_duplicates_in_file(
                    path,
                    sections=frozenset({"decision", "decision_category"}),
                )
                rendered[path] = serialize_decisions_file(
                    categories, self._original_files.get(path, "")
                )
            for path, categories in self._group_decision_categories_by_file(
                dirty_only=True
            ).items():
                self._assert_no_unmodeled_duplicates_in_file(
                    path,
                    sections=frozenset({"decision_category"}),
                )
                category_text = serialize_decision_categories_file(
                    categories, self._original_files.get(path, "")
                )
                rendered[path] = category_text if category_text.strip() else None

        if "ideas" in self._dirty:
            for path, ideas in self._group_ideas_by_file(dirty_only=True).items():
                self._assert_no_unmodeled_duplicates_in_file(
                    path,
                    sections=frozenset({"idea"}),
                )
                container = self._idea_file_containers.get(path)
                if container is None:
                    container = "ideas" if path.parent.name == "ideas" else "country_ideas"
                rendered[path] = serialize_ideas_file(
                    ideas,
                    container_name=container,
                    original=self._original_files.get(path, ""),
                )

        if "ideologies" in self._dirty:
            for path in self._dirty_ideology_files:
                self._assert_no_unmodeled_duplicates_in_file(
                    path,
                    sections=frozenset({"ideology"}),
                )
                ideologies = [
                    ideology
                    for ideology in self._ideologies.values()
                    if ideology.path == path
                ]
                rendered[path] = serialize_ideologies_file(
                    ideologies,
                    original=self._original_files.get(path, ""),
                )

        if "dynamic_modifiers" in self._dirty:
            for path in self._dirty_dynamic_modifier_files:
                self._assert_no_unmodeled_duplicates_in_file(
                    path,
                    sections=frozenset({"dynamic_modifier"}),
                )
                modifiers = [
                    modifier
                    for modifier in self._dynamic_modifiers.values()
                    if modifier.path == path
                ]
                dynamic_text = serialize_dynamic_modifiers_file(
                    modifiers,
                    original=self._original_files.get(path, ""),
                )
                rendered[path] = dynamic_text if dynamic_text.strip() else None

        if "bookmarks" in self._dirty:
            for path in self._dirty_bookmark_files:
                self._assert_no_unmodeled_duplicates_in_file(
                    path,
                    sections=frozenset({"bookmark"}),
                )
                bookmarks = [bookmark for bookmark in self._bookmarks if bookmark.path == path]
                rendered[path] = serialize_bookmarks_file(
                    bookmarks,
                    original=self._original_files.get(path, ""),
                )

        if "bookmark_dates" in self._dirty and self._bookmark_date_defines is not None:
            path, start_date, end_date = self._bookmark_date_defines
            rendered[path] = patch_bookmark_dates_defines(
                self._original_files.get(path, ""),
                start_date=start_date,
                end_date=end_date,
            )

        if "focus" in self._dirty:
            for path, trees in self._group_focus_trees_by_file(dirty_only=True).items():
                self._assert_no_unmodeled_duplicates_in_file(
                    path,
                    sections=frozenset({"focus_tree", "focus"}),
                )
                rendered[path] = serialize_focus_file(trees, self._original_files.get(path, ""))

        if "localization" in self._dirty:
            for path, entries in self._group_loc_by_file(dirty_only=True).items():
                rendered[path] = (
                    serialize_localization_file(
                        entries,
                        original=self._original_files.get(path, ""),
                    )
                    if entries
                    else None
                )

        return {
            resolve_mod_output_path(self.mod_root, path): content
            for path, content in rendered.items()
        }

    @staticmethod
    def _read_current_text(path: Path) -> str:
        return path.read_text(encoding="utf-8-sig", errors="ignore") if path.exists() else ""

    def _capture_source_baseline(self) -> None:
        """Fingerprint text sources so legacy writers cannot be overwritten silently."""

        suffixes = {".txt", ".yml", ".gfx", ".lua", ".mod"}
        self._source_baseline = {
            path.resolve(strict=False): path.read_bytes()
            for path in self.mod_root.rglob("*")
            if path.is_file() and path.suffix.lower() in suffixes
        }

    def _assert_no_external_modifications(
        self, rendered: dict[Path, str | None]
    ) -> None:
        conflicts: list[Path] = []
        for raw_path in rendered:
            path = raw_path.resolve(strict=False)
            baseline = self._source_baseline.get(path)
            if baseline is None:
                if path.exists():
                    conflicts.append(path)
                continue
            if not path.is_file() or path.read_bytes() != baseline:
                conflicts.append(path)
        for path, baseline in self._pending_asset_baselines.items():
            current = path.read_bytes() if path.is_file() else None
            if current != baseline:
                conflicts.append(path)
        if conflicts:
            displayed = ", ".join(
                str(path.relative_to(self.mod_root)) for path in sorted(conflicts)
            )
            raise ExternalModificationError(
                "Refusing to overwrite files changed after Mod loaded them: "
                f"{displayed}. Call reload() and reapply the intended edit."
            )

    def _commit_rendered_files(
        self,
        rendered: dict[Path, str | None],
        assets: dict[Path, bytes] | None = None,
    ) -> None:
        assets = assets or {}
        overlap = set(rendered) & set(assets)
        if overlap:
            raise RuntimeError(
                "A file cannot be staged as both text and binary: "
                + ", ".join(str(path) for path in sorted(overlap))
            )
        targets = set(rendered) | set(assets)
        backups = {path: path.read_bytes() if path.exists() else None for path in targets}
        prepared: dict[Path, Path] = {}
        try:
            for path, content in rendered.items():
                if content is None:
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
                    handle.write(content.encode("utf-8-sig" if path.suffix == ".yml" else "utf-8"))
                    prepared[path] = Path(handle.name)
            for path, binary_content in assets.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
                    handle.write(binary_content)
                    prepared[path] = Path(handle.name)
            for path, text_content in rendered.items():
                if text_content is None:
                    path.unlink(missing_ok=True)
                else:
                    temporary = prepared[path]
                    os.replace(temporary, path)
                    del prepared[path]
            for path in assets:
                temporary = prepared[path]
                os.replace(temporary, path)
                del prepared[path]
        except Exception:
            for temporary in prepared.values():
                temporary.unlink(missing_ok=True)
            for path, backup in backups.items():
                if backup is None:
                    path.unlink(missing_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(backup)
            raise

    @contextmanager
    def transaction(self, *, save: bool = False):
        """Temporarily mutate the mod, then save or restore in-memory state.

        Use ``with mod.transaction():`` for dry runs that call ``preview()`` inside
        the block. On normal exit the transaction is discarded unless
        ``save=True`` is passed. On exceptions, in-memory state is restored.
        """
        snapshot = self._snapshot()
        try:
            yield self
            if save:
                self.save()
            else:
                self._restore(snapshot)
        except Exception:
            self._restore(snapshot)
            raise

    def discard(self) -> None:
        self._load_diagnostics.clear()
        self._countries.clear()
        self._characters.clear()
        self._oobs.clear()
        self._states.clear()
        self._state_ids.clear()
        self._state_source_paths.clear()
        self._events.clear()
        self._event_namespaces.clear()
        self._on_actions.clear()
        self._on_action_occurrences.clear()
        self._decisions.clear()
        self._decision_categories.clear()
        self._ideas.clear()
        self._focus_trees.clear()
        self._dirty_focus_trees.clear()
        self._dirty_focus_files.clear()
        self._dirty_countries.clear()
        self._deleted_countries.clear()
        self._created_country_tags.clear()
        self._dirty_characters.clear()
        self._dirty_character_files.clear()
        self._dirty_oobs.clear()
        self._dirty_oob_files.clear()
        self._dirty_states.clear()
        self._state_history_patches.clear()
        self._dirty_events.clear()
        self._dirty_event_files.clear()
        self._event_file_namespaces.clear()
        self._dirty_on_actions.clear()
        self._dirty_on_action_files.clear()
        self._dirty_decision_categories.clear()
        self._dirty_decision_files.clear()
        self._dirty_decision_category_files.clear()
        self._dirty_ideas.clear()
        self._dirty_idea_files.clear()
        self._idea_file_containers.clear()
        self._cached_idea_file.clear()
        self._ideologies.clear()
        self._vanilla_ideologies.clear()
        self._dirty_ideologies.clear()
        self._dirty_ideology_files.clear()
        self._dynamic_modifiers.clear()
        self._dynamic_modifier_sources.clear()
        self._dirty_dynamic_modifiers.clear()
        self._dirty_dynamic_modifier_files.clear()
        self._bookmarks.clear()
        self._dirty_bookmarks.clear()
        self._dirty_bookmark_files.clear()
        self._bookmark_date_defines = None
        self._loc_entries.clear()
        self._loc_sources.clear()
        self._dirty_loc_keys.clear()
        self._dirty_loc_files.clear()
        self._pending_asset_writes.clear()
        self._pending_asset_baselines.clear()
        self._country_name_pool_updates.clear()
        self._original_files.clear()
        self._dirty.clear()
        self._scan_cache.clear()
        self._equipment_unlock_cache = None
        self._sprite_texture_cache = None
        self._state_index_cache.clear()
        self._vanilla_state_loc_entries = None
        self._country_context_cache.clear()
        self._country_tag_mappings = {
            base: _parse_tag_file_mapping(base / "common" / "country_tags")
            for base in (self.mod_root, self.hoi4_install)
            if base is not None
        }
        self._load()
        self._capture_source_baseline()

    def reload(self) -> None:
        """Discard queued changes and reload current files from disk."""

        self.discard()

    def _snapshot(self) -> dict[str, object]:
        keys = [
            "_focus_trees",
            "_dirty_focus_trees",
            "_dirty_focus_files",
            "_countries",
            "_dirty_countries",
            "_deleted_countries",
            "_created_country_tags",
            "_characters",
            "_dirty_characters",
            "_dirty_character_files",
            "_oobs",
            "_dirty_oobs",
            "_dirty_oob_files",
            "_states",
            "_state_ids",
            "_state_source_paths",
            "_dirty_states",
            "_state_history_patches",
            "_events",
            "_event_namespaces",
            "_dirty_events",
            "_dirty_event_files",
            "_event_file_namespaces",
            "_on_actions",
            "_on_action_occurrences",
            "_dirty_on_actions",
            "_dirty_on_action_files",
            "_decisions",
            "_decision_categories",
            "_dirty_decision_categories",
            "_dirty_decision_files",
            "_dirty_decision_category_files",
            "_ideas",
            "_dirty_ideas",
            "_dirty_idea_files",
            "_idea_file_containers",
            "_cached_idea_file",
            "_ideologies",
            "_vanilla_ideologies",
            "_dirty_ideologies",
            "_dirty_ideology_files",
            "_dynamic_modifiers",
            "_dynamic_modifier_sources",
            "_dirty_dynamic_modifiers",
            "_dirty_dynamic_modifier_files",
            "_bookmarks",
            "_dirty_bookmarks",
            "_dirty_bookmark_files",
            "_bookmark_date_defines",
            "_loc_entries",
            "_loc_sources",
            "_dirty_loc_keys",
            "_dirty_loc_files",
            "_original_files",
            "_pending_asset_writes",
            "_pending_asset_baselines",
            "_country_name_pool_updates",
            "_dirty",
            "_sprite_texture_cache",
            "_country_context_cache",
            "_country_tag_mappings",
        ]
        return copy.deepcopy({key: getattr(self, key) for key in keys})

    def _restore(self, snapshot: dict[str, object]) -> None:
        for key, value in snapshot.items():
            setattr(self, key, value)

    def _semantic_preview_lines(self) -> list[str]:
        lines: list[str] = []
        if "assets" in self._dirty:
            for path in sorted(self._pending_asset_writes):
                lines.append(
                    f"asset {path.relative_to(self.mod_root).as_posix()}: stage"
                )
        if "country_names" in self._dirty:
            for tag in sorted(self._country_name_pool_updates):
                lines.append(f"country name pool {tag}: create/update")
        if "countries" in self._dirty:
            for tag in sorted(self._dirty_countries):
                country = self._countries.get(tag)
                if country:
                    lines.append(f"country {tag}: create/update {country.name or tag}")
        if "characters" in self._dirty:
            for character_id in sorted(self._dirty_characters):
                character = self._characters.get(character_id)
                if character is None:
                    lines.append(f"character {character_id}: delete")
                else:
                    role_names = ",".join(
                        role_key(role) for role in character.roles
                    )
                    lines.append(
                        f"character {character_id}: roles={role_names or '<instance-only>'}"
                    )
        if "oobs" in self._dirty:
            for name in sorted(self._dirty_oobs):
                oob = self._oobs.get(name)
                if oob is None:
                    lines.append(f"OOB {name}: delete")
                else:
                    lines.append(
                        f"OOB {name}: {len(oob.templates)} template(s), "
                        f"{len(oob.divisions)} division(s)"
                    )
        if "states" in self._dirty:
            for sid in sorted(self._dirty_states):
                state = self._states.get(sid)
                if state:
                    lines.append(
                        f"state {sid}: owner={state.owner or '<none>'} cores={','.join(state.cores) or '<none>'}"
                    )
        if "ideas" in self._dirty:
            for iid in sorted(self._dirty_ideas):
                idea = self._ideas.get(iid)
                if idea:
                    lines.append(
                        f"idea {iid}: category={idea.category or '<none>'} modifiers={','.join(sorted(idea.modifier)) or '<none>'}"
                    )
        if "ideologies" in self._dirty:
            for ideology_id in sorted(self._dirty_ideologies):
                ideology = self._ideologies.get(ideology_id)
                if ideology is not None:
                    lines.append(
                        f"ideology {ideology_id}: {len(ideology.types)} subtype(s)"
                    )
            for path in sorted(self._dirty_ideology_files):
                lines.append(f"ideology file {self._display_path(path)}: updated")
        if "dynamic_modifiers" in self._dirty:
            for modifier_id in sorted(self._dirty_dynamic_modifiers):
                if modifier_id in self._dynamic_modifiers:
                    lines.append(f"dynamic modifier {modifier_id}: create/update")
                else:
                    lines.append(f"dynamic modifier {modifier_id}: deleted")
        if "bookmarks" in self._dirty:
            for path in sorted(self._dirty_bookmark_files):
                count = sum(bookmark.path == path for bookmark in self._bookmarks)
                lines.append(
                    f"bookmark file {self._display_path(path)}: {count} bookmark(s)"
                )
        if "bookmark_dates" in self._dirty and self._bookmark_date_defines is not None:
            _, start_date, end_date = self._bookmark_date_defines
            lines.append(f"bookmark date range: {start_date} through {end_date}")
        if "events" in self._dirty:
            for eid in sorted(self._dirty_events):
                event = self._events.get(eid)
                if event:
                    lines.append(f"event {eid}: {len(event.options)} option(s)")
                    event_script = _join_script_parts(
                        [event.immediate] + [option.effect for option in event.options]
                    )
                    lines.extend(self._effect_semantic_lines(event_script, f"event {eid}"))
        if "on_actions" in self._dirty:
            for action_id in sorted(self._dirty_on_actions):
                if action_id in self._on_actions:
                    lines.append(f"on_action {action_id}: updated")
            for path in sorted(self._dirty_on_action_files):
                lines.append(f"on_action file {self._display_path(path)}: rewritten")
        if "decisions" in self._dirty:
            for cid in sorted(self._dirty_decision_categories):
                category = self._decision_categories.get(cid)
                if category:
                    lines.append(f"decision category {cid}: {len(category.decisions)} decision(s)")
                    for decision in category.decisions:
                        lines.extend(
                            self._effect_semantic_lines(
                                _join_script_parts(
                                    [decision.complete_effect, decision.remove_effect]
                                ),
                                f"decision {decision.id}",
                            )
                        )
        if "focus" in self._dirty:
            for tree_id in sorted(self._dirty_focus_trees):
                tree = self._focus_trees.get(tree_id)
                if tree is None:
                    continue
                old_tree = load_focus_tree(tree.path) if tree.path and tree.path.exists() else None
                old_focuses = {focus.id: focus for focus in old_tree.focuses} if old_tree else {}
                new_focuses = {focus.id: focus for focus in tree.focuses}
                if (
                    old_tree
                    and old_tree.continuous_focus_position != tree.continuous_focus_position
                ):
                    lines.append(
                        f"{tree_id} continuous_focus_position: "
                        f"{old_tree.continuous_focus_position or '<none>'} -> {tree.continuous_focus_position or '<none>'}"
                    )
                for fid in sorted(new_focuses):
                    focus = new_focuses[fid]
                    old = old_focuses.get(fid)
                    if old is None:
                        lines.append(f"{tree_id} focus {fid}: added at x={focus.x} y={focus.y}")
                        lines.extend(
                            self._effect_semantic_lines(
                                focus.completion_reward, f"{tree_id} focus {fid}"
                            )
                        )
                        continue
                    changes: list[str] = []
                    if (old.x, old.y) != (focus.x, focus.y):
                        changes.append(f"position x={old.x} y={old.y} -> x={focus.x} y={focus.y}")
                    if old.completion_reward.strip() != focus.completion_reward.strip():
                        changes.append("completion_reward changed")
                    if old.icon != focus.icon:
                        changes.append(f"icon {old.icon} -> {focus.icon}")
                    if changes:
                        lines.append(f"{tree_id} focus {fid}: {'; '.join(changes)}")
                        if old.completion_reward.strip() != focus.completion_reward.strip():
                            lines.extend(
                                self._effect_semantic_lines(
                                    focus.completion_reward, f"{tree_id} focus {fid}"
                                )
                            )
                for fid in sorted(set(old_focuses) - set(new_focuses)):
                    lines.append(f"{tree_id} focus {fid}: removed")
        if "localization" in self._dirty:
            lines.append(f"localization: {len(self._dirty_loc_keys)} key(s)")
        return lines

    def _effect_semantic_lines(self, script: str, owner: str) -> list[str]:
        if not script:
            return []
        lines: list[str] = []
        for tree_id in _LOAD_FOCUS_TREE_RE.findall(script):
            lines.append(f"{owner}: load focus tree {tree_id}")
        for tag, state_id in re.findall(
            r"\b([A-Z][A-Z0-9]{2})\s*=\s*\{[^{}]*\btransfer_state\s*=\s*([0-9]+)\b",
            script,
            flags=re.DOTALL,
        ):
            lines.append(f"{owner}: transfer state {state_id} to {tag}")
        for state_id, tag in re.findall(
            r"\b([0-9]+)\s*=\s*\{[^{}]*\badd_core_of\s*=\s*([A-Z][A-Z0-9]{2})\b",
            script,
            flags=re.DOTALL,
        ):
            lines.append(f"{owner}: add {tag} core on state {state_id}")
        for state_id, tag in re.findall(
            r"\b([0-9]+)\s*=\s*\{[^{}]*\bremove_core_of\s*=\s*([A-Z][A-Z0-9]{2})\b",
            script,
            flags=re.DOTALL,
        ):
            lines.append(f"{owner}: remove {tag} core from state {state_id}")
        for overlord, puppet in re.findall(
            r"\b([A-Z][A-Z0-9]{2})\s*=\s*\{[^{}]*\bend_puppet\s*=\s*([A-Z][A-Z0-9]{2})\b",
            script,
            flags=re.DOTALL,
        ):
            lines.append(f"{owner}: end puppet {puppet} under {overlord}")
        for puppet in re.findall(r"(?m)^\s*end_puppet\s*=\s*([A-Z][A-Z0-9]{2})\b", script):
            lines.append(f"{owner}: end puppet {puppet}")
        for leader, target in re.findall(
            r"\b([A-Z][A-Z0-9]{2})\s*=\s*\{[^{}]*\badd_to_faction\s*=\s*([A-Z][A-Z0-9]{2})\b",
            script,
            flags=re.DOTALL,
        ):
            lines.append(f"{owner}: {target} joins {leader} faction")
        for actor, target in re.findall(
            r"\b([A-Z][A-Z0-9]{2})\s*=\s*\{[^{}]*\bdeclare_war_on\s*=\s*\{[^{}]*\btarget\s*=\s*([A-Z][A-Z0-9]{2})\b",
            script,
            flags=re.DOTALL,
        ):
            lines.append(f"{owner}: {actor} declares war on {target}")
        for target in re.findall(
            r"(?m)^\s*declare_war_on\s*=\s*\{[^{}]*\btarget\s*=\s*([A-Z][A-Z0-9]{2})\b",
            script,
            flags=re.DOTALL,
        ):
            lines.append(f"{owner}: declare war on {target}")
        seen: set[str] = set()
        unique: list[str] = []
        for line in lines:
            if line not in seen:
                unique.append(line)
                seen.add(line)
        return unique

    def _display_path(self, path: Path) -> str:
        return (
            str(path.relative_to(self.mod_root))
            if path.is_relative_to(self.mod_root)
            else str(path)
        )

    def game_script_vocabulary(self) -> GameScriptVocabulary:
        """Return documented script tokens and installed-game usage counts."""

        if self.hoi4_install is None:
            raise ValueError(
                "game_script_vocabulary() requires a configured HOI4 installation"
            )
        if self._script_vocabulary_cache is None:
            self._script_vocabulary_cache = load_game_script_vocabulary(
                self.hoi4_install,
                ideology_ids=self._ideologies,
            )
        return self._script_vocabulary_cache

    def validate_script_vocabulary(
        self,
        *,
        allowlist: Sequence[str] = (),
    ) -> list[ValidationError]:
        """Warn about undocumented effect, trigger, and modifier tokens."""

        return validate_script_sources(
            self._semantic_script_sources(),
            self.game_script_vocabulary(),
            mod_root=self.mod_root,
            allowlist=allowlist,
        )

    def _semantic_script_sources(self) -> list[ScriptSource]:
        sources: list[ScriptSource] = []

        def add(
            text: str,
            kind: Literal["effect", "trigger", "modifier"],
            owner_kind: str,
            owner_id: str,
            path: Path | None,
            scope: str | None = None,
        ) -> None:
            if text.strip():
                sources.append(
                    ScriptSource(
                        text=text,
                        kind=kind,
                        owner_kind=owner_kind,
                        owner_id=owner_id,
                        file_path=str(path) if path is not None else None,
                        scope=scope,
                    )
                )

        for tree in self._focus_trees.values():
            for focus in tree.focuses:
                for text in (
                    focus.completion_reward,
                    focus.select_effect,
                    focus.complete_tooltip,
                ):
                    add(
                        text,
                        "effect",
                        "focus",
                        focus.id,
                        tree.path,
                        scope="COUNTRY",
                    )
                for text in (focus.available, focus.bypass, focus.allow_branch):
                    add(
                        text,
                        "trigger",
                        "focus",
                        focus.id,
                        tree.path,
                        scope="COUNTRY",
                    )
        for event in self._events.values():
            event_scope = {
                "state_event": "STATE",
                "unit_leader_event": "CHARACTER",
                "operative_leader_event": "CHARACTER",
            }.get(event.event_type, "COUNTRY")
            add(
                event.trigger,
                "trigger",
                "event",
                event.id,
                event.path,
                scope=event_scope,
            )
            add(
                event.immediate,
                "effect",
                "event",
                event.id,
                event.path,
                scope=event_scope,
            )
            for option in event.options:
                add(
                    option.trigger,
                    "trigger",
                    "event",
                    event.id,
                    event.path,
                    scope=event_scope,
                )
                add(
                    option.effect,
                    "effect",
                    "event",
                    event.id,
                    event.path,
                    scope=event_scope,
                )
        for occurrences in self._on_action_occurrences.values():
            for action in occurrences:
                add(action.effect, "effect", "on_action", action.id, action.path)
                if action.events or action.random_events:
                    add(
                        "\n".join(
                            f"country_event = {{ id = {event_id} }}"
                            for event_id in (*action.events, *action.random_events)
                        ),
                        "effect",
                        "on_action",
                        action.id,
                        action.path,
                    )
        for decision in self._decisions.values():
            add(
                decision.available,
                "trigger",
                "decision",
                decision.id,
                decision.path,
                scope="COUNTRY",
            )
            add(
                decision.visible,
                "trigger",
                "decision",
                decision.id,
                decision.path,
                scope="COUNTRY",
            )
            add(
                decision.complete_effect,
                "effect",
                "decision",
                decision.id,
                decision.path,
                scope="COUNTRY",
            )
            add(
                decision.remove_effect,
                "effect",
                "decision",
                decision.id,
                decision.path,
                scope="COUNTRY",
            )
        for idea in self._ideas.values():
            add(
                idea.allowed,
                "trigger",
                "idea",
                idea.id,
                idea.path,
                scope="COUNTRY",
            )
            if idea.modifier:
                add(
                    "\n".join(f"{key} = 0" for key in idea.modifier),
                    "modifier",
                    "idea",
                    idea.id,
                    idea.path,
                )
        for modifier in self._dynamic_modifiers.values():
            add(
                modifier.enable,
                "trigger",
                "dynamic_modifier",
                modifier.id,
                modifier.path,
            )
            add(
                modifier.remove_trigger,
                "trigger",
                "dynamic_modifier",
                modifier.id,
                modifier.path,
            )
            if modifier.modifier:
                add(
                    "\n".join(f"{key} = 0" for key in modifier.modifier),
                    "modifier",
                    "dynamic_modifier",
                    modifier.id,
                    modifier.path,
                )
        for bookmark in self._bookmarks:
            add(
                bookmark.effect,
                "effect",
                "bookmark",
                bookmark.name,
                bookmark.path,
                scope="COUNTRY",
            )
            for country in bookmark.countries:
                add(
                    country.available,
                    "trigger",
                    "bookmark",
                    bookmark.name,
                    bookmark.path,
                    scope="COUNTRY",
                )
        for character in self._characters.values():
            scopes: list[tuple[list[CharacterRole], str | None]] = [
                (character.roles, None)
            ]
            scopes.extend((instance.roles, instance.allowed) for instance in character.instances)
            for roles, allowed in scopes:
                add(
                    allowed or "",
                    "trigger",
                    "character",
                    character.id,
                    character.path,
                    scope="COUNTRY",
                )
                for role in roles:
                    for attribute in ("allowed", "visible", "available"):
                        add(
                            str(getattr(role, attribute, "")),
                            "trigger",
                            "character",
                            character.id,
                            character.path,
                            scope="COUNTRY",
                        )
        for kind, relative in (
            ("effect", Path("common/scripted_effects")),
            ("trigger", Path("common/scripted_triggers")),
        ):
            directory = self.mod_root / relative
            if not directory.is_dir():
                continue
            for path in sorted(directory.rglob("*.txt")):
                try:
                    text = path.read_text(
                        encoding="utf-8-sig",
                        errors="ignore",
                    )
                    for span in top_level_assignments(text):
                        if (
                            span.is_block
                            and span.body_start is not None
                            and span.body_end is not None
                        ):
                            add(
                                text[span.body_start : span.body_end],
                                cast(
                                    Literal["effect", "trigger", "modifier"],
                                    kind,
                                ),
                                f"scripted_{kind}",
                                span.key,
                                path,
                            )
                except (OSError, ValueError):
                    continue
        return sources

    def _script_entries(self) -> list[tuple[str, str, str | None, str | None]]:
        entries: list[tuple[str, str, str | None, str | None]] = []
        for tree in self._focus_trees.values():
            for focus in tree.focuses:
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
                entries.append((script, "focus", focus.id, str(tree.path) if tree.path else None))
        for event in self._events.values():
            scripts = [event.trigger, event.immediate, event.mean_time_to_happen]
            scripts.extend(option.trigger + "\n" + option.effect for option in event.options)
            entries.append(
                ("\n".join(scripts), "event", event.id, str(event.path) if event.path else None)
            )
        for occurrences in self._on_action_occurrences.values():
            for action in occurrences:
                entries.append(
                    (
                        action.effect,
                        "on_action",
                        action.id,
                        str(action.path) if action.path else None,
                    )
                )
                if action.events:
                    entries.append(
                        (
                            "\n".join(
                                f"country_event = {{ id = {event_id} }}"
                                for event_id in action.events
                            ),
                            "on_action",
                            action.id,
                            str(action.path) if action.path else None,
                        )
                    )
        for decision in self._decisions.values():
            entries.append(
                (
                    "\n".join(
                        [
                            decision.available,
                            decision.visible,
                            decision.complete_effect,
                            decision.remove_effect,
                        ]
                    ),
                    "decision",
                    decision.id,
                    str(decision.path) if decision.path else None,
                )
            )
        return entries

    def _validate_script_references(
        self,
        *,
        known_ideas: set[str],
        known_events: set[str],
        known_technologies: set[str],
        known_equipment: set[str],
        known_focus_trees: set[str],
        entries: list[tuple[str, str, str | None, str | None]] | None = None,
    ) -> list[ValidationError]:
        errors: list[ValidationError] = []
        script_entries = entries or self._script_entries()
        known_state_ids = (
            {entry["id"] for entry in self.state_index()}
            if any("start_civil_war" in script for script, _, _, _ in script_entries)
            else set()
        )
        runtime_entries = list(script_entries)
        if entries is None:
            runtime_entries.extend(
                (
                    source.text,
                    source.owner_kind,
                    source.owner_id,
                    source.file_path,
                )
                for source in self._semantic_script_sources()
                if source.owner_kind == "scripted_effect"
            )
        for script, kind, obj_id, file_path in runtime_entries:
            if not script:
                continue
            match = re.search(
                r"\brecruit_character\s*=",
                self._mask_script_comments(script),
            )
            if match is None:
                continue
            line, column = self._script_reference_location(
                script,
                match.start(),
                obj_id=obj_id,
                file_path=file_path,
                token="recruit_character",
            )
            errors.append(
                self._script_ref_error(
                    (
                        f"{kind} '{obj_id}' uses recruit_character at runtime. "
                        "HOI4 only executes recruit_character from game/history "
                        "at scenario start; recruit the character in country "
                        "history and gate its role availability instead."
                    ),
                    "runtime_recruit_character",
                    kind,
                    obj_id,
                    file_path,
                    severity="error",
                    line=line,
                    column=column,
                )
            )
        for script, kind, obj_id, file_path in script_entries:
            if not script:
                continue
            for idea_id in sorted(
                set(_IDEA_EFFECT_RE.findall(script) + _HAS_IDEA_RE.findall(script))
            ):
                if idea_id not in known_ideas:
                    errors.append(
                        self._script_ref_error(
                            f"{kind} '{obj_id}' references unknown idea '{idea_id}'",
                            "unknown_idea_reference",
                            kind,
                            obj_id,
                            file_path,
                            idea_id=idea_id,
                        )
                    )
                elif re.search(rf"\badd_ideas\s*=\s*{re.escape(idea_id)}\b", script):
                    idea = self._ideas.get(idea_id)
                    if idea is not None and idea.category != "country":
                        errors.append(
                            self._script_ref_error(
                                f"{kind} '{obj_id}' adds idea '{idea_id}', but its category is '{idea.category or '<none>'}', not 'country'",
                                "idea_not_addable",
                                kind,
                                obj_id,
                                file_path,
                                idea_id=idea_id,
                            )
                        )
            for event_id in sorted(set(_EVENT_REF_RE.findall(script))):
                if known_events and event_id not in known_events:
                    errors.append(
                        self._script_ref_error(
                            f"{kind} '{obj_id}' references unknown event '{event_id}'",
                            "unknown_event_reference",
                            kind,
                            obj_id,
                            file_path,
                            event_id=event_id,
                        )
                    )
            for tree_id in sorted(set(_LOAD_FOCUS_TREE_RE.findall(script))):
                if tree_id not in known_focus_trees:
                    errors.append(
                        self._script_ref_error(
                            f"{kind} '{obj_id}' references unknown focus tree '{tree_id}'",
                            "unknown_focus_tree_reference",
                            kind,
                            obj_id,
                            file_path,
                        )
                    )
            if known_state_ids:
                for body, _, _ in iter_assignment_blocks(script, "start_civil_war"):
                    capital_match = re.search(r"\bcapital\s*=\s*(\d+)\b", body)
                    if (
                        capital_match is not None
                        and int(capital_match.group(1)) not in known_state_ids
                    ):
                        errors.append(
                            self._script_ref_error(
                                (
                                    f"{kind} '{obj_id}' uses civil-war capital "
                                    f"{capital_match.group(1)}, which is not a known state ID"
                                ),
                                "civil_war_capital_ref",
                                kind,
                                obj_id,
                                file_path,
                            )
                        )
            if known_technologies:
                for tech_id in sorted(set(self._technology_refs(script))):
                    if tech_id not in known_technologies:
                        errors.append(
                            self._script_ref_error(
                                f"{kind} '{obj_id}' references unknown technology '{tech_id}'",
                                "unknown_technology_reference",
                                kind,
                                obj_id,
                                file_path,
                            )
                        )
            if known_equipment:
                for equipment_id in sorted(set(self._equipment_refs(script))):
                    if equipment_id not in known_equipment:
                        errors.append(
                            self._script_ref_error(
                                f"{kind} '{obj_id}' references unknown equipment '{equipment_id}'",
                                "unknown_equipment_reference",
                                kind,
                                obj_id,
                                file_path,
                            )
                        )
            remove_count = len(re.findall(r"\bremove_ideas\s*=", script))
            add_count = len(re.findall(r"\badd_ideas\s*=", script))
            if remove_count >= 2 and add_count >= 1 and "swap_ideas" not in script:
                errors.append(
                    self._script_ref_error(
                        f"{kind} '{obj_id}' removes {remove_count} ideas and adds {add_count}; consider Mod.effect_swap_idea() or hidden effects for cleaner tooltips",
                        "bad_idea_tooltip_pattern",
                        kind,
                        obj_id,
                        file_path,
                    )
                )
        return errors

    def _validate_state_effect_assumptions(
        self,
        *,
        entries: list[tuple[str, str, str | None, str | None]] | None = None,
    ) -> list[ValidationError]:
        errors: list[ValidationError] = []
        for script, kind, obj_id, file_path in entries or self._script_entries():
            if not script:
                continue
            for state_id_text in sorted(
                set(
                    re.findall(
                        r"\b([0-9]+)\s*=\s*\{[^{}]*\badd_resistance\s*=",
                        script,
                        flags=re.DOTALL,
                    )
                )
            ):
                state = self._read_state_readonly(int(state_id_text))
                if state and state.owner and state.owner in state.cores:
                    errors.append(
                        self._script_ref_error(
                            (
                                f"{kind} '{obj_id}' adds resistance to state {state.id}, but current owner "
                                f"{state.owner} is already a core. Occupation resistance will not behave like "
                                "generic unrest; use flags, variables, decisions, or revolt events."
                            ),
                            "resistance_on_core_state",
                            kind,
                            obj_id,
                            file_path,
                        )
                    )
            for tag, state_id_text in sorted(
                set(
                    re.findall(
                        r"\b([A-Z][A-Z0-9]{2})\s*=\s*\{[^{}]*\btransfer_state\s*=\s*([0-9]+)\b",
                        script,
                        flags=re.DOTALL,
                    )
                )
            ):
                state = self._read_state_readonly(int(state_id_text))
                if state and state.owner == tag:
                    errors.append(
                        self._script_ref_error(
                            (
                                f"{kind} '{obj_id}' transfers state {state.id} to {tag}, but {tag} already "
                                "owns that state in loaded history. Check released/existing-country edge cases."
                            ),
                            "revolt_state_already_owned",
                            kind,
                            obj_id,
                            file_path,
                        )
                    )
        return errors

    def _read_state_readonly(self, state_id: int) -> State | None:
        if state_id in self._states:
            return self._states[state_id]
        for base in (self.mod_root, self.hoi4_install):
            if base is None:
                continue
            path = find_state_file(base / "history" / "states", state_id)
            if path:
                try:
                    return read_state(path)
                except Exception:
                    return None
        return None

    def _validate_localization_references(self) -> list[ValidationError]:
        errors: list[ValidationError] = []

        def add_missing(
            message: str,
            *,
            focus_id: str | None = None,
            event_id: str | None = None,
            idea_id: str | None = None,
            country_tag: str | None = None,
            file_path: str | None = None,
        ) -> None:
            errors.append(
                ValidationError(
                    message=message,
                    severity="warning",
                    code="missing_localization",
                    focus_id=focus_id,
                    event_id=event_id,
                    idea_id=idea_id,
                    country_tag=country_tag,
                    file_path=file_path,
                )
            )

        for event in self._events.values():
            if event.title and not self._has_loc(event.title):
                add_missing(
                    f"Event '{event.id}' title localization '{event.title}' not found",
                    event_id=event.id,
                    file_path=str(event.path) if event.path else None,
                )
            if event.description and not self._has_loc(event.description):
                add_missing(
                    f"Event '{event.id}' description localization '{event.description}' not found",
                    event_id=event.id,
                    file_path=str(event.path) if event.path else None,
                )
            for option in event.options:
                if option.name and not self._has_loc(option.name):
                    add_missing(
                        f"Event '{event.id}' option localization '{option.name}' not found",
                        event_id=event.id,
                        file_path=str(event.path) if event.path else None,
                    )

        for idea in self._ideas.values():
            if not self._has_loc(idea.id):
                add_missing(
                    f"Idea '{idea.id}' localization not found",
                    idea_id=idea.id,
                    file_path=str(idea.path) if idea.path else None,
                )
            if not self._has_loc(f"{idea.id}_desc"):
                add_missing(
                    f"Idea '{idea.id}' description localization '{idea.id}_desc' not found",
                    idea_id=idea.id,
                    file_path=str(idea.path) if idea.path else None,
                )

        for country in self._countries.values():
            for key in (country.tag, f"{country.tag}_DEF", f"{country.tag}_ADJ"):
                if not self._has_loc(key):
                    add_missing(
                        f"Country '{country.tag}' localization '{key}' not found",
                        country_tag=country.tag,
                    )
            if (
                country.leader
                and country.leader.character_id
                and not self._has_loc(country.leader.character_id)
            ):
                add_missing(
                    f"Country '{country.tag}' leader localization '{country.leader.character_id}' not found",
                    country_tag=country.tag,
                )
        return errors

    def _has_loc(self, key: str) -> bool:
        return key in self._loc_entries or f"{key}:0" in self._loc_entries

    def _validate_idea_mutation_collisions(self) -> list[ValidationError]:
        focus_mutations: dict[str, set[str]] = {}
        runtime_mutations: dict[str, set[str]] = {}
        for script, kind, obj_id, _ in self._script_entries():
            ideas = _idea_mutation_ids(
                script,
                ignore_guarded_additions=kind != "focus",
            )
            if not ideas:
                continue
            target = focus_mutations if kind == "focus" else runtime_mutations
            target.setdefault(obj_id or "<unknown>", set()).update(ideas)
        focus_ideas = set().union(*focus_mutations.values()) if focus_mutations else set()
        runtime_ideas = set().union(*runtime_mutations.values()) if runtime_mutations else set()
        collisions = sorted(focus_ideas & runtime_ideas)
        if not collisions:
            return []
        return [
            ValidationError(
                message=(
                    "Focuses and runtime events/decisions mutate the same idea IDs "
                    f"({', '.join(collisions)}). Check delayed events cannot downgrade a staged spirit."
                ),
                severity="warning",
                code="idea_mutation_collision",
            )
        ]

    def _script_ref_error(
        self,
        message: str,
        code: str,
        kind: str,
        obj_id: str | None,
        file_path: str | None,
        *,
        idea_id: str | None = None,
        event_id: str | None = None,
        severity: Literal["error", "warning"] = "warning",
        line: int | None = None,
        column: int | None = None,
    ) -> ValidationError:
        return ValidationError(
            message=message,
            severity=severity,
            code=code,
            file_path=file_path,
            focus_id=obj_id if kind == "focus" else None,
            event_id=event_id or (obj_id if kind == "event" else None),
            idea_id=idea_id,
            line=line,
            column=column,
        )

    @staticmethod
    def _mask_script_comments(text: str) -> str:
        """Replace comments and quoted values while retaining source offsets."""

        chars = list(text)
        in_quote = False
        index = 0
        while index < len(chars):
            char = chars[index]
            if char == "\\" and in_quote:
                chars[index] = " "
                if index + 1 < len(chars) and chars[index + 1] != "\n":
                    chars[index + 1] = " "
                index += 2
                continue
            if char == '"':
                chars[index] = " "
                in_quote = not in_quote
                index += 1
                continue
            if in_quote:
                if char != "\n":
                    chars[index] = " "
                index += 1
                continue
            if char == "#" and not in_quote:
                while index < len(chars) and chars[index] != "\n":
                    chars[index] = " "
                    index += 1
                continue
            index += 1
        return "".join(chars)

    @classmethod
    def _script_reference_location(
        cls,
        script: str,
        script_offset: int,
        *,
        obj_id: str | None,
        file_path: str | None,
        token: str,
    ) -> tuple[int, int]:
        """Resolve a script finding to its source file when possible."""

        if file_path is None:
            return cls._line_column(script, script_offset)
        path = Path(file_path)
        try:
            source = path.read_text(encoding="utf-8-sig", errors="ignore")
        except OSError:
            return cls._line_column(script, script_offset)

        ranges: list[tuple[int, int]] = []

        def find_object_ranges(fragment: str, base_offset: int) -> None:
            try:
                spans = top_level_assignments(fragment)
            except (ParseError, ValueError):
                return
            for span in spans:
                if (
                    not span.is_block
                    or span.body_start is None
                    or span.body_end is None
                ):
                    continue
                body = fragment[span.body_start : span.body_end]
                start = base_offset + span.body_start
                end = base_offset + span.body_end
                direct_id = next(
                    (
                        item
                        for item in top_level_assignments(body)
                        if item.key == "id" and not item.is_block
                    ),
                    None,
                )
                value = (
                    body[direct_id.value_start : direct_id.value_end]
                    .strip()
                    .strip('"')
                    if direct_id is not None
                    else ""
                )
                if obj_id is not None and (span.key == obj_id or value == obj_id):
                    ranges.append((start, end))
                find_object_ranges(body, start)

        if obj_id is not None:
            find_object_ranges(source, 0)
        masked = cls._mask_script_comments(source)
        pattern = re.compile(rf"\b{re.escape(token)}\s*=")
        for start, end in ranges:
            match = pattern.search(masked, start, end)
            if match is not None:
                return cls._line_column(source, match.start())
        base = source.find(script)
        if base >= 0:
            return cls._line_column(source, base + script_offset)
        match = pattern.search(masked)
        if match is not None:
            return cls._line_column(source, match.start())
        return cls._line_column(script, script_offset)

    @staticmethod
    def _technology_refs(script: str) -> list[str]:
        refs: list[str] = []
        for body in _TECH_BLOCK_RE.findall(script):
            refs.extend(
                key for key in re.findall(r"\b([A-Za-z0-9_.:-]+)\s*=", body) if key != "popup"
            )
        return refs

    @staticmethod
    def _equipment_refs(script: str) -> list[str]:
        refs: list[str] = []
        for body in _EQUIPMENT_STOCKPILE_RE.findall(script):
            refs.extend(re.findall(r"\btype\s*=\s*([A-Za-z0-9_.:-]+)", body))
        return refs

    def _known_idea_ids(self) -> set[str]:
        ids = set(self._ideas)
        cached = self._scan_cache.get("ideas")
        if cached is not None:
            ids.update(cached)
            return ids
        scanned: set[str] = set()
        for base in self._data_roots():
            for rel in ("common/ideas", "common/national_ideas"):
                ideas_dir = base / rel
                if not ideas_dir.exists():
                    continue
                for path in ideas_dir.glob("*.txt"):
                    try:
                        scanned.update(scan_idea_ids_file(path))
                    except (OSError, ValueError):
                        continue
        self._scan_cache["ideas"] = scanned
        ids.update(scanned)
        return ids

    def _known_event_ids(self) -> set[str]:
        ids = set(self._events)
        cached = self._scan_cache.get("events")
        if cached is not None:
            ids.update(cached)
            return ids
        scanned: set[str] = set()
        for base in self._data_roots():
            events_dir = base / "events"
            if not events_dir.exists():
                continue
            for path in events_dir.glob("*.txt"):
                try:
                    scanned.update(scan_event_ids_file(path))
                except (OSError, ValueError):
                    continue
        self._scan_cache["events"] = scanned
        ids.update(scanned)
        return ids

    def _known_character_ids(self) -> set[str]:
        cached = self._scan_cache.get("characters")
        if cached is not None:
            return set(cached)
        character_ids = collect_character_ids(self.mod_root, self.hoi4_install)
        self._scan_cache["characters"] = character_ids
        return set(character_ids)

    def _known_focus_icons(self) -> set[str]:
        cached = self._scan_cache.get("focus_icons")
        if cached is not None:
            return set(cached)
        icons: set[str] = set()
        for base in self._data_roots():
            interface_dir = base / "interface"
            if not interface_dir.exists():
                continue
            for path in interface_dir.rglob("*.gfx"):
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                icons.update(name for name in _GFX_NAME_RE.findall(text) if name.startswith("GFX_"))
        self._scan_cache["focus_icons"] = icons
        return icons

    def _known_sprite_textures(self) -> dict[str, str]:
        def scan_text(text: str, textures: dict[str, str]) -> None:
            for body, _, _ in iter_assignment_blocks(text, "spriteType"):
                assignments = {
                    span.key: span
                    for span in top_level_assignments(body)
                    if not span.is_block
                }
                name_span = assignments.get("name")
                texture_span = assignments.get("texturefile")
                if name_span is None or texture_span is None:
                    continue
                name = body[name_span.value_start : name_span.value_end].strip().strip('"')
                texture = (
                    body[texture_span.value_start : texture_span.value_end]
                    .strip()
                    .strip('"')
                    .replace("\\", "/")
                )
                if name and texture:
                    textures[name] = texture

        def scan(base: Path, textures: dict[str, str]) -> None:
            interface_dir = base / "interface"
            if not interface_dir.is_dir():
                return
            for path in interface_dir.rglob("*.gfx"):
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                    scan_text(text, textures)
                except (OSError, ValueError):
                    continue

        if self._sprite_texture_cache is None:
            vanilla: dict[str, str] = {}
            if self.hoi4_install is not None:
                scan(self.hoi4_install, vanilla)
            self._sprite_texture_cache = vanilla
        textures = dict(self._sprite_texture_cache)
        scan(self.mod_root, textures)
        for path, content in self._pending_asset_writes.items():
            if path.suffix.lower() == ".gfx":
                scan_text(content.decode("utf-8", errors="ignore"), textures)
        return dict(textures)

    def _texture_exists(self, relative_path: str) -> bool:
        normalized = relative_path.replace("\\", "/").lstrip("/")
        return any(
            (root / normalized).is_file()
            for root in (self.mod_root, self.hoi4_install)
            if root is not None
        ) or (self.mod_root / normalized) in self._pending_asset_writes

    def _known_technology_ids(self) -> set[str]:
        cached = self._scan_cache.get("technologies")
        if cached is not None:
            return set(cached)
        ids: set[str] = set()
        ignored = {
            "technologies",
            "folder",
            "path",
            "xor",
            "research_cost",
            "start_year",
            "categories",
        }
        for base in self._data_roots():
            tech_dir = base / "common" / "technologies"
            if not tech_dir.exists():
                continue
            for path in tech_dir.glob("*.txt"):
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                ids.update(
                    candidate
                    for candidate in _SCRIPT_BLOCK_ID_RE.findall(text)
                    if candidate not in ignored
                )
        self._scan_cache["technologies"] = ids
        return ids

    def _equipment_unlock_technologies(self) -> dict[str, tuple[str, ...]]:
        if self._equipment_unlock_cache is not None:
            return dict(self._equipment_unlock_cache)
        unlocks: dict[str, list[str]] = {}
        for base in self._data_roots():
            directory = base / "common" / "technologies"
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.txt")):
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                    wrappers = [
                        span
                        for span in top_level_assignments(text)
                        if span.key == "technologies"
                        and span.is_block
                        and span.body_start is not None
                        and span.body_end is not None
                    ]
                    for wrapper in wrappers:
                        assert wrapper.body_start is not None
                        assert wrapper.body_end is not None
                        body = text[wrapper.body_start : wrapper.body_end]
                        for technology in top_level_assignments(body):
                            if (
                                not technology.is_block
                                or technology.body_start is None
                                or technology.body_end is None
                            ):
                                continue
                            tech_body = body[
                                technology.body_start : technology.body_end
                            ]
                            equipment_span = next(
                                (
                                    span
                                    for span in top_level_assignments(tech_body)
                                    if span.key == "enable_equipments"
                                    and span.is_block
                                    and span.body_start is not None
                                    and span.body_end is not None
                                ),
                                None,
                            )
                            if equipment_span is None:
                                continue
                            assert equipment_span.body_start is not None
                            assert equipment_span.body_end is not None
                            equipment_body = strip_comments(
                                tech_body[
                                    equipment_span.body_start : equipment_span.body_end
                                ]
                            )
                            for equipment in re.findall(
                                r"\b[A-Za-z][A-Za-z0-9_.:-]*\b",
                                equipment_body,
                            ):
                                unlocks.setdefault(equipment, []).append(
                                    technology.key
                                )
                except (OSError, ValueError):
                    continue
        self._equipment_unlock_cache = {
            equipment: tuple(dict.fromkeys(technologies))
            for equipment, technologies in unlocks.items()
        }
        return dict(self._equipment_unlock_cache)

    def _validate_equipment_variant_unlocks(
        self,
        country: Country,
    ) -> list[ValidationError]:
        unlock_map = self._equipment_unlock_technologies()
        grants, occurrences = self._history_equipment_operations(country.raw_history)
        grants.extend(
            self._modeled_technology_grants_missing_from_history(country, grants)
        )
        errors: list[ValidationError] = []
        reported_offsets: set[int] = set()
        for occurrence in occurrences:
            variant = occurrence.variant
            if not (
                variant.equipment_type.startswith("ship_hull_")
                or "airframe" in variant.equipment_type
            ):
                continue
            unlocks = unlock_map.get(variant.equipment_type, ())
            if not unlocks:
                continue
            available_conditions = [
                (grant.required_dlc, grant.excluded_dlc)
                for grant in grants
                if grant.technology in unlocks
                and (grant.offset < occurrence.offset or grant.offset < 0)
            ]
            if self._dlc_conditions_are_covered(
                variant.required_dlc,
                variant.excluded_dlc,
                available_conditions,
            ):
                continue
            if occurrence.offset in reported_offsets:
                continue
            reported_offsets.add(occurrence.offset)
            line, column = self._line_column(country.raw_history, occurrence.offset)
            errors.append(
                ValidationError(
                    message=(
                        f"Country '{country.tag}' creates modular variant "
                        f"'{variant.name}' ({variant.equipment_type}) before its "
                        "chassis technology is unlocked on every matching DLC path. "
                        "Grant one of these technologies earlier in the same or a "
                        f"broader DLC branch: {', '.join(unlocks)}. "
                        "allow_without_tech is insufficient."
                    ),
                    severity="error",
                    code="equipment_variant_chassis_not_unlocked",
                    country_tag=country.tag,
                    file_path=(
                        str(country.history_path)
                        if country.history_path is not None
                        else None
                    ),
                    line=line,
                    column=column,
                )
            )
        return errors

    @classmethod
    def _history_equipment_operations(
        cls,
        history: str,
    ) -> tuple[list[_HistoryTechnologyGrant], list[_HistoryVariantOccurrence]]:
        """Return ordered scenario-start technology and variant operations.

        Boolean ``AND``/``OR``/``NOT`` DLC gates are retained so a technology
        must cover every satisfiable path where a variant can run. Later dated
        history is intentionally excluded.
        """

        grants: list[_HistoryTechnologyGrant] = []
        variants: list[_HistoryVariantOccurrence] = []

        true_formula: _DLCFormula = (((), ()),)
        false_formula: _DLCFormula = ()

        def normalize(conditions: Sequence[_DLCCondition]) -> _DLCFormula:
            result: list[_DLCCondition] = []
            seen: set[_DLCCondition] = set()
            for required, excluded in conditions:
                normalized = (
                    tuple(dict.fromkeys(required)),
                    tuple(dict.fromkeys(excluded)),
                )
                if set(normalized[0]) & set(normalized[1]) or normalized in seen:
                    continue
                seen.add(normalized)
                result.append(normalized)
            return tuple(result)

        def formula_or(left: _DLCFormula, right: _DLCFormula) -> _DLCFormula:
            return normalize((*left, *right))

        def formula_and(left: _DLCFormula, right: _DLCFormula) -> _DLCFormula:
            combined: list[_DLCCondition] = []
            for left_required, left_excluded in left:
                for right_required, right_excluded in right:
                    combined.append(
                        (
                            (*left_required, *right_required),
                            (*left_excluded, *right_excluded),
                        )
                    )
            return normalize(combined)

        def formula_not(formula: _DLCFormula) -> _DLCFormula:
            result = true_formula
            for required, excluded in formula:
                negated_clause: _DLCFormula = normalize(
                    [
                        *(((), (dlc,)) for dlc in required),
                        *(((dlc,), ()) for dlc in excluded),
                    ]
                )
                result = formula_and(result, negated_clause)
            return result

        def dlc_formula(
            fragment: str,
            *,
            operator: Literal["AND", "OR"] = "AND",
        ) -> _DLCFormula | None:
            terms: list[_DLCFormula] = []
            for item in top_level_assignments(fragment):
                term: _DLCFormula | None = None
                if not item.is_block:
                    if item.key.lower() == "has_dlc":
                        value = fragment[item.value_start : item.value_end]
                        term = (((value.strip().strip('"'),), ()),)
                elif item.body_start is not None and item.body_end is not None:
                    child = fragment[item.body_start : item.body_end]
                    key = item.key.upper()
                    if key in {"AND", "OR"}:
                        term = dlc_formula(child, operator=cast(Literal["AND", "OR"], key))
                    elif key == "NOT":
                        nested = dlc_formula(child)
                        term = formula_not(nested) if nested is not None else None
                if term is not None:
                    terms.append(term)
            if not terms:
                return None
            result = true_formula if operator == "AND" else false_formula
            for term in terms:
                result = (
                    formula_and(result, term)
                    if operator == "AND"
                    else formula_or(result, term)
                )
            return result

        def process_conditional(
            child: str,
            child_offset: int,
            incoming: _DLCFormula,
        ) -> _DLCFormula:
            children = top_level_assignments(child)
            limit = next(
                (
                    item
                    for item in children
                    if item.key.lower() == "limit"
                    and item.is_block
                    and item.body_start is not None
                    and item.body_end is not None
                ),
                None,
            )
            predicate: _DLCFormula | None = None
            if limit is not None:
                assert limit.body_start is not None
                assert limit.body_end is not None
                predicate = dlc_formula(child[limit.body_start : limit.body_end])
            if predicate is None:
                positive = incoming
                remaining = incoming
            else:
                positive = formula_and(incoming, predicate)
                remaining = formula_and(incoming, formula_not(predicate))
            for item in children:
                key = item.key.lower()
                if key == "limit":
                    continue
                if key == "else_if" and item.is_block:
                    if item.body_start is None or item.body_end is None:
                        continue
                    remaining = process_conditional(
                        child[item.body_start : item.body_end],
                        child_offset + item.body_start,
                        remaining,
                    )
                    continue
                if key == "else" and item.is_block:
                    if item.body_start is None or item.body_end is None:
                        continue
                    walk(
                        child[item.body_start : item.body_end],
                        child_offset + item.body_start,
                        remaining,
                    )
                    remaining = false_formula
                    continue
                process_span(child, item, child_offset, positive)
            return remaining

        def process_span(
            fragment: str,
            span: AssignmentSpan,
            base_offset: int,
            conditions: _DLCFormula,
        ) -> None:
            if not conditions:
                return
            if (
                not span.is_block
                or span.body_start is None
                or span.body_end is None
            ):
                return
            if _DATE_ASSIGNMENT_RE.fullmatch(span.key):
                return
            child = fragment[span.body_start : span.body_end]
            child_offset = base_offset + span.body_start
            if span.key.lower() == "if":
                process_conditional(child, child_offset, conditions)
                return
            if span.key == "set_technology":
                for technology in top_level_assignments(child):
                    if technology.is_block:
                        continue
                    value = child[
                        technology.value_start : technology.value_end
                    ].strip().lower()
                    if value in {"0", "no"}:
                        continue
                    for required_dlc, excluded_dlc in conditions:
                        grants.append(
                            _HistoryTechnologyGrant(
                                technology=technology.key,
                                required_dlc=required_dlc,
                                excluded_dlc=excluded_dlc,
                                offset=child_offset + technology.start,
                            )
                        )
                return
            if span.key == "create_equipment_variant":
                parsed = find_equipment_variants(fragment[span.start : span.end])
                if parsed:
                    for required_dlc, excluded_dlc in conditions:
                        variants.append(
                            _HistoryVariantOccurrence(
                                variant=dataclasses.replace(
                                    parsed[0],
                                    required_dlc=required_dlc,
                                    excluded_dlc=excluded_dlc,
                                ),
                                offset=base_offset + span.start,
                            )
                        )
                return
            walk(child, child_offset, conditions)

        def walk(
            fragment: str,
            base_offset: int,
            conditions: _DLCFormula,
        ) -> None:
            for span in top_level_assignments(fragment):
                process_span(
                    fragment,
                    span,
                    base_offset,
                    conditions,
                )

        walk(history, 0, true_formula)
        grants.sort(key=lambda item: item.offset)
        variants.sort(key=lambda item: item.offset)
        return grants, variants

    @staticmethod
    def _modeled_technology_grants_missing_from_history(
        country: Country,
        history_grants: Sequence[_HistoryTechnologyGrant],
    ) -> list[_HistoryTechnologyGrant]:
        """Represent unsaved model-only technologies as unconditional grants."""

        history_ids = {grant.technology for grant in history_grants}
        return [
            _HistoryTechnologyGrant(technology, (), (), -1)
            for technology, level in country.technologies.items()
            if level and technology not in history_ids
        ]

    @staticmethod
    def _dlc_conditions_are_covered(
        required_dlc: Sequence[str],
        excluded_dlc: Sequence[str],
        grant_conditions: Sequence[tuple[Sequence[str], Sequence[str]]],
    ) -> bool:
        """Return whether grants cover every feasible DLC path for an effect."""

        assignment: dict[str, bool] = {}
        for dlc in required_dlc:
            assignment[dlc] = True
        for dlc in excluded_dlc:
            if assignment.get(dlc) is True:
                return True  # The variant condition itself is impossible.
            assignment[dlc] = False

        clauses: list[dict[str, bool]] = []
        for required, excluded in grant_conditions:
            clause: dict[str, bool] = {}
            impossible = False
            for dlc in required:
                clause[dlc] = True
            for dlc in excluded:
                if clause.get(dlc) is True:
                    impossible = True
                    break
                clause[dlc] = False
            if not impossible:
                clauses.append(clause)

        def has_uncovered_path(
            index: int,
            current: dict[str, bool],
        ) -> bool:
            if index == len(clauses):
                return True
            clause = clauses[index]
            if any(
                name in current and current[name] is not expected
                for name, expected in clause.items()
            ):
                return has_uncovered_path(index + 1, current)
            undecided = [name for name in clause if name not in current]
            if not undecided:
                return False
            for name in undecided:
                branch = dict(current)
                branch[name] = not clause[name]
                if has_uncovered_path(index + 1, branch):
                    return True
            return False

        return not has_uncovered_path(0, assignment)

    @classmethod
    def _history_technology_grants(cls, history: str) -> set[str]:
        """Return scenario-start technologies, excluding later dated blocks."""

        grants, _ = cls._history_equipment_operations(history)
        return {grant.technology for grant in grants}

    @staticmethod
    def _line_column(text: str, offset: int) -> tuple[int, int]:
        offset = max(0, min(offset, len(text)))
        line = text.count("\n", 0, offset) + 1
        previous_newline = text.rfind("\n", 0, offset)
        return line, offset - previous_newline

    def _known_equipment_ids(self) -> set[str]:
        cached = self._scan_cache.get("equipment")
        if cached is not None:
            return set(cached)
        ids: set[str] = set()
        for base in self._data_roots():
            equipment_dir = base / "common" / "units" / "equipment"
            if not equipment_dir.exists():
                continue
            for path in equipment_dir.glob("*.txt"):
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                ids.update(_SCRIPT_BLOCK_ID_RE.findall(text))
        self._scan_cache["equipment"] = ids
        return ids

    def _known_country_name_pool_tags(self) -> set[str]:
        cached = self._scan_cache.get("country_name_pools")
        if cached is not None:
            return set(cached) | set(self._country_name_pool_updates)
        tags: set[str] = set()
        for base in self._data_roots():
            directory = base / "common" / "names"
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.txt")):
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                    tags.update(
                        span.key
                        for span in top_level_assignments(text)
                        if span.is_block
                        and re.fullmatch(r"[A-Z0-9]{3}", span.key)
                    )
                except (OSError, ValueError):
                    continue
        self._scan_cache["country_name_pools"] = tags
        return set(tags) | set(self._country_name_pool_updates)

    def _known_sub_unit_types(self) -> set[str]:
        cached = self._scan_cache.get("sub_units")
        if cached is not None:
            return set(cached)
        result: set[str] = set()
        for root in self._data_roots():
            directory = root / "common" / "units"
            if not directory.is_dir():
                continue
            for path in directory.glob("*.txt"):
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                    for body, _, _ in iter_assignment_blocks(text, "sub_units"):
                        result.update(
                            span.key
                            for span in top_level_assignments(body)
                            if span.is_block
                        )
                except (OSError, ValueError):
                    continue
        self._scan_cache["sub_units"] = result
        return result

    def _effective_province_types(self) -> dict[int, str]:
        cached = self._scan_cache.get("province_type_rows")
        if cached is not None:
            # The scan cache stores strings, so encode ``id:type`` pairs.
            cached_result: dict[int, str] = {}
            for item in cached:
                id_text, separator, province_type = item.partition(":")
                if separator and id_text.isdigit():
                    cached_result[int(id_text)] = province_type
            return cached_result
        path: Path | None = None
        for root in (self.mod_root, self.hoi4_install):
            if root is None:
                continue
            candidate = root / "map" / "definition.csv"
            if candidate.is_file():
                path = candidate
                break
        province_types: dict[int, str] = {}
        if path is not None:
            with path.open(encoding="utf-8", errors="ignore", newline="") as handle:
                for row in csv.reader(handle, delimiter=";"):
                    if len(row) < 5:
                        continue
                    try:
                        numeric_id = int(row[0].strip())
                    except ValueError:
                        continue
                    province_types[numeric_id] = row[4].strip().lower()
        self._scan_cache["province_type_rows"] = {
            f"{province_id}:{province_type}"
            for province_id, province_type in province_types.items()
        }
        return province_types

    def _validate_oob_context(
        self,
        oob: OrderOfBattle,
        country_tag: str,
    ) -> list[ValidationError]:
        errors: list[ValidationError] = []
        known_units = self._known_sub_unit_types()
        known_equipment = self._known_equipment_ids()
        known_tags = set(load_all_tags(self.hoi4_install, self.mod_root))
        known_tags.update(self._countries)
        variants_by_tag: dict[str, tuple[EquipmentVariant, ...]] = {}

        def variants_for(tag: str) -> tuple[EquipmentVariant, ...]:
            if tag not in variants_by_tag:
                try:
                    variants_by_tag[tag] = find_equipment_variants(
                        self.get_country(tag).raw_history
                    )
                except (KeyError, OSError, ValueError):
                    variants_by_tag[tag] = ()
            return variants_by_tag[tag]

        def variant_available(
            variant: EquipmentVariant,
        ) -> bool:
            required = set(oob.required_dlc)
            excluded = set(oob.excluded_dlc)
            return bool(
                not (set(variant.required_dlc) & excluded)
                and not (set(variant.excluded_dlc) & required)
                and set(variant.required_dlc) <= required
                and set(variant.excluded_dlc) <= excluded
            )

        def has_matching_variant(
            equipment_type: str,
            version_name: str,
            *,
            owner: str,
            creator: str,
        ) -> bool:
            candidate_tags = tuple(
                dict.fromkeys(
                    tag for tag in (creator, owner, country_tag) if tag
                )
            )
            return any(
                variant.name == version_name
                and variant.equipment_type == equipment_type
                and variant_available(variant)
                for tag in candidate_tags
                for variant in variants_for(tag)
            )

        reported_legacy_mtg_mismatch = False
        reported_ungated_mtg_oob = False
        for template in oob.templates:
            for battalion in (*template.battalions, *template.support):
                if known_units and battalion.unit_type not in known_units:
                    errors.append(
                        ValidationError(
                            message=(
                                f"OOB '{oob.name}' template '{template.name}' uses "
                                f"unknown sub-unit type '{battalion.unit_type}'"
                            ),
                            severity="error",
                            code="unknown_oob_unit_type",
                            country_tag=country_tag,
                            file_path=str(oob.path) if oob.path is not None else None,
                        )
                    )

        province_to_state: dict[int, State] = {}
        for state_id in sorted(set(self._state_ids) | set(self._states)):
            try:
                state = self.get_state(state_id)
            except (KeyError, OSError, ValueError):
                continue
            for province_id in state.provinces:
                province_to_state[province_id] = state
        province_types = self._effective_province_types()
        for division in oob.divisions:
            location_state = province_to_state.get(division.location)
            label = division.name or "<ordered name>"
            if location_state is None or (
                province_types
                and division.location not in province_types
            ):
                errors.append(
                    ValidationError(
                        message=(
                            f"OOB '{oob.name}' division '{label}' uses unknown "
                            f"province {division.location}"
                        ),
                        severity="error",
                        code="unknown_oob_province",
                        country_tag=country_tag,
                        state_id=(
                            location_state.id
                            if location_state is not None
                            else None
                        ),
                        file_path=str(oob.path) if oob.path is not None else None,
                    )
                )
                continue
            province_type = province_types.get(division.location)
            if province_type is not None and province_type != "land":
                errors.append(
                    ValidationError(
                        message=(
                            f"OOB '{oob.name}' division '{label}' is placed in "
                            f"{province_type} province {division.location}"
                        ),
                        severity="error",
                        code="oob_water_location",
                        country_tag=country_tag,
                        state_id=location_state.id,
                        file_path=str(oob.path) if oob.path is not None else None,
                    )
                )
            if location_state.owner != country_tag:
                errors.append(
                    ValidationError(
                        message=(
                            f"OOB '{oob.name}' division '{label}' is placed in "
                            f"province {division.location}, owned by {location_state.owner} "
                            f"instead of {country_tag}"
                        ),
                        severity="error",
                        code="oob_location_not_owned",
                        country_tag=country_tag,
                        state_id=location_state.id,
                        file_path=str(oob.path) if oob.path is not None else None,
                    )
                )
        def validate_owned_location(
            location: int,
            *,
            label: str,
            code_unknown: str,
            code_owner: str,
            allow_state_id: bool = False,
        ) -> None:
            location_state: State | None = None
            if allow_state_id:
                try:
                    location_state = self.get_state(location)
                except (KeyError, OSError, ValueError):
                    location_state = None
            if location_state is None:
                location_state = province_to_state.get(location)
            if location_state is None:
                errors.append(
                    ValidationError(
                        message=(
                            f"OOB '{oob.name}' {label} uses unknown location {location}"
                        ),
                        severity="error",
                        code=code_unknown,
                        country_tag=country_tag,
                        file_path=str(oob.path) if oob.path is not None else None,
                    )
                )
            elif location_state.owner != country_tag:
                errors.append(
                    ValidationError(
                        message=(
                            f"OOB '{oob.name}' {label} is at {location}, owned by "
                            f"{location_state.owner} instead of {country_tag}"
                        ),
                        severity="error",
                        code=code_owner,
                        country_tag=country_tag,
                        state_id=location_state.id,
                        file_path=str(oob.path) if oob.path is not None else None,
                    )
                )

        for fleet in oob.fleets:
            validate_owned_location(
                fleet.naval_base,
                label=f"fleet '{fleet.name}' naval base",
                code_unknown="unknown_oob_naval_location",
                code_owner="oob_naval_location_not_owned",
            )
            for task_force in fleet.task_forces:
                validate_owned_location(
                    task_force.location,
                    label=f"task force '{task_force.name}' location",
                    code_unknown="unknown_oob_naval_location",
                    code_owner="oob_naval_location_not_owned",
                )
                for ship in task_force.ships:
                    if known_units and ship.definition not in known_units:
                        errors.append(
                            ValidationError(
                                message=(
                                    f"OOB '{oob.name}' ship '{ship.name}' uses "
                                    f"unknown definition '{ship.definition}'"
                                ),
                                severity="error",
                                code="unknown_oob_ship_definition",
                                country_tag=country_tag,
                                file_path=(
                                    str(oob.path) if oob.path is not None else None
                                ),
                            )
                        )
                    for equipment in ship.equipment:
                        if (
                            known_equipment
                            and equipment.equipment_type not in known_equipment
                        ):
                            errors.append(
                                ValidationError(
                                    message=(
                                        f"OOB '{oob.name}' ship '{ship.name}' uses "
                                        "unknown equipment "
                                        f"'{equipment.equipment_type}'"
                                    ),
                                    severity="error",
                                    code="unknown_oob_equipment",
                                    country_tag=country_tag,
                                    file_path=(
                                        str(oob.path)
                                        if oob.path is not None
                                        else None
                                    ),
                                )
                            )
                        is_mtg_hull = equipment.equipment_type.startswith(
                            "ship_hull_"
                        )
                        mtg_required = "Man the Guns" in oob.required_dlc
                        mtg_excluded = "Man the Guns" in oob.excluded_dlc
                        if not is_mtg_hull and not mtg_excluded:
                            code = (
                                "mtg_naval_oob_uses_legacy_equipment"
                                if mtg_required
                                else "legacy_naval_oob_without_dlc_fallback"
                            )
                            if not reported_legacy_mtg_mismatch:
                                errors.append(
                                    ValidationError(
                                        message=(
                                            f"OOB '{oob.name}' uses legacy naval "
                                            f"equipment '{equipment.equipment_type}' "
                                            "when Man the Guns may be active. HOI4 "
                                            "will skip these ships because no proper "
                                            "equipment variant exists. Assign a "
                                            "legacy naval OOB with excluded_dlc="
                                            "('Man the Guns',) and a hull-based OOB "
                                            "with required_dlc=('Man the Guns',)."
                                        ),
                                        severity="error",
                                        code=code,
                                        country_tag=country_tag,
                                        file_path=(
                                            str(oob.path)
                                            if oob.path is not None
                                            else None
                                        ),
                                    )
                                )
                                reported_legacy_mtg_mismatch = True
                        if is_mtg_hull and not mtg_required:
                            if not reported_ungated_mtg_oob:
                                errors.append(
                                    ValidationError(
                                        message=(
                                            f"OOB '{oob.name}' uses Man the Guns hull "
                                            "equipment but is not gated with "
                                            "required_dlc=('Man the Guns',). Add the "
                                            "gate and a legacy naval fallback."
                                        ),
                                        severity="error",
                                        code="ungated_mtg_naval_oob",
                                        country_tag=country_tag,
                                        file_path=(
                                            str(oob.path)
                                            if oob.path is not None
                                            else None
                                        ),
                                    )
                                )
                                reported_ungated_mtg_oob = True
                        if is_mtg_hull and not equipment.version_name:
                            errors.append(
                                ValidationError(
                                    message=(
                                        f"OOB '{oob.name}' ship '{ship.name}' uses "
                                        f"'{equipment.equipment_type}' without a "
                                        "version_name matching a country-history "
                                        "create_equipment_variant definition."
                                    ),
                                    severity="error",
                                    code="missing_mtg_ship_variant_name",
                                    country_tag=country_tag,
                                    file_path=(
                                        str(oob.path)
                                        if oob.path is not None
                                        else None
                                    ),
                                )
                            )
                        elif is_mtg_hull:
                            if not has_matching_variant(
                                equipment.equipment_type,
                                equipment.version_name,
                                owner=equipment.owner,
                                creator=equipment.creator,
                            ):
                                errors.append(
                                    ValidationError(
                                        message=(
                                            f"OOB '{oob.name}' ship '{ship.name}' "
                                            f"references {equipment.equipment_type} "
                                            f"variant '{equipment.version_name}', but "
                                            "no compatible create_equipment_variant "
                                            "definition was found in the owner or "
                                            "creator country history."
                                        ),
                                        severity="error",
                                        code="missing_mtg_equipment_variant",
                                        country_tag=country_tag,
                                        file_path=(
                                            str(oob.path)
                                            if oob.path is not None
                                            else None
                                        ),
                                    )
                                )
                        for owner in (equipment.owner, equipment.creator):
                            if owner and owner not in known_tags:
                                errors.append(
                                    ValidationError(
                                        message=(
                                            f"OOB '{oob.name}' ship '{ship.name}' "
                                            f"uses unknown equipment owner '{owner}'"
                                        ),
                                        severity="error",
                                        code="unknown_oob_equipment_owner",
                                        country_tag=country_tag,
                                        file_path=(
                                            str(oob.path)
                                            if oob.path is not None
                                            else None
                                        ),
                                    )
                                )
        for wing in oob.air_wings:
            validate_owned_location(
                wing.location,
                label=f"air wing '{wing.equipment_type}' location",
                code_unknown="unknown_oob_air_location",
                code_owner="oob_air_location_not_owned",
                allow_state_id=True,
            )
            if known_equipment and wing.equipment_type not in known_equipment:
                errors.append(
                    ValidationError(
                        message=(
                            f"OOB '{oob.name}' air wing uses unknown equipment "
                            f"'{wing.equipment_type}'"
                        ),
                        severity="error",
                        code="unknown_oob_equipment",
                        country_tag=country_tag,
                        file_path=str(oob.path) if oob.path is not None else None,
                    )
                )
            is_bba_airframe = "airframe" in wing.equipment_type
            bba_required = "By Blood Alone" in oob.required_dlc
            if is_bba_airframe and not bba_required:
                errors.append(
                    ValidationError(
                        message=(
                            f"OOB '{oob.name}' uses By Blood Alone airframe "
                            f"equipment '{wing.equipment_type}' but is not gated "
                            "with required_dlc=('By Blood Alone',). Add the gate "
                            "and a legacy air OOB fallback."
                        ),
                        severity="error",
                        code="ungated_bba_air_oob",
                        country_tag=country_tag,
                        file_path=str(oob.path) if oob.path is not None else None,
                    )
                )
            if is_bba_airframe and not wing.version_name:
                errors.append(
                    ValidationError(
                        message=(
                            f"OOB '{oob.name}' air wing uses "
                            f"'{wing.equipment_type}' without a version_name "
                            "matching a country-history "
                            "create_equipment_variant definition."
                        ),
                        severity="error",
                        code="missing_bba_air_variant_name",
                        country_tag=country_tag,
                        file_path=str(oob.path) if oob.path is not None else None,
                    )
                )
            elif is_bba_airframe and not has_matching_variant(
                wing.equipment_type,
                wing.version_name,
                owner=wing.owner,
                creator=wing.creator,
            ):
                errors.append(
                    ValidationError(
                        message=(
                            f"OOB '{oob.name}' air wing references "
                            f"{wing.equipment_type} variant "
                            f"'{wing.version_name}', but no compatible "
                            "create_equipment_variant definition was found in "
                            "the owner or creator country history."
                        ),
                        severity="error",
                        code="missing_bba_equipment_variant",
                        country_tag=country_tag,
                        file_path=str(oob.path) if oob.path is not None else None,
                    )
                )
            for owner in (wing.owner, wing.creator):
                if owner and owner not in known_tags:
                    errors.append(
                        ValidationError(
                            message=(
                                f"OOB '{oob.name}' air wing uses unknown equipment "
                                f"owner '{owner}'"
                            ),
                            severity="error",
                            code="unknown_oob_equipment_owner",
                            country_tag=country_tag,
                            file_path=str(oob.path) if oob.path is not None else None,
                        )
                    )
        return errors

    def _data_roots(self) -> list[Path]:
        roots = [self.mod_root]
        if self.hoi4_install is not None:
            roots.append(self.hoi4_install)
        return roots

    def _sync_country_loc(self, country: Country) -> None:
        name = country.name or country.tag
        adj = country.adjective or name
        dirty_keys: list[str] = []
        target = self.mod_root / "localisation" / "english" / f"{country.tag}_country_l_english.yml"
        for suffix in _country_localisation_suffixes(country):
            k = f"{country.tag}{suffix}"
            self._loc_entries[k] = name
            self._loc_entries[f"{k}_DEF"] = name
            dirty_keys.extend([k, f"{k}_DEF"])
        self._loc_entries[f"{country.tag}_ADJ"] = adj
        dirty_keys.append(f"{country.tag}_ADJ")
        if country.leader:
            self._loc_entries[country.leader.character_id] = country.leader.name
            self._loc_entries[f"{country.leader.character_id}_desc"] = (
                f"{country.leader.name} (leader)"
            )
            dirty_keys.extend([country.leader.character_id, f"{country.leader.character_id}_desc"])
        for key in dirty_keys:
            # Existing country localization may intentionally live in a shared
            # file. Keep that routing instead of creating a second definition
            # in the generated country file. Only newly introduced keys use the
            # country-specific default target.
            self._loc_sources.setdefault(key, target)
            self._dirty_loc_files.add(self._loc_sources[key])
        self._dirty_loc_keys.update(dirty_keys)
        self._dirty.add("localization")

    def _sync_character_loc(self, character: Character) -> None:
        display_name = character.name or character.id
        target = (
            self.mod_root
            / "localisation"
            / "english"
            / f"{character.country_tag or 'mod'}_characters_l_english.yml"
        )
        values = {
            character.id: display_name,
            f"{character.id}_desc": f"{display_name}",
        }
        for key, value in values.items():
            self._loc_entries[key] = value
            self._loc_sources.setdefault(key, target)
            self._dirty_loc_files.add(self._loc_sources[key])
            self._dirty_loc_keys.add(key)
        self._dirty.add("localization")

    def _group_characters_by_file(
        self,
        *,
        dirty_only: bool = False,
    ) -> dict[Path, list[Character]]:
        dirty_files = set(self._dirty_character_files) if dirty_only else set()
        grouped: dict[Path, list[Character]] = {
            path: [] for path in dirty_files
        }
        for character in self._characters.values():
            path = character.path or (
                self.mod_root
                / "common"
                / "characters"
                / f"{character.country_tag or 'mod'}_characters.txt"
            )
            if dirty_only and path not in dirty_files:
                continue
            grouped.setdefault(path, []).append(character)
        for characters in grouped.values():
            characters.sort(
                key=lambda item: (
                    item.source_index if item.source_index >= 0 else 1_000_000,
                    item.id,
                )
            )
        return grouped

    def _group_loc_by_file(self, dirty_only: bool = False) -> dict[Path, dict[str, str]]:
        dirty_files: set[Path] = set(self._dirty_loc_files) if dirty_only else set()
        if dirty_only:
            for key in self._dirty_loc_keys:
                source = self._loc_sources.get(key, self.default_loc_file)
                dirty_files.add(source)

        file_entries: dict[Path, dict[str, str]] = {path: {} for path in dirty_files}
        for key, value in self._loc_entries.items():
            source = self._loc_sources.get(key, self.default_loc_file)
            if dirty_only and source not in dirty_files:
                continue
            if source not in file_entries:
                file_entries[source] = {}
            file_entries[source][key] = value
        return file_entries

    def _group_events_by_file(
        self, dirty_only: bool = False
    ) -> tuple[dict[Path, list[Event]], dict[Path, Optional[str]]]:
        file_events: dict[Path, list[Event]] = {}
        file_ns: dict[Path, Optional[str]] = {}
        dirty_files: set[Path] = set(self._dirty_event_files) if dirty_only else set()
        if dirty_only:
            for eid in self._dirty_events:
                event = self._events.get(eid)
                if event is None:
                    continue
                p = (
                    event.path
                    or self.mod_root
                    / "events"
                    / f"{self._event_namespaces.get(eid) or 'mod'}_events.txt"
                )
                dirty_files.add(p)
        for path in dirty_files:
            file_events[path] = []
            file_ns[path] = self._event_file_namespaces.get(path)
        for eid, event in self._events.items():
            p = (
                event.path
                or self.mod_root
                / "events"
                / f"{self._event_namespaces.get(eid) or 'mod'}_events.txt"
            )
            if dirty_only and p not in dirty_files:
                continue
            if p not in file_events:
                file_events[p] = []
                file_ns[p] = self._event_namespaces.get(eid)
            elif self._event_namespaces.get(eid) is not None:
                file_ns[p] = self._event_namespaces.get(eid)
            file_events[p].append(event)
        return file_events, file_ns

    def _group_on_actions_by_file(self, dirty_only: bool = False) -> dict[Path, list[OnAction]]:
        file_actions: dict[Path, list[OnAction]] = {}
        dirty_files: set[Path] = set(self._dirty_on_action_files) if dirty_only else set()
        if dirty_only:
            for path in dirty_files:
                file_actions[path] = []
        for occurrences in self._on_action_occurrences.values():
            for action in occurrences:
                p = (
                    action.path
                    or self.mod_root / "common" / "on_actions" / "mod_on_actions.txt"
                )
                if dirty_only and p not in dirty_files:
                    continue
                if p not in file_actions:
                    file_actions[p] = []
                file_actions[p].append(action)
        return file_actions

    def _group_decisions_by_file(
        self, dirty_only: bool = False
    ) -> dict[Path, list[DecisionCategory]]:
        file_categories: dict[Path, list[DecisionCategory]] = {}
        dirty_files: set[Path] = set(self._dirty_decision_files) if dirty_only else set()
        if dirty_only:
            for category_id in self._dirty_decision_categories:
                category = self._decision_categories.get(category_id)
                if category is None:
                    continue
                dirty_files.add(
                    category.path or self.mod_root / "common" / "decisions" / "mod_decisions.txt"
                )
        for path in dirty_files:
            file_categories[path] = []
        for category in self._decision_categories.values():
            p = category.path or self.mod_root / "common" / "decisions" / "mod_decisions.txt"
            if dirty_only and p not in dirty_files:
                continue
            if p not in file_categories:
                file_categories[p] = []
            file_categories[p].append(category)
        return file_categories

    def _group_decision_categories_by_file(
        self, dirty_only: bool = False
    ) -> dict[Path, list[DecisionCategory]]:
        dirty_files = (
            set(self._dirty_decision_category_files) if dirty_only else set()
        )
        grouped: dict[Path, list[DecisionCategory]] = {
            path: [] for path in dirty_files
        }
        for category in self._decision_categories.values():
            path = category.definition_path
            if path is None:
                decision_path = (
                    category.path
                    or self.mod_root
                    / "common"
                    / "decisions"
                    / "mod_decisions.txt"
                )
                path = _decision_category_definition_path(
                    self.mod_root, decision_path
                )
            if dirty_only and path not in dirty_files:
                continue
            grouped.setdefault(path, []).append(category)
        return grouped

    def _group_ideas_by_file(self, dirty_only: bool = False) -> dict[Path, list[Idea]]:
        file_ideas: dict[Path, list[Idea]] = {}
        dirty_files: set[Path] = set(self._dirty_idea_files) if dirty_only else set()
        if dirty_only:
            for iid in self._dirty_ideas:
                idea = self._ideas.get(iid)
                if idea is None:
                    continue
                dirty_files.add(idea.path or self.mod_root / "common" / "ideas" / "mod_ideas.txt")
        for path in dirty_files:
            file_ideas[path] = []
        for idea in self._ideas.values():
            p = idea.path or self.mod_root / "common" / "ideas" / "mod_ideas.txt"
            if dirty_only and p not in dirty_files:
                continue
            if p not in file_ideas:
                file_ideas[p] = []
            file_ideas[p].append(idea)
        return file_ideas

    def _group_focus_trees_by_file(self, dirty_only: bool = False) -> dict[Path, list[FocusTree]]:
        dirty_files = set(self._dirty_focus_files) if dirty_only else set()
        if dirty_only:
            for tree_id in self._dirty_focus_trees:
                tree = self._focus_trees.get(tree_id)
                if tree is not None and tree.path is not None:
                    dirty_files.add(tree.path)
        grouped: dict[Path, list[FocusTree]] = {path: [] for path in dirty_files}
        for tree in self._focus_trees.values():
            path = (
                tree.path
                or self.mod_root / "common" / "national_focus" / f"{tree.country_tag}_focus.txt"
            )
            path = resolve_mod_output_path(self.mod_root, path)
            if dirty_only and path not in dirty_files:
                continue
            grouped.setdefault(path, []).append(tree)
        return grouped
