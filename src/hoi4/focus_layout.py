"""Static layout of local focus models, without changing serialized offsets."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

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


def visual_overlap_issues(
    tree: FocusTree, layout: FocusPositions, *, min_continuous_padding: int = 100
) -> list[str]:
    """Check known positions; continuous bounds require a complete layout."""
    occupied: dict[tuple[int, int], str] = {}
    issues: list[str] = []
    for focus in tree.focuses:
        pos = layout.positions.get(focus.id)
        if pos is None:
            continue
        if pos in occupied:
            issues.append(f"{focus.id} overlaps {occupied[pos]} at x={pos[0]}, y={pos[1]}")
        else:
            occupied[pos] = focus.id
    if tree.continuous_focus_position and not layout.unresolved:
        match = re.search(r"\by\s*=\s*(-?\d+)", tree.continuous_focus_position)
        if match:
            max_y = max((pos[1] for pos in layout.positions.values()), default=0)
            min_y = (max_y + 1) * 100 + min_continuous_padding
            y = int(match.group(1))
            if y < min_y:
                issues.append(
                    f"continuous_focus_position y={y} is above recommended minimum y={min_y}"
                )
    return issues
