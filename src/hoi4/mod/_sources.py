"""Per-file grouping of loaded models and the effective script texts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Iterator, Optional, cast

from ..characters import (
    Character,
)
from ..patching import (
    block_bodies_equivalent,
    set_block,
    top_level_assignments,
)
from ..paths import (
    resolve_mod_output_path,
)
from ..script_vocabulary import (
    ScriptTokenKind,
)
from ..types import (
    Decision,
    DecisionCategory,
    Event,
    FocusTree,
    Idea,
    OnAction,
)
from ._localisation import LocalisationLayer
from ..textio import read_text
from ._shared import (
    _decision_category_definition_path,
)


def _definition_only(category: DecisionCategory) -> bool:
    """A category known only from ``common/decisions/categories``.

    It has no block in any decisions file, and editing its metadata must not
    create one.
    """

    return category.path is None and not category.decisions


class SourcesLayer(LocalisationLayer):
    """Per-file grouping of loaded models and the effective script texts."""

    def _render_country_name_pools(self) -> str:
        relative = Path("common/names/00_generated_names.txt")
        # The effective fallback already applies layer precedence and replace_path.
        # Test existence, not truthiness: an empty writable override is authoritative.
        text = ""
        for root in self._data_roots():
            source = root / relative
            if source.is_file():
                text = self._read_current_text(source)
                break
        # Compare original bodies in one scan. Rebuilding a large roster
        # should not rescan the entire names file once per unchanged tag.
        original_bodies: dict[str, str] = {}
        for span in top_level_assignments(text):
            if span.is_block and span.body_start is not None and span.body_end is not None:
                original_bodies.setdefault(span.key, text[span.body_start:span.body_end])
        for tag, body in sorted(self._country_name_pool_updates.items()):
            if tag in original_bodies and block_bodies_equivalent(original_bodies[tag], body.strip()):
                continue
            text = set_block(text, tag, body)
        return text

    def _effective_script_texts(self, relative: str) -> Iterator[tuple[Path, str]]:
        """Read effective support scripts with pending imports and name-pool edits."""
        files: dict[Path, Path] = {}
        for root in reversed(self._data_roots()):
            for source in sorted((root / relative).rglob("*.txt")):
                files[self.mod_root / source.relative_to(root)] = source
        for target in self._script_file_updates:
            if target.is_relative_to(self.mod_root / relative):
                files.setdefault(target, target)
        names_path = self.mod_root / "common/names/00_generated_names.txt"
        pending_names = "country_names" in self._dirty and names_path.is_relative_to(
            self.mod_root / relative
        )
        if pending_names:
            files.setdefault(names_path, names_path)
        for target, source in files.items():
            try:
                text = (
                    self._render_country_name_pools()
                    if pending_names and target == names_path
                    else self._script_file_updates.get(target)
                )
                if text is None:
                    text = read_text(source)
                yield target, text
            except (OSError, ValueError):
                continue

    def _effective_custom_script_tokens(self) -> dict[ScriptTokenKind, set[str]]:
        result: dict[ScriptTokenKind, set[str]] = {"effect": set(), "trigger": set()}
        for kind, relative in (("effect", "common/scripted_effects"),
                               ("trigger", "common/scripted_triggers")):
            typed_kind = cast(ScriptTokenKind, kind)
            for _, text in self._effective_script_texts(relative):
                try:
                    result[typed_kind].update(span.key for span in top_level_assignments(text)
                                              if span.is_block)
                except ValueError:
                    continue
        return result

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
        tables: list[tuple[Path, dict[str, str], dict[str, Path], set[str]]] = [
            (self.default_loc_file, self._loc_entries, self._loc_sources, self._dirty_loc_keys)
        ]
        for language in sorted(self._loaded_loc_languages):
            tables.append(
                (
                    self._default_loc_file_for(language),
                    self._foreign_loc_entries.get(language, {}),
                    self._foreign_loc_sources.get(language, {}),
                    self._foreign_dirty_loc_keys.get(language, set()),
                )
            )
        dirty_files: set[Path] = set(self._dirty_loc_files) if dirty_only else set()
        if dirty_only:
            for default_file, _, sources, dirty_keys in tables:
                for key in dirty_keys:
                    source = sources.get(key, default_file)
                    dirty_files.add(source)

        file_entries: dict[Path, dict[str, str]] = {path: {} for path in dirty_files}
        for default_file, entries, sources, _ in tables:
            for key, value in entries.items():
                source = sources.get(key, default_file)
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
                if category is None or _definition_only(category):
                    continue
                dirty_files.add(
                    category.path or self.mod_root / "common" / "decisions" / "mod_decisions.txt"
                )
        for path in dirty_files:
            file_categories[path] = []
        owned: set[int] = set()
        for category in self._decision_categories.values():
            owned.update(id(decision) for decision in category.decisions)
            if _definition_only(category):
                continue
            p = category.path or self.mod_root / "common" / "decisions" / "mod_decisions.txt"
            if dirty_only and p not in dirty_files:
                continue
            if p not in file_categories:
                file_categories[p] = []
            file_categories[p].append(category)
        # A category extended across files is modeled once, from the file
        # loaded last. Its decisions in the other files are in no category's
        # list; render them as part of their own file, or rewriting that file
        # would delete them.
        extensions: dict[tuple[Path, str], list[Decision]] = {}
        for decision in self._decisions.values():
            if id(decision) in owned or decision.path is None:
                continue
            if dirty_only and decision.path not in dirty_files:
                continue
            extensions.setdefault((decision.path, decision.category), []).append(decision)
        for (path, category_id), decisions in extensions.items():
            listed = file_categories.setdefault(path, [])
            index = next(
                (i for i, category in enumerate(listed) if category.id == category_id),
                None,
            )
            if index is None:
                listed.append(DecisionCategory(id=category_id, path=path, decisions=decisions))
            else:
                listed[index] = replace(
                    listed[index], decisions=[*listed[index].decisions, *decisions]
                )
        # A file whose last decision of such a category was deleted keeps the
        # category block; only the decision span goes. (A category that moved
        # away still has its own decisions recorded at this path, and leaves.)
        for path, listed in file_categories.items():
            original = self._original_files.get(path)
            if not original:
                continue
            present = {category.id for category in listed}
            for span in top_level_assignments(original):
                category = self._decision_categories.get(span.key)
                if (
                    span.key in present
                    or not span.is_block
                    or category is None
                    or category.path is None
                    or category.path == path
                    or any(decision.path == path for decision in category.decisions)
                ):
                    continue
                present.add(span.key)
                listed.append(DecisionCategory(id=span.key, path=path))
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
