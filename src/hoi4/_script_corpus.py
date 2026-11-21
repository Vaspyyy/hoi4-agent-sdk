"""Find every effect, trigger, modifier and weight block in the script files of a content root.

This reads files directly through the syntax tree, not through the SDK's
models, so it can walk a whole game install (or a mod) without loading it.
It covers the same definitions the vocabulary check reads from a loaded mod:
events, national focuses, decisions and their categories, ideas,
on-actions, scripted effects and triggers, characters, dynamic modifiers
and bookmarks.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from .script_scopes import BlockKind, entry_scope
from .syntax import Block, parse
from .textio import read_text

EVENT_TYPES = (
    "country_event",
    "news_event",
    "state_event",
    "unit_leader_event",
    "operative_leader_event",
)


@dataclass(frozen=True)
class CorpusBlock:
    """One block to walk, with what it holds and where it starts."""

    path: Path
    block: Block
    kind: BlockKind
    scope: str | None
    owner_kind: str
    owner_id: str
    from_scope: str | None = None
    #: Keys of the block to leave alone (an event option's ``name``...).
    arguments: frozenset[str] = frozenset()


def iter_script_blocks(root: Path) -> Iterator[CorpusBlock]:
    """Yield every checkable block of the script files under ``root``."""

    for relative, reader in _FOLDERS:
        directory = root / relative
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.txt")):
            try:
                document = parse(read_text(path))
            except (OSError, ValueError):
                continue
            yield from reader(path, document.root)


# ── per folder ────────────────────────────────────────────────────


def _blocks(path: Path, owner_kind: str, owner_id: str, block: Block, fields: dict[str, BlockKind], *, definition: str, from_scope: str | None = None) -> Iterator[CorpusBlock]:
    for item in block.assignments():
        kind = fields.get(item.name.lower())
        inner = item.block
        if kind is None or inner is None:
            continue
        yield CorpusBlock(
            path,
            inner,
            kind,
            entry_scope(definition, item.name.lower()),
            owner_kind,
            owner_id,
            from_scope,
        )


_EVENT_FIELDS: dict[str, BlockKind] = {
    "trigger": "trigger",
    "immediate": "effect",
    "after": "effect",
    "mean_time_to_happen": "weight",
}
_OPTION_ARGUMENTS = frozenset({"name", "trigger", "ai_chance", "original_recipient_only"})
DYNAMIC_MODIFIER_ARGUMENTS = frozenset({"enable", "remove_trigger", "icon", "attacker_modifier"})


def _events(path: Path, root: Block) -> Iterator[CorpusBlock]:
    for event in root.assignments():
        body = event.block
        event_type = event.name.lower()
        if body is None or event_type not in EVENT_TYPES:
            continue
        event_id = body.scalar("id") or ""
        yield from _blocks(path, "event", event_id, body, _EVENT_FIELDS, definition=event_type)
        for key in ("title", "desc", "picture"):
            for text in body.blocks(key):
                trigger = text.block("trigger")
                if trigger is not None:
                    yield CorpusBlock(path, trigger, "trigger", entry_scope(event_type, "*"), "event", event_id)
        for option in body.blocks("option"):
            yield CorpusBlock(path, option, "effect", entry_scope(event_type, "*"), "event", event_id, arguments=_OPTION_ARGUMENTS)
            yield from _blocks(path, "event", event_id, option, {"trigger": "trigger", "ai_chance": "weight"}, definition=event_type)
            for text in option.blocks("name"):
                trigger = text.block("trigger")
                if trigger is not None:
                    yield CorpusBlock(path, trigger, "trigger", entry_scope(event_type, "*"), "event", event_id)


_FOCUS_FIELDS: dict[str, BlockKind] = {
    "completion_reward": "effect",
    "select_effect": "effect",
    "complete_tooltip": "effect",
    "available": "trigger",
    "bypass": "trigger",
    "allow_branch": "trigger",
    "cancel": "trigger",
    "ai_will_do": "weight",
}


def _focuses(path: Path, root: Block) -> Iterator[CorpusBlock]:
    containers = [item.block for item in root.assignments("focus_tree") if item.block is not None]
    for focus in _focus_blocks(root):
        yield from _blocks(path, "focus", focus.scalar("id") or "", focus, _FOCUS_FIELDS, definition="focus")
    for tree in containers:
        for focus in _focus_blocks(tree):
            yield from _blocks(path, "focus", focus.scalar("id") or "", focus, _FOCUS_FIELDS, definition="focus")
        country = tree.block("country")
        if country is not None:
            yield CorpusBlock(path, country, "weight", "COUNTRY", "focus_tree", tree.scalar("id") or "")


def _focus_blocks(block: Block) -> Iterator[Block]:
    for item in block.assignments():
        if item.name in ("focus", "shared_focus", "joint_focus") and item.block is not None:
            yield item.block


_DECISION_FIELDS: dict[str, BlockKind] = {
    "allowed": "trigger",
    "visible": "trigger",
    "available": "trigger",
    "activation": "trigger",
    "cancel_trigger": "trigger",
    "remove_trigger": "trigger",
    "target_trigger": "trigger",
    "target_root_trigger": "trigger",
    "custom_cost_trigger": "trigger",
    "complete_effect": "effect",
    "remove_effect": "effect",
    "timeout_effect": "effect",
    "cancel_effect": "effect",
    "ai_will_do": "weight",
    "modifier": "modifier",
}


def _decisions(path: Path, root: Block) -> Iterator[CorpusBlock]:
    categories_folder = "categories" in path.parts
    for category in root.assignments():
        body = category.block
        if body is None:
            continue
        if categories_folder:
            yield from _blocks(path, "decision_category", category.name, body, {"allowed": "trigger", "visible": "trigger"}, definition="decision")
            continue
        for decision in body.assignments():
            inner = decision.block
            if inner is None:
                continue
            from_scope = "STATE" if inner.scalar("state_target") == "yes" else None
            yield from _blocks(path, "decision", decision.name, inner, _DECISION_FIELDS, definition="decision", from_scope=from_scope)


_IDEA_FIELDS: dict[str, BlockKind] = {
    "allowed": "trigger",
    "allowed_civil_war": "trigger",
    "available": "trigger",
    "visible": "trigger",
    "cancel": "trigger",
    "on_add": "effect",
    "on_remove": "effect",
    "ai_will_do": "weight",
    "modifier": "modifier",
}


def _ideas(path: Path, root: Block) -> Iterator[CorpusBlock]:
    for container in root.assignments("ideas"):
        body = container.block
        if body is None:
            continue
        for category in body.assignments():
            inner = category.block
            if inner is None:
                continue
            for idea in inner.assignments():
                idea_body = idea.block
                if idea_body is not None:
                    yield from _blocks(path, "idea", idea.name, idea_body, _IDEA_FIELDS, definition="idea")


def _on_actions(path: Path, root: Block) -> Iterator[CorpusBlock]:
    for container in root.assignments("on_actions"):
        body = container.block
        if body is None:
            continue
        for action in body.assignments():
            inner = action.block
            if inner is None:
                continue
            for effect in inner.blocks("effect"):
                yield CorpusBlock(path, effect, "effect", None, "on_action", action.name)


def _scripted(kind: BlockKind) -> Callable[[Path, Block], Iterator[CorpusBlock]]:
    def reader(path: Path, root: Block) -> Iterator[CorpusBlock]:
        for item in root.assignments():
            if item.block is not None:
                yield CorpusBlock(path, item.block, kind, None, f"scripted_{kind}", item.name)

    return reader


_ROLE_FIELDS: dict[str, BlockKind] = {
    "allowed": "trigger",
    "visible": "trigger",
    "available": "trigger",
    "on_add": "effect",
    "on_remove": "effect",
    "ai_will_do": "weight",
    "modifier": "modifier",
}


def _characters(path: Path, root: Block) -> Iterator[CorpusBlock]:
    for container in root.assignments("characters"):
        body = container.block
        if body is None:
            continue
        for character in body.assignments():
            inner = character.block
            if inner is None:
                continue
            for role in inner.assignments():
                role_body = role.block
                if role_body is None:
                    continue
                if role.name == "instance":
                    allowed = role_body.block("allowed")
                    if allowed is not None:
                        yield CorpusBlock(path, allowed, "trigger", None, "character", character.name)
                    continue
                yield from _blocks(path, "character", character.name, role_body, _ROLE_FIELDS, definition="character")


def _dynamic_modifiers(path: Path, root: Block) -> Iterator[CorpusBlock]:
    for item in root.assignments():
        body = item.block
        if body is None:
            continue
        yield from _blocks(path, "dynamic_modifier", item.name, body, {"enable": "trigger", "remove_trigger": "trigger"}, definition="dynamic_modifier")
        yield CorpusBlock(path, body, "modifier", None, "dynamic_modifier", item.name, arguments=DYNAMIC_MODIFIER_ARGUMENTS)


def _bookmarks(path: Path, root: Block) -> Iterator[CorpusBlock]:
    for container in root.assignments("bookmarks"):
        body = container.block
        if body is None:
            continue
        for bookmark in body.assignments("bookmark"):
            inner = bookmark.block
            if inner is None:
                continue
            name = inner.scalar("name") or ""
            yield from _blocks(path, "bookmark", name, inner, {"effect": "effect"}, definition="bookmark")


_FOLDERS = (
    ("events", _events),
    ("common/national_focus", _focuses),
    ("common/decisions", _decisions),
    ("common/ideas", _ideas),
    ("common/on_actions", _on_actions),
    ("common/scripted_effects", _scripted("effect")),
    ("common/scripted_triggers", _scripted("trigger")),
    ("common/characters", _characters),
    ("common/dynamic_modifiers", _dynamic_modifiers),
    ("common/bookmarks", _bookmarks),
)
