"""Find what a piece of script refers to, read from the syntax tree.

Every check that asks "which ideas, events, flags, technologies, equipment,
focus trees or country tags does this script name?" reads the answer here.
Files are split into owners by :mod:`hoi4._script_corpus` and every script
block is walked by the scope walker of :mod:`hoi4.script_scopes`, the same
walker the token check uses: it decides what a statement is (a command, a
control structure, an iterator, a scope change or an argument) and the scope
it runs in. ``123 = { add_core_of = TAG }`` and ``every_owned_state = {
add_core_of = TAG }`` run in a state, a bare ``add_core_of`` in a focus reward
runs in the country. A comment or a quoted string can never look like a
reference.

A scope is only named when it is certain. Inside the arguments of a command
(a scripted effect with arguments, ``start_civil_war = { ... }``) or behind
``var:x`` the scope is ``UNKNOWN`` and ``certain`` is false, so checks stay
quiet there. The parts of a file that hold no script (a focus's
``prerequisite``, a scripted GUI) are still read for names, as data.

A file is read once into a :class:`FileReferences`: its owners (a focus, an
event, a decision, a scripted effect, ...) with their references, and what the
file defines (events, ideas, technologies, ...). Nothing in it keeps the
syntax tree alive, so the whole game can be indexed at once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Collection, Iterable, Iterator

from ._disk_cache import DiskAnalysis
from ._script_corpus import split_file
from .script_scopes import CONTROL, BlockKind, Role, WalkState, walk_data, walk_script
from .syntax import Assignment, Block, Document, Scalar, Tagged, parse

COUNTRY = "COUNTRY"
STATE = "STATE"
CHARACTER = "CHARACTER"
UNKNOWN = "UNKNOWN"

TAG_RE = re.compile(r"^[A-Z][A-Z0-9]{2}$")
DYNAMIC_TAG_RE = re.compile(r"^D[0-9]{2}$")
# Parts of a name the game fills in while running.
_DYNAMIC_PART_RE = re.compile(
    r"@\[[^\]]*\]|@[A-Za-z0-9_.:]+|\[[^\]]*\]|\$[^$]*\$|\?[A-Za-z0-9_.:]*"
)

EVENT_EFFECTS = frozenset(
    {
        "country_event",
        "news_event",
        "state_event",
        "unit_leader_event",
        "operative_leader_event",
    }
)
# ``start_border_war`` names the events it fires with these keys.
_BORDER_WAR_EVENT_KEYS = frozenset({"on_win", "on_lose", "on_cancel"})
_FLAG_KINDS = ("country", "global", "state", "character", "unit_leader", "mio", "project")
FLAG_WRITERS = frozenset(
    f"{verb}_{kind}_flag" for verb in ("set", "clr", "modify") for kind in _FLAG_KINDS
)
FLAG_READERS = frozenset(f"has_{kind}_flag" for kind in _FLAG_KINDS)
_STATEMENT_KEYS = {
    "add_core_of": "core_effect",
    "remove_core_of": "core_effect",
    "add_to_faction": "faction_effect",
    "start_civil_war": "civil_war",
    "recruit_character": "recruit_character",
    "transfer_state": "transfer_state",
    "add_resistance": "resistance",
    "swap_ideas": "swap_ideas",
}


def is_dynamic(value: str) -> bool:
    """True when the game builds part of the name while it runs.

    That covers ``@ROOT``, ``[ROOT.GetTag]``, ``$parameter$``, ``?value`` and a
    value read from a variable or event target (``var:x``).
    """

    return any(mark in value for mark in "@[$?:")


def name_pattern(value: str) -> str:
    """The name with every part the game fills in replaced by ``*``."""

    return _DYNAMIC_PART_RE.sub("*", value)


def event_key(event_id: str) -> str:
    """An event id as the game compares it: the namespace without regard to case.

    ``NSB_news.200`` and ``nsb_news.200`` name the same event; the number
    behind the last dot is compared as it is. Use this wherever an event id
    is looked up or a fired id is matched to a defined one.
    """

    namespace, dot, number = event_id.rpartition(".")
    return f"{namespace.lower()}.{number}" if dot else event_id


def is_country_tag(key: str) -> bool:
    return bool(TAG_RE.match(key) or DYNAMIC_TAG_RE.match(key))


@dataclass(frozen=True, slots=True)
class ScriptRef:
    """One thing a statement names, where it is and the scope it runs in.

    ``kind`` is what is named: ``idea``, ``event``, ``flag``, ``technology``,
    ``equipment``, ``focus_tree``, ``country`` (a tag used as a scope) or a
    statement such as
    ``core_effect``. ``action`` is what the statement does with it: ``add``,
    ``remove``, ``has``, ``show``, ``fire``, ``set``, ``clear``, ``read``,
    ``load``, ``scope`` or ``use``.

    ``scope`` is the scope the statement runs in and ``certain`` says whether
    the walk is sure of it. ``statement`` is false inside the argument block
    of an effect (``start_civil_war = { ... }``), where keys are not
    statements. ``scope_key`` is the key that made that scope
    (``GER`` in ``GER = { transfer_state = 5 }``). ``hidden`` is true inside
    ``hidden_effect``, ``guarded`` inside a condition. ``iterator`` is
    ``country``, ``state`` or ``character`` inside ``every_country`` and its
    kin. ``root`` is the offset of the effect block (an option, a reward) the
    statement belongs to.
    """

    kind: str
    action: str
    value: str
    offset: int
    line: int
    column: int
    scope: str
    key: str
    certain: bool = True
    statement: bool = True
    scope_key: str = ""
    hidden: bool = False
    guarded: bool = False
    iterator: str = ""
    root: int = -1

    @property
    def dynamic(self) -> bool:
        return is_dynamic(self.value)


@dataclass(slots=True)
class ScriptOwner:
    """The references of one piece of script with one owner.

    The owner is a focus, an event, a decision, a scripted effect, an
    on-action, a country's history, ... ``line`` is where it starts.
    """

    kind: str
    id: str
    scope: str
    line: int
    refs: list[ScriptRef] = field(default_factory=list)

    def of(self, kind: str) -> Iterator[ScriptRef]:
        return (ref for ref in self.refs if ref.kind == kind)


def _scalar_of(value: object) -> str | None:
    return value.value if type(value) is Scalar else None


def _list_values(block: Block) -> list[Scalar]:
    return [item for item in block.items if type(item) is Scalar]


def _block_scalar(block: Block, key: str) -> Scalar | None:
    found = block.first(key, fold=True)
    if found is not None and type(found.value) is Scalar:
        return found.value
    return None


class _Collector:
    """Records what the statements of one owner name.

    The walker of :mod:`hoi4.script_scopes` decides what each statement is
    and where it runs; this class only reads the names out of it.
    """

    def __init__(self, doc: Document, owner: ScriptOwner) -> None:
        self.doc = doc
        self.owner = owner
        #: The start of the effect block (an option, a reward) being walked.
        self.root = -1

    def add(
        self,
        kind: str,
        action: str,
        value: str,
        offset: int,
        key: str,
        statement: bool,
        scope: str | None,
        state: WalkState,
    ) -> None:
        if not value:
            return
        line, column = self.doc.line_col(offset)
        self.owner.refs.append(
            ScriptRef(
                kind, action, value, offset, line, column, scope or UNKNOWN, key,
                statement and scope is not None, statement, state.scope_key if statement else "",
                state.hidden, state.guarded, state.iterator if statement else "", self.root,
            )
        )

    def visit(
        self,
        item: Assignment,
        role: Role,
        kind: BlockKind | None,
        scope: str | None,
        state: WalkState,
    ) -> None:
        key = item.key.value
        lowered = key.lower()
        value = item.value
        offset = item.start
        scalar = _scalar_of(value)
        block = value if type(value) is Block else None
        statement = role != "argument"
        if not statement:
            scope = None

        def add(kind: str, action: str, name: str, at: int, by: str = key) -> None:
            self.add(kind, action, name, at, by, statement, scope, state)

        if lowered in EVENT_EFFECTS:
            if scalar is not None:
                add("event", "fire", scalar, offset)
            elif block is not None:
                found = _block_scalar(block, "id")
                if found is not None:
                    add("event", "fire", found.value, found.start)
            return
        if lowered in _BORDER_WAR_EVENT_KEYS and scalar is not None and "." in scalar:
            add("event", "fire", scalar, offset)
            return
        if lowered in {"add_ideas", "remove_ideas"}:
            action = "add" if lowered == "add_ideas" else "remove"
            if scalar is not None:
                add("idea", action, scalar, offset)
            elif block is not None:
                for entry in _list_values(block):
                    add("idea", action, entry.value, entry.start)
            return
        if lowered in {"add_timed_idea", "modify_timed_idea"}:
            action = "add" if lowered == "add_timed_idea" else "has"
            found = _block_scalar(block, "idea") if block is not None else None
            if found is not None:
                add("idea", action, found.value, found.start)
            elif scalar is not None:
                add("idea", action, scalar, offset)
            return
        if lowered == "swap_ideas":
            add("swap_ideas", "use", key, offset)
            if block is not None:
                for field_name, action in (("remove_idea", "remove"), ("add_idea", "add")):
                    found = _block_scalar(block, field_name)
                    if found is not None:
                        add("idea", action, found.value, found.start)
            return
        if lowered == "has_idea" and scalar is not None:
            add("idea", "has", scalar, offset)
            return
        if lowered == "show_ideas_tooltip" and scalar is not None:
            add("idea", "show", scalar, offset)
            return
        if lowered == "ideas" and block is not None and self.owner.kind == "bookmark":
            for entry in _list_values(block):
                add("idea", "show", entry.value, entry.start)
            return
        if lowered in FLAG_WRITERS or lowered in FLAG_READERS:
            action = (
                "read" if lowered.startswith("has_")
                else "clear" if lowered.startswith("clr_") else "set"
            )
            if scalar is not None:
                add("flag", action, scalar, offset)
            elif block is not None:
                found = _block_scalar(block, "flag")
                if found is not None:
                    add("flag", action, found.value, found.start)
            return
        if lowered == "set_technology" and block is not None:
            for tech in block.assignments():
                if tech.key.value.lower() != "popup":
                    add("technology", "add", tech.key.value, tech.start)
            return
        if lowered == "has_tech" and scalar is not None:
            add("technology", "has", scalar, offset)
            return
        if lowered in {"add_equipment_to_stockpile", "send_equipment"} and block is not None:
            found = _block_scalar(block, "type")
            if found is not None:
                add("equipment", "add", found.value, found.start)
            return
        if lowered == "load_focus_tree":
            found = _block_scalar(block, "tree") if block is not None else None
            if found is not None:
                add("focus_tree", "load", found.value, found.start)
            elif scalar is not None:
                add("focus_tree", "load", scalar, offset)
            return
        statement_kind = _STATEMENT_KEYS.get(lowered)
        if statement_kind == "civil_war":
            # The value lists the fields the block sets, joined by "+".
            present = [
                name for name in ("capital", "states", "ideology")
                if block is not None and block.first(name, fold=True) is not None
            ]
            add("civil_war", "use", "+".join(present) or key, offset)
            capital = _block_scalar(block, "capital") if block is not None else None
            if capital is not None:
                add("civil_war_capital", "use", capital.value, capital.start, "capital")
            return
        if statement_kind is not None:
            add(statement_kind, "use", scalar or key, offset)
            return
        if role == "control" or lowered in CONTROL:
            # ``AND = { }`` and ``NOT = { }`` look like country tags.
            return
        if type(value) in (Block, Tagged) and is_country_tag(key):
            add("country", "scope", key, offset)


def _on_action_events(doc: Document, owner: ScriptOwner, block: Block) -> None:
    """``events = { ... }`` and ``random_events = { 10 = id }`` of an on-action."""

    def fire(event_id: str, offset: int, key: str, guarded: bool) -> None:
        line, column = doc.line_col(offset)
        owner.refs.append(
            ScriptRef("event", "fire", event_id, offset, line, column, UNKNOWN,
                      key, False, False, "", False, guarded, "", block.start)
        )

    for item in block.assignments():
        lowered = item.key.value.lower()
        inner = item.block
        if inner is None:
            continue
        if lowered == "events":
            for entry in _list_values(inner):
                fire(entry.value, entry.start, "events", False)
        elif lowered == "random_events":
            for weighted in inner.assignments():
                event_id = _scalar_of(weighted.value)
                if event_id and event_id != "0":
                    fire(event_id, weighted.value.start, "random_events", True)
            for entry in _list_values(inner):
                if entry.value != "0":
                    fire(entry.value, entry.start, "random_events", True)


def _nested(block: Block, depth: int) -> Iterator[Assignment]:
    """Block-valued assignments ``depth`` levels below ``block``."""

    level: list[Assignment] = [item for item in block.assignments() if item.block is not None]
    for _ in range(depth - 1):
        level = [
            child
            for parent in level
            if parent.block is not None
            for child in parent.block.assignments()
            if child.block is not None
        ]
    return iter(level)


def owners_of(
    doc: Document, relative: str, *, characters: Collection[str] = ()
) -> list[ScriptOwner]:
    """Every owner in a file, each with the references of its script.

    ``relative`` is the path inside the game or mod folder, such as
    ``events/Germany.txt``; it decides how the file is split
    (:func:`hoi4._script_corpus.split_file`). Each script block of an owner is
    walked by the scope walker; the rest of the owner, and whatever lies
    outside every owner, is read as data. ``characters`` are the character
    ids (lower case) that open a character scope when used as a key, as in
    ``GER_erwin_rommel = { owner = { ... } }``; without them such a block is
    read as data, which loses the scope of what is inside.
    """

    owners: list[ScriptOwner] = []
    claimed: set[int] = set()
    for found in split_file(doc.root, relative):
        owner = ScriptOwner(
            found.kind, found.id, found.scope or UNKNOWN, doc.line_col(found.block.start)[0]
        )
        collector = _Collector(doc, owner)
        walked: set[int] = set()
        for part in found.blocks:
            collector.root = part.block.start
            walk_script(
                part.block,
                part.kind,
                scope=part.scope,
                from_scope=part.from_scope,
                characters=characters,
                arguments=part.arguments,
                visit=collector.visit,
            )
            walked.add(part.block.start)
        if found.block.start not in walked:
            collector.root = found.block.start
            walk_data(found.block, collector.visit, skip=walked)
        if found.kind == "on_action":
            _on_action_events(doc, owner, found.block)
        # Blocks are walked one by one; keep the references in file order.
        owner.refs.sort(key=lambda ref: ref.offset)
        owners.append(owner)
        claimed.add(found.block.start)
    if doc.root.start not in claimed:
        # Whatever lies outside every owner, such as a statement at the top
        # of an events file or a whole scripted GUI file, still counts as a use.
        rest = ScriptOwner("file", relative, UNKNOWN, 1)
        collector = _Collector(doc, rest)
        collector.root = doc.root.start
        walk_data(doc.root, collector.visit, skip=claimed)
        if rest.refs:
            owners.append(rest)
    return owners


def fragment_owner(
    text: str,
    kind: str,
    owner_id: str,
    scope: str = COUNTRY,
    *,
    characters: Collection[str] = (),
) -> ScriptOwner:
    """The references of a loose piece of effect script, such as one event option."""

    doc = parse(text)
    owner = ScriptOwner(kind, owner_id, scope, 1)
    collector = _Collector(doc, owner)
    collector.root = doc.root.start
    walk_script(
        doc.root,
        "effect",
        scope=None if scope == UNKNOWN else scope,
        characters=characters,
        visit=collector.visit,
    )
    return owner


# ----------------------------------------------------------------------------
# What a file defines


@dataclass(slots=True)
class Definitions:
    """What one file defines, read from its tree."""

    events: dict[str, tuple[bool, int]] = field(default_factory=dict)
    ideas: dict[str, tuple[str, int]] = field(default_factory=dict)
    advisor_ideas: set[str] = field(default_factory=set)
    focus_trees: set[str] = field(default_factory=set)
    technologies: set[str] = field(default_factory=set)
    equipment: dict[str, str] = field(default_factory=dict)
    equipment_copies: list[tuple[str, str, frozenset[str]]] = field(default_factory=list)
    tags: set[str] = field(default_factory=set)
    organizations: set[str] = field(default_factory=set)


def _top_blocks(doc: Document) -> Iterator[Assignment]:
    return (item for item in doc.root.assignments() if item.block is not None)


def definitions_of(doc: Document, relative: str) -> Definitions:
    """Read what a file defines, by where it sits in the game's folders."""

    found = Definitions()
    parts = PurePosixPath(relative).parts
    top = parts[0] if parts else ""
    sub = parts[1] if len(parts) > 2 else ""
    if top == "events":
        for item in _top_blocks(doc):
            if item.key.value.lower() not in EVENT_EFFECTS:
                continue
            assert item.block is not None
            event_id = item.block.scalar("id")
            if event_id:
                triggered = (item.block.scalar("is_triggered_only") or "").lower() == "yes"
                found.events[event_id] = (triggered, doc.line_col(item.start)[0])
        return found
    if top != "common":
        return found
    if sub == "ideas":
        for category in _nested(doc.root, 2):
            assert category.block is not None
            for idea in category.block.assignments():
                if idea.block is not None:
                    found.ideas[idea.key.value] = (category.key.value, doc.line_col(idea.start)[0])
    elif sub == "characters":
        for assignment, _ in doc.root.walk():
            if assignment.key.value.lower() == "idea_token" and assignment.scalar:
                found.advisor_ideas.add(assignment.scalar)
    elif sub == "national_focus":
        for item in _top_blocks(doc):
            if item.key.value.lower() == "focus_tree":
                assert item.block is not None
                tree_id = item.block.scalar("id")
                if tree_id:
                    found.focus_trees.add(tree_id)
    elif sub == "technologies":
        for item in _top_blocks(doc):
            if item.key.value.lower() == "technologies":
                assert item.block is not None
                found.technologies.update(
                    tech.key.value
                    for tech in item.block.assignments()
                    if tech.block is not None and not tech.key.value.startswith("@")
                )
    elif sub == "units" and len(parts) > 3 and parts[2] == "equipment":
        for item in _top_blocks(doc):
            name = item.key.value.lower()
            assert item.block is not None
            if name == "equipments":
                for entry in item.block.assignments():
                    if entry.block is not None:
                        found.equipment[entry.key.value] = entry.block.scalar("archetype") or ""
            elif name == "duplicate_archetypes":
                for entry in item.block.assignments():
                    if entry.block is None:
                        continue
                    variants = entry.block.block("variant_name")
                    named = frozenset(
                        variant.key.value for variant in variants.assignments()
                    ) if variants is not None else frozenset()
                    found.equipment_copies.append(
                        (entry.key.value, entry.block.scalar("archetype") or "", named)
                    )
    elif sub == "country_tags":
        found.tags.update(
            item.key.value
            for item in doc.root.assignments()
            if is_country_tag(item.key.value) and item.scalar is not None
        )
    elif sub == "military_industrial_organization":
        found.organizations.update(item.key.value for item in _top_blocks(doc))
    elif sub == "country_tag_aliases":
        found.tags.update(item.key.value for item in _top_blocks(doc) if TAG_RE.match(item.key.value))
    return found


