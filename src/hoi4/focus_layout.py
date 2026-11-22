"""Static layout of local focus models, without changing serialized offsets."""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field

from .syntax import Block, parse
from .types import Focus, FocusTree


@dataclass
class FocusPositions:
    positions: dict[str, tuple[int, int]] = field(default_factory=dict)
    unresolved: dict[str, str] = field(default_factory=dict)

    def require_complete(self) -> None:
        if self.unresolved:
            raise ValueError(
                "Cannot resolve focus positions: "
                + "; ".join(f"{focus_id}: {reason}" for focus_id, reason in self.unresolved.items())
            )


def resolve_focus_positions(tree: FocusTree) -> FocusPositions:
    """Resolve local anchor chains iteratively; absent/shared anchors stay unknown."""
    result = FocusPositions()
    focuses = {focus.id: focus for focus in tree.focuses}
    for focus in tree.focuses:
        if focus.id in result.positions or focus.id in result.unresolved:
            continue
        chain: list[str] = []
        indices: dict[str, int] = {}
        current = focus.id
        reason = ""
        while current not in result.positions:
            if current in result.unresolved:
                reason = result.unresolved[current]
                break
            if current in indices:
                cycle = chain[indices[current] :] + [current]
                reason = "relative_position_id cycle: " + " -> ".join(cycle)
                break
            if current not in focuses:
                reason = f"relative_position_id anchor '{current}' is not available in this tree"
                break
            indices[current] = len(chain)
            chain.append(current)
            node = focuses[current]
            if not node.relative_position_id:
                result.positions[current] = (node.x, node.y)
                chain.pop()
                break
            current = node.relative_position_id
        for focus_id in reversed(chain):
            if reason:
                result.unresolved[focus_id] = reason
            else:
                node = focuses[focus_id]
                x, y = result.positions[node.relative_position_id]
                result.positions[focus_id] = (x + node.x, y + node.y)
    return result


def _moves(value: str | None) -> bool:
    """Whether an ``x`` or ``y`` of an ``offset`` block is a real step.

    A value that is not a plain number (a variable) may be one.
    """

    if value is None:
        return False
    try:
        return float(value) != 0
    except ValueError:
        return True


_COMMENT_RE = re.compile(r'("[^"\n]*")|#[^\n]*')
_OPERATOR_RE = re.compile(r"\s*([={}<>])\s*")


def _condition(text: str) -> str:
    """An ``allow_branch`` body as one line, or ``""`` when it always holds.

    No comments, one space between tokens, names in front of ``=``, ``<``
    and ``>`` in lower case (the game reads them without regard to case),
    and no quotes around a single word (``has_dlc = "X"`` is
    ``has_dlc = X``). An empty body and ``always = yes`` hide nothing.
    """

    text = _COMMENT_RE.sub(lambda match: match.group(1) or "", text)
    tokens = _OPERATOR_RE.sub(r" \1 ", text).split()
    for index, token in enumerate(tokens):
        if len(token) > 2 and token[0] == token[-1] == '"' and '"' not in token[1:-1]:
            tokens[index] = token[1:-1]
        elif index + 1 < len(tokens) and tokens[index + 1] in ("=", "<", ">"):
            tokens[index] = token.lower()
    condition = " ".join(tokens)
    return "" if condition.lower() == "always = yes" else condition


def allow_branch_condition(focus: Focus) -> str | None:
    """The ``allow_branch`` condition of a focus, as ``save()`` will write it.

    ``None``: the focus has no ``allow_branch`` block. ``""``: it has one that
    always holds. An empty block still counts: with it the focus checks only
    itself, so it stays visible when its prerequisites are hidden.

    ``focus.allow_branch`` is what the reader took from the first block
    spelled ``allow_branch``, or what the caller set. The file may hold more
    than that: an empty block, or one spelled in another case
    (``ALLOW_BRANCH``, which the game reads all the same and ``save()`` keeps).
    """

    if focus.allow_branch.strip():
        return _condition(focus.allow_branch)
    if "allow_branch" not in focus.raw_block.lower():
        return None
    found: str | None = None
    read = False
    for item in parse(focus.raw_block).root.assignments("allow_branch", fold=True):
        block = item.block
        if block is None:
            continue
        body = block.body.strip()
        if item.name == "allow_branch" and not read:
            read = True
            if body:
                # The reader put this body into ``focus.allow_branch``, which
                # is empty now: the block was cleared through the API, and
                # ``save()`` removes it.
                continue
        if found is None:
            found = _condition(body)
    return found


