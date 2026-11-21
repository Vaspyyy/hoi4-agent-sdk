"""Walk effect, trigger, modifier and weight blocks the way the game reads them.

The walker reads the lossless syntax tree (:mod:`hoi4.syntax`) and decides,
for every statement of a block, what it is:

* a **command**: an effect, trigger or modifier the game runs. The walker
  yields these as :class:`ScriptStatement` with the scope they run in;
* a **scope change**: ``ROOT``, ``PREV.PREV``, ``FROM.owner``, a country tag,
  a state id, a character id, ``var:x``, ``event_target:x``, an iterator such
  as ``every_owned_state``. Its block is walked in the new scope;
* a **control structure**: ``if``/``else_if``/``else``, ``AND``/``OR``/
  ``NOT``, ``hidden_effect``, ``random_list``, loops and so on. Its block is
  walked, with ``limit`` read as triggers;
* an **argument** of a command or a control structure
  (``country_event = { id = x days = 3 }``, ``random_select_amount = 2``).
  Arguments are data, never read as commands.

Keywords are matched without regard to case, as the game does (``IF``,
``Else_If``, ``OR``, ``ROOT``, ``OVERLORD``).

Scopes are tracked on a stack: ``ROOT`` is the bottom, ``THIS`` the top,
``PREV`` the one below the top (``PREV.PREV`` the one below that), ``FROM``
whatever the source says it is. A scope the walker cannot be sure of is
``None``; the vocabulary check never reports a scope problem for ``None``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Literal, Mapping

from .syntax import Assignment, Block, parse

BlockKind = Literal["effect", "trigger", "modifier", "weight"]
CommandKind = Literal["effect", "trigger", "modifier"]

# ── the language tables ───────────────────────────────────────────

#: Where an iterator leads. Names are folded to lower case. An iterator
#: missing here still works (see ``ITERATOR_PREFIXES``), its scope is just not
#: known for sure.
ITERATOR_SCOPES: Mapping[str, str | None] = {
    **{
        f"{prefix}_{suffix}": "COUNTRY"
        for prefix in ("every", "random", "any", "all", "ordered")
        for suffix in (
            "allied_country",
            "country",
            "country_with_original_tag",
            "country_with_original_tag_of",
            "enemy_country",
            "faction_member",
            "neighbor_country",
            "occupied_country",
            "other_country",
            "other_country_of",
            "other_country_with_original_tag_of",
            "possible_country",
            "subject_country",
            "subject_countries",
            "guaranteed_country",
            "country_of",
            "country_with_core",
            "home_area_neighbor_country",
            "claimant",
        )
    },
    **{
        f"{prefix}_{suffix}": "STATE"
        for prefix in ("every", "random", "any", "all", "ordered")
        for suffix in (
            "controlled_state",
            "core_state",
            "neighbor_state",
            "owned_state",
            "owned_controlled_state",
            "state",
            "state_in",
            "state_of",
        )
    },
    **{
        f"{prefix}_{suffix}": "CHARACTER"
        for prefix in ("every", "random", "any", "all", "ordered")
        for suffix in (
            "active_scientist",
            "army_leader",
            "character",
            "navy_leader",
            "operative",
            "operative_leader",
            "scientist",
            "scientists",
            "unit_leader",
        )
    },
    **{
        f"{prefix}_military_industrial_organization": "INDUSTRIAL_ORG"
        for prefix in ("every", "random", "any", "all", "ordered")
    },
    **{
        f"{prefix}_{suffix}": "PURCHASE_CONTRACT"
        for prefix in ("every", "random", "any", "all", "ordered")
        for suffix in ("purchase_contract", "purchase_contracts")
    },
    # Divisions and collection elements have no documented scope name.
    **{
        f"{prefix}_{suffix}": None
        for prefix in ("every", "random", "any", "all", "ordered")
        for suffix in (
            "country_division",
            "state_division",
            "collection_element",
            "collection_elements",
        )
    },
    "global_every_army_leader": "CHARACTER",
    "party_leader": "CHARACTER",
}

#: Statements that look like iterators but take plain arguments.
NOT_ITERATORS = frozenset(
    {
        "any_province_building_level",
        "any_war_score",
        "any_war_participation",
        "any_claim",
        "random_list",
    }
)

ITERATOR_PREFIXES = ("every_", "random_", "any_", "all_", "ordered_")

#: Arguments every iterator may take (data, never commands); ``limit`` is
#: read as triggers.
ITERATOR_ARGUMENTS = frozenset(
    {
        "tooltip",
        "random_select_amount",
        "display_individual_scopes",
        "include_invisible",
        "prioritize",
        "original_tag_to_check",
        "target",
        "collection",
        "input",
        "count",
        "array",
        "continent",
        "ai_area",
        "strategic_region",
        "order_by",
        "position",
        "min",
        "max",
        "check_range_bounds",
        "visible_when_empty",
    }
)

#: Scope links: the scope a link leads to, whatever scope it starts from.
SCOPE_LINKS: Mapping[str, str | None] = {
    "owner": "COUNTRY",
    "controller": "COUNTRY",
    "overlord": "COUNTRY",
    "faction_leader": "COUNTRY",
    "occupied": "COUNTRY",
    "capital_scope": "STATE",
    "capital": "STATE",
    "character": "CHARACTER",
    "opponent": None,
}

#: Prefixed scopes such as ``var:x``: the scope they lead to.
SCOPE_PREFIXES: Mapping[str, str | None] = {
    "var": None,
    "event_target": None,
    "global_event_target": None,
    "scope": None,
    "mio": "INDUSTRIAL_ORG",
    "sp": "SPECIAL_PROJECT",
    "character": "CHARACTER",
    "state": "STATE",
    "country": "COUNTRY",
}


@dataclass(frozen=True)
class _Control:
    """How a control structure reads its block."""

    #: What the block's statements are: ``"same"`` keeps the current kind.
    body: Literal["same", "effect", "trigger", "weighted"]
    arguments: frozenset[str] = frozenset()
    #: Arguments read as triggers.
    trigger_arguments: frozenset[str] = frozenset()
    #: ``True`` when the block runs in a scope the walker cannot know.
    unknown_scope: bool = False
    #: A scope the block always runs in (``create_dynamic_country``).
    scope: str | None = None
    #: The block runs only when a condition holds (``if``, ``random``).
    guard: bool = False
    #: The block is hidden from the player (``hidden_effect``).
    hidden: bool = False

    def works_in(self, kind: str) -> bool:
        """Whether the game knows this control among statements of ``kind``.

        A control whose block holds triggers is a trigger, one whose block
        holds effects (or weighted effects) is an effect; ``if`` and
        ``custom_override_tooltip`` work in both. This is what the game's
        effect and trigger documentation lists.
        """

        if self.body == "same":
            return True
        return kind == ("effect" if self.body == "weighted" else self.body)


_LIMIT = frozenset({"limit"})

#: Control structures, folded. Their own names are never checked against the
#: vocabulary: the game knows them in every scope.
CONTROL: Mapping[str, _Control] = {
    "if": _Control("same", trigger_arguments=_LIMIT, guard=True),
    "else_if": _Control("same", trigger_arguments=_LIMIT, guard=True),
    # ``else = { limit = { } }`` works like ``else_if``.
    "else": _Control("same", trigger_arguments=_LIMIT, guard=True),
    "and": _Control("trigger"),
    "or": _Control("trigger"),
    "not": _Control("trigger"),
    "hidden_trigger": _Control("trigger", hidden=True),
    "custom_trigger_tooltip": _Control("trigger", frozenset({"tooltip"})),
    "custom_override_tooltip": _Control("same", frozenset({"tooltip", "not_tooltip"})),
    "count_triggers": _Control("trigger", frozenset({"amount"})),
    "hidden_effect": _Control("effect", hidden=True),
    "effect_tooltip": _Control("effect"),
    "random": _Control("effect", frozenset({"chance"}), guard=True),
    "random_list": _Control("weighted", frozenset({"seed", "log"}), guard=True),
    "while_loop_effect": _Control("effect", frozenset({"break"}), _LIMIT),
    "for_loop_effect": _Control(
        "effect", frozenset({"start", "end", "compare_type", "add", "value", "break"})
    ),
    "for_each_loop": _Control("effect", frozenset({"array", "value", "index", "break"})),
    "for_each_scope_loop": _Control(
        "effect", frozenset({"array", "break", "tooltip"}), unknown_scope=True
    ),
    "random_scope_in_array": _Control(
        "effect", frozenset({"array", "break"}), _LIMIT, unknown_scope=True
    ),
    "any_of": _Control("trigger", frozenset({"array", "value", "index"})),
    "all_of": _Control("trigger", frozenset({"array", "value", "index"})),
    "any_of_scopes": _Control("trigger", frozenset({"array", "tooltip"}), unknown_scope=True),
    "all_of_scopes": _Control("trigger", frozenset({"array", "tooltip"}), unknown_scope=True),
    "create_dynamic_country": _Control(
        "effect", frozenset({"original_tag", "copy_tag"}), scope="COUNTRY"
    ),
}

#: Controls whose own name is also checked as a command (documented with scopes).
CHECKED_CONTROLS = frozenset({"create_dynamic_country"})

#: Arguments of a weight block (``ai_will_do``, ``mean_time_to_happen``,
#: ``ai_chance``) and of its ``modifier = { }`` entries.
WEIGHT_ARGUMENTS = frozenset({"factor", "base", "add", "days", "months", "years", "weeks", "hours"})
WEIGHT_MODIFIER_ARGUMENTS = frozenset({"factor", "add", "base"})

#: Keys of a modifier block that are not modifiers (``icon`` and
#: ``attacker_modifier`` belong to a dynamic modifier's own block).
#: ``hidden_modifier`` holds modifiers of its own.
MODIFIER_ARGUMENTS = frozenset({"custom_modifier_tooltip", "icon", "attacker_modifier"})

#: Keywords that look like country tags.
_KEYWORDS = frozenset({"AND", "NOT", "ALL", "ANY"})
_TAG_RE = re.compile(r"^[A-Z][A-Z0-9]{2}$")
_NUMBER_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


# ── where each kind of block starts ───────────────────────────────

#: The scope each block of a definition runs in, when it is certain.
#: ``(definition, field) -> scope``; a field that is missing is unknown.
ENTRY_SCOPES: Mapping[tuple[str, str], str | None] = {
    ("focus", "completion_reward"): "COUNTRY",
    ("focus", "select_effect"): "COUNTRY",
    # Only shown as a tooltip, never run.
    ("focus", "complete_tooltip"): None,
    ("focus", "available"): "COUNTRY",
    ("focus", "bypass"): "COUNTRY",
    ("focus", "allow_branch"): "COUNTRY",
    ("focus", "cancel"): "COUNTRY",
    ("focus", "ai_will_do"): "COUNTRY",
    ("country_event", "*"): "COUNTRY",
    ("news_event", "*"): "COUNTRY",
    ("state_event", "*"): "STATE",
    # These fire for the leader's owner; which scope THIS is varies.
    ("unit_leader_event", "*"): None,
    ("operative_leader_event", "*"): None,
    ("decision", "*"): "COUNTRY",
    ("idea", "allowed"): "COUNTRY",
    ("idea", "allowed_civil_war"): "COUNTRY",
    ("idea", "available"): "COUNTRY",
    ("idea", "visible"): "COUNTRY",
    ("idea", "cancel"): "COUNTRY",
    ("idea", "on_add"): "COUNTRY",
    ("idea", "on_remove"): "COUNTRY",
    ("idea", "ai_will_do"): "COUNTRY",
    ("bookmark", "effect"): None,
    ("bookmark", "available"): "COUNTRY",
}


def entry_scope(definition: str, field: str) -> str | None:
    """The scope a definition's block runs in, or ``None`` when it is not certain."""

    if (definition, field) in ENTRY_SCOPES:
        return ENTRY_SCOPES[(definition, field)]
    return ENTRY_SCOPES.get((definition, "*"))