def dynamic_tags_of(root: Block) -> set[str]:
    """Tags a ``common/country_tags`` file declares after ``dynamic_tags = yes``.

    These are civil-war and released tags the game names at runtime.
    """

    tags: set[str] = set()
    dynamic = False
    for item in root.assignments():
        if item.key.value.lower() == "dynamic_tags":
            dynamic = (item.scalar or "").lower() == "yes"
        elif dynamic:
            tags.add(item.key.value)
    return tags


def resolve_equipment(
    equipment: dict[str, str], copies: Iterable[tuple[str, str, frozenset[str]]]
) -> set[str]:
    """Every equipment name, with the copies ``duplicate_archetypes`` makes.

    A copy ``new = { archetype = old }`` duplicates every equipment of
    archetype ``old`` whose name starts with ``old``, with ``new`` in its
    place; its ``variant_name`` block names copies too.
    """

    ids = set(equipment)
    for new, old, named in copies:
        ids.add(new)
        ids.update(named)
        if not old:
            continue
        for name, archetype in equipment.items():
            if archetype == old and name.startswith(old):
                ids.add(new + name[len(old):])
    return ids


@dataclass(slots=True)
class FileReferences:
    """The owners, references and definitions of one script file.

    ``own`` is true for a file of the mod itself, false for a file it
    inherits from the game or a base mod.
    """

    path: str
    relative: str
    own: bool
    owners: list[ScriptOwner]
    defines: Definitions

    def refs(self) -> Iterator[tuple[ScriptOwner, ScriptRef]]:
        for owner in self.owners:
            for ref in owner.refs:
                yield owner, ref


