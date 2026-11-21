"""Split a script file into its owners and their effect, trigger, modifier and weight blocks.

This is the one place that knows how the game's folders are laid out: which
blocks of an events file are events, where a focus keeps its reward, which
fields of a decision hold triggers. Everything that reads script by owner
uses it:

* the token check (``Mod._semantic_script_sources``) walks every block of
  the mod's own files, with their pending edits;
* the reference reader (:mod:`hoi4.script_references`) walks the same blocks
  for the ideas, events, flags and statements they name, and reads the rest
  of each owner (``prerequisite``, ``targets``, ...) as plain data;
* the corpus test walks every block of a real install, so it tests the same
  split ``validate()`` uses.

A file is read by its path inside the game or mod folder (``relative``, such
as ``events/Germany.txt``), never by its content alone.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

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

#: Owners whose script is history: read for references, not token-checked.
HISTORY_OWNERS = frozenset({"country_history", "state_history"})


@dataclass(frozen=True)
class CorpusBlock:
    """One block to walk, with what it holds and the scope it starts in."""

    block: Block
    kind: BlockKind
    scope: str | None
    owner_kind: str
    owner_id: str
    from_scope: str | None = None
    #: Keys of the block to leave alone (an event option's ``name``...).
    arguments: frozenset[str] = frozenset()


@dataclass(frozen=True)
class CorpusOwner:
    """One definition of a file (a focus, an event, a decision, ...) and its script blocks.

    ``scope`` is where the owner's script runs when that is the same for all
    of it (``None`` when not certain). The parts of ``block`` outside
    ``blocks`` hold no script.
    """

    kind: str
    id: str
    block: Block
    scope: str | None
    blocks: tuple[CorpusBlock, ...] = field(default=())


def split_file(root: Block, relative: str) -> list[CorpusOwner]:
    """Every owner of a parsed file, by where the file sits.

    Folders without owners (``common/scripted_guis``, ``history/units``, ...)
    give an empty list; their content is still data for the reference reader.
    """

    parts = PurePosixPath(relative).parts
    top = parts[0] if parts else ""
    sub = parts[1] if len(parts) > 2 else ""
    if top == "events":
        return list(_events(root, relative))
    if top == "history":
        if sub == "countries":
            name = PurePosixPath(relative).name[:3]
            return [
                CorpusOwner(
                    "country_history", name, root, "COUNTRY",
                    (CorpusBlock(root, "effect", "COUNTRY", "country_history", name),),
                )
            ]
        if sub == "states":
            state = root.block("state")
            state_id = state.scalar("id") if state is not None else None
            return [
                CorpusOwner("state_history", state_id or PurePosixPath(relative).stem, root, "STATE")
            ]
        return []
    if top != "common":
        return []
    reader = _COMMON.get(sub)
    if reader is None:
        return []
    if sub == "decisions" and len(parts) > 3 and parts[2] == "categories":
        return list(_decision_categories(root))
    return list(reader(root, relative))


def script_blocks(root: Block, relative: str, *, history: bool = False) -> Iterator[CorpusBlock]:
    """Every checkable block of a parsed file; history only with ``history``."""

    for owner in split_file(root, relative):
        if history or owner.kind not in HISTORY_OWNERS:
            yield from owner.blocks


def iter_script_blocks(root: Path) -> Iterator[tuple[Path, CorpusBlock]]:
    """Every checkable block of the script files under a content root, with its file."""

    for top in ("events", "common"):
        directory = root / top
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.txt")):
            try:
                document = parse(read_text(path))
            except (OSError, ValueError):
                continue
            relative = path.relative_to(root).as_posix()
            for block in script_blocks(document.root, relative):
                yield path, block


# ── per folder ────────────────────────────────────────────────────


def _block_items(block: Block) -> Iterator[tuple[str, Block]]:
    """``(key, block)`` of every block-valued item, keys as written."""

    for item in block.assignments():
        inner = item.block
        if inner is not None:
            yield item.key.value, inner


def _fields(
    owner_kind: str,
    owner_id: str,
    block: Block,
    fields: dict[str, BlockKind],
    *,
    definition: str,
    from_scope: str | None = None,
) -> list[CorpusBlock]:
    found: list[CorpusBlock] = []
    for item in block.assignments():
        folded = item.key.value.lower()
        kind = fields.get(folded)
        inner = item.block
        if kind is None or inner is None:
            continue
        found.append(
            CorpusBlock(
                inner, kind, entry_scope(definition, folded), owner_kind, owner_id, from_scope
            )
        )
    return found


_EVENT_FIELDS: dict[str, BlockKind] = {
    "trigger": "trigger",
    "immediate": "effect",
    "after": "effect",
    "mean_time_to_happen": "weight",
}
_OPTION_FIELDS: dict[str, BlockKind] = {"trigger": "trigger", "ai_chance": "weight"}
_OPTION_ARGUMENTS = frozenset({"name", "trigger", "ai_chance", "original_recipient_only"})
DYNAMIC_MODIFIER_ARGUMENTS = frozenset({"enable", "remove_trigger", "icon", "attacker_modifier"})


def _events(root: Block, relative: str) -> Iterator[CorpusOwner]:
    for key, body in _block_items(root):
        event_type = key.lower()
        if event_type not in EVENT_TYPES:
            yield CorpusOwner("event_file", relative, body, None)
            continue
        event_id = body.scalar("id") or ""
        scope = entry_scope(event_type, "*")
        blocks = _fields("event", event_id, body, _EVENT_FIELDS, definition=event_type)
        for text_key in ("title", "desc", "picture"):
            for text in body.blocks(text_key, fold=True):
                trigger = text.block("trigger", fold=True)
                if trigger is not None:
                    blocks.append(CorpusBlock(trigger, "trigger", scope, "event", event_id))
        for option in body.blocks("option", fold=True):
            blocks.append(
                CorpusBlock(option, "effect", scope, "event", event_id, arguments=_OPTION_ARGUMENTS)
            )
            blocks.extend(_fields("event", event_id, option, _OPTION_FIELDS, definition=event_type))
            for text in option.blocks("name", fold=True):
                trigger = text.block("trigger", fold=True)
                if trigger is not None:
                    blocks.append(CorpusBlock(trigger, "trigger", scope, "event", event_id))
        yield CorpusOwner("event", event_id, body, scope, tuple(blocks))


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
_FOCUS_KEYS = frozenset({"focus", "shared_focus", "joint_focus"})


def _focus(body: Block) -> CorpusOwner:
    focus_id = body.scalar("id") or ""
    return CorpusOwner(
        "focus", focus_id, body, "COUNTRY",
        tuple(_fields("focus", focus_id, body, _FOCUS_FIELDS, definition="focus")),
    )


def _focuses(root: Block, relative: str) -> Iterator[CorpusOwner]:
    for key, body in _block_items(root):
        name = key.lower()
        if name in _FOCUS_KEYS:
            yield _focus(body)
        elif name == "focus_tree":
            tree_id = body.scalar("id") or ""
            for child_key, child in _block_items(body):
                child_name = child_key.lower()
                if child_name in _FOCUS_KEYS:
                    yield _focus(child)
                elif child_name == "country":
                    yield CorpusOwner(
                        "focus_tree", tree_id, child, "COUNTRY",
                        (CorpusBlock(child, "weight", "COUNTRY", "focus_tree", tree_id),),
                    )
                else:
                    yield CorpusOwner("focus_tree", tree_id, child, "COUNTRY")
        else:
            yield CorpusOwner("file", relative, body, None)


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
_CATEGORY_FIELDS: dict[str, BlockKind] = {"allowed": "trigger", "visible": "trigger"}


def _decision_categories(root: Block) -> Iterator[CorpusOwner]:
    for key, body in _block_items(root):
        yield CorpusOwner(
            "decision_category", key, body, "COUNTRY",
            tuple(_fields("decision_category", key, body, _CATEGORY_FIELDS, definition="decision")),
        )


def _decisions(root: Block, relative: str) -> Iterator[CorpusOwner]:
    for _, category in _block_items(root):
        for key, body in _block_items(category):
            from_scope = "STATE" if body.scalar("state_target") == "yes" else None
            yield CorpusOwner(
                "decision", key, body, "COUNTRY",
                tuple(
                    _fields(
                        "decision", key, body, _DECISION_FIELDS,
                        definition="decision", from_scope=from_scope,
                    )
                ),
            )


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


def _ideas(root: Block, relative: str) -> Iterator[CorpusOwner]:
    for _, container in _block_items(root):
        for _, category in _block_items(container):
            for key, body in _block_items(category):
                yield CorpusOwner(
                    "idea", key, body, "COUNTRY",
                    tuple(_fields("idea", key, body, _IDEA_FIELDS, definition="idea")),
                )


def _on_actions(root: Block, relative: str) -> Iterator[CorpusOwner]:
    for _, container in _block_items(root):
        for key, body in _block_items(container):
            yield CorpusOwner(
                "on_action", key, body, None,
                tuple(
                    CorpusBlock(effect, "effect", None, "on_action", key)
                    for effect in body.blocks("effect", fold=True)
                ),
            )


def _scripted_effects(root: Block, relative: str) -> Iterator[CorpusOwner]:
    for key, body in _block_items(root):
        yield CorpusOwner(
            "scripted_effect", key, body, None,
            (CorpusBlock(body, "effect", None, "scripted_effect", key),),
        )


def _scripted_triggers(root: Block, relative: str) -> Iterator[CorpusOwner]:
    for key, body in _block_items(root):
        yield CorpusOwner(
            "scripted_trigger", key, body, None,
            (CorpusBlock(body, "trigger", None, "scripted_trigger", key),),
        )


_ROLE_FIELDS: dict[str, BlockKind] = {
    "allowed": "trigger",
    "visible": "trigger",
    "available": "trigger",
    "on_add": "effect",
    "on_remove": "effect",
    "ai_will_do": "weight",
    "modifier": "modifier",
}


def _characters(root: Block, relative: str) -> Iterator[CorpusOwner]:
    for _, container in _block_items(root):
        for key, body in _block_items(container):
            blocks: list[CorpusBlock] = []
            for role_key, role in _block_items(body):
                if role_key.lower() == "instance":
                    allowed = role.block("allowed", fold=True)
                    if allowed is not None:
                        blocks.append(CorpusBlock(allowed, "trigger", None, "character", key))
                    continue
                blocks.extend(_fields("character", key, role, _ROLE_FIELDS, definition="character"))
            yield CorpusOwner("character", key, body, None, tuple(blocks))


def _dynamic_modifiers(root: Block, relative: str) -> Iterator[CorpusOwner]:
    for key, body in _block_items(root):
        blocks = _fields(
            "dynamic_modifier", key, body,
            {"enable": "trigger", "remove_trigger": "trigger"},
            definition="dynamic_modifier",
        )
        blocks.append(
            CorpusBlock(
                body, "modifier", None, "dynamic_modifier", key,
                arguments=DYNAMIC_MODIFIER_ARGUMENTS,
            )
        )
        yield CorpusOwner("dynamic_modifier", key, body, None, tuple(blocks))


def _bookmarks(root: Block, relative: str) -> Iterator[CorpusOwner]:
    for _, container in _block_items(root):
        for key, body in _block_items(container):
            name = body.scalar("name") or key
            yield CorpusOwner(
                "bookmark", name, body, None,
                tuple(_fields("bookmark", name, body, {"effect": "effect"}, definition="bookmark")),
            )


_COMMON = {
    "national_focus": _focuses,
    "decisions": _decisions,
    "ideas": _ideas,
    "on_actions": _on_actions,
    "scripted_effects": _scripted_effects,
    "scripted_triggers": _scripted_triggers,
    "characters": _characters,
    "dynamic_modifiers": _dynamic_modifiers,
    "bookmarks": _bookmarks,
}
