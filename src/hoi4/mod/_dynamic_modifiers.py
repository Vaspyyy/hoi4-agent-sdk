"""Dynamic modifier authoring."""

from __future__ import annotations

from pathlib import Path

from ..dynamic_modifiers import (
    DynamicModifier,
)
from ..paths import (
    require_script_id,
    resolve_mod_output_path,
    safe_file_stem,
)
from ..script import (
    normalize_block_body,
)
from ._core import CoreLayer
from ._shared import (
    _set_fields,
)


class DynamicModifiersLayer(CoreLayer):
    """Dynamic modifier authoring."""

    # ── Dynamic Modifiers ────────────────────────────────────────

    def list_dynamic_modifiers(self) -> list[str]:
        return sorted(self._dynamic_modifiers)

    def get_dynamic_modifier(self, modifier_id: str) -> DynamicModifier:
        try:
            return self._dynamic_modifiers[modifier_id]
        except KeyError as error:
            raise KeyError(
                f"Dynamic modifier '{modifier_id}' not found. "
                f"Available: {self.list_dynamic_modifiers()}"
            ) from error

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
        modifier_id = require_script_id(
            modifier_id,
            label="dynamic modifier ID",
        )
        existing = self._dynamic_modifiers.get(modifier_id)
        if existing is not None and not overwrite:
            raise ValueError(f"Dynamic modifier '{modifier_id}' already exists")
        target = (
            resolve_mod_output_path(self.mod_root, path)
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

    def update_dynamic_modifier(self, modifier_id: str, **kwargs: object) -> bool:
        dynamic_modifier = self._dynamic_modifiers.get(modifier_id)
        if dynamic_modifier is None:
            return False
        forbidden = {"id", "path", "raw_block", "touched_fields"} & set(kwargs)
        if forbidden:
            raise ValueError(
                f"Immutable/internal dynamic modifier fields: {sorted(forbidden)}"
            )
        for key in ("enable", "remove_trigger"):
            if key in kwargs:
                kwargs[key] = normalize_block_body(str(kwargs[key]))
        _set_fields(dynamic_modifier, dict(kwargs))
        dynamic_modifier.touched_fields.update(kwargs)
        if dynamic_modifier.path is not None:
            self._dirty_dynamic_modifier_files.add(dynamic_modifier.path)
        self._dirty_dynamic_modifiers.add(modifier_id)
        self._dirty.add("dynamic_modifiers")
        return True

    def delete_dynamic_modifier(self, modifier_id: str) -> bool:
        dynamic_modifier = self._dynamic_modifiers.pop(modifier_id, None)
        if dynamic_modifier is None:
            return False
        self._dynamic_modifier_sources.pop(modifier_id, None)
        if dynamic_modifier.path is not None:
            self._dirty_dynamic_modifier_files.add(dynamic_modifier.path)
        self._dirty_dynamic_modifiers.add(modifier_id)
        self._dirty.add("dynamic_modifiers")
        return True