# ── the walker ────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScriptStatement:
    """One command of a block: its name as written, kind, line and scope."""

    name: str
    kind: CommandKind
    line: int
    column: int
    #: The scope the command runs in, or ``None`` when that is not certain.
    scope: str | None


IsCommand = Callable[[CommandKind, str], bool]

#: What a statement is, for a ``visit`` callback of :func:`walk_script`:
#: a command, a control structure, an iterator, a scope change, or an
#: argument (data inside a command's block, or a control's own argument).
Role = Literal["command", "control", "iterator", "scope", "argument"]


class WalkState:
    """What surrounds the statement a ``visit`` callback is given.

    ``scope_key`` is the key that made the current scope (``GER`` in
    ``GER = { ... }``, ``every_owned_state``), ``iterator`` the scope an
    enclosing iterator leads to in lower case (``"state"``; ``"other"`` when
    the table does not know; ``""`` outside an iterator or after a later
    scope change). ``hidden`` is true inside ``hidden_effect`` and
    ``hidden_trigger``, ``guarded`` inside a condition (``if``, ``limit``,
    ``random``, ``random_list``).
    """

    __slots__ = ("scope_key", "iterator", "hidden", "guarded")

    def __init__(self) -> None:
        self.scope_key = ""
        self.iterator = ""
        self.hidden = False
        self.guarded = False


