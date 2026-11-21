"""Find what a piece of script refers to, read from the syntax tree.

Every check that asks "which ideas, events, flags, technologies, equipment,
focus trees, country tags or states does this script name?" reads the answer
here. The walk follows the lossless tree of ``hoi4.syntax``, so a comment or a
quoted string can never look like a reference. It also keeps track of the
scope each statement runs in: ``123 = { add_core_of = TAG }`` and
``every_owned_state = { add_core_of = TAG }`` run in a state, a bare
``add_core_of`` in a focus reward runs in the country.

A scope is only named when it is certain. Inside a key the walk does not know
(a scripted effect with arguments, ``var:x``, ``FROM``) the scope is
``UNKNOWN`` and ``certain`` is false, so checks stay quiet there.

A file is read once into a :class:`FileReferences`: its owners (a focus, an
event, a decision, a scripted effect, ...) with their references, and what the
file defines (events, ideas, technologies, ...). Nothing in it keeps the
syntax tree alive, so the whole game can be indexed at once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Iterable, Iterator

from .syntax import Assignment, Block, Document, Scalar, Tagged, parse

COUNTRY = "COUNTRY"
STATE = "STATE"
CHARACTER = "CHARACTER"
UNKNOWN = "UNKNOWN"

# Statements inside these blocks run in the scope around them.
_CONTROL_KEYS = frozenset(
    {
        "if", "else_if", "else", "limit", "hidden_effect", "hidden_trigger",
        "and", "or", "not", "nor", "nand", "custom_trigger_tooltip",
        "custom_effect_tooltip", "effect_tooltip", "random", "random_list",
        "while_loop_effect", "for_loop_effect", "for_each_loop", "count_triggers",
        "trigger", "immediate", "option", "completion_reward", "select_effect",
        "remove_effect", "complete_effect", "timeout_effect", "cancel_effect",
        "available", "visible", "bypass", "allow_branch", "allowed", "activation",
        "cancel_trigger", "remove_trigger", "target_trigger", "custom_cost_trigger",
        "on_add", "on_remove", "cancel", "effect", "mean_time_to_happen",
        "modifier", "ai_will_do", "ai_chance", "complete_tooltip", "enable",
        "allowed_civil_war", "visible_when_not_available", "on_complete",
        "on_start", "on_cancel",
    }
)
# Statements inside these blocks only run when a condition holds.
_GUARD_KEYS = frozenset({"if", "else_if", "else", "limit", "random_list", "random"})
_HIDDEN_KEYS = frozenset({"hidden_effect", "hidden_trigger"})
# Blocks whose body is one effect as the player sees it in a tooltip.
_EFFECT_ROOT_KEYS = frozenset(
    {
        "completion_reward", "select_effect", "immediate", "option",
        "complete_effect", "remove_effect", "timeout_effect", "cancel_effect",
        "on_add", "on_remove", "effect",
    }
)
_COUNTRY_SCOPE_KEYS = frozenset(
    {"owner", "controller", "overlord", "faction_leader"}
)
_STATE_SCOPE_KEYS = frozenset({"capital_scope"})
_SCOPE_ALIAS_RE = re.compile(r"^(?:root|from|prev|this)(?:\.[a-z_]+)*$", re.IGNORECASE)
_ITERATOR_RE = re.compile(r"^(?:every|random|any|all)_(.+)$", re.IGNORECASE)
TAG_RE = re.compile(r"^[A-Z][A-Z0-9]{2}$")
DYNAMIC_TAG_RE = re.compile(r"^D[0-9]{2}$")
_NUMBER_RE = re.compile(r"^[0-9]+$")
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
_FLAG_KINDS = ("country", "global", "state", "character", "unit_leader", "mio")
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


def is_country_tag(key: str) -> bool:
    return bool(TAG_RE.match(key) or DYNAMIC_TAG_RE.match(key))


@dataclass(frozen=True, slots=True)
class ScriptRef:
    """One thing a statement names, where it is and the scope it runs in.

    ``kind`` is what is named: ``idea``, ``event``, ``flag``, ``technology``,
    ``equipment``, ``focus_tree``, ``country`` (a tag used as a scope),
    ``state`` (a state id used as a scope) or a statement such as
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


@dataclass(frozen=True, slots=True)
class _Context:
    scope: str
    certain: bool
    scope_key: str
    hidden: bool
    guarded: bool
    iterator: str
    root: int
    statements: bool


