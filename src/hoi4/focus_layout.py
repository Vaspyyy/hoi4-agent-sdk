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


class FocusBranches:
    """Which focuses of one tree the game can show at the same time.

    A focus is hidden with its whole branch when an ``allow_branch`` on it or
    on any focus it depends on fails. Two focuses are never taken together
    when a focus they depend on (or they themselves) is mutually exclusive
    with a focus the other depends on.
    """

    def __init__(self, tree: FocusTree) -> None:
        self._by_id = {focus.id: focus for focus in tree.focuses if focus.id}
        self._ancestors: dict[str, frozenset[str]] = {}

    def ancestors(self, focus_id: str) -> frozenset[str]:
        """Return ``focus_id`` and every focus it depends on through prerequisites."""

        cached = self._ancestors.get(focus_id)
        if cached is not None:
            return cached
        found: set[str] = set()
        pending = [focus_id]
        while pending:
            current = pending.pop()
            if current in found:
                continue
            found.add(current)
            done = self._ancestors.get(current)
            if done is not None:
                found |= done
                continue
            focus = self._by_id.get(current)
            if focus is not None:
                pending.extend(ref for group in focus.prerequisites for ref in group)
        result = frozenset(found)
        self._ancestors[focus_id] = result
        return result

    def conditional(self, focus_id: str) -> bool:
        return any(
            self._by_id[ref].allow_branch.strip()
            for ref in self.ancestors(focus_id)
            if ref in self._by_id
        )

    def exclusive(self, first: str, second: str) -> bool:
        first_branch, second_branch = self.ancestors(first), self.ancestors(second)
        for branch, other in ((first_branch, second_branch), (second_branch, first_branch)):
            for ref in branch:
                focus = self._by_id.get(ref)
                if focus is not None and any(
                    not other.isdisjoint(group) for group in focus.mutually_exclusive
                ):
                    return True
        return False

    def never_shown_together(self, first: str, second: str) -> bool:
        return self.exclusive(first, second) or (
            self.conditional(first) and self.conditional(second)
        )


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

    Two focuses may share a position when the game never shows both (see
    :class:`FocusBranches`), which is how alternative branches are drawn.
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
            (other for other in sharing if not branches.never_shown_together(focus.id, other)),
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