#: ``visit(item, role, kind, scope, state)``: ``kind`` is the kind of block the
#: statement stands in (``None`` for data), ``scope`` the scope it runs in
#: (``None`` when not certain).
Visit = Callable[[Assignment, Role, "BlockKind | None", "str | None", WalkState], None]


def walk_script(
    text: str | Block,
    kind: BlockKind,
    *,
    scope: str | None = None,
    from_scope: str | None = None,
    is_command: IsCommand = lambda kind, name: True,
    characters: Collection[str] = (),
    arguments: Collection[str] = (),
    visit: Visit | None = None,
) -> list[ScriptStatement]:
    """Return every command of a block body, in source order.

    ``text`` is a block body (or a :class:`Block` of a parsed file, whose lines
    are then file lines). ``kind`` says what the block holds. ``scope`` is the
    scope it runs in when that is certain. ``is_command(kind, folded_name)``
    tells the walker which names the game knows; it uses that to tell an
    iterator from an argument-taking command that only looks like one.
    ``characters`` are character ids in lower case, which may stand as scopes.
    ``arguments`` are keys of the block itself to leave alone (folded), such
    as ``name`` and ``ai_chance`` of an event option.

    ``visit``, when given, is called for every assignment the walk passes
    (commands, control structures, iterators, scope changes and, as
    ``"argument"``, everything inside a command's block), except the
    ``arguments`` above and the weights of ``random_list``. The reference
    reader of :mod:`hoi4.script_references` uses it.
    """

    block = parse(text).root if isinstance(text, str) else text
    walker = _Walker(block, is_command, characters, from_scope, visit)
    walker.block(block, kind, [scope], frozenset(arguments))
    return walker.found


