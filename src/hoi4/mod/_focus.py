"""Focus tree authoring and layout."""

from __future__ import annotations

import copy
import dataclasses
from pathlib import Path
from typing import Optional

from ..focus import FOCUS_KINDS
from ..focus_layout import resolve_focus_positions, visual_overlap_issues
from ..paths import (
    require_country_tag,
    require_script_id,
    resolve_mod_output_path,
)
from ..types import (
    Focus,
    FocusTree,
)
from ..errors import not_found
from ._context import ContextLayer
from ._effects import EffectsLayer
from ._shared import (
    _set_fields,
)

# Tree-level fields of a real ``focus_tree``; a file container has none.
_TREE_ONLY_FIELDS = frozenset({"default", "continuous_focus_position", "shared_focuses"})


def _require_focus_kind(tree: FocusTree, kind: str) -> None:
    """A focus inside a ``focus_tree`` is a ``focus``; a top-level block can be any kind."""

    allowed = FOCUS_KINDS if tree.kind == "file" else ("focus",)
    if kind not in allowed:
        raise ValueError(
            f"Focus kind {kind!r} is not allowed in {tree.kind} '{tree.id}'. "
            f"Use one of: {', '.join(allowed)}"
        )


class FocusLayer(ContextLayer, EffectsLayer):
    """Focus tree authoring and layout."""

    # ── Focus Trees ──────────────────────────────────────────────

    def list_focus_trees(self) -> list[str]:
        return sorted(self._focus_trees.keys())

    def get_focus_tree(self, tree_id: str) -> FocusTree:
        if tree_id not in self._focus_trees:
            raise not_found("focus tree", tree_id, self.list_focus_trees())
        return self._focus_trees[tree_id]

    def create_focus_tree(
        self, tree_id: str, country_tag: str, overwrite: bool = False
    ) -> FocusTree:
        tree_id = require_script_id(tree_id, label="focus tree ID")
        existing = self._focus_trees.get(tree_id)
        if existing is not None and not overwrite:
            raise ValueError(
                f"Focus tree '{tree_id}' already exists. "
                "Use overwrite=True to replace it or update_focus_tree()/add_focus() to patch it."
            )
        tag = require_country_tag(country_tag)
        path = (
            existing.path
            if existing is not None and existing.path is not None
            else self.mod_root / "common" / "national_focus" / f"{tag}_focus.txt"
        )
        tree = FocusTree(id=tree_id, country_tag=tag, path=path, touched=True)
        self._focus_trees[tree_id] = tree
        self._original_files.setdefault(path, "")
        self._dirty.add("focus")
        self._dirty_focus_trees.add(tree_id)
        return tree

    def ensure_focus_tree(self, tree_id: str, country_tag: str, **kwargs) -> FocusTree:
        """Create a focus tree if missing, otherwise patch tree-level fields."""
        if tree_id in self._focus_trees:
            if kwargs:
                self.update_focus_tree(tree_id, **kwargs)
            return self._focus_trees[tree_id]
        tree = self.create_focus_tree(tree_id, country_tag)
        if kwargs:
            self.update_focus_tree(tree_id, **kwargs)
        return tree

    def delete_focus_tree(self, tree_id: str) -> bool:
        if tree_id not in self._focus_trees:
            return False
        tree = self._focus_trees.pop(tree_id)
        if tree.path:
            self._dirty_focus_files.add(tree.path)
        self._dirty.add("focus")
        self._dirty_focus_trees.discard(tree_id)
        return True

    def update_focus_tree(self, tree_id: str, **kwargs) -> bool:
        """Patch tree-level fields; country selector reassignment is unsupported."""
        tree = self._focus_trees.get(tree_id)
        if tree is None:
            return False
        if "country_tag" in kwargs:
            raise TypeError(
                "update_focus_tree() cannot update country_tag, even to the existing tag. "
                "Remove country_tag from this call; country selector reassignment "
                "is not supported. Set country_tag when creating a new tree instead."
            )
        if "id" in kwargs:
            raise ValueError("Focus tree IDs are immutable; create a new tree instead")
        if "kind" in kwargs:
            raise ValueError("A focus tree's kind is fixed by its file; it cannot be changed")
        if tree.kind == "file" and _TREE_ONLY_FIELDS & set(kwargs):
            raise ValueError(
                f"'{tree_id}' holds the top-level focus blocks of a file, not a focus_tree; "
                f"it has no {', '.join(sorted(_TREE_ONLY_FIELDS & set(kwargs)))}"
            )
        old_path = tree.path
        if "path" in kwargs:
            kwargs["path"] = resolve_mod_output_path(self.mod_root, kwargs["path"])
        _set_fields(tree, kwargs, allow_path=True)
        tree.touched = True
        if old_path is not None:
            self._dirty_focus_files.add(old_path)
        if tree.path is not None:
            self._dirty_focus_files.add(tree.path)
        self._dirty.add("focus")
        self._dirty_focus_trees.add(tree_id)
        return True

    # ── Focuses ──────────────────────────────────────────────────

    def add_focus(self, tree_id: str, focus: Focus) -> None:
        tree = self.get_focus_tree(tree_id)
        _require_focus_kind(tree, focus.kind)
        focus.touched = True
        tree.focuses.append(focus)
        tree.touched = True
        self._dirty.add("focus")
        self._dirty_focus_trees.add(tree_id)

    def remove_focus(self, tree_id: str, focus_id: str) -> bool:
        tree = self.get_focus_tree(tree_id)
        for i, f in enumerate(tree.focuses):
            if f.id == focus_id:
                tree.focuses.pop(i)
                tree.touched = True
                self._dirty.add("focus")
                self._dirty_focus_trees.add(tree_id)
                return True
        return False

    def get_focus(self, tree_id: str, focus_id: str) -> Optional[Focus]:
        tree = self.get_focus_tree(tree_id)
        for f in tree.focuses:
            if f.id == focus_id:
                return f
        return None

    def update_focus(self, tree_id: str, focus_id: str, **kwargs) -> bool:
        focus = self.get_focus(tree_id, focus_id)
        if focus is None:
            return False
        if "id" in kwargs:
            raise ValueError("Focus IDs are immutable; create a new focus instead")
        tree = self.get_focus_tree(tree_id)
        if "kind" in kwargs:
            _require_focus_kind(tree, kwargs["kind"])
        _set_fields(focus, kwargs)
        focus.touched = True
        tree.touched = True
        self._dirty.add("focus")
        self._dirty_focus_trees.add(tree_id)
        return True

    def upsert_focus(self, tree_id: str, focus: Focus) -> Focus:
        """Add a focus if missing, otherwise replace its modeled fields."""
        existing = self.get_focus(tree_id, focus.id)
        if existing is None:
            self.add_focus(tree_id, focus)
            return focus
        updates = {
            field.name: copy.deepcopy(getattr(focus, field.name))
            for field in dataclasses.fields(Focus)
            # ``kind`` is where the block lives, not content to replace.
            if field.name not in {"id", "raw_block", "touched", "kind"}
        }
        self.update_focus(tree_id, focus.id, **updates)
        return self.get_focus(tree_id, focus.id) or focus

    def focus_tree_bounds(self, tree_id: str) -> dict[str, int]:
        tree = self.get_focus_tree(tree_id)
        if not tree.focuses:
            return {"min_x": 0, "max_x": 0, "min_y": 0, "max_y": 0, "width": 0, "height": 0}
        layout = resolve_focus_positions(tree)
        layout.require_complete()
        xs = [pos[0] for pos in layout.positions.values()]
        ys = [pos[1] for pos in layout.positions.values()]
        return {
            "min_x": min(xs),
            "max_x": max(xs),
            "min_y": min(ys),
            "max_y": max(ys),
            "width": max(xs) - min(xs) + 1,
            "height": max(ys) - min(ys) + 1,
        }

    def place_continuous_focus_below_tree(
        self, tree_id: str, padding: int = 400, x: int = 50
    ) -> str:
        bounds = self.focus_tree_bounds(tree_id)
        y = (bounds["max_y"] + 1) * 100 + padding
        position = f"x = {x} y = {y}"
        self.update_focus_tree(tree_id, continuous_focus_position=position)
        return position

    def assert_no_visual_overlap(self, tree_id: str, *, min_continuous_padding: int = 100) -> bool:
        tree = self.get_focus_tree(tree_id)
        layout = resolve_focus_positions(tree)
        layout.require_complete()
        own = {focus.id for focus in tree.focuses}
        outside = any(
            ref not in own
            for focus in tree.focuses
            for group in focus.prerequisites
            for ref in group
        )
        # A focus below a shared focus of another file is hidden with it.
        parents, conditions = self._shared_focuses() if outside else (None, None)
        issues = visual_overlap_issues(
            tree,
            layout,
            min_continuous_padding=min_continuous_padding,
            shared_focus_parents=parents,
            shared_focus_conditions=conditions,
        )
        if issues:
            raise ValueError("; ".join(issues))
        return True

    def auto_layout_branch(
        self,
        tree_id: str,
        focuses: list[Focus],
        *,
        anchor_focus_id: str | None = None,
        x: int | None = None,
        y_start: int | None = None,
        spacing_y: int = 1,
        chain_prerequisites: bool = True,
    ) -> list[Focus]:
        tree = self.get_focus_tree(tree_id)
        if not focuses:
            return []
        if x is None:
            if anchor_focus_id:
                anchor = self.get_focus(tree_id, anchor_focus_id)
                x = anchor.x if anchor else 0
            else:
                x = max((focus.x for focus in tree.focuses), default=0) + 2
        if y_start is None:
            if anchor_focus_id:
                anchor = self.get_focus(tree_id, anchor_focus_id)
                y_start = (
                    (anchor.y + spacing_y)
                    if anchor
                    else max((focus.y for focus in tree.focuses), default=-1) + spacing_y
                )
            else:
                y_start = max((focus.y for focus in tree.focuses), default=-1) + spacing_y
        previous = anchor_focus_id
        for index, focus in enumerate(focuses):
            focus.x = x
            focus.y = y_start + index * spacing_y
            if chain_prerequisites and previous and not focus.prerequisites:
                focus.prerequisites = [[previous]]
            previous = focus.id
        return focuses

    def insert_focus_after(
        self,
        tree_id: str,
        anchor_focus_id: str,
        focus: Focus,
        *,
        add_prerequisite: bool = True,
        relative_position: bool = True,
    ) -> None:
        tree = self.get_focus_tree(tree_id)
        _require_focus_kind(tree, focus.kind)
        for idx, existing in enumerate(tree.focuses):
            if existing.id == anchor_focus_id:
                if add_prerequisite and not focus.prerequisites:
                    focus.prerequisites = [[anchor_focus_id]]
                if relative_position and not focus.relative_position_id:
                    focus.relative_position_id = anchor_focus_id
                focus.touched = True
                tree.focuses.insert(idx + 1, focus)
                self._dirty.add("focus")
                self._dirty_focus_trees.add(tree_id)
                return
        raise not_found(
            "focus",
            anchor_focus_id,
            (existing.id for existing in tree.focuses),
            where=f"focus tree '{tree_id}'",
            plural="focuses",
        )

    def insert_branch(
        self,
        tree_id: str,
        anchor_focus_id: str,
        focuses: list[Focus],
        *,
        chain_prerequisites: bool = True,
    ) -> None:
        previous = anchor_focus_id
        for focus in focuses:
            if chain_prerequisites and not focus.prerequisites:
                focus.prerequisites = [[previous]]
            if not focus.relative_position_id:
                focus.relative_position_id = previous
            self.insert_focus_after(tree_id, previous, focus, add_prerequisite=False)
            previous = focus.id

    def append_to_focus_reward(self, tree_id: str, focus_id: str, effect: str) -> bool:
        focus = self.get_focus(tree_id, focus_id)
        if focus is None:
            return False
        focus.completion_reward = "\n".join(
            part for part in [focus.completion_reward.strip(), effect.strip()] if part
        )
        focus.touched = True
        self._dirty.add("focus")
        self._dirty_focus_trees.add(tree_id)
        return True

    def set_focuses_mutually_exclusive(
        self, tree_id: str, focus_a_id: str, focus_b_id: str
    ) -> bool:
        focus_a = self.get_focus(tree_id, focus_a_id)
        focus_b = self.get_focus(tree_id, focus_b_id)
        if focus_a is None or focus_b is None:
            return False
        if [focus_b_id] not in focus_a.mutually_exclusive:
            focus_a.mutually_exclusive.append([focus_b_id])
        if [focus_a_id] not in focus_b.mutually_exclusive:
            focus_b.mutually_exclusive.append([focus_a_id])
        focus_a.touched = True
        focus_b.touched = True
        self._dirty.add("focus")
        self._dirty_focus_trees.add(tree_id)
        return True

    def set_focus_loc(
        self,
        focus_id: str,
        name: str,
        description: str,
        file_path: str | Path | None = None,
    ) -> None:
        self.set_loc(focus_id, name, file_path=file_path)
        self.set_loc(f"{focus_id}_desc", description, file_path=file_path)

    def create_industrial_branch(
        self,
        tree_id: str,
        tag: str,
        *,
        anchor_focus_id: str | None = None,
        state_id: int | None = None,
        grounded: bool = True,
    ) -> list[Focus]:
        tag = tag.upper()
        tree = (
            self.get_focus_tree(tree_id)
            if tree_id in self._focus_trees
            else self.create_focus_tree(tree_id, tag)
        )
        context = self.get_country_context(tag)
        target_state = (
            state_id or context.get("capital") or (context.get("states") or [{}])[0].get("id") or 1
        )
        y_base = max((focus.y for focus in tree.focuses), default=-1) + 1
        x_base = 0 if not tree.focuses else max(focus.x for focus in tree.focuses) + 2
        prefix = tag
        focuses = [
            Focus(
                id=f"{prefix}_map_domestic_industry",
                icon="GFX_goal_generic_construct_civ_factory",
                x=x_base,
                y=y_base,
                cost=5 if grounded else 10,
                search_filters=["FOCUS_FILTER_INDUSTRY"],
                completion_reward="add_political_power = 50",
            ),
            Focus(
                id=f"{prefix}_modernize_transport_links",
                icon="GFX_goal_generic_construct_infrastructure",
                x=0,
                y=1,
                cost=10,
                search_filters=["FOCUS_FILTER_INDUSTRY"],
                completion_reward=self.effect_add_infrastructure(int(target_state), 1),
            ),
            Focus(
                id=f"{prefix}_support_domestic_steel",
                icon="GFX_goal_generic_production",
                x=0,
                y=1,
                cost=10,
                search_filters=["FOCUS_FILTER_INDUSTRY"],
                completion_reward="\n".join(
                    [
                        self.effect_add_civilian_factory(int(target_state), 1),
                        self.effect_add_industry_bonus(
                            f"{prefix}_steel_industry_bonus", uses=1, bonus=0.5
                        ),
                    ]
                ),
            ),
            Focus(
                id=f"{prefix}_precision_workshops",
                icon="GFX_goal_generic_construct_mil_factory",
                x=0,
                y=1,
                cost=10,
                search_filters=["FOCUS_FILTER_INDUSTRY", "FOCUS_FILTER_RESEARCH"],
                completion_reward="\n".join(
                    [
                        self.effect_add_military_factory(int(target_state), 1),
                        self.effect_add_industry_bonus(
                            f"{prefix}_precision_tools_bonus", uses=1, bonus=0.5
                        ),
                    ]
                ),
            ),
        ]
        loc_text = {
            focuses[0].id: (
                "Map Domestic Industry",
                "A careful survey of local workshops, transport bottlenecks, and available capital will let us expand without pretending we are a great power.",
            ),
            focuses[1].id: (
                "Modernize Transport Links",
                "Industry depends on reliable roads and rail connections. Targeted improvements can make our small economy more resilient.",
            ),
            focuses[2].id: (
                "Support Domestic Steel",
                "Steel remains the backbone of our heavy industry. State support can help established firms modernize production.",
            ),
            focuses[3].id: (
                "Precision Workshops",
                "Small states must compete through skilled labor and precision tools rather than sheer scale.",
            ),
        }
        for focus in focuses:
            name, desc = loc_text[focus.id]
            self.set_focus_loc(focus.id, name, desc)

        if anchor_focus_id:
            self.insert_branch(tree_id, anchor_focus_id, focuses)
        else:
            for offset, focus in enumerate(focuses):
                focus.x = x_base
                focus.y = y_base + offset
                if offset and not focus.prerequisites:
                    focus.prerequisites = [[focuses[offset - 1].id]]
                self.add_focus(tree_id, focus)
        return focuses