def block_allow_branch_condition(block: Block) -> str | None:
    """The ``allow_branch`` condition of a focus block as it stands in a file.

    ``None`` without such a block, ``""`` when it always holds. For the
    shared focuses of other files, which are read from their text.
    """

    for item in block.assignments("allow_branch", fold=True):
        if item.block is not None:
            return _condition(item.block.body)
    return None


class FocusBranches:
    """Which focuses of one tree the game can show at the same time.

    The game draws every focus of a tree, both sides of a mutually exclusive
    choice included: the player sees both and picks one. Only two things keep
    a focus off the spot where its ``x``, ``y`` and ``relative_position_id``
    put it:

    - ``allow_branch``. The HOI4 wiki (National focus modding) puts it this
      way: "By default, a focus will also be disallowed if either of the
      parent focuses (as set by prerequisites) is disallowed. However, a
      focus containing allow_branch within its definition will only check
      its own allowed status, still showing up if it has disallowed
      parents." So a focus with ``allow_branch`` is drawn exactly when its
      own condition holds, and a focus without one is drawn when all of its
      prerequisite focuses are.
    - An ``offset = { x = ... y = ... trigger = { ... } }`` block moves the
      focus while its trigger holds, and with it every focus that is placed
      relative to it (``relative_position_id``, directly or through others).

    So two focuses may share a spot when an ``offset`` block moves one of
    them and not the other and ``allow_branch`` can hide at least one of
    them, or when each of them needs an ``allow_branch`` condition the other
    does not need (see :meth:`needs`; a condition is its text, so two focuses
    that each write ``allow_branch = { has_dlc = "X" }`` appear together).
    The game's own trees use both forms for variant branches: Ethiopia's
    Italian branch steps aside by an ``offset`` on its first focus when the
    exile branch appears. Two focuses behind the same condition are shown
    together when it holds, two focuses moved by the same ``offset`` blocks
    move together, and two focuses that are both always drawn meet whenever
    the trigger of the offset does not hold.

    A prerequisite may be a shared focus of another file, which the tree
    pulls in with ``shared_focus = ID``. ``shared_parents`` (the
    prerequisites of each shared focus, by id) and ``shared_conditions``
    (its ``allow_branch`` condition: ``None`` without one, ``""`` when it
    always holds) say what such a focus hangs on, so that a focus below it
    is hidden with it. Without them a prerequisite outside the tree counts
    as always drawn.
    """

    def __init__(
        self,
        tree: FocusTree,
        *,
        shared_parents: Mapping[str, Collection[str]] | None = None,
        shared_conditions: Mapping[str, str | None] | None = None,
    ) -> None:
        self._by_id = {focus.id: focus for focus in tree.focuses if focus.id}
        self._shared_parents = shared_parents or {}
        self._shared_conditions = shared_conditions or {}
        self._needs: dict[str, frozenset[str]] = {}
        self._offsets: dict[str, bool] = {}
        self._movers: dict[str, frozenset[str]] = {}

    def _parents(self, focus_id: str) -> list[str]:
        focus = self._by_id.get(focus_id)
        if focus is None:
            return list(self._shared_parents.get(focus_id, ()))
        return [ref for group in focus.prerequisites for ref in group]

    def _post_order(self, focus_id: str) -> list[str]:
        """``focus_id`` and every focus it depends on, each after its prerequisites.

        Iterative, so a long chain does not hit the recursion limit. A cycle
        is cut where it closes.
        """

        order: list[str] = []
        state: dict[str, bool] = {}
        stack: list[tuple[str, bool]] = [(focus_id, False)]
        while stack:
            current, expanded = stack.pop()
            if expanded:
                state[current] = True
                order.append(current)
                continue
            if current in state:
                continue
            state[current] = False
            stack.append((current, True))
            stack.extend((ref, False) for ref in self._parents(current) if ref not in state)
        return order

    def needs(self, focus_id: str) -> frozenset[str]:
        """The ``allow_branch`` conditions that all have to hold for ``focus_id`` to be drawn.

        A focus with ``allow_branch`` needs its own condition and nothing
        else: the game shows it also when its prerequisites are hidden. A
        focus without one needs what each of its prerequisite focuses needs,
        whichever ``prerequisite`` block names them: it is hidden as soon as
        one of them is. The focus is drawn exactly when all of these hold. A
        condition is its text without comments and spacing, so the same
        condition on two focuses is one condition.
        """

        for current in self._post_order(focus_id):
            if current in self._needs:
                continue
            focus = self._by_id.get(current)
            found: set[str] = set()
            own = (
                allow_branch_condition(focus)
                if focus is not None
                else self._shared_conditions.get(current)
            )
            if own is not None:
                if own:
                    found.add(own)
            else:
                for ref in self._parents(current):
                    found |= self._needs.get(ref, frozenset())
            self._needs[current] = frozenset(found)
        return self._needs[focus_id]

    def conditional(self, focus_id: str) -> bool:
        """Whether ``allow_branch`` can hide ``focus_id``: it needs a condition."""

        return bool(self.needs(focus_id))

    def _has_offset(self, focus_id: str) -> bool:
        """Whether an ``offset`` block of ``focus_id`` moves it at all."""

        cached = self._offsets.get(focus_id)
        if cached is None:
            focus = self._by_id.get(focus_id)
            cached = False
            if focus is not None and "offset" in focus.raw_block.lower():
                cached = any(
                    _moves(block.scalar("x", fold=True)) or _moves(block.scalar("y", fold=True))
                    for block in parse(focus.raw_block).root.blocks("offset", fold=True)
                )
            self._offsets[focus_id] = cached
        return cached

    def movers(self, focus_id: str) -> frozenset[str]:
        """The focuses whose ``offset`` block moves ``focus_id`` on a trigger.

        That is the focus itself and every anchor up its chain of
        ``relative_position_id``: a focus placed relative to another moves
        with it.
        """

        cached = self._movers.get(focus_id)
        if cached is None:
            found: set[str] = set()
            seen: set[str] = set()
            current = focus_id
            while current in self._by_id and current not in seen:
                seen.add(current)
                if self._has_offset(current):
                    found.add(current)
                current = self._by_id[current].relative_position_id or ""
            cached = frozenset(found)
            self._movers[focus_id] = cached
        return cached

    def never_shown_together(self, first: str, second: str) -> bool:
        """Whether two focuses on one spot are never drawn there at the same time."""

        # An offset steps aside for something that is not always there. Two
        # focuses that are both always drawn meet whenever the trigger of
        # the offset does not hold.
        if self.movers(first) != self.movers(second) and (
            self.conditional(first) or self.conditional(second)
        ):
            return True
        # Each has to need a condition the other does not need. A focus whose
        # conditions the other needs as well is drawn whenever the other is.
        first_needs, second_needs = self.needs(first), self.needs(second)
        return bool(first_needs - second_needs) and bool(second_needs - first_needs)


