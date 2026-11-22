"""Idea (national spirit) authoring."""

from __future__ import annotations

from pathlib import Path

from ..idea_icons import (
    DEFAULT_IDEA_ICON,
    normalize_idea_icon,
)
from ..paths import (
    require_country_tag,
    require_script_id,
    resolve_mod_output_path,
)
from ..types import (
    Idea,
)
from ..errors import not_found
from ._core import CoreLayer
from ._shared import (
    _set_fields,
)


class IdeasLayer(CoreLayer):
    """Idea (national spirit) authoring."""

    # ── Ideas ───────────────────────────────────────────────────

    def list_ideas(self) -> list[str]:
        return sorted(self._ideas.keys())

    def get_idea(self, idea_id: str) -> Idea:
        if idea_id not in self._ideas:
            raise not_found("idea", idea_id, self.list_ideas())
        return self._ideas[idea_id]

    def create_idea(
        self,
        idea_id: str,
        icon: str = DEFAULT_IDEA_ICON,
        modifier: dict[str, str | int | float | bool] | None = None,
        category: str = "country",
        path: str | Path | None = None,
        overwrite: bool = False,
        *,
        desc: str = "",
        removal_cost: str | int | float | None = None,
    ) -> Idea:
        idea_id = require_script_id(idea_id, label="idea ID")
        expected_desc = f"{idea_id}_desc"
        if desc and desc != expected_desc:
            raise ValueError(
                "HOI4 idea descriptions use the fixed localization key "
                f"'{expected_desc}'; call set_loc('{expected_desc}', text) instead"
            )
        existing = self._ideas.get(idea_id)
        if existing is not None and not overwrite:
            raise ValueError(
                f"Idea '{idea_id}' already exists. Use overwrite=True to replace it or update_idea() to patch it."
            )
        idea = Idea(
            id=idea_id,
            icon=normalize_idea_icon(icon),
            desc=desc,
            removal_cost=removal_cost,
            modifier=dict(modifier or {}),
            category=category,
            touched=True,
        )
        if existing is not None and existing.path is not None:
            self._dirty_idea_files.add(existing.path)
        if path is not None:
            idea.path = resolve_mod_output_path(self.mod_root, path)
        elif existing is not None and existing.path is not None:
            idea.path = existing.path
        else:
            prefix = idea_id.split("_")[0] if "_" in idea_id else idea_id
            if prefix in self._cached_idea_file:
                idea.path = self._cached_idea_file[prefix]
            elif len(prefix) == 3 and prefix.isupper():
                idea.path = self.mod_root / "common" / "ideas" / f"{prefix}_ideas.txt"
            else:
                idea.path = self.mod_root / "common" / "ideas" / "mod_ideas.txt"
        if idea.path is not None and idea.path.parent.name == "ideas":
            self._idea_file_containers.setdefault(idea.path, "ideas")
        self._ideas[idea_id] = idea
        self._dirty.add("ideas")
        self._dirty_ideas.add(idea_id)
        return idea

    def ensure_idea(self, idea_id: str, *, merge_modifier: bool = False, **kwargs) -> Idea:
        """Create an idea if missing, otherwise update the existing idea.

        Existing modifiers are replaced by default. Pass
        ``merge_modifier=True`` to merge supplied keys instead.
        """
        if idea_id in self._ideas:
            self.update_idea(idea_id, merge_modifier=merge_modifier, **kwargs)
            return self._ideas[idea_id]
        return self.create_idea(idea_id, **kwargs)

    def update_idea(self, idea_id: str, *, merge_modifier: bool = False, **kwargs) -> bool:
        """Update an idea, replacing its modifier mapping by default.

        ``modifier={}`` therefore clears the block. Pass
        ``merge_modifier=True`` for the legacy key-by-key merge behavior.
        """
        idea = self._ideas.get(idea_id)
        if idea is None:
            return False
        if "id" in kwargs:
            raise ValueError("Idea IDs are immutable; create a new idea instead")
        if "desc" in kwargs:
            expected_desc = f"{idea_id}_desc"
            desc = kwargs["desc"]
            if desc and desc != expected_desc:
                raise ValueError(
                    "HOI4 idea descriptions use the fixed localization key "
                    f"'{expected_desc}'; call set_loc('{expected_desc}', text) instead"
                )
        if "icon" in kwargs:
            kwargs["icon"] = normalize_idea_icon(kwargs["icon"])
        touched_fields = set(kwargs) - {"path"}
        old_path = idea.path
        if "path" in kwargs:
            kwargs["path"] = resolve_mod_output_path(self.mod_root, kwargs["path"])
        modifier_supplied = "modifier" in kwargs
        modifier_val = kwargs.pop("modifier", None)
        if modifier_supplied and modifier_val is not None:
            if not isinstance(modifier_val, dict):
                raise TypeError(f"modifier must be a dict, got {type(modifier_val).__name__}")
            if merge_modifier:
                idea.modifier.update(modifier_val)
            else:
                idea.modifier = dict(modifier_val)
            idea.modifier_merge = merge_modifier
            touched_fields.add("modifier")
        if kwargs:
            _set_fields(idea, kwargs, allow_path=True)
        idea.touched = True
        idea.touched_fields.update(touched_fields)
        if old_path is not None:
            self._dirty_idea_files.add(old_path)
        if idea.path is not None:
            self._dirty_idea_files.add(idea.path)
        self._dirty.add("ideas")
        self._dirty_ideas.add(idea_id)
        return True

    def delete_idea(self, idea_id: str) -> bool:
        idea = self._ideas.get(idea_id)
        if idea is None:
            return False
        if idea.path is not None:
            self._dirty_idea_files.add(idea.path)
        del self._ideas[idea_id]
        self._dirty.add("ideas")
        self._dirty_ideas.discard(idea_id)
        return True

    def set_idea_path(self, tag: str, path: str | Path) -> None:
        tag = require_country_tag(tag)
        self._cached_idea_file[tag] = resolve_mod_output_path(self.mod_root, path)
