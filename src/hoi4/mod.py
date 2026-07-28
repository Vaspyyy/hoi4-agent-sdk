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
from typing import Any, Optional, Sequence, TypedDict, cast

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
from .config import find_config
from .content_validation import (
    validate_bookmark,
    validate_dynamic_modifier,
    validate_ideology,
)
from .countries import (
    _country_localisation_suffixes,
    country_color_tags,
    country_file_paths,
    read_country,
    seed_country_colors_file,
    serialize_country_colors_file,
    serialize_country_files,
)
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
from .ideas import read_ideas_file, scan_idea_ids_file, serialize_ideas_file
from .ideologies import Ideology, SubIdeology, load_ideologies_file, serialize_ideologies_file
from .localisation import (
    normalize_localization_key,
    parse_localization_dir,
    serialize_localization_file,
)
from .on_actions import load_on_actions_file, serialize_on_actions_file
from .paths import (
    require_country_tag,
    require_event_namespace,
    require_script_id,
    resolve_mod_output_path,
    safe_file_stem,
)
from .parser import iter_assignment_blocks
from .patching import top_level_assignments
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
    scope_block,
    validate_script_syntax,
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
        self._dirty: set[str] = set()
        self._vanilla_tags: set[str] = (
            load_vanilla_tags(self.hoi4_install) if self.hoi4_install else set()
        )
        self._scan_cache: dict[str, set[str]] = {}
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
    def from_config(cls, start: str | Path | None = None) -> Mod:
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
        return cls(cfg.mod_path, hoi4_install=cfg.hoi4_install)

    def _load(self) -> None:
        self._load_countries()
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

        loc_cache = _build_loc_cache(self.mod_root)
        mod_mapping = self._load_mod_country_tag_mapping()
        self._country_tag_mappings[self.mod_root] = mod_mapping
        for tag in sorted(mod_mapping):
            try:
                self._countries[tag] = read_country(
                    self.mod_root,
                    tag,
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
        self._dirty.add("countries")
        self._dirty_countries.add(tag)
        self._sync_country_loc(country)

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
        self._dirty.add("countries")
        self._dirty_countries.add(tag)
        return True

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
        icon: str = "GFX_idea_generic",
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
            icon=icon,
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
            icon for icon in self._known_focus_icons() if icon.startswith("GFX_idea")
        )
        return self._rank_icons(query, icons, count)

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
        progress: ProgressCallback | None = None,
        cancelled: CancelCallback | None = None,
    ) -> list[ValidationError]:
        errors: list[ValidationError] = []
        phase_total = 10
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
        for country in self._countries.values():
            errors.extend(validate_country(country, known_parties=known_parties))
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
                and idea.icon not in known_focus_icons
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
                            f"Idea '{idea.id}' icon '{idea.icon}' was not found."
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
        if strict_localization:
            errors.extend(self._validate_localization_references())

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
        if not changed:
            message = "No files written after processing dirty sections"
            if require_changes:
                raise RuntimeError(message)
            return SaveResult(dirty_sections=dirty_sections, no_changes=True, message=message)
        self._commit_rendered_files(changed)
        written_files = sorted(changed)
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
                rendered.update(country_files)
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
        if conflicts:
            displayed = ", ".join(
                str(path.relative_to(self.mod_root)) for path in sorted(conflicts)
            )
            raise ExternalModificationError(
                "Refusing to overwrite files changed after Mod loaded them: "
                f"{displayed}. Call reload() and reapply the intended edit."
            )

    def _commit_rendered_files(self, rendered: dict[Path, str | None]) -> None:
        backups = {path: path.read_bytes() if path.exists() else None for path in rendered}
        prepared: dict[Path, Path] = {}
        try:
            for path, content in rendered.items():
                if content is None:
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
                    handle.write(content.encode("utf-8-sig" if path.suffix == ".yml" else "utf-8"))
                    prepared[path] = Path(handle.name)
            for path, content in rendered.items():
                if content is None:
                    path.unlink(missing_ok=True)
                else:
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
        self._original_files.clear()
        self._dirty.clear()
        self._scan_cache.clear()
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
            "_dirty",
            "_country_context_cache",
            "_country_tag_mappings",
        ]
        return copy.deepcopy({key: getattr(self, key) for key in keys})

    def _restore(self, snapshot: dict[str, object]) -> None:
        for key, value in snapshot.items():
            setattr(self, key, value)

    def _semantic_preview_lines(self) -> list[str]:
        lines: list[str] = []
        if "countries" in self._dirty:
            for tag in sorted(self._dirty_countries):
                country = self._countries.get(tag)
                if country:
                    lines.append(f"country {tag}: create/update {country.name or tag}")
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
            ideas = set(_IDEA_EFFECT_RE.findall(script))
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
    ) -> ValidationError:
        return ValidationError(
            message=message,
            severity="warning",
            code=code,
            file_path=file_path,
            focus_id=obj_id if kind == "focus" else None,
            event_id=event_id or (obj_id if kind == "event" else None),
            idea_id=idea_id,
        )

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
