"""Ideology authoring."""

from __future__ import annotations

from typing import Any, Unpack

import copy
from pathlib import Path

from ..fields import IdeologyFields
from ..ideologies import Ideology, SubIdeology
from ..paths import (
    require_script_id,
)
from ..errors import AlreadyExistsError, ReadOnlyError, not_found
from ._core import CoreLayer
from ._shared import MISSING, _D, _Missing
from ._shared import (
    _check_fields,
    _require_items,
    _set_fields,
)


class IdeologiesLayer(CoreLayer):
    """Ideology authoring."""

    # ── Ideologies ───────────────────────────────────────────────

    def list_ideologies(self, *, include_vanilla: bool = False) -> list[str]:
        """Return the mod's ideology IDs, or all visible IDs when requested."""
        ids = set(self._ideologies)
        if include_vanilla:
            ids.update(self._vanilla_ideologies)
        return sorted(ids)

    def get_ideology(
        self,
        ideology_id: str,
        *,
        include_vanilla: bool = True,
        default: _D | _Missing = MISSING,
    ) -> Ideology | _D:
        """Return the ideology ``ideology_id``; ``include_vanilla`` also looks at the game's.

        Args:
            default: Returned instead of raising ``NotFoundError`` when it
                does not exist. Without it, a missing id raises.

        Raises:
            NotFoundError: No ideology has this id.
        """
        if ideology_id is None:
            raise TypeError("Ideology ID cannot be None")
        ideology = self._ideologies.get(ideology_id)
        if ideology is None and include_vanilla:
            ideology = copy.deepcopy(self._vanilla_ideologies.get(ideology_id))
        if ideology is None:
            if default is not MISSING:
                return default
            raise not_found(
                "ideology",
                ideology_id,
                self.list_ideologies(include_vanilla=include_vanilla),
            )
        return ideology

    def create_ideology(
        self,
        ideology_id: str,
        *,
        color: tuple[int, int, int] = (128, 128, 128),
        types: list[SubIdeology] | None = None,
        rules: dict[str, str] | None = None,
        modifiers: dict[str, str] | None = None,
        hidden_modifiers: dict[str, str] | None = None,
        faction_modifiers: dict[str, str] | None = None,
        dynamic_faction_names: list[str] | None = None,
        ai_behavior: str = "",
        can_host_government_in_exile: bool = False,
        can_collaborate: bool = False,
        effects: list[str] | None = None,
        path: str | Path | None = None,
        overwrite: bool = False,
    ) -> Ideology:
        """Create an ideology group (``common/ideologies``); staged until ``save()``.

        Args:
            color: The map colour as ``(red, green, blue)``, each 0 to 255.
            types: The sub-ideologies (leader ideologies) of the group.
            rules: Rules such as ``{"can_puppet": "yes"}``.
            modifiers: Modifiers for countries ruled by the group;
                ``hidden_modifiers`` and ``faction_modifiers`` likewise.
            dynamic_faction_names: Localisation keys for faction names.
            ai_behavior: Which game AI flag to set: ``"fascist"`` writes
                ``ai_fascist = yes``; empty sets none.
            effects: Values written into the ideology's ``effects`` block.
            path: The output file. Without it,
                ``common/ideologies/00_mod_ideologies.txt``.
            overwrite: Replace an ideology of this id that the mod defines.

        Returns:
            The new ideology.

        Raises:
            TypeError: ``types`` holds something that is not a ``SubIdeology``; nothing changed.
            AlreadyExistsError: The mod defines the id and ``overwrite`` is false.
            ValueError: The id is not a valid script id, or ``path`` is
                outside the mod.
        """
        ideology_id = require_script_id(ideology_id, label="ideology ID")
        types = _require_items("create_ideology", "types", types or [], (SubIdeology,))
        existing = self._ideologies.get(ideology_id)
        if existing is not None and not overwrite:
            raise AlreadyExistsError(
                f"Ideology '{ideology_id}' already exists. Use overwrite=True or update_ideology()."
            )
        target = (
            self._output_path(path)
            if path is not None
            else self.mod_root / "common" / "ideologies" / "00_mod_ideologies.txt"
        )
        ideology = Ideology(
            id=ideology_id,
            color=color,
            types=list(types or []),
            rules=dict(rules or {}),
            modifiers=dict(modifiers or {}),
            hidden_modifiers=dict(hidden_modifiers or {}),
            faction_modifiers=dict(faction_modifiers or {}),
            dynamic_faction_names=list(dynamic_faction_names or []),
            ai_behavior=ai_behavior,
            can_host_government_in_exile=can_host_government_in_exile,
            can_collaborate=can_collaborate,
            effects=list(effects or []),
            path=target,
        )
        if existing is not None and existing.path is not None:
            self._dirty_ideology_files.add(existing.path)
        self._ideologies[ideology_id] = ideology
        self._script_vocabulary_cache = None
        self._dirty_ideologies.add(ideology_id)
        self._dirty_ideology_files.add(target)
        self._dirty.add("ideologies")
        return ideology

    def update_ideology(self, ideology_id: str, **fields: Unpack[IdeologyFields]) -> Ideology:
        """Patch fields of an ideology; a game ideology is copied into the mod first.

        Returns:
            The updated ideology.

        Raises:
            NotFoundError: No one defines ``ideology_id``.
            TypeError: A field name is unknown; nothing changed.
        """
        kwargs: dict[str, Any] = dict(fields)
        existing = self._ideologies.get(ideology_id)
        source = existing if existing is not None else self.get_ideology(ideology_id)
        forbidden = {"id", "raw_block", "touched_fields", "is_vanilla"} & set(kwargs)
        if forbidden:
            raise ValueError(f"Immutable/internal ideology fields: {sorted(forbidden)}")
        _check_fields("update_ideology", source, kwargs, allow_path=True)
        ideology = source
        if existing is None:
            ideology = copy.deepcopy(source)
            ideology.is_vanilla = False
            ideology.path = (
                self.mod_root / "common" / "ideologies" / "00_mod_ideologies.txt"
            )
            self._ideologies[ideology_id] = ideology
        old_path = ideology.path
        if "path" in kwargs:
            kwargs["path"] = self._output_path(kwargs["path"])
        _set_fields(ideology, kwargs, allow_path=True)
        ideology.touched_fields.update(key for key in kwargs if key != "path")
        if old_path is not None:
            self._dirty_ideology_files.add(old_path)
        if ideology.path is not None:
            self._dirty_ideology_files.add(ideology.path)
        self._dirty_ideologies.add(ideology_id)
        self._dirty.add("ideologies")
        return ideology

    def delete_ideology(self, ideology_id: str) -> None:
        """Stage the deletion of an ideology the mod defines.

        Raises:
            NotFoundError: No one defines ``ideology_id``.
            ReadOnlyError: Only the game or a base mod defines it.
        """
        ideology = self._ideologies.get(ideology_id)
        if ideology is None:
            if ideology_id in self._vanilla_ideologies:
                raise ReadOnlyError(
                    f"Ideology '{ideology_id}' is game content; override its file "
                    "instead of deleting it."
                )
            raise not_found(
                "ideology", ideology_id, self.list_ideologies(include_vanilla=True)
            )
        del self._ideologies[ideology_id]
        if ideology.path is not None:
            self._dirty_ideology_files.add(ideology.path)
        self._dirty_ideologies.discard(ideology_id)
        self._script_vocabulary_cache = None
        self._dirty.add("ideologies")