def child_scope(key: str, scope: str, root_scope: str) -> tuple[str, bool, str]:
    """The scope inside ``key = { ... }``.

    Returns the scope, whether the block holds statements (``False`` for the
    argument block of an effect such as ``start_civil_war = { ... }``) and
    the iterator kind (``"country"`` for ``every_country``).
    """

    lowered = key.lower()
    if lowered in _CONTROL_KEYS:
        return scope, True, ""
    if is_country_tag(key):
        return COUNTRY, True, ""
    if _NUMBER_RE.match(key):
        return STATE, True, ""
    if lowered == "root":
        return root_scope, True, ""
    if _SCOPE_ALIAS_RE.match(key) or ":" in key or "@" in key:
        return UNKNOWN, True, ""
    if lowered in _COUNTRY_SCOPE_KEYS:
        return COUNTRY, True, ""
    if lowered in _STATE_SCOPE_KEYS:
        return STATE, True, ""
    match = _ITERATOR_RE.match(lowered)
    if match:
        rest = match.group(1)
        if "state" in rest:
            return STATE, True, "state"
        if "countr" in rest:
            return COUNTRY, True, "country"
        if any(word in rest for word in ("character", "leader", "operative", "advisor")):
            return CHARACTER, True, "character"
        return UNKNOWN, True, "other"
    return UNKNOWN, False, ""


def _scalar_of(value: object) -> str | None:
    return value.value if type(value) is Scalar else None


def _list_values(block: Block) -> list[Scalar]:
    return [item for item in block.items if type(item) is Scalar]


def _block_scalar(block: Block, key: str) -> Scalar | None:
    found = block.first(key, fold=True)
    if found is not None and type(found.value) is Scalar:
        return found.value
    return None


