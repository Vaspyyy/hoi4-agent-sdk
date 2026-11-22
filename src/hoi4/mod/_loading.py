"""Loaders of every content type, run section by section on first use."""

from __future__ import annotations

import re
from pathlib import Path
from typing import cast

from .._disk_cache import DiskAnalysis, string_map
from ..bookmarks import (
    load_bookmarks_file,
)
from ..characters import (
    load_characters_file,
)
from ..countries import (
    country_color_tags,
    country_file_paths,
    read_country,
)
from ..decisions import (
    load_decision_categories_file,
    load_decisions_file,
    merge_decision_category,
)
from ..dynamic_modifiers import (
    load_dynamic_modifiers_text,
)
from ..errors import LoadError, SaveRefusedError
from ..events import load_events_file
from ..focus import load_focus_trees
from ..ideas import read_ideas_text
from ..ideologies import load_ideologies_text
from ..layers import FolderView
from ..on_actions import load_on_actions_file
from ..oob import (
    OOBReference,
    load_oob_file,
)
from ..states import (
    build_state_index,
    state_header_of,
)
from ..syntax import parse, require_well_formed
from ..tags import (
    country_tag_entries,
    tag_file_mapping,
)
from ..textio import read_text
from ..types import (
    Country,
    Decision,
    DecisionCategory,
    LoadDiagnostic,
)
from ._countries import CountriesLayer
from ._shared import (
    _decision_category_definition_path,
)

#: ``tag -> {key: text}`` of one localisation file (see ``country_loc_entries``).
def _country_loc_from_disk(data: object) -> dict[str, dict[str, str]]:
    if not isinstance(data, dict):
        raise TypeError("stored country texts must be a map")
    return {str(tag): string_map(entries) for tag, entries in data.items()}


_COUNTRY_LOC_ON_DISK: DiskAnalysis[dict[str, dict[str, str]]] = DiskAnalysis(
    "country_loc_entries", dict, _country_loc_from_disk
)


class _DuplicateIdentifierError(LoadError):
    pass


