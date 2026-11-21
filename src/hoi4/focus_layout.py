"""Static layout of local focus models, without changing serialized offsets."""

from __future__ import annotations

from dataclasses import dataclass, field

from .syntax import parse
from .types import FocusTree


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


class FocusBranches:
    """Which focuses of one tree the game can show at the same time.

    The game draws every focus of a tree, both sides of a mutually exclusive
    choice included: the player sees both and picks one. Only two things keep
    a focus off the spot where its ``x``, ``y`` and ``relative_position_id``
    put it:

    - ``allow_branch``: when it fails, the focus is hidden, and so is every
      focus that can only be reached through hidden focuses.
    - An ``offset = { x = ... y = ... trigger = { ... } }`` block moves the
      focus while its trigger holds, and with it every focus that is placed
      relative to it (``relative_position_id``, directly or through others).

    So two focuses may share a spot when an ``offset`` block moves one of
    them and not the other and ``allow_branch`` can hide at least one of
    them, or when each of them is hidden behind an ``allow_branch`` condition
    the other does not depend on (a condition is its text: two focuses that
    each write ``allow_branch = { has_dlc = "X" }`` appear together). The
    game's own trees use both forms for
    variant branches: Ethiopia's Italian branch steps aside by an ``offset``
    on its first focus when the exile branch appears. Two focuses behind the
    same condition are shown together when it holds, two focuses moved by
    the same ``offset`` blocks move together, and two focuses that are both
    always drawn meet whenever the trigger of the offset does not hold.
    """

    def __init__(self, tree: FocusTree) -> None:
        self._by_id = {focus.id: focus for focus in tree.focuses if focus.id}
        self._gates: dict[str, frozenset[str]] = {}
        self._hidden: dict[str, bool] = {}
        self._offsets: dict[str, bool] = {}
        self._movers: dict[str, frozenset[str]] = {}

    def _parents(self, focus_id: str) -> list[str]:
        focus = self._by_id.get(focus_id)
        if focus is None:
            return []
        return [ref for group in focus.prerequisites for ref in group]

    def _has_gate(self, focus_id: str) -> bool:
        focus = self._by_id.get(focus_id)
        return focus is not None and bool(focus.allow_branch.strip())

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

    def gates(self, focus_id: str) -> frozenset[str]:
        """The focuses with ``allow_branch`` that ``focus_id`` depends on, itself included.

        Every prerequisite counts, also each focus of an "either" group.
        """

        for current in self._post_order(focus_id):
            if current in self._gates:
                continue
            found: set[str] = {current} if self._has_gate(current) else set()
            for ref in self._parents(current):
                found |= self._gates.get(ref, frozenset())
            self._gates[current] = frozenset(found)
        return self._gates[focus_id]

    def conditional(self, focus_id: str) -> bool:
        """Whether ``allow_branch`` can hide ``focus_id``.

        That is when it has ``allow_branch`` itself, or when every chain of
        prerequisites from it up to a focus without prerequisites passes a
        focus with ``allow_branch``.
        """

        for current in self._post_order(focus_id):
            if current in self._hidden:
                continue
            parents = self._parents(current)
            self._hidden[current] = self._has_gate(current) or (
                bool(parents) and all(self._hidden.get(ref, False) for ref in parents)
            )
        return self._hidden[focus_id]

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
        if not (self.conditional(first) and self.conditional(second)):
            return False
        # Each has to hang on a condition the other does not: two gate
        # focuses with the same ``allow_branch`` text appear together.
        first_gates, second_gates = self.gates(first), self.gates(second)
        first_only = self._conditions(first_gates - second_gates)
        second_only = self._conditions(second_gates - first_gates)
        return bool(first_only - second_only) and bool(second_only - first_only)

    def _conditions(self, gates: frozenset[str]) -> set[str]:
        """The ``allow_branch`` texts of some gate focuses, spacing aside."""

        return {" ".join(self._by_id[gate].allow_branch.split()) for gate in gates}


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
    tree: FocusTree, layout: FocusPositions, *, min_continuous_padding: int = 100
) -> list[str]:
    """Check known positions; continuous bounds require a complete layout.

    Two focuses may share a position when the game never draws both there
    (see :class:`FocusBranches`), which is how variant branches are drawn.
    """
    occupied: dict[tuple[int, int], list[str]] = {}
    issues: list[str] = []
    if tree.kind == "file":
        # Top-level shared/joint focuses of one file are placed into different
        # trees, so they do not share one canvas.
        return issues
    branches = FocusBranches(tree)
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
