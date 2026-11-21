"""Initial load of every content type from disk."""

from __future__ import annotations

import re
from pathlib import Path
from typing import cast

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
)
from ..dynamic_modifiers import (
    load_dynamic_modifiers_file,
)
from ..events import load_events_file
from ..focus import load_focus_trees
from ..ideas import read_ideas_file
from ..ideologies import load_ideologies_file
from ..on_actions import load_on_actions_file
from ..oob import (
    OOBReference,
    load_oob_file,
)
from ..states import (
    build_state_index,
)
from ..tags import (
    _parse_country_tag_line,
)
from ..types import (
    Country,
    Decision,
    LoadDiagnostic,
)
from ._countries import CountriesLayer
from ._localisation import LocalisationLayer
from ._shared import (
    _decision_category_definition_path,
)


class _DuplicateIdentifierError(RuntimeError):
    pass


class LoadingLayer(CountriesLayer, LocalisationLayer):
    """Initial load of every content type from disk."""

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
                entry = _parse_country_tag_line(line)
                if entry is None:
                    continue
                tag, target = entry
                if tag in sources:
                    self._record_duplicate_identifier(
                        "country_tag", tag, sources[tag], path
                    )
                    continue
                sources[tag] = path
                mapping[tag] = target
        return mapping

    def _load_countries(self) -> None:
        from ..countries import _build_loc_cache

        self._country_color_sources.clear()
        for base in (self._base_content_root, self.mod_root):
            if base is None:
                continue
            colors_path = base / "common" / "countries" / "colors.txt"
            if not colors_path.is_file():
                continue
            try:
                source = colors_path.read_text(encoding="utf-8", errors="ignore")
                country_color_tags(source)
            except Exception as error:
                self._country_color_sources[colors_path] = None
                self._record_load_error("country_colors", colors_path, error)
            else:
                self._country_color_sources[colors_path] = source

        loc_cache: dict[str, dict[str, str]] = {}
        for base in (self._base_content_root, self.mod_root):
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
                entry[0]
                for line in generated_tags.read_text(
                    encoding="utf-8",
                    errors="ignore",
                ).splitlines()
                if (entry := _parse_country_tag_line(line)) is not None
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
            self._country_tag_mappings.get(self._base_content_root, {})
            if self._base_content_root is not None
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
                    self._base_content_root,
                    _loc_cache=loc_cache,
                    _tag_mappings=self._country_tag_mappings,
                    _color_sources=self._country_color_sources,
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
        self._writable_state_directory_stamp = states_dir.stat().st_mtime_ns if states_dir.is_dir() else None
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
        if self._base_content_root is not None:
            directory = self._base_content_root / "common" / "ideologies"
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