def walk_data(block: Block, visit: Visit, *, skip: Collection[int] = ()) -> None:
    """Pass every assignment of a block that holds no script to ``visit`` as data.

    Blocks whose start offset is in ``skip`` are left out (they are walked
    as script on their own).
    """

    walker = _Walker(block, lambda kind, name: True, (), None, visit)
    walker.data(block, frozenset(skip))


class _Walker:
    def __init__(
        self,
        root: Block,
        is_command: IsCommand,
        characters: Collection[str],
        from_scope: str | None,
        visit: Visit | None = None,
    ) -> None:
        self.doc = root.doc
        self.is_command = is_command
        self.characters = characters
        self.from_scope = from_scope
        self.visit = visit
        self.state = WalkState()
        self.found: list[ScriptStatement] = []

    # Each method gets the scope stack; the top is THIS, the bottom ROOT.

    def emit(self, item: Assignment, kind: CommandKind, stack: list[str | None]) -> None:
        line, column = self.doc.line_col(item.key.start)
        self.found.append(ScriptStatement(item.key.value, kind, line, column, stack[-1]))

    def note(
        self, item: Assignment, role: Role, kind: BlockKind | None, scope: str | None
    ) -> None:
        if self.visit is not None:
            self.visit(item, role, kind, scope, self.state)

    def enter(
        self,
        *,
        scope_key: str | None = None,
        iterator: str | None = None,
        hidden: bool = False,
        guarded: bool = False,
    ) -> tuple[str, str, bool, bool]:
        """Change what surrounds the next statements; :meth:`leave` undoes it."""

        state = self.state
        saved = (state.scope_key, state.iterator, state.hidden, state.guarded)
        if scope_key is not None:
            state.scope_key = scope_key
        if iterator is not None:
            state.iterator = iterator
        state.hidden = state.hidden or hidden
        state.guarded = state.guarded or guarded
        return saved

    def leave(self, saved: tuple[str, str, bool, bool]) -> None:
        state = self.state
        state.scope_key, state.iterator, state.hidden, state.guarded = saved

    def data(self, block: Block, skip: Collection[int] = ()) -> None:
        """Pass everything inside ``block`` to ``visit`` as data."""

        if self.visit is None:
            return
        saved = self.enter(scope_key="", iterator="")
        self._data(block, skip)
        self.leave(saved)

    def _data(self, block: Block, skip: Collection[int]) -> None:
        visit = self.visit
        assert visit is not None
        for item in block.items:
            if type(item) is Assignment:
                inner = item.block
                if inner is not None and inner.start in skip:
                    continue
                visit(item, "argument", None, None, self.state)
                if inner is not None:
                    self._data(inner, skip)
            elif type(item) is Block and item.start not in skip:
                self._data(item, skip)

    def argument(self, item: Assignment) -> None:
        """An argument of a control structure, an iterator or a weight block."""

        if self.visit is not None:
            self.note(item, "argument", None, None)
            inner = item.block
            if inner is not None:
                self.data(inner)

    def block(
        self,
        block: Block,
        kind: BlockKind,
        stack: list[str | None],
        skip: Collection[str] = (),
    ) -> None:
        """Walk a block; keys in ``skip`` (folded) are arguments of its owner."""

        if kind == "modifier":
            self.modifiers(block, stack, skip)
        elif kind == "weight":
            self.weight(block, stack, skip)
        else:
            self.arguments_and_body(block, kind, stack, (), (), skip)

    def statement(self, item: Assignment, kind: Literal["effect", "trigger"], stack: list[str | None]) -> None:
        key = item.key.value
        folded = key.lower()
        body = item.block
        control = CONTROL.get(folded)
        if control is not None and body is not None:
            if not control.works_in(kind):
                # ``OR = { }`` among effects, ``hidden_effect`` among triggers:
                # the game does not know the name there, so its block is data.
                self.emit(item, kind, stack)
                self.note(item, "command", kind, stack[-1])
                self.data(body)
                return
            if folded in CHECKED_CONTROLS:
                self.emit(item, kind, stack)
            self.note(item, "control", kind, stack[-1])
            saved = self.enter(hidden=control.hidden, guarded=control.guard)
            self.control(body, control, kind, stack)
            self.leave(saved)
            return
        if body is not None and self.is_iterator(folded, kind):
            self.emit(item, kind, stack)
            if not self.is_command(kind, folded):
                # ``any_owned_state`` among effects: report the iterator only.
                self.note(item, "command", kind, stack[-1])
                self.data(body)
                return
            target = ITERATOR_SCOPES.get(folded)
            self.note(item, "iterator", kind, stack[-1])
            saved = self.enter(scope_key=key, iterator=(target or "other").lower())
            self.arguments_and_body(
                body, kind, [*stack, target], ITERATOR_ARGUMENTS, _LIMIT
            )
            self.leave(saved)
            return
        resolved = self.resolve_scope(key, stack)
        if resolved is not _NOT_A_SCOPE:
            if body is not None:
                self.note(item, "scope", kind, stack[-1])
                saved = self.enter(scope_key=key, iterator="")
                self.block(body, kind, [*stack, resolved])  # type: ignore[list-item]
                self.leave(saved)
                return
            if kind == "trigger" and not self.is_command(kind, folded):
                # ``ROOT = PREV`` and the like compare scopes in a trigger;
                # among effects ``GER = 5`` does nothing and is reported.
                self.note(item, "scope", kind, stack[-1])
                return
        self.emit(item, kind, stack)
        self.note(item, "command", kind, stack[-1])
        if body is not None:
            self.data(body)

    def is_iterator(self, folded: str, kind: CommandKind) -> bool:
        if folded in NOT_ITERATORS:
            return False
        if folded in ITERATOR_SCOPES:
            return True
        return folded.startswith(ITERATOR_PREFIXES) and self.is_command(kind, folded)

    def control(
        self,
        body: Block,
        control: _Control,
        kind: Literal["effect", "trigger"],
        stack: list[str | None],
    ) -> None:
        if control.unknown_scope:
            stack = [*stack, None]
        elif control.scope is not None:
            stack = [*stack, control.scope]
        if control.body == "weighted":
            self.weighted(body, control.arguments, kind, stack)
            return
        inner: Literal["effect", "trigger"] = kind if control.body == "same" else control.body  # type: ignore[assignment]
        self.arguments_and_body(
            body, inner, stack, control.arguments, control.trigger_arguments
        )

    def arguments_and_body(
        self,
        body: Block,
        kind: Literal["effect", "trigger"],
        stack: list[str | None],
        arguments: Collection[str],
        trigger_arguments: Collection[str],
        skip: Collection[str] = (),
    ) -> None:
        for item in body.items:
            if type(item) is not Assignment:
                continue
            folded = item.key.value.lower()
            if folded in skip:
                continue
            if folded in trigger_arguments:
                inner = item.block
                self.note(item, "control", "trigger", stack[-1])
                if inner is not None:
                    saved = self.enter(guarded=True)
                    self.block(inner, "trigger", stack)
                    self.leave(saved)
                continue
            if folded in arguments:
                self.argument(item)
                continue
            self.statement(item, kind, stack)

    def weighted(
        self,
        body: Block,
        arguments: Collection[str],
        kind: Literal["effect", "trigger"],
        stack: list[str | None],
    ) -> None:
        """``random_list = { 10 = { modifier = { factor = 2 <triggers> } <effects> } }``."""

        for item in body.items:
            if type(item) is not Assignment:
                continue
            if item.key.value.lower() in arguments:
                self.argument(item)
                continue
            entry = item.block
            if entry is None:
                continue
            for statement in entry.items:
                if type(statement) is not Assignment:
                    continue
                if statement.key.value.lower() == "modifier" and statement.block is not None:
                    self.note(statement, "control", "trigger", stack[-1])
                    self.arguments_and_body(
                        statement.block, "trigger", stack, WEIGHT_MODIFIER_ARGUMENTS, ()
                    )
                    continue
                self.statement(statement, kind, stack)

    def weight(self, block: Block, stack: list[str | None], skip: Collection[str] = ()) -> None:
        """``ai_will_do = { factor = 1 modifier = { factor = 0 <triggers> } }``."""

        for item in block.items:
            if type(item) is not Assignment:
                continue
            folded = item.key.value.lower()
            if folded in skip:
                continue
            if folded in WEIGHT_ARGUMENTS:
                self.argument(item)
                continue
            if folded == "modifier" and item.block is not None:
                self.note(item, "control", "trigger", stack[-1])
                self.arguments_and_body(
                    item.block, "trigger", stack, WEIGHT_MODIFIER_ARGUMENTS, ()
                )
                continue
            self.statement(item, "trigger", stack)

    def modifiers(self, block: Block, stack: list[str | None], skip: Collection[str] = ()) -> None:
        for item in block.items:
            if type(item) is not Assignment:
                continue
            folded = item.key.value.lower()
            if folded in skip:
                continue
            if folded in MODIFIER_ARGUMENTS:
                self.argument(item)
                continue
            inner = item.block
            if folded == "hidden_modifier" and inner is not None:
                self.note(item, "control", "modifier", None)
                self.modifiers(inner, stack)
                continue
            if inner is not None:
                self.argument(item)
                continue
            line, column = self.doc.line_col(item.key.start)
            self.found.append(ScriptStatement(item.key.value, "modifier", line, column, None))
            self.note(item, "command", "modifier", None)

    def resolve_scope(self, key: str, stack: list[str | None]) -> object:
        """The scope a key leads to, ``None`` when unknown, ``_NOT_A_SCOPE`` if it is no scope."""

        if _NUMBER_RE.match(key):
            return "STATE" if key.isdigit() else _NOT_A_SCOPE
        parts = key.split(".")
        if any(not part for part in parts):
            return _NOT_A_SCOPE
        depth = len(stack) - 1  # index of THIS
        current: str | None = stack[-1]
        used_from = 0
        typed = False
        for position, part in enumerate(parts):
            folded = part.lower()
            if folded == "root":
                depth, current = 0, stack[0]
            elif folded == "this":
                current = stack[depth] if depth >= 0 else None
            elif folded == "prev":
                depth -= 1
                current = stack[depth] if depth >= 0 else None
            elif folded == "from":
                current = self.from_scope if used_from == 0 else None
                used_from += 1
                depth = -1
            elif folded in SCOPE_LINKS:
                current = SCOPE_LINKS[folded]
                depth = -1
            elif ":" in part:
                prefix, _, name = part.partition(":")
                prefix = prefix.lower()
                if prefix not in SCOPE_PREFIXES:
                    return _NOT_A_SCOPE
                target = SCOPE_PREFIXES[prefix]
                if target is None or ":" in name:
                    # ``var:ETH.host_nation``: the rest of the key names the variable.
                    return None
                # ``state:123.owner``: the links behind a typed prefix still count.
                current, depth, typed = target, -1, True
            elif _TAG_RE.match(part) and part not in _KEYWORDS:
                current = "COUNTRY"
                depth = -1
            elif part.isdigit() and position == 0:
                current = "STATE"
                depth = -1
            elif folded in self.characters:
                current = "CHARACTER"
                depth = -1
            elif typed:
                # A word behind ``state:123`` that is no link may name a variable.
                return None
            else:
                return _NOT_A_SCOPE
        return current


_NOT_A_SCOPE = object()


__all__ = [
    "CONTROL",
    "ENTRY_SCOPES",
    "ITERATOR_ARGUMENTS",
    "ITERATOR_SCOPES",
    "SCOPE_LINKS",
    "SCOPE_PREFIXES",
    "ScriptStatement",
    "entry_scope",
    "walk_script",
]