class _Walker:
    """Walks one owner's script and records what it names."""

    def __init__(self, doc: Document, owner: ScriptOwner, skip: frozenset[int] = frozenset()) -> None:
        self.doc = doc
        self.owner = owner
        # Blocks another owner of the file already walks, by their start.
        self.skip = skip

    def add(self, kind: str, action: str, value: str, offset: int, key: str, ctx: _Context) -> None:
        if not value:
            return
        line, column = self.doc.line_col(offset)
        self.owner.refs.append(
            ScriptRef(
                kind, action, value, offset, line, column, ctx.scope, key,
                ctx.certain and ctx.statements, ctx.statements, ctx.scope_key, ctx.hidden,
                ctx.guarded, ctx.iterator, ctx.root,
            )
        )

    def run(self, block: Block) -> None:
        scope = self.owner.scope
        self.walk(block, _Context(scope, scope != UNKNOWN, "", False, False, "", block.start, True))

    def walk(self, block: Block, ctx: _Context) -> None:
        root_scope = self.owner.scope
        for item in block.items:
            if type(item) is Block:
                self.walk(item, ctx)
                continue
            if type(item) is not Assignment:
                continue
            value = item.value
            inner = value if type(value) is Block else value.block if type(value) is Tagged else None
            if inner is not None and inner.start in self.skip:
                continue
            key = item.key.value
            lowered = key.lower()
            self.statement(item, key, lowered, ctx)
            if inner is None:
                continue
            root = item.start if lowered in _EFFECT_ROOT_KEYS else ctx.root
            if lowered == "random_list":
                # Its keys are weights, not state ids.
                weighted = _Context(ctx.scope, ctx.certain, ctx.scope_key, ctx.hidden, True,
                                    ctx.iterator, root, ctx.statements)
                for entry in inner.items:
                    if type(entry) is Assignment and type(entry.value) is Block:
                        self.walk(entry.value, weighted)
                continue
            if not ctx.statements:
                # Inside the arguments of an effect nothing has a known scope.
                self.walk(inner, _Context(UNKNOWN, False, "", ctx.hidden, ctx.guarded, "", root, False))
                continue
            scope, statements, iterator = child_scope(key, ctx.scope, root_scope)
            if not statements:
                self.walk(inner, _Context(UNKNOWN, False, "", ctx.hidden, ctx.guarded, "", root, False))
                continue
            control = lowered in _CONTROL_KEYS
            self.walk(
                inner,
                _Context(
                    scope,
                    ctx.certain if control else scope != UNKNOWN,
                    ctx.scope_key if control else key,
                    ctx.hidden or lowered in _HIDDEN_KEYS,
                    ctx.guarded or lowered in _GUARD_KEYS,
                    ctx.iterator if control else iterator,
                    root,
                    True,
                ),
            )

    def statement(self, item: Assignment, key: str, lowered: str, ctx: _Context) -> None:
        value = item.value
        offset = item.start
        scalar = _scalar_of(value)
        block = value if type(value) is Block else None
        if lowered in EVENT_EFFECTS:
            if scalar is not None:
                self.add("event", "fire", scalar, offset, key, ctx)
            elif block is not None:
                found = _block_scalar(block, "id")
                if found is not None:
                    self.add("event", "fire", found.value, found.start, key, ctx)
            return
        if lowered in _BORDER_WAR_EVENT_KEYS and scalar is not None and "." in scalar:
            self.add("event", "fire", scalar, offset, key, ctx)
            return
        if lowered in {"add_ideas", "remove_ideas"}:
            action = "add" if lowered == "add_ideas" else "remove"
            if scalar is not None:
                self.add("idea", action, scalar, offset, key, ctx)
            elif block is not None:
                for entry in _list_values(block):
                    self.add("idea", action, entry.value, entry.start, key, ctx)
            return
        if lowered in {"add_timed_idea", "modify_timed_idea"}:
            action = "add" if lowered == "add_timed_idea" else "has"
            found = _block_scalar(block, "idea") if block is not None else None
            if found is not None:
                self.add("idea", action, found.value, found.start, key, ctx)
            elif scalar is not None:
                self.add("idea", action, scalar, offset, key, ctx)
            return
        if lowered == "swap_ideas":
            self.add("swap_ideas", "use", key, offset, key, ctx)
            if block is not None:
                for field_name, action in (("remove_idea", "remove"), ("add_idea", "add")):
                    found = _block_scalar(block, field_name)
                    if found is not None:
                        self.add("idea", action, found.value, found.start, key, ctx)
            return
        if lowered == "has_idea" and scalar is not None:
            self.add("idea", "has", scalar, offset, key, ctx)
            return
        if lowered == "show_ideas_tooltip" and scalar is not None:
            self.add("idea", "show", scalar, offset, key, ctx)
            return
        if lowered == "ideas" and block is not None and self.owner.kind == "bookmark":
            for entry in _list_values(block):
                self.add("idea", "show", entry.value, entry.start, key, ctx)
            return
        if lowered in FLAG_WRITERS or lowered in FLAG_READERS:
            action = (
                "read" if lowered.startswith("has_")
                else "clear" if lowered.startswith("clr_") else "set"
            )
            if scalar is not None:
                self.add("flag", action, scalar, offset, key, ctx)
            elif block is not None:
                found = _block_scalar(block, "flag")
                if found is not None:
                    self.add("flag", action, found.value, found.start, key, ctx)
            return
        if lowered == "set_technology" and block is not None:
            for tech in block.assignments():
                if tech.key.value.lower() != "popup":
                    self.add("technology", "add", tech.key.value, tech.start, key, ctx)
            return
        if lowered == "has_tech" and scalar is not None:
            self.add("technology", "has", scalar, offset, key, ctx)
            return
        if lowered in {"add_equipment_to_stockpile", "send_equipment"} and block is not None:
            found = _block_scalar(block, "type")
            if found is not None:
                self.add("equipment", "add", found.value, found.start, key, ctx)
            return
        if lowered == "load_focus_tree":
            found = _block_scalar(block, "tree") if block is not None else None
            if found is not None:
                self.add("focus_tree", "load", found.value, found.start, key, ctx)
            elif scalar is not None:
                self.add("focus_tree", "load", scalar, offset, key, ctx)
            return
        statement_kind = _STATEMENT_KEYS.get(lowered)
        if statement_kind == "civil_war":
            # The value lists the fields the block sets, joined by "+".
            present = [
                name for name in ("capital", "states", "ideology")
                if block is not None and block.first(name, fold=True) is not None
            ]
            self.add("civil_war", "use", "+".join(present) or key, offset, key, ctx)
            capital = _block_scalar(block, "capital") if block is not None else None
            if capital is not None:
                self.add("civil_war_capital", "use", capital.value, capital.start, "capital", ctx)
            return
        if statement_kind is not None:
            self.add(statement_kind, "use", scalar or key, offset, key, ctx)
            return
        if lowered in _CONTROL_KEYS:
            return
        if type(value) in (Block, Tagged) and is_country_tag(key):
            self.add("country", "scope", key, offset, key, ctx)
        elif block is not None and _NUMBER_RE.match(key):
            self.add("state", "scope", key, offset, key, ctx)


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


_EVENT_SCOPES = {
    "country_event": COUNTRY,
    "news_event": COUNTRY,
    "state_event": STATE,
    "unit_leader_event": CHARACTER,
    "operative_leader_event": CHARACTER,
}


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