class LoadingLayer(CountriesLayer):
    """Loaders of every content type, one per section (see ``SECTIONS`` in ``_core``).

    Each loader runs on the first read of one of its section's attributes and
    fills them from disk.
    """

    @property
    def load_diagnostics(self) -> tuple[LoadDiagnostic, ...]:
        """Files skipped while loading; empty means all discovered files loaded cleanly.

        Reading this loads every section that is not loaded yet.
        """
        return tuple(self._load_diagnostics)

    def _record_load_error(self, section: str, path: Path, error: Exception) -> None:
        diagnostic = LoadDiagnostic(
            section=section,
            path=path,
            error_type=type(error).__name__,
            message=str(error),
        )
        self._add_load_diagnostic(diagnostic)
        if self.strict_loading:
            raise LoadError(
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
        self._add_load_diagnostic(diagnostic)
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
            for diagnostic in self._loaded_diagnostics()
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
        raise SaveRefusedError(
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
            text: str | None = None
            try:
                text = self._read_source(path)
                require_well_formed(text)
            except Exception as error:
                self._record_load_error("country_tags", path, error)
            for tag, target in country_tag_entries(read_text(path) if text is None else text):
                if tag in sources:
                    self._record_duplicate_identifier(
                        "country_tag", tag, sources[tag], path
                    )
                    continue
                sources[tag] = path
                mapping[tag] = target
        return mapping

    def _load_countries(self) -> None:
        from ..countries import _build_loc_cache, country_loc_entries

        mod_view = FolderView(self.mod_root)
        for view in (mod_view, self._base_content):
            if view is not None:
                self._country_tag_mappings[view] = tag_file_mapping(view)
        for view in (self._base_content, mod_view):
            if view is None:
                continue
            colors_path = view.file("common/countries/colors.txt")
            if colors_path is None:
                continue
            try:
                # Noted for the mod's own table, which save() rewrites.
                source = self._read_source(colors_path)
                if view is mod_view:
                    require_well_formed(source)
                country_color_tags(source)
            except Exception as error:
                self._country_color_sources[colors_path] = None
                self._record_load_error("country_colors", colors_path, error)
            else:
                self._country_color_sources[colors_path] = source

        def read_entries(path: Path) -> dict[str, dict[str, str]]:
            # The game's and base mods' answers are kept on disk as well.
            return self._cached_result(
                "country_loc_entries", path, country_loc_entries, _COUNTRY_LOC_ON_DISK
            )

        loc_cache: dict[str, dict[str, str]] = {}
        for view in (self._base_content, mod_view):
            if view is None:
                continue
            for tag, entries in _build_loc_cache(view, file_entries=read_entries).items():
                loc_cache.setdefault(tag, {}).update(entries)
        mod_mapping = self._load_mod_country_tag_mapping()
        self._country_tag_mappings[mod_view] = mod_mapping
        generated_tags = (
            self.mod_root
            / "common"
            / "country_tags"
            / "00_generated_tags.txt"
        )
        if generated_tags.is_file():
            self._created_country_tags.update(
                tag for tag, _ in country_tag_entries(self._read_source(generated_tags))
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
            self._country_tag_mappings.get(self._base_content, {})
            if self._base_content is not None
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
                    self._base_content,
                    _loc_cache=loc_cache,
                    _tag_mappings=self._country_tag_mappings,
                    _color_sources=self._country_color_sources,
                )
            except Exception as error:
                self._record_load_error("country", self.mod_root, error)
                self._countries[tag] = Country(tag=tag)
            for path in country_file_paths(self.mod_root, self._countries[tag]):
                if path.exists() and path.resolve(strict=False).is_relative_to(
                    self.mod_root.resolve(strict=False)
                ):
                    # The country is still read the forgiving way, so it can be
                    # inspected, but saving refuses to rewrite the broken file.
                    text: str | None = None
                    try:
                        text = read_text(path)
                        require_well_formed(text)
                    except Exception as error:
                        self._record_load_error("country", path, error)
                else:
                    text = None
                if path.exists() and path not in self._original_files:
                    self._original_files[path] = (
                        self._read_source(path) if text is None else self._keep_source(path, text)
                    )

    def _load_states(self) -> None:
        states_dir = self.mod_root / "history" / "states"
        self._stage(
            "_writable_state_directory_stamp",
            states_dir.stat().st_mtime_ns if states_dir.is_dir() else None,
        )
        if not states_dir.exists():
            return
        # Each file is read and parsed once, for its header and to find out
        # whether it is well formed.
        broken: dict[Path, str] = {}

        def header(path: Path) -> tuple[int, str, str | None] | None:
            text = self._read_source(path)
            document = parse(text)
            if document.problems:
                broken[path] = text
            return state_header_of(document.root)

        for entry in build_state_index(states_dir, header=header):
            state_id = entry["id"]
            path = Path(entry["path"])
            if path in broken:
                try:
                    require_well_formed(broken[path])
                except Exception as error:
                    self._record_load_error("state", path, error)
                    continue
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
                text, _ = self._read_well_formed(path)
                for character in load_characters_file(path, text=text):
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
                self._original_files[path] = self._keep_source(path, text)
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
                text, _ = self._read_well_formed(path)
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
                    text=text,
                )
                self._oobs[oob.name] = oob
                self._original_files[path] = self._keep_source(path, text)
            except Exception as error:
                self._record_load_error("oob", path, error)

    def _load_focus_trees(self) -> None:
        focus_dir = self.mod_root / "common" / "national_focus"
        if not focus_dir.exists():
            return
        for f in sorted(focus_dir.glob("*.txt")):
            try:
                text, document = self._read_well_formed(f)
                self._keep_source(f, text)
                # ``focus_tree`` blocks, plus one ``kind="file"`` container
                # for top-level ``shared_focus``/``joint_focus``/``focus``.
                trees = load_focus_trees(f, text=text, document=document)
                # Preserve non-focus companion files too. A subsequently
                # created tree may legitimately target the same file and must
                # append to, not replace, its unmodeled declarations.
                self._original_files[f] = text
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

    def _load_events(self) -> None:
        events_dir = self.mod_root / "events"
        if not events_dir.exists():
            return
        for f in sorted(events_dir.glob("*.txt")):
            try:
                text, document = self._read_well_formed(f)
                namespace, events = load_events_file(f, document=document)
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
                self._original_files[f] = self._keep_source(f, text)
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
                text, document = self._read_well_formed(f)
                for action in load_on_actions_file(f, document=document):
                    # Unlike most Paradox objects, on-actions are hooks.  The
                    # game composes repeated definitions across files (and
                    # even repeated definitions in one file), so retain every
                    # occurrence instead of diagnosing or dropping duplicates.
                    self._on_action_occurrences.setdefault(action.id, []).append(action)
                    self._on_actions.setdefault(action.id, action)
                self._original_files[f] = self._keep_source(f, text)
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
                    text, _ = self._read_well_formed(f)
                    for category in load_decision_categories_file(f, text=text):
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
                    self._original_files[f] = self._keep_source(f, text)
                except _DuplicateIdentifierError:
                    raise
                except Exception as error:
                    self._record_load_error("decision_category", f, error)
                    continue
        # Categories seen in decision-content files so far. The game merges a
        # category's blocks from every file (and from repeated blocks in one
        # file) into one category, so a later file adds to it.
        content_categories: dict[str, DecisionCategory] = {}
        for f in sorted(decisions_dir.glob("*.txt")):
            try:
                text, _ = self._read_well_formed(f)
                # A category reopened later in the same file is one category:
                # load_decisions_file merges its blocks.
                categories = load_decisions_file(f, text=text)
                for category in categories:
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
                    loaded = content_categories.get(category.id)
                    if loaded is not None:
                        # Presentation from a categories file wins; legacy
                        # inline copies only count when there is none.
                        merge_decision_category(
                            loaded,
                            category,
                            presentation=category.id not in category_sources,
                        )
                        continue
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
                            self.mod_root, category.path or f, self._shadows_inherited
                        )
                    self._decision_categories[category.id] = category
                    content_categories[category.id] = category
                self._original_files[f] = self._keep_source(f, text)
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
                    text, document = self._read_well_formed(f)
                    self._keep_source(f, text)
                    ideas, container = read_ideas_text(text, f, document=document)
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
                    self._original_files[f] = text
                except _DuplicateIdentifierError:
                    raise
                except Exception as error:
                    self._record_load_error("idea", f, error)
                    continue

        self._match_idea_files_to_countries()

    def _load_ideologies(self) -> None:
        if self._base_content is not None:
            for path in self._base_content.glob("common/ideologies", "*.txt").values():
                try:
                    # Inherited files are read the way the game reads them.
                    text = read_text(path)
                    for ideology in load_ideologies_text(text, path, is_vanilla=True):
                        self._vanilla_ideologies[ideology.id] = ideology
                except Exception:
                    # Vanilla is reference data; unreadable or version-specific files
                    # must not make an otherwise valid mod impossible to open.
                    continue

        directory = self.mod_root / "common" / "ideologies"
        if not directory.is_dir():
            return
        for path in sorted(directory.glob("*.txt")):
            try:
                text, document = self._read_well_formed(path)
                self._keep_source(path, text)
                for ideology in load_ideologies_text(text, path, document=document):
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
                self._original_files[path] = text
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
                text, document = self._read_well_formed(path)
                self._original_files[path] = self._keep_source(path, text)
                for modifier in load_dynamic_modifiers_text(text, path, document=document):
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
                text, _ = self._read_well_formed(path)
                for bookmark in load_bookmarks_file(path, text=text):
                    first_path = sources.get(bookmark.name)
                    if first_path is not None:
                        self._record_duplicate_identifier(
                            "bookmark", bookmark.name, first_path, bookmark.path or path
                        )
                        continue
                    sources[bookmark.name] = bookmark.path or path
                    self._bookmarks.append(bookmark)
                self._original_files[path] = self._keep_source(path, text)
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
                if stem in (tag.lower(), name_lower, name_lower.replace(" ", "_")):
                    self._cached_idea_file[tag] = f
                    break
