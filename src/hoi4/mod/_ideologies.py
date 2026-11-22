"""Ideology authoring."""

from __future__ import annotations

import copy
from pathlib import Path

from ..ideologies import Ideology, SubIdeology
from ..paths import (
    require_script_id,
    resolve_mod_output_path,
)
from ..errors import ReadOnlyError, not_found
from ._core import CoreLayer
from ._shared import (
    _set_fields,
)


class IdeologiesLayer(CoreLayer):
    """Ideology authoring."""

    # ── Ideologies ───────────────────────────────────────────────

    def list_ideologies(self, *, include_vanilla: bool = True) -> list[str]:
        ids = set(self._ideologies)
        if include_vanilla:
            ids.update(self._vanilla_ideologies)
        return sorted(ids)

    def get_ideology(self, ideology_id: str, *, include_vanilla: bool = True) -> Ideology:
        ideology = self._ideologies.get(ideology_id)
        if ideology is None and include_vanilla:
            ideology = self._vanilla_ideologies.get(ideology_id)
        if ideology is None:
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
        ideology_id = require_script_id(ideology_id, label="ideology ID")
        existing = self._ideologies.get(ideology_id)
        if existing is not None and not overwrite:
            raise ValueError(
                f"Ideology '{ideology_id}' already exists. Use overwrite=True or update_ideology()."
            )
        target = (
            resolve_mod_output_path(self.mod_root, path)
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

    def update_ideology(self, ideology_id: str, **kwargs) -> bool:
        ideology = self._ideologies.get(ideology_id)
        if ideology is None:
            vanilla = self._vanilla_ideologies.get(ideology_id)
            if vanilla is None:
                return False
            ideology = copy.deepcopy(vanilla)
            ideology.is_vanilla = False
            ideology.path = (
                self.mod_root / "common" / "ideologies" / "00_mod_ideologies.txt"
            )
            self._ideologies[ideology_id] = ideology
        forbidden = {"id", "raw_block", "touched_fields", "is_vanilla"} & set(kwargs)
        if forbidden:
            raise ValueError(f"Immutable/internal ideology fields: {sorted(forbidden)}")
        old_path = ideology.path
        if "path" in kwargs:
            kwargs["path"] = resolve_mod_output_path(self.mod_root, kwargs["path"])
        _set_fields(ideology, kwargs, allow_path=True)
        ideology.touched_fields.update(key for key in kwargs if key != "path")
        if old_path is not None:
            self._dirty_ideology_files.add(old_path)
        if ideology.path is not None:
            self._dirty_ideology_files.add(ideology.path)
        self._dirty_ideologies.add(ideology_id)
        self._dirty.add("ideologies")
        return True

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