def owner_blocks(doc: Document, relative: str) -> Iterator[tuple[str, str, Block, str]]:
    """Split a file into owners: ``(kind, id, block, scope)``.

    ``relative`` is the path inside the game or mod folder, such as
    ``events/Germany.txt``; it decides how the file is read.
    """

    parts = PurePosixPath(relative).parts
    top = parts[0] if parts else ""
    sub = parts[1] if len(parts) > 2 else ""
    root = doc.root
    if top == "events":
        for item in _nested(root, 1):
            assert item.block is not None
            scope = _EVENT_SCOPES.get(item.key.value.lower())
            if scope is None:
                yield ("event_file", relative, item.block, UNKNOWN)
            else:
                yield ("event", item.block.scalar("id") or "", item.block, scope)
        return
    if top == "history" and sub == "countries":
        yield ("country_history", PurePosixPath(relative).name[:3], root, COUNTRY)
        return
    if top == "history" and sub == "states":
        state = root.block("state")
        state_id = state.scalar("id") if state is not None else None
        yield ("state_history", state_id or PurePosixPath(relative).stem, root, STATE)
        return
    if top != "common":
        yield ("file", relative, root, UNKNOWN)
        return
    if sub == "national_focus":
        for item in _nested(root, 1):
            block = item.block
            assert block is not None
            name = item.key.value.lower()
            if name in {"shared_focus", "joint_focus"}:
                yield ("focus", block.scalar("id") or "", block, COUNTRY)
            elif name == "focus_tree":
                tree_id = block.scalar("id") or ""
                for child in _nested(block, 1):
                    assert child.block is not None
                    if child.key.value.lower() == "focus":
                        yield ("focus", child.block.scalar("id") or "", child.block, COUNTRY)
                    else:
                        yield ("focus_tree", tree_id, child.block, COUNTRY)
            else:
                yield ("file", relative, block, UNKNOWN)
        return
    if sub == "decisions":
        if len(parts) > 3 and parts[2] == "categories":
            for item in _nested(root, 1):
                assert item.block is not None
                yield ("decision_category", item.key.value, item.block, COUNTRY)
            return
        for item in _nested(root, 2):
            assert item.block is not None
            yield ("decision", item.key.value, item.block, COUNTRY)
        return
    if sub == "ideas":
        for item in _nested(root, 3):
            assert item.block is not None
            yield ("idea", item.key.value, item.block, COUNTRY)
        return
    if sub in {"scripted_effects", "scripted_triggers"}:
        kind = "scripted_effect" if sub == "scripted_effects" else "scripted_trigger"
        for item in _nested(root, 1):
            assert item.block is not None
            yield (kind, item.key.value, item.block, UNKNOWN)
        return
    if sub == "on_actions":
        for item in _nested(root, 2):
            assert item.block is not None
            yield ("on_action", item.key.value, item.block, UNKNOWN)
        return
    if sub == "characters":
        for item in _nested(root, 2):
            assert item.block is not None
            yield ("character", item.key.value, item.block, UNKNOWN)
        return
    if sub == "bookmarks":
        for item in _nested(root, 2):
            assert item.block is not None
            yield ("bookmark", item.block.scalar("name") or item.key.value, item.block, UNKNOWN)
        return
    yield ("file", relative, root, UNKNOWN)


def owners_of(doc: Document, relative: str) -> list[ScriptOwner]:
    """Every owner in a file, each with the references of its script."""

    owners: list[ScriptOwner] = []
    walked: set[int] = set()
    for kind, owner_id, block, scope in owner_blocks(doc, relative):
        owner = ScriptOwner(kind, owner_id, scope, doc.line_col(block.start)[0])
        _Walker(doc, owner).run(block)
        if kind == "on_action":
            _on_action_events(doc, owner, block)
        owners.append(owner)
        walked.add(block.start)
    if doc.root.start not in walked:
        # Whatever lies outside every owner, such as a statement at the top
        # of an events file, still counts as a use.
        rest = ScriptOwner("file", relative, UNKNOWN, 1)
        _Walker(doc, rest, frozenset(walked)).run(doc.root)
        if rest.refs:
            owners.append(rest)
    return owners


def fragment_owner(text: str, kind: str, owner_id: str, scope: str = COUNTRY) -> ScriptOwner:
    """The references of a loose piece of script, such as one effect."""

    doc = parse(text)
    owner = ScriptOwner(kind, owner_id, scope, 1)
    _Walker(doc, owner).run(doc.root)
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


def file_references(path: str, relative: str, text: str, *, own: bool) -> FileReferences:
    """Read one file. ``relative`` is its path inside the game or mod folder."""

    doc = parse(text)
    return FileReferences(path, relative, own, owners_of(doc, relative), definitions_of(doc, relative))
