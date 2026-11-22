"""On-action authoring."""

from __future__ import annotations

from pathlib import Path

from ..paths import (
    require_script_id,
    resolve_mod_output_path,
)
from ..script import (
    normalize_block_body,
)
from ..types import (
    OnAction,
)
from ..errors import NotFoundError, not_found
from ._core import CoreLayer
from ._shared import (
    _check_fields,
    _set_fields,
)


class OnActionsLayer(CoreLayer):
    """On-action authoring."""

    # ── On Actions ───────────────────────────────────────────────

    def list_on_actions(self) -> list[str]:
        return sorted(self._on_actions.keys())

    def get_on_action_occurrences(self, action_id: str) -> tuple[OnAction, ...]:
        """Return every compositional occurrence of an on-action hook."""

        occurrences = self._on_action_occurrences.get(action_id)
        if not occurrences:
            raise not_found("on-action", action_id, self.list_on_actions())
        return tuple(occurrences)

    def _select_on_action_occurrence(
        self,
        action_id: str,
        *,
        occurrence: int | None,
        source_path: str | Path | None,
        require_unique: bool,
    ) -> tuple[int, OnAction]:
        actions = self._on_action_occurrences.get(action_id)
        if not actions:
            raise not_found("on-action", action_id, self.list_on_actions())
        candidates = list(enumerate(actions))
        if source_path is not None:
            target = resolve_mod_output_path(self.mod_root, source_path)
            candidates = [
                (index, action)
                for index, action in candidates
                if (action.path or self.mod_root / "common/on_actions/mod_on_actions.txt")
                .resolve(strict=False)
                == target
            ]
            if not candidates:
                raise NotFoundError(
                    f"On-action '{action_id}' has no occurrence in {target}.",
                    kind="on-action occurrence",
                    id=action_id,
                )
        if occurrence is None:
            if require_unique and len(candidates) != 1:
                locations = ", ".join(
                    f"{index}: {action.path or '<generated>'}"
                    for index, action in candidates
                )
                raise ValueError(
                    f"On-action '{action_id}' has {len(candidates)} compositional "
                    f"occurrences ({locations}). Specify occurrence= or source_path= "
                    "to choose one safely."
                )
            occurrence = 0
        if occurrence < 0 or occurrence >= len(candidates):
            raise NotFoundError(
                f"On-action '{action_id}' occurrence {occurrence} is out of range "
                f"for {len(candidates)} matching occurrence(s).",
                kind="on-action occurrence",
                id=occurrence,
            )
        return candidates[occurrence]

    def get_on_action(
        self,
        action_id: str,
        *,
        occurrence: int = 0,
        source_path: str | Path | None = None,
    ) -> OnAction:
        """Return one hook occurrence, defaulting to the first in load order."""

        _, action = self._select_on_action_occurrence(
            action_id,
            occurrence=occurrence,
            source_path=source_path,
            require_unique=False,
        )
        return action

    def create_on_action(
        self,
        action_id: str,
        *,
        effect: str = "",
        events: list[str] | None = None,
        random_events: list[str] | None = None,
        path: str | Path | None = None,
        overwrite: bool = False,
    ) -> OnAction:
        action_id = require_script_id(action_id, label="on-action ID")
        existing = self._on_action_occurrences.get(action_id, [])
        default_path = self.mod_root / "common" / "on_actions" / "mod_on_actions.txt"
        if path is None and overwrite and len(existing) > 1:
            raise ValueError(
                f"On-action '{action_id}' has multiple compositional occurrences; "
                "specify path= to choose which file to overwrite"
            )
        target = (
            resolve_mod_output_path(self.mod_root, path)
            if path is not None
            else (
                existing[0].path
                if overwrite and len(existing) == 1 and existing[0].path is not None
                else default_path
            )
        )
        target_existing = [
            action
            for action in existing
            if (action.path or default_path).resolve(strict=False) == target
        ]
        if target_existing and not overwrite:
            raise ValueError(
                f"On-action '{action_id}' already has an occurrence in {target}. "
                "Use overwrite=True to replace that occurrence, update_on_action() "
                "to patch it, or choose another path for an additive extension."
            )
        if len(target_existing) > 1:
            raise ValueError(
                f"On-action '{action_id}' occurs multiple times in {target}; "
                "create_on_action(overwrite=True) cannot choose one safely. Use "
                "update_on_action(..., source_path=..., occurrence=...)."
            )
        replaced = target_existing[0] if target_existing else None
        if replaced is not None and replaced.path is not None:
            self._dirty_on_action_files.add(replaced.path)
        action = OnAction(
            id=action_id,
            effect=normalize_block_body(effect),
            events=list(events or ()),
            random_events=list(random_events or ()),
            path=target,
            touched=True,
            source_path=replaced.source_path if replaced is not None else None,
            source_occurrence=(
                replaced.source_occurrence if replaced is not None else -1
            ),
        )
        if replaced is None:
            existing.append(action)
            self._on_action_occurrences[action_id] = existing
            self._on_actions.setdefault(action_id, action)
        else:
            index = existing.index(replaced)
            existing[index] = action
            if self._on_actions.get(action_id) is replaced:
                self._on_actions[action_id] = action
        self._dirty.add("on_actions")
        self._dirty_on_actions.add(action_id)
        self._dirty_on_action_files.add(target)
        return action

    def ensure_on_action(self, action_id: str, **kwargs) -> OnAction:
        """Ensure one occurrence, using the requested/default file as its identity."""

        occurrences = self._on_action_occurrences.get(action_id, [])
        if len(occurrences) == 1:
            self.update_on_action(action_id, occurrence=0, **kwargs)
            return occurrences[0]
        if occurrences:
            path_arg = kwargs.get("path")
            target = (
                resolve_mod_output_path(self.mod_root, path_arg)
                if path_arg is not None
                else self.mod_root / "common" / "on_actions" / "mod_on_actions.txt"
            )
            matching = [
                index
                for index, action in enumerate(occurrences)
                if (
                    action.path
                    or self.mod_root / "common" / "on_actions" / "mod_on_actions.txt"
                ).resolve(strict=False)
                == target
            ]
            if len(matching) == 1:
                self.update_on_action(action_id, occurrence=matching[0], **kwargs)
                return occurrences[matching[0]]
            if len(matching) > 1:
                raise ValueError(
                    f"On-action '{action_id}' occurs multiple times in {target}; "
                    "ensure_on_action() cannot choose one safely"
                )
        return self.create_on_action(action_id, **kwargs)

    def update_on_action(
        self,
        action_id: str,
        *,
        occurrence: int | None = None,
        source_path: str | Path | None = None,
        **kwargs,
    ) -> OnAction:
        """Patch one occurrence of an on-action hook.

        When the hook has several occurrences, pass ``occurrence=`` or
        ``source_path=`` to choose one.

        Returns:
            The updated occurrence.

        Raises:
            NotFoundError: The hook, or the chosen occurrence, does not exist.
            TypeError: A field name is unknown; nothing changed.
            ValueError: The hook has several occurrences and none was chosen.
        """
        _, action = self._select_on_action_occurrence(
            action_id,
            occurrence=occurrence,
            source_path=source_path,
            require_unique=True,
        )
        if "id" in kwargs:
            raise ValueError("On-action IDs are immutable; create a new on-action instead")
        _check_fields("update_on_action", action, kwargs, allow_path=True)
        old_path = action.path
        if "effect" in kwargs and isinstance(kwargs["effect"], str):
            kwargs["effect"] = normalize_block_body(kwargs["effect"])
        if "id" in kwargs:
            raise ValueError("On-action IDs are immutable; create a new on-action instead")
        if "path" in kwargs:
            kwargs["path"] = resolve_mod_output_path(self.mod_root, kwargs["path"])
        _set_fields(action, kwargs, allow_path=True)
        action.touched = True
        self._dirty.add("on_actions")
        self._dirty_on_actions.add(action_id)
        if old_path is not None:
            self._dirty_on_action_files.add(old_path)
        self._dirty_on_action_files.add(
            action.path or self.mod_root / "common" / "on_actions" / "mod_on_actions.txt"
        )
        return action

    def delete_on_action(
        self,
        action_id: str,
        *,
        occurrence: int | None = None,
        source_path: str | Path | None = None,
    ) -> None:
        """Stage the deletion of one occurrence of an on-action hook.

        When the hook has several occurrences, pass ``occurrence=`` or
        ``source_path=`` to choose one.

        Raises:
            NotFoundError: The hook, or the chosen occurrence, does not exist.
            ValueError: The hook has several occurrences and none was chosen.
        """
        index, action = self._select_on_action_occurrence(
            action_id,
            occurrence=occurrence,
            source_path=source_path,
            require_unique=True,
        )
        actions = self._on_action_occurrences[action_id]
        del actions[index]
        if actions:
            self._on_actions[action_id] = actions[0]
        else:
            del self._on_action_occurrences[action_id]
            del self._on_actions[action_id]
        self._dirty.add("on_actions")
        self._dirty_on_actions.discard(action_id)
        self._dirty_on_action_files.add(
            action.path or self.mod_root / "common" / "on_actions" / "mod_on_actions.txt"
        )