def file_references(
    path: str, relative: str, text: str, *, own: bool, characters: Collection[str] = ()
) -> FileReferences:
    """Read one file. ``relative`` is its path inside the game or mod folder."""

    doc = parse(text)
    return FileReferences(
        path,
        relative,
        own,
        owners_of(doc, relative, characters=characters),
        definitions_of(doc, relative),
    )


# Storing on disk
#
# ``FILE_REFERENCES_ON_DISK`` turns the references of a file into plain JSON
# data and back, so ``hoi4._disk_cache`` can keep the game's references
# between processes. A ref is a list of its fields in order; the fields at
# the end that hold their default value are left out.

_REF_DEFAULTS = (True, True, "", False, False, "", -1)
_REF_REQUIRED = 8


def _encode_ref(ref: ScriptRef) -> list[object]:
    row: list[object] = [
        ref.kind, ref.action, ref.value, ref.offset, ref.line, ref.column, ref.scope, ref.key,
        ref.certain, ref.statement, ref.scope_key, ref.hidden, ref.guarded, ref.iterator,
        ref.root,
    ]
    end = len(row)
    while end > _REF_REQUIRED and row[end - 1] == _REF_DEFAULTS[end - 1 - _REF_REQUIRED]:
        end -= 1
    return row[:end]