def continuous_focus_position_issue(
    tree: FocusTree, layout: FocusPositions, *, padding: int = 100
) -> str:
    """Return advice when the continuous focus panel starts above the tree's last row."""

    if tree.kind == "file" or not tree.continuous_focus_position or layout.unresolved:
        return ""
    raw_y = parse(tree.continuous_focus_position).root.scalar("y", fold=True)
    if raw_y is None or not raw_y.lstrip("-").isdigit():
        return ""
    max_y = max((pos[1] for pos in layout.positions.values()), default=0)
    min_y = (max_y + 1) * 100 + padding
    y = int(raw_y)
    if y >= min_y:
        return ""
    return f"continuous_focus_position y={y} is above the recommended minimum y={min_y}"


def visual_overlap_issues(
    tree: FocusTree,
    layout: FocusPositions,
    *,
    min_continuous_padding: int = 100,
    shared_focus_parents: Mapping[str, Collection[str]] | None = None,
    shared_focus_conditions: Mapping[str, str | None] | None = None,
) -> list[str]:
    """Check known positions; continuous bounds require a complete layout.

    Two focuses may share a position when the game never draws both there
    (see :class:`FocusBranches`), which is how variant branches are drawn.
    ``shared_focus_parents`` and ``shared_focus_conditions`` describe the
    shared focuses of other files, for a focus that hangs below one.
    """
    occupied: dict[tuple[int, int], list[str]] = {}
    issues: list[str] = []
    if tree.kind == "file":
        # Top-level shared/joint focuses of one file are placed into different
        # trees, so they do not share one canvas.
        return issues
    branches = FocusBranches(
        tree, shared_parents=shared_focus_parents, shared_conditions=shared_focus_conditions
    )
    for focus in tree.focuses:
        pos = layout.positions.get(focus.id)
        if pos is None:
            continue
        sharing = occupied.setdefault(pos, [])
        clash = next(
            (
                other
                for other in sharing
                if other != focus.id and not branches.never_shown_together(focus.id, other)
            ),
            None,
        )
        if clash is not None:
            issues.append(f"{focus.id} overlaps {clash} at x={pos[0]}, y={pos[1]}")
        sharing.append(focus.id)
    continuous_issue = continuous_focus_position_issue(
        tree, layout, padding=min_continuous_padding
    )
    if continuous_issue:
        issues.append(continuous_issue)
    return issues
