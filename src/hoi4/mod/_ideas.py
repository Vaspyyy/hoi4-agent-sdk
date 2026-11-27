"""Idea (national spirit) authoring."""

from __future__ import annotations

from typing import Any, Unpack

from pathlib import Path

from ..fields import EnsureIdeaFields, IdeaFields
from ..idea_icons import (
    DEFAULT_IDEA_ICON,
    normalize_idea_icon,
)
from ..paths import (
    require_script_id,
)
from ..types import (
    Idea,
)
from ..errors import AlreadyExistsError
from ._countries import CountriesLayer
from ._shared import MISSING, _D, _Missing
from ._shared import (
    _check_field_names,
    _check_fields,
    _create_arguments,
    _set_fields,
)


def _refuse_desc(method: str, idea_id: str, kwargs: dict[str, Any]) -> None:
    """Refuse ``desc``: the game reads an idea's description from localisation only.

    The writer never puts ``desc`` into the idea, so setting it would change
    nothing in the files while the returned object said it was set.
    """
    if "desc" in kwargs:
        raise TypeError(
            f"{method}(): 'desc' is not a field of an idea. The description is the "
            f"localisation key '{idea_id}_desc': call set_loc('{idea_id}_desc', text)."
        )


class IdeasLayer(CountriesLayer):
    """Idea (national spirit) authoring."""

    # ── Ideas ───────────────────────────────────────────────────

    def list_ideas(self, *, include_vanilla: bool = False) -> list[str]:
        """Return the mod's idea IDs, or all visible IDs when requested."""
        return self._listed("idea", self._ideas, include_vanilla=include_vanilla)

    def get_idea(self, idea_id: str, *, include_vanilla: bool = True, default: _D | _Missing = MISSING) -> Idea | _D:
        """Return the idea ``idea_id``.

        Args:
            default: Returned instead of raising ``NotFoundError`` when it
                does not exist. Without it, a missing id raises.

        Raises:
            NotFoundError: No idea has this id.
        """
        return self._lookup("idea", self._ideas, idea_id, include_vanilla=include_vanilla, default=default)

    def create_idea(
        self,
        idea_id: str,
        *,
        icon: str = DEFAULT_IDEA_ICON,
        modifier: dict[str, str | int | float | bool] | None = None,
        category: str = "country",
        path: str | Path | None = None,
        overwrite: bool = False,
        removal_cost: str | int | float | None = None,
    ) -> Idea:
        """Create an idea (a national spirit, law or advisor slot entry); staged until ``save()``.

        Args:
            icon: The picture name, with or without the ``GFX_idea_`` prefix.
            modifier: The modifier values by name, such as
                ``{"stability_factor": 0.1}``.
            category: The idea category, such as ``"country"`` (national
                spirits) or a law category.
            path: The output file. Without it, the existing idea's file, the
                file of the tag the id starts with (``ABC_x`` goes to
                ``common/ideas/ABC_ideas.txt``) or ``common/ideas/mod_ideas.txt``.
            overwrite: Replace an existing idea of this id.
            removal_cost: The political power it costs to remove; ``None``
                leaves it out.

        Returns:
            The new idea.

        Raises:
            AlreadyExistsError: The id exists and ``overwrite`` is false.
            ValueError: The id is not a valid script id, or ``path`` is
                outside the mod.
        """
        idea_id = require_script_id(idea_id, label="idea ID")
        existing = self._ideas.get(idea_id)
        if existing is not None and not overwrite:
            raise AlreadyExistsError(
                f"Idea '{idea_id}' already exists. Use overwrite=True to replace it or update_idea() to patch it."
            )
        idea = Idea(
            id=idea_id,
            icon=normalize_idea_icon(icon),
            removal_cost=removal_cost,
            modifier=dict(modifier or {}),
            category=category,
            touched=True,
        )
        if existing is not None and existing.path is not None:
            self._dirty_idea_files.add(existing.path)
        if path is not None:
            idea.path = self._output_path(path)
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
        if idea.path is not None:
            self._note_target(idea.path)
        self._ideas[idea_id] = idea
        self._dirty.add("ideas")
        self._dirty_ideas.add(idea_id)
        return idea

    def ensure_idea(self, idea_id: str, *, merge_modifier: bool = False, **fields: Unpack[EnsureIdeaFields]) -> Idea:
        """Create an idea if missing, otherwise update the existing idea.

        Existing modifiers are replaced by default. Pass
        ``merge_modifier=True`` to merge supplied keys instead.

        Returns:
            The created or updated idea.

        Raises:
            TypeError: A field name is unknown; nothing changed.
        """
        kwargs: dict[str, Any] = dict(fields)
        existing = self.get_idea(idea_id, include_vanilla=False, default=None)
        if existing is not None:
            _refuse_desc("ensure_idea", idea_id, kwargs)
            if kwargs.get("modifier", ...) is None:
                del kwargs["modifier"]  # None means "leave the modifier as it is"
            _check_fields("ensure_idea", existing, kwargs, allow_path=True)
            return self.update_idea(idea_id, merge_modifier=merge_modifier, **kwargs)
        _check_field_names(
            "ensure_idea", kwargs, _create_arguments(self.create_idea, skip={"idea_id"})
        )
        return self.create_idea(idea_id, **kwargs)

    def update_idea(self, idea_id: str, *, merge_modifier: bool = False, **fields: Unpack[IdeaFields]) -> Idea:
        """Update an idea, replacing its modifier mapping by default.

        ``modifier={}`` therefore clears the block. Pass
        ``merge_modifier=True`` for the legacy key-by-key merge behavior.

        Returns:
            The updated idea.

        Raises:
            NotFoundError: No idea has this id.
            TypeError: A field name is unknown, or ``desc`` is passed (an
                idea's description is localisation); nothing changed.
            ValueError: ``id`` is passed (ids cannot change).
        """
        kwargs: dict[str, Any] = dict(fields)
        idea = self._mod_definition("idea", self._ideas, idea_id)
        if "id" in kwargs:
            raise ValueError("Idea IDs are immutable; create a new idea instead")
        _refuse_desc("update_idea", idea_id, kwargs)
        if kwargs.get("modifier", ...) is None:
            del kwargs["modifier"]  # None means "leave the modifier as it is"
        _check_fields("update_idea", idea, kwargs, allow_path=True)
        if "icon" in kwargs:
            kwargs["icon"] = normalize_idea_icon(kwargs["icon"])
        touched_fields = set(kwargs) - {"path"}
        old_path = idea.path
        if "path" in kwargs:
            kwargs["path"] = self._output_path(kwargs["path"])
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
        return idea

    def delete_idea(self, idea_id: str) -> None:
        """Stage the deletion of an idea.

        Raises:
            NotFoundError: No idea has this id.
        """
        idea = self._mod_definition("idea", self._ideas, idea_id)
        if idea.path is not None:
            self._dirty_idea_files.add(idea.path)
        del self._ideas[idea_id]
        self._dirty.add("ideas")
        self._dirty_ideas.discard(idea_id)

    def set_default_idea_file(self, tag: str, path: str | Path) -> None:
        """Make ``path`` the file that new ideas named ``<tag>_...`` go to.

        Raises:
            NotFoundError: A game install is configured and no one defines ``tag``.
            ValueError: ``tag`` is invalid or ``path`` is outside the mod.
        """
        tag = self._require_country_reference(tag)
        self._cached_idea_file[tag] = self._output_path(path)
        # Not an edit that a save writes: discard() and a save of other
        # things forget it with the section, as a full reload used to.
        self._reload_on_discard.add("ideas")
