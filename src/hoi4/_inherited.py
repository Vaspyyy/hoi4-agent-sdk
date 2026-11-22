"""What the game and the base mods define, kind by kind, for read-only lookups.

A lookup such as ``Mod.get_event("germany.1")`` looks in the mod first and
then in what the mod inherits (the base mods, then the game). This module
describes, for every kind of content found that way, which inherited folders
hold it, how one file of it is read (with the kind's own loader) and what
the id of one definition is.

``Mod`` finds an inherited id through an index of the ids each inherited
file defines. The index of a file is kept on disk between processes (see
``hoi4._disk_cache``), so a lookup reads the store and then parses only the
file that holds the id. See ``CoreLayer._inherited_definition``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._disk_cache import DiskAnalysis, string_list
from .bookmarks import load_bookmarks_file
from .characters import load_characters_file
from .decisions import (
    load_decision_categories_file,
    load_decisions_file,
    merge_decision_category,
)
from .dynamic_modifiers import load_dynamic_modifiers_text
from .events import load_events_file
from .focus import load_focus_trees
from .ideas import read_ideas_text
from .on_actions import load_on_actions_file
from .syntax import parse
from .types import DecisionCategory

__all__ = ["INHERITED_IDS_ON_DISK", "INHERITED_KINDS", "InheritedKind"]

#: The ids one inherited file defines, as they go to disk and back.
INHERITED_IDS_ON_DISK: DiskAnalysis[list[str]] = DiskAnalysis("inherited_ids", list, string_list)


def _first(matches: Sequence[tuple[Path, Any]]) -> Any:
    return matches[0][1]


@dataclass(frozen=True)
class InheritedKind:
    """One kind of content a lookup can find in the game or a base mod.

    ``folders`` are the content folders whose files (directly in the folder,
    like the mod's own loaders read them) hold it. ``read`` returns the
    definitions of one file, read by the kind's own loader, and ``key`` the
    id of one definition. When several files define an id, ``combine`` makes
    the one result from all of them, in the order of the lookup (the highest
    layer first); by default the first one wins.

    ``whole_file`` is true when the game can only replace an inherited
    definition by replacing its whole file (a mod file of the same name): an
    edit of such a definition raises ``ReadOnlyError`` that names the call
    which copies the file into the mod.
    """

    name: str
    folders: tuple[str, ...]
    read: Callable[[Path, str], Sequence[Any]]
    key: Callable[[Any], str]
    plural: str | None = None
    whole_file: bool = True
    combine: Callable[[Sequence[tuple[Path, Any]]], Any] = _first


def _focus_trees(path: Path, text: str) -> Sequence[Any]:
    return load_focus_trees(path, text=text)


def _events(path: Path, text: str) -> Sequence[Any]:
    return load_events_file(path, document=parse(text))[1]


def _ideas(path: Path, text: str) -> Sequence[Any]:
    return read_ideas_text(text, path)[0]


def _decisions(path: Path, text: str) -> Sequence[Any]:
    return [
        decision
        for category in load_decisions_file(path, text=text)
        for decision in category.decisions
    ]


def _is_category_file(path: Path) -> bool:
    return path.parent.name == "categories"


def _decision_categories(path: Path, text: str) -> Sequence[Any]:
    if _is_category_file(path):
        return load_decision_categories_file(path, text=text)
    return load_decisions_file(path, text=text)


def _combine_categories(matches: Sequence[tuple[Path, Any]]) -> Any:
    """One category from its definition and every decisions file that has a block of it.

    The same merge as the mod's own loader: the game joins the blocks of a
    category from every file, and the definition in
    ``common/decisions/categories`` gives its presentation.
    """

    definitions = [model for path, model in matches if _is_category_file(path)]
    contents = [model for path, model in matches if not _is_category_file(path)]
    definition: DecisionCategory | None = definitions[0] if definitions else None
    if not contents:
        return definition
    # The contents in load order (the file name order of the merged folder).
    contents.sort(key=lambda category: category.path.name if category.path else "")
    category: DecisionCategory = contents[0]
    for later in contents[1:]:
        merge_decision_category(category, later, presentation=definition is None)
    if definition is not None:
        category.icon = definition.icon
        category.allowed = definition.allowed
        category.visible = definition.visible
        category.definition_path = definition.definition_path
        category.definition_raw_block = definition.definition_raw_block
    return category


def _bookmarks(path: Path, text: str) -> Sequence[Any]:
    return load_bookmarks_file(path, text=text)


def _dynamic_modifiers(path: Path, text: str) -> Sequence[Any]:
    return load_dynamic_modifiers_text(text, path)


def _on_actions(path: Path, text: str) -> Sequence[Any]:
    return load_on_actions_file(path, document=parse(text))


def _characters(path: Path, text: str) -> Sequence[Any]:
    return load_characters_file(path, text=text)


def _id(model: Any) -> str:
    return str(model.id)


def _name(model: Any) -> str:
    return str(model.name)


#: Every kind found through the index, by the name ``Mod`` uses for it.
INHERITED_KINDS: dict[str, InheritedKind] = {
    "focus_tree": InheritedKind("focus tree", ("common/national_focus",), _focus_trees, _id),
    "event": InheritedKind("event", ("events",), _events, _id),
    "idea": InheritedKind("idea", ("common/ideas", "common/national_ideas"), _ideas, _id),
    "decision": InheritedKind("decision", ("common/decisions",), _decisions, _id),
    "decision_category": InheritedKind(
        "decision category",
        ("common/decisions/categories", "common/decisions"),
        _decision_categories,
        _id,
        plural="decision categories",
        combine=_combine_categories,
    ),
    "bookmark": InheritedKind("bookmark", ("common/bookmarks",), _bookmarks, _name),
    "dynamic_modifier": InheritedKind(
        "dynamic modifier", ("common/dynamic_modifiers",), _dynamic_modifiers, _id
    ),
    # Hooks add up across files: every inherited occurrence counts, and an
    # edit of one is refused with its own message (see ``Mod.update_on_action``).
    "on_action": InheritedKind("on-action", ("common/on_actions",), _on_actions, _id),
    # Characters are overridden one by one: an edit copies the character into
    # the mod (see ``CharactersLayer``).
    "character": InheritedKind(
        "character", ("common/characters",), _characters, _id, whole_file=False
    ),
}
