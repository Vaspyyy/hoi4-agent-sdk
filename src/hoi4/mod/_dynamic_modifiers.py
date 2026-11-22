"""Dynamic modifier authoring."""

from __future__ import annotations

from typing import Any, Unpack

from pathlib import Path

from ..fields import DynamicModifierFields
from ..dynamic_modifiers import (
    DynamicModifier,
)
from ..paths import (
    require_script_id,
    safe_file_stem,
)
from ..script import (
    normalize_block_body,
)
from ..errors import AlreadyExistsError, not_found
from ._core import CoreLayer
from ._shared import MISSING, _D, _Missing
from ._shared import (
    _check_fields,
    _set_fields,
)


class DynamicModifiersLayer(CoreLayer):
    """Dynamic modifier authoring."""

    # ── Dynamic Modifiers ────────────────────────────────────────

    def list_dynamic_modifiers(self) -> list[str]:
        """Return the sorted ids of the mod's dynamic modifiers."""
        return sorted(self._dynamic_modifiers)

    def get_dynamic_modifier(
        self, modifier_id: str, *, default: _D | _Missing = MISSING
    ) -> DynamicModifier | _D:
        """Return the dynamic modifier ``modifier_id``.

        Args:
            default: Returned instead of raising ``NotFoundError`` when it
                does not exist. Without it, a missing id raises.

        Raises:
            NotFoundError: No dynamic modifier has this id.
        """
        if modifier_id not in self._dynamic_modifiers:
            if default is not MISSING:
                return default
            raise not_found("dynamic modifier", modifier_id, self.list_dynamic_modifiers())
        return self._dynamic_modifiers[modifier_id]

    def create_dynamic_modifier(
        self,
        modifier_id: str,
        *,
        icon: str = "",
        enable: str = "",
        remove_trigger: str = "",
        attacker_modifier: bool | None = None,
        modifier: dict[str, str | int | float | bool] | None = None,
        path: str | Path | None = None,
        overwrite: bool = False,
    ) -> DynamicModifier:
        """Create a dynamic modifier (``common/dynamic_modifiers``); staged until ``save()``.

        Args:
            icon: The icon sprite name shown on the country or state.
            enable: Body of the trigger that keeps the modifier active.
            remove_trigger: Body of the trigger that removes it.
            attacker_modifier: Write ``attacker_modifier = yes/no``; ``None``
                leaves it out.
            modifier: The modifier values by name; a value may be a variable
                name as a string.
            path: The output file. Without it,
                ``common/dynamic_modifiers/<modifier_id>.txt``.
            overwrite: Replace an existing modifier of this id.

        Returns:
            The new dynamic modifier.

        Raises:
            AlreadyExistsError: The id exists and ``overwrite`` is false.
            ValueError: The id is not a valid script id, or ``path`` is
                outside the mod.
        """
        modifier_id = require_script_id(
            modifier_id,
            label="dynamic modifier ID",
        )
        existing = self._dynamic_modifiers.get(modifier_id)
        if existing is not None and not overwrite:
            raise AlreadyExistsError(f"Dynamic modifier '{modifier_id}' already exists")
        target = (
            self._output_path(path)
            if path is not None
            else self.mod_root
            / "common"
            / "dynamic_modifiers"
            / f"{safe_file_stem(modifier_id)}.txt"
        )
        if existing is not None and existing.path is not None:
            self._dirty_dynamic_modifier_files.add(existing.path)
        dynamic_modifier = DynamicModifier(
            id=modifier_id,
            icon=icon,
            enable=normalize_block_body(enable),
            remove_trigger=normalize_block_body(remove_trigger),
            attacker_modifier=attacker_modifier,
            modifier=dict(modifier or {}),
            path=target,
        )
        if existing is not None:
            # Replace every modeled value, including omitted optional fields,
            # through the source patcher so unknown blocks and comments survive.
            dynamic_modifier.touched_fields.update({
                "icon", "enable", "remove_trigger", "attacker_modifier", "modifier",
            })
        self._dynamic_modifiers[modifier_id] = dynamic_modifier
        self._dynamic_modifier_sources[modifier_id] = target
        self._dirty_dynamic_modifiers.add(modifier_id)
        self._dirty_dynamic_modifier_files.add(target)
        self._dirty.add("dynamic_modifiers")
        return dynamic_modifier

    def update_dynamic_modifier(self, modifier_id: str, **fields: Unpack[DynamicModifierFields]) -> DynamicModifier:
        """Patch fields of a dynamic modifier in place.

        Returns:
            The updated dynamic modifier.

        Raises:
            NotFoundError: No dynamic modifier has this id.
            TypeError: A field name is unknown; nothing changed.
        """
        kwargs: dict[str, Any] = dict(fields)
        dynamic_modifier = self.get_dynamic_modifier(modifier_id)
        forbidden = {"id", "path", "raw_block", "touched_fields"} & set(kwargs)
        if forbidden:
            raise ValueError(
                f"Immutable/internal dynamic modifier fields: {sorted(forbidden)}"
            )
        _check_fields("update_dynamic_modifier", dynamic_modifier, kwargs)
        for key in ("enable", "remove_trigger"):
            if key in kwargs:
                kwargs[key] = normalize_block_body(str(kwargs[key]))
        _set_fields(dynamic_modifier, kwargs)
        dynamic_modifier.touched_fields.update(kwargs)
        if dynamic_modifier.path is not None:
            self._dirty_dynamic_modifier_files.add(dynamic_modifier.path)
        self._dirty_dynamic_modifiers.add(modifier_id)
        self._dirty.add("dynamic_modifiers")
        return dynamic_modifier

    def delete_dynamic_modifier(self, modifier_id: str) -> None:
        """Stage the deletion of a dynamic modifier, keeping the rest of its file.

        Raises:
            NotFoundError: No dynamic modifier has this id.
        """
        dynamic_modifier = self.get_dynamic_modifier(modifier_id)
        del self._dynamic_modifiers[modifier_id]
        self._dynamic_modifier_sources.pop(modifier_id, None)
        if dynamic_modifier.path is not None:
            self._dirty_dynamic_modifier_files.add(dynamic_modifier.path)
        self._dirty_dynamic_modifiers.add(modifier_id)
        self._dirty.add("dynamic_modifiers")