def _encode_definitions(found: Definitions) -> dict[str, object]:
    data: dict[str, object] = {}
    if found.events:
        data["events"] = {key: list(value) for key, value in found.events.items()}
    if found.ideas:
        data["ideas"] = {key: list(value) for key, value in found.ideas.items()}
    if found.equipment:
        data["equipment"] = dict(found.equipment)
    if found.equipment_copies:
        data["equipment_copies"] = [
            [new, old, sorted(named)] for new, old, named in found.equipment_copies
        ]
    for name in ("advisor_ideas", "focus_trees", "technologies", "tags", "organizations"):
        values = getattr(found, name)
        if values:
            data[name] = sorted(values)
    return data


def _decode_definitions(data: dict[str, Any]) -> Definitions:
    found = Definitions()
    for key, (has_id, line) in data.get("events", {}).items():
        found.events[key] = (bool(has_id), int(line))
    for key, (category, line) in data.get("ideas", {}).items():
        found.ideas[key] = (str(category), int(line))
    found.equipment.update(data.get("equipment", {}))
    found.equipment_copies.extend(
        (str(new), str(old), frozenset(named)) for new, old, named in data.get("equipment_copies", ())
    )
    for name in ("advisor_ideas", "focus_trees", "technologies", "tags", "organizations"):
        getattr(found, name).update(data.get(name, ()))
    return found


#: How :class:`Definitions` of one file go to disk and back.
DEFINITIONS_ON_DISK: DiskAnalysis[Definitions] = DiskAnalysis(
    "definitions", _encode_definitions, _decode_definitions
)


def encode_file_references(found: FileReferences) -> dict[str, object]:
    """``found`` as plain JSON data (see :func:`decode_file_references`)."""

    return {
        "path": found.path,
        "relative": found.relative,
        "own": found.own,
        "owners": [
            [owner.kind, owner.id, owner.scope, owner.line, [_encode_ref(ref) for ref in owner.refs]]
            for owner in found.owners
        ],
        "defines": _encode_definitions(found.defines),
    }


def decode_file_references(data: dict) -> FileReferences:
    """The :class:`FileReferences` that :func:`encode_file_references` was given."""

    owners = [
        ScriptOwner(kind, owner_id, scope, line, [ScriptRef(*row) for row in refs])
        for kind, owner_id, scope, line, refs in data["owners"]
    ]
    return FileReferences(
        str(data["path"]),
        str(data["relative"]),
        bool(data["own"]),
        owners,
        _decode_definitions(data["defines"]),
    )
