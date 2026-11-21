"""Shared state, content-layer lookup, raw readers and transaction snapshots."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Optional, Sequence, TypeVar

from ..bookmarks import (
    Bookmark,
)
from ..characters import (
    Character,
)
from ..dynamic_modifiers import (
    DynamicModifier,
)
from ..events import load_events_file
from ..focus import load_focus_trees
from ..ideas import read_ideas_file
from ..ideologies import Ideology
from ..layers import ContentLayers, relative_content_path, replace_paths
from ..on_actions import load_on_actions_file
from ..oob import (
    OrderOfBattle,
)
from ..parser import (
    PdxNode,
)
from ..paths import (
    resolve_mod_output_path,
)
from ..script_vocabulary import (
    GameScriptVocabulary,
)
from ..tags import (
    _parse_tag_file_mapping,
    load_vanilla_tags,
)
from ..types import (
    Country,
    Decision,
    DecisionCategory,
    Event,
    FocusTree,
    Idea,
    LoadDiagnostic,
    OnAction,
    State,
)

_InheritedModel = TypeVar("_InheritedModel", FocusTree, Event, Idea)


class CoreLayer:
    """Shared state, content-layer lookup, raw readers and transaction snapshots."""

    def __init__(
        self,
        mod_root: str | Path,
        hoi4_install: str | Path | None = None,
        *,
        strict_loading: bool = False,
        base_mod_paths: Sequence[str | Path] = (),
    ):
        self.mod_root = Path(mod_root).resolve()
        self.hoi4_install = Path(hoi4_install).resolve() if hoi4_install else None
        if isinstance(base_mod_paths, (str, bytes)):
            raise ValueError("base_mod_paths must be an ordered sequence of paths")
        self.base_mod_paths = tuple(Path(path).resolve() for path in base_mod_paths)
        self._content_layers: ContentLayers | None = None
        self._refresh_content_layers()
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
        self._deleted_state_ids: set[int] = set()
        self._deleted_state_files: dict[Path, str | None] = {}
        self._map_file_updates: dict[str, str] = {}
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
        # English stays in the tables above; every other language is indexed
        # lazily, as a whole (inherited layers plus the mod), on first use.
        self._foreign_loc_entries: dict[str, dict[str, str]] = {}
        self._foreign_loc_sources: dict[str, dict[str, Path]] = {}
        self._foreign_dirty_loc_keys: dict[str, set[str]] = {}
        self._loaded_loc_languages: set[str] = set()
        self._original_files: dict[Path, str] = {}
        self._source_baseline: dict[Path, bytes] = {}
        self._pending_asset_writes: dict[Path, bytes] = {}
        self._pending_asset_baselines: dict[Path, bytes | None] = {}
        self._country_name_pool_updates: dict[str, str] = {}
        self._script_file_updates: dict[Path, str] = {}
        self._dirty: set[str] = set()
        self._vanilla_tags: set[str] = (
            load_vanilla_tags(self._base_content_root) if self._base_content_root else set()
        )
        self._scan_cache: dict[str, set[str]] = {}
        self._script_vocabulary_cache: GameScriptVocabulary | None = None
        self._equipment_unlock_cache: dict[str, tuple[str, ...]] | None = None
        self._sprite_texture_cache: dict[str, str] | None = None
        self._sprite_text_parse_cache: dict[str, dict[str, str]] = {}
        self._runtime_activation_parse_cache: dict[str, PdxNode | None] = {}
        self._script_trigger_index_cache: dict[str, dict[str, PdxNode]] = {}
        self._state_index_cache: dict[bool, list[dict]] = {}
        self._inherited_state_source_paths: dict[int, Path] | None = None
        self._writable_state_directory_stamp: int | None = None
        self._vanilla_state_loc_entries: dict[str, str] | None = None
        self._country_context_cache: dict[str, dict] = {}
        self._country_tag_mappings: dict[Path, dict[str, str]] = {
            base: _parse_tag_file_mapping(base / "common" / "country_tags")
            for base in (self.mod_root, self._base_content_root)
            if base is not None
        }
        self._country_color_sources: dict[Path, str | None] = {}

    def _refresh_content_layers(self) -> None:
        previous = self._content_layers
        self._content_layers = (
            ContentLayers(self.hoi4_install, self.base_mod_paths, self.mod_root)
            if self.base_mod_paths else None
        )
        self._base_content_root = (
            self._content_layers.root if self._content_layers else self.hoi4_install
        )
        if previous is not None:
            previous.close()

    def content_source(self, relative_path: str | Path) -> Path | None:
        """Original winning file in game/base/output layers; never a snapshot path."""
        relative = relative_content_path(relative_path)
        if self._content_layers is not None:
            return self._content_layers.source(relative)
        hidden_paths = replace_paths(self.mod_root) if self.hoi4_install is not None else ()
        writable = self.mod_root / relative
        if writable.is_file():
            return writable
        if self.hoi4_install is not None and not any(
            relative.is_relative_to(hidden) for hidden in hidden_paths
        ):
            vanilla = self.hoi4_install / relative
            if vanilla.is_file():
                return vanilla
        return None

    def content_files(self, directory: str | Path) -> dict[str, Path]:
        """Map effective relative filenames to original winning source paths."""
        relative = relative_content_path(directory)
        if self._content_layers is not None:
            files = {str(path): source for path, source in self._content_layers.sources.items()
                     if path.is_relative_to(relative)}
        else:
            files = {}
            if self.hoi4_install is not None:
                hidden_paths = replace_paths(self.mod_root)
                if not any(relative.is_relative_to(hidden) for hidden in hidden_paths):
                    for path in (self.hoi4_install / relative).rglob("*"):
                        if path.is_file():
                            path_relative = path.relative_to(self.hoi4_install)
                            if not any(path_relative.is_relative_to(hidden) for hidden in hidden_paths):
                                files[str(path_relative)] = path
        for path in (self.mod_root / relative).rglob("*"):
            if path.is_file():
                files[str(path.relative_to(self.mod_root))] = path
        return dict(sorted(files.items()))

    def load_inherited_content(self, relative_path: str | Path) -> None:
        """Load one effective inherited script file for facade editing.

        Focus, event, idea and on-action files are opt-in, like vanilla country
        reads. All siblings in the file are retained. Reading alone writes
        nothing; edits serialize a complete override into this mod on save.
        Duplicate focus IDs within an inherited tree reject the entire import
        before any trees are installed, regardless of ``strict_loading``.
        """
        relative = relative_content_path(relative_path)
        source = self.content_source(relative)
        if source is None:
            raise FileNotFoundError(relative)
        if source.is_relative_to(self.mod_root):
            return
        target = resolve_mod_output_path(self.mod_root, relative)
        if target in self._original_files:
            return
        original = source.read_bytes().decode("utf-8")

        def install(models: Sequence[_InheritedModel], destination: dict[str, _InheritedModel]) -> None:
            seen: set[str] = set()
            for model in models:
                if model.id in destination or model.id in seen:
                    raise ValueError(f"Inherited ID {model.id!r} conflicts with another definition")
                seen.add(model.id)
            for model in models:
                model.path = target
                destination[model.id] = model

        directory = relative.parent.as_posix()
        if directory == "common/national_focus":
            trees = load_focus_trees(source)
            for tree in trees:
                seen_focus_ids: set[str] = set()
                for focus in tree.focuses:
                    if focus.id in seen_focus_ids:
                        raise ValueError(
                            f"Duplicate focus ID {focus.id!r} in inherited tree {tree.id!r} "
                            f"from {relative}"
                        )
                    seen_focus_ids.add(focus.id)
            install(trees, self._focus_trees)
        elif directory == "events":
            namespace, events = load_events_file(source)
            install(events, self._events)
            for event in events:
                self._event_namespaces[event.id] = namespace
            self._event_file_namespaces[target] = namespace
        elif directory in ("common/ideas", "common/national_ideas"):
            ideas, container = read_ideas_file(source)
            install(ideas, self._ideas)
            self._idea_file_containers[target] = container
        elif directory == "common/on_actions":
            actions = load_on_actions_file(source)
            for action in actions:
                action.path = target
                self._on_action_occurrences.setdefault(action.id, []).append(action)
                self._on_actions.setdefault(action.id, action)
        else:
            raise ValueError("Inherited loading supports focus, event, idea and on-action files")
        self._original_files[target] = original

    def _is_shadowed_by_mod(self, base: Path, path: Path) -> bool:
        try:
            relative = path.relative_to(base)
        except ValueError:
            return False
        return self._mod_hides_inherited(relative)

    def _mod_hides_inherited(self, relative: Path) -> bool:
        """Whether the mod hides an inherited file (same name or replace_path).

        Layered snapshots already omit hidden files; this covers a direct
        ``hoi4_install`` base.
        """

        if self._content_layers is not None:
            return False
        if (self.mod_root / relative).is_file():
            return True
        hidden = self._scan_cache.get("mod_replace_paths")
        if hidden is None:
            try:
                hidden = {path.as_posix() for path in replace_paths(self.mod_root)}
            except (OSError, ValueError):
                hidden = set()
            self._scan_cache["mod_replace_paths"] = hidden
        return any(relative.is_relative_to(Path(path)) for path in hidden)

    @staticmethod
    def _read_current_text(path: Path) -> str:
        return path.read_text(encoding="utf-8-sig", errors="ignore") if path.exists() else ""

    @staticmethod
    def _read_bookmark_date_text(path: Path) -> str:
        # read_text() translates CRLF before the Lua patcher can preserve it.
        return path.read_bytes().decode("utf-8-sig", errors="ignore") if path.exists() else ""

    def _read_rendered_text(self, path: Path) -> str:
        if self._bookmark_date_defines is not None and path == self._bookmark_date_defines[0]:
            return self._read_bookmark_date_text(path)
        return self._read_current_text(path)

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
            "_inherited_state_source_paths",
            "_writable_state_directory_stamp",
            "_dirty_states",
            "_deleted_state_ids",
            "_deleted_state_files",
            "_map_file_updates",
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
            "_foreign_loc_entries",
            "_foreign_loc_sources",
            "_foreign_dirty_loc_keys",
            "_loaded_loc_languages",
            "_original_files",
            "_pending_asset_writes",
            "_pending_asset_baselines",
            "_country_name_pool_updates",
            "_script_file_updates",
            "_dirty",
            "_sprite_texture_cache",
            "_country_context_cache",
            "_country_color_sources",
            "_country_tag_mappings",
        ]
        return copy.deepcopy({key: getattr(self, key) for key in keys})

    def _restore(self, snapshot: dict[str, object]) -> None:
        for key, value in snapshot.items():
            setattr(self, key, value)
        # Derived catalogs may reflect imports or localization rolled back above.
        self._state_index_cache.clear()
        self._scan_cache.clear()
        self._equipment_unlock_cache = None

    def _display_path(self, path: Path) -> str:
        if self._content_layers is not None and path.is_relative_to(self._content_layers.root):
            source = self.content_source(path.relative_to(self._content_layers.root))
            if source is not None:
                return str(source)
        return (
            str(path.relative_to(self.mod_root))
            if path.is_relative_to(self.mod_root)
            else str(path)
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

    @staticmethod
    def _line_column(text: str, offset: int) -> tuple[int, int]:
        offset = max(0, min(offset, len(text)))
        line = text.count("\n", 0, offset) + 1
        previous_newline = text.rfind("\n", 0, offset)
        return line, offset - previous_newline

    def _data_roots(self) -> list[Path]:
        roots = [self.mod_root]
        if self._base_content_root is not None:
            roots.append(self._base_content_root)
        return roots
