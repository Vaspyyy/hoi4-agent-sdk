"""Decision and decision-category authoring."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast, Unpack
from collections.abc import Iterable, Mapping, Sequence

from ..fields import DecisionCategoryFields, DecisionFields, EnsureDecisionCategoryFields, EnsureDecisionFields, MissionOptions, TargetedDecisionOptions
from ..decisions import category_files
from ..paths import (
    require_script_id,
)
from ..syntax import parse, quote
from ..textio import read_text
from ..types import (
    Decision,
    DecisionCategory,
)
from ..errors import AlreadyExistsError, not_found
from .. import effects
from ._shared import MISSING, _D, _Missing
from ._localisation import LocalisationLayer
from ._shared import (
    _decision_category_definition_path,
    _check_field_names,
    _typed_dict_keys,
    _check_fields,
    _create_arguments,
    _join_script_parts,
    _set_fields,
)


def _decision_targets(value: object) -> list[str]:
    """Normalize ``targets`` to a list of bare tokens (tags or scopes)."""
    if value is None:
        return []
    items = value.split() if isinstance(value, str) else list(cast(Iterable[object], value))
    targets: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text or any(ch.isspace() or ch in '{}#"=' for ch in text):
            raise ValueError(f"Invalid decision target {item!r}; pass tags such as 'GER'")
        targets.append(text)
    return targets


def _insert_in_file_order(category: DecisionCategory, decision: Decision) -> None:
    """Add a decision where a reload would put it.

    Decisions load file by file, so a decision added to an earlier file of a
    category spread over several files goes after that file's decisions and
    before the decisions of later files.
    """

    rank = {path: index for index, path in enumerate(category_files(category))}
    own = rank.get(cast(Path, decision.path), len(rank))
    for index, candidate in enumerate(category.decisions):
        if rank.get(cast(Path, candidate.path or category.path), len(rank)) > own:
            category.decisions.insert(index, decision)
            return
    category.decisions.append(decision)


#: The keys a step of ``create_decision_chain()`` may have.
_CHAIN_STEP_KEYS = frozenset(
    {
        "id",
        "flag",
        "icon",
        "cost",
        "days_remove",
        "fire_only_once",
        "visible",
        "available",
        "complete_effect",
        "remove_effect",
        "ai_will_do",
        "event",
        "event_days",
        "clear_flags",
        "loc_name",
        "loc_desc",
    }
)


class DecisionsLayer(LocalisationLayer):
    """Decision and decision-category authoring."""

    def _point_decision_category_at(self, category: DecisionCategory, target: Path) -> None:
        """Make ``target`` the file new decisions of ``category`` go to.

        A file that already holds a block of the category only becomes the
        target; nothing moves. Any other file receives the whole category:
        every decision moves there, and the blocks in the old files go.
        """

        old_files = category_files(category)
        self._dirty_decision_files.update(old_files)
        self._dirty_decision_files.add(target)
        category.path = target
        if target in old_files:
            return
        category.paths = [target]
        for decision in category.decisions:
            decision.path = target
        category.touched = True

    # ── Decisions ────────────────────────────────────────────────

    def list_decision_categories(self, *, include_vanilla: bool = False) -> list[str]:
        """Return the sorted ids of the mod's decision categories."""
        return self._listed("decision_category", self._decision_categories, include_vanilla=include_vanilla)

    def list_decisions(self, *, include_vanilla: bool = False) -> list[str]:
        """Return the sorted ids of the mod's decisions."""
        return self._listed("decision", self._decisions, include_vanilla=include_vanilla)

    def get_decision(
        self, decision_id: str, *, include_vanilla: bool = True, default: _D | _Missing = MISSING
    ) -> Decision | _D:
        """Return the decision ``decision_id``.

        Args:
            default: Returned instead of raising ``NotFoundError`` when it
                does not exist. Without it, a missing id raises.

        Raises:
            NotFoundError: No decision has this id.
        """
        return self._lookup("decision", self._decisions, decision_id, include_vanilla=include_vanilla, default=default)

    def get_decision_category(
        self, category_id: str, *, include_vanilla: bool = True, default: _D | _Missing = MISSING
    ) -> DecisionCategory | _D:
        """Return the decision category ``category_id``.

        When the mod has blocks of the category (its own decisions in a game
        category, or a copied definition file), the result is the mod's part:
        the object an edit changes. Its ``decisions`` are then the mod's
        only; ``list_decisions(include_vanilla=True)`` and ``get_decision()``
        reach the ones other layers add. A category only the game or a base
        mod has comes back as a merged copy.

        Args:
            default: Returned instead of raising ``NotFoundError`` when it
                does not exist. Without it, a missing id raises.

        Raises:
            NotFoundError: No such category is loaded.
        """
        return self._lookup("decision_category", self._decision_categories, category_id, include_vanilla=include_vanilla, default=default)

    def create_decision_category(
        self,
        category_id: str,
        *,
        icon: str = "",
        allowed: str = "",
        visible: str = "",
        path: str | Path | None = None,
        category_path: str | Path | None = None,
        overwrite: bool = False,
    ) -> DecisionCategory:
        """Create a decision category; staged until ``save()``.

        The category's decisions go into ``path``; its definition (icon,
        ``allowed``, ``visible``) goes into ``common/decisions/categories``.

        Args:
            icon: The category's icon sprite name.
            allowed: Body of the ``allowed`` trigger block.
            visible: Body of the ``visible`` trigger block.
            path: The file for the category's decisions. Without it, the
                existing category's file or ``common/decisions/mod_decisions.txt``.
            category_path: The definition file. Without it, a name derived
                from ``path`` that hides no game or base-mod file.
            overwrite: Replace an existing category of this id; its
                decisions are dropped.

        Returns:
            The new category.

        Raises:
            AlreadyExistsError: The category exists and ``overwrite`` is false.
            RuntimeError: ``overwrite`` is true but the category's decisions
                are spread over several files.
            ValueError: The id is not a valid script id, ``category_path`` is
                not in ``common/decisions/categories``, or a path is outside
                the mod.
        """
        category_id = require_script_id(category_id, label="decision category ID")
        existing = self._decision_categories.get(category_id)
        if existing is not None and not overwrite:
            raise AlreadyExistsError(
                f"Decision category '{category_id}' already exists. "
                "Use overwrite=True to replace it or update existing decisions."
            )
        existing_decisions = [
            decision
            for decision in self._decisions.values()
            if decision.category == category_id
        ]
        if existing is not None:
            canonical_path = existing.path
            extended_paths = {
                decision.path.resolve(strict=False)
                for decision in existing_decisions
                if decision.path is not None
                and (
                    canonical_path is None
                    or decision.path.resolve(strict=False)
                    != canonical_path.resolve(strict=False)
                )
            }
            if extended_paths:
                raise RuntimeError(
                    f"Decision category '{category_id}' is extended across multiple files; "
                    "cannot safely overwrite the whole category. Consolidate it first."
                )
        target = (
            self._output_path(path)
            if path is not None
            else (
                existing.path
                if existing is not None and existing.path is not None
                else self.mod_root / "common" / "decisions" / "mod_decisions.txt"
            )
        )
        definition_target = (
            self._output_path(category_path)
            if category_path is not None
            else (
                existing.definition_path
                if existing is not None and existing.definition_path is not None
                else _decision_category_definition_path(self.mod_root, target, self._shadows_inherited)
            )
        )
        expected_categories_dir = self.mod_root / "common" / "decisions" / "categories"
        if definition_target.parent != expected_categories_dir:
            raise ValueError(
                "Decision category_path must point inside common/decisions/categories"
            )
        for noted in (target, definition_target):
            self._note_target(noted)
        if existing is not None:
            self._dirty_decision_files.update(category_files(existing))
            if existing.definition_path is not None:
                self._dirty_decision_category_files.add(existing.definition_path)
            for decision in existing_decisions:
                self._decisions.pop(decision.id, None)
        category = DecisionCategory(
            id=category_id,
            icon=icon,
            allowed=allowed,
            visible=visible,
            path=target,
            definition_path=definition_target,
        )
        category.touched = True
        self._decision_categories[category_id] = category
        self._dirty.add("decisions")
        self._dirty_decision_categories.add(category_id)
        self._dirty_decision_files.add(target)
        self._dirty_decision_category_files.add(definition_target)
        return category

    def ensure_decision_category(self, category_id: str, **fields: Unpack[EnsureDecisionCategoryFields]) -> DecisionCategory:
        """Create a decision category if missing, otherwise patch the existing one.

        Returns:
            The created or updated category.

        Raises:
            TypeError: A field name is unknown; nothing changed.
        """
        kwargs: dict[str, Any] = dict(fields)
        existing = self.get_decision_category(category_id, include_vanilla=False, default=None)
        if existing is None:
            self._refuse_inherited(
                "decision_category",
                category_id,
                hint="To add decisions to this category, call create_decision() with its id: "
                "that needs no definition of the category in the mod.",
            )
        if existing is not None:
            _check_fields(
                "ensure_decision_category", existing, kwargs, extra={"path", "category_path"}
            )
            return self.update_decision_category(category_id, **kwargs)
        _check_field_names(
            "ensure_decision_category",
            kwargs,
            _create_arguments(self.create_decision_category, skip={"category_id"}),
        )
        return self.create_decision_category(category_id, **kwargs)

    def update_decision_category(self, category_id: str, **fields: Unpack[DecisionCategoryFields]) -> DecisionCategory:
        """Patch fields of a decision category.

        ``path=`` moves its decisions' block to another file and
        ``category_path=`` its definition in ``common/decisions/categories``.

        Returns:
            The updated category.

        Raises:
            NotFoundError: No decision category has this id.
            TypeError: A field name is unknown; nothing changed.
        """
        kwargs: dict[str, Any] = dict(fields)
        category = self._mod_definition("decision_category", self._decision_categories, category_id)
        if "id" in kwargs:
            raise ValueError("Decision category IDs are immutable")
        if "paths" in kwargs:
            raise ValueError(
                "Decision category paths are read from the files; pass path= to move it"
            )
        _check_fields(
            "update_decision_category", category, kwargs, extra={"path", "category_path"}
        )
        old_definition_path = category.definition_path
        category_path = kwargs.pop("category_path", None)
        target = (
            self._output_path(kwargs.pop("path"))
            if "path" in kwargs
            else None
        )
        # Work out the new definition file and check everything before the
        # category changes: a rejected call leaves it as it was.
        definition_path = category.definition_path
        if category_path is not None:
            definition_path = self._output_path(category_path)
        elif target is not None and definition_path is None:
            definition_path = _decision_category_definition_path(
                self.mod_root, target, self._shadows_inherited
            )
        if (
            definition_path is not None
            and definition_path.parent != self.mod_root / "common" / "decisions" / "categories"
        ):
            raise ValueError(
                "Decision category_path must point inside common/decisions/categories"
            )
        category.definition_path = definition_path
        _set_fields(category, kwargs)
        if target is not None:
            self._point_decision_category_at(category, target)
        category.touched = True
        # The edit moves legacy inline presentation out of every file that
        # holds a block of the category.
        self._dirty_decision_files.update(category_files(category))
        if old_definition_path is not None:
            self._dirty_decision_category_files.add(old_definition_path)
        if category.definition_path is not None:
            self._dirty_decision_category_files.add(category.definition_path)
        self._dirty.add("decisions")
        self._dirty_decision_categories.add(category_id)
        return category

    def _require_new_decision_id(self, decision_id: str, *, overwrite: bool) -> str:
        """Return the checked ``decision_id``; raise when it exists and ``overwrite`` is false."""
        decision_id = require_script_id(decision_id, label="decision ID")
        if decision_id in self._decisions and not overwrite:
            raise AlreadyExistsError(
                f"Decision '{decision_id}' already exists. "
                "Use overwrite=True to replace it or update_decision() to patch it.",
                kind="decision",
                id=decision_id,
            )
        return decision_id

    def create_decision(
        self,
        category_id: str,
        decision_id: str,
        *,
        icon: str = "",
        cost: int | None = None,
        days_remove: int | None = None,
        fire_only_once: bool | None = None,
        available: str = "",
        visible: str = "",
        complete_effect: str = "",
        remove_effect: str = "",
        ai_will_do: str = "",
        path: str | Path | None = None,
        overwrite: bool = False,
        days_mission_timeout: int | None = None,
        activation: str = "",
        timeout_effect: str = "",
        cancel_trigger: str = "",
        cancel_effect: str = "",
        is_good: bool | None = None,
        selectable_mission: bool | None = None,
        target_trigger: str = "",
        targets: Sequence[str] | str = (),
        target_array: str = "",
        target_root_trigger: str = "",
        state_target: bool | None = None,
        targets_dynamic: bool | None = None,
        target_non_existing: bool | None = None,
        on_map_mode: str = "",
        allowed: str = "",
        days_re_enable: int | None = None,
        war_with_on_remove: str = "",
        custom_cost_trigger: str = "",
        custom_cost_text: str = "",
        cancel_if_not_visible: bool | None = None,
        modifier: str = "",
        remove_trigger: str = "",
        highlight_states: str = "",
        priority: int | None = None,
    ) -> Decision:
        """Create a decision, mission, or targeted decision.

        Missions use ``days_mission_timeout`` with ``activation``,
        ``timeout_effect``, ``cancel_trigger``/``cancel_effect``, ``is_good``,
        and ``selectable_mission``. Targeted decisions use ``target_trigger``
        with ``targets`` (tags, rendered ``targets = { A B }``),
        ``target_array``, ``target_root_trigger``, ``state_target``,
        ``targets_dynamic``, ``target_non_existing``, and ``on_map_mode``.
        Block fields take raw Paradox script bodies without outer braces.
        Staged until ``save()``.

        A category that the mod does not define may be one of the game or a
        base mod: the decision then goes into a block of ``path`` (default
        ``common/decisions/mod_decisions.txt``) that adds to it.

        Args:
            path: The file for the decision. Without it, the category's file.
            overwrite: Replace an existing decision of this id, also one in
                another category.

        Returns:
            The new decision.

        Raises:
            AlreadyExistsError: The id exists and ``overwrite`` is false.
            NotFoundError: With a game install configured, no one defines the
                category.
            ValueError: An id or a target is not valid, or ``path`` is
                outside the mod.
        """
        category_id = require_script_id(category_id, label="decision category ID")
        decision_id = self._require_new_decision_id(decision_id, overwrite=overwrite)
        existing = self._decisions.get(decision_id)
        # Check every value before the first change: a call that raises
        # leaves nothing staged.
        target_list = _decision_targets(targets)
        category = self._decision_categories.get(category_id)
        if category is None:
            category = self._extend_inherited_decision_category(category_id, path)
        elif path is not None:
            self._point_decision_category_at(
                category, self._output_path(path)
            )
        if existing is not None and existing.path is not None:
            # The file that held the replaced decision loses it.
            self._dirty_decision_files.add(existing.path)
        if existing is not None:
            old_category = self._decision_categories.get(existing.category)
            if old_category is not None:
                old_category.decisions = [
                    candidate for candidate in old_category.decisions if candidate.id != decision_id
                ]
                self._dirty_decision_categories.add(old_category.id)
        decision = Decision(
            id=decision_id,
            category=category_id,
            icon=icon,
            cost=cost,
            days_remove=days_remove,
            fire_only_once=fire_only_once,
            available=available,
            visible=visible,
            complete_effect=complete_effect,
            remove_effect=remove_effect,
            ai_will_do=ai_will_do,
            path=category.path,
            touched=True,
            days_mission_timeout=days_mission_timeout,
            activation=activation,
            timeout_effect=timeout_effect,
            cancel_trigger=cancel_trigger,
            cancel_effect=cancel_effect,
            is_good=is_good,
            selectable_mission=selectable_mission,
            target_trigger=target_trigger,
            targets=target_list,
            target_array=target_array,
            target_root_trigger=target_root_trigger,
            state_target=state_target,
            targets_dynamic=targets_dynamic,
            target_non_existing=target_non_existing,
            on_map_mode=on_map_mode,
            allowed=allowed,
            days_re_enable=days_re_enable,
            war_with_on_remove=war_with_on_remove,
            custom_cost_trigger=custom_cost_trigger,
            custom_cost_text=custom_cost_text,
            cancel_if_not_visible=cancel_if_not_visible,
            modifier=modifier,
            remove_trigger=remove_trigger,
            highlight_states=highlight_states,
            priority=priority,
        )
        # Without ``path`` the decision goes to ``category.path``: for a
        # category spread over several files that is the last file in load
        # order, so the new decision lands after every existing one, at the end
        # of that file's last block.
        _insert_in_file_order(category, decision)
        self._decisions[decision_id] = decision
        self._dirty.add("decisions")
        self._dirty_decision_categories.add(category_id)
        if category.path is not None:
            self._dirty_decision_files.add(category.path)
        if category.definition_path is not None and category.touched:
            self._dirty_decision_category_files.add(category.definition_path)
        return decision

    def _inherited_decision_category_ids(self) -> set[str]:
        """Categories that the game and the base mods define, minus files the mod hides."""

        cached = self._scan_cache.get("inherited_decision_categories")
        if cached is not None:
            return set(cached)
        ids: set[str] = set()
        if self._base_content is not None:
            files = self._base_content.glob("common/decisions/categories", "*.txt")
            for path in files.values():
                if self._is_shadowed_by_mod(self._base_content, path):
                    continue
                try:
                    document = parse(read_text(path))
                except (OSError, ValueError):
                    continue
                ids.update(
                    item.key.value
                    for item in document.root.assignments()
                    if item.block is not None
                )
        self._scan_cache["inherited_decision_categories"] = set(ids)
        return ids

    def _category_for_new_decisions(
        self, category_id: str, *, icon: str = "", path: str | Path | None = None
    ) -> None:
        """Make sure ``category_id`` is there for decisions that are about to be created.

        A category the mod defines is updated with ``icon`` and ``path``. A
        category no one defines is created. A category that only the game or
        a base mod defines is left alone unless an ``icon`` is asked for:
        ``create_decision()`` adds to it, and a definition written here would
        replace theirs (the icon and the conditions of the category).
        """
        fields: EnsureDecisionCategoryFields = {}
        if icon:
            fields["icon"] = icon
        if path is not None:
            fields["path"] = path
        if category_id in self._decision_categories:
            if fields:
                self.ensure_decision_category(category_id, **fields)
            return
        if not icon and category_id in self._inherited_decision_category_ids():
            return
        # With an icon this is the explicit choice to give the category a
        # definition of the mod's own, also on top of an inherited one.
        self.create_decision_category(category_id, **fields)

    def _extend_inherited_decision_category(
        self, category_id: str, path: str | Path | None
    ) -> DecisionCategory:
        """A block of the mod's decisions file that adds to a category defined elsewhere.

        Nothing is written to ``common/decisions/categories``: that would
        replace the game's or a base mod's definition. Without a configured
        game install the category may be the game's, so it is accepted and
        ``validate()`` checks it.
        """

        inherited = self._inherited_decision_category_ids()
        if self.hoi4_install is not None and inherited and category_id not in inherited:
            raise not_found(
                "decision category",
                category_id,
                set(self._decision_categories) | self._inherited_decision_category_ids(),
                plural="decision categories",
                hint="Create it with create_decision_category() first.",
            )
        target = self._output_path(path or Path("common") / "decisions" / "mod_decisions.txt")
        category = DecisionCategory(id=category_id, path=target)
        self._decision_categories[category_id] = category
        self._dirty.add("decisions")
        self._dirty_decision_categories.add(category_id)
        self._dirty_decision_files.add(target)
        return category

    def ensure_decision(self, category_id: str, decision_id: str, **fields: Unpack[EnsureDecisionFields]) -> Decision:
        """Create a decision if missing, otherwise patch the existing one.

        Returns:
            The created or updated decision.

        Raises:
            TypeError: A field name is unknown; nothing changed.
            ValueError: The decision exists in another category.
        """
        kwargs: dict[str, Any] = dict(fields)
        existing = self.get_decision(decision_id, include_vanilla=False, default=None)
        if existing is None:
            self._refuse_inherited("decision", decision_id)
        if existing is not None:
            if existing.category != category_id:
                raise ValueError(
                    f"Decision '{decision_id}' is in category '{existing.category}', "
                    f"not '{category_id}'; recreate it to move it."
                )
            _check_fields("ensure_decision", existing, kwargs)
            return self.update_decision(decision_id, **kwargs)
        _check_field_names(
            "ensure_decision",
            kwargs,
            _create_arguments(self.create_decision, skip={"category_id", "decision_id"}),
        )
        return self.create_decision(category_id, decision_id, **kwargs)

    def update_decision(self, decision_id: str, **fields: Unpack[DecisionFields]) -> Decision:
        """Patch fields of a decision in place; its file and category stay.

        Returns:
            The updated decision.

        Raises:
            NotFoundError: No decision has this id.
            TypeError: A field name is unknown; nothing changed.
        """
        kwargs: dict[str, Any] = dict(fields)
        decision = self._mod_definition("decision", self._decisions, decision_id)
        if "id" in kwargs or "category" in kwargs:
            raise ValueError(
                "Decision identity/category are immutable; recreate the decision to move it"
            )
        _check_fields("update_decision", decision, kwargs)
        if "targets" in kwargs:
            kwargs["targets"] = _decision_targets(kwargs["targets"])
        _set_fields(decision, kwargs)
        decision.touched = True
        self._dirty.add("decisions")
        self._dirty_decision_categories.add(decision.category)
        if decision.path is not None:
            # A category may continue in other files; patch the decision
            # in the file that holds it.
            self._dirty_decision_files.add(decision.path)
        category = self._decision_categories.get(decision.category)
        if category is not None and category.path is not None:
            self._dirty_decision_files.add(category.path)
        return decision

    def delete_decision(self, decision_id: str) -> None:
        """Stage the deletion of a decision; its category drops the reference.

        Raises:
            NotFoundError: No decision has this id.
        """
        decision = self._mod_definition("decision", self._decisions, decision_id)
        del self._decisions[decision_id]
        if decision.path is not None:
            self._dirty_decision_files.add(decision.path)
        category = self._decision_categories.get(decision.category)
        if category:
            category.decisions = [
                candidate for candidate in category.decisions if candidate.id != decision_id
            ]
            self._dirty_decision_categories.add(category.id)
            if category.path is not None:
                self._dirty_decision_files.add(category.path)
        self._dirty.add("decisions")

    def create_decision_chain(
        self,
        category_id: str,
        steps: Sequence[Mapping[str, Any] | str],
        *,
        prefix: str | None = None,
        icon: str = "",
        category_icon: str = "",
        path: str | Path | None = None,
        final_event: str | None = None,
        overwrite: bool = False,
    ) -> list[Decision]:
        """Create decisions that unlock one after another; staged until ``save()``.

        Each step sets a country flag (``<id>_done`` unless the step names
        its ``flag``) that the next step's ``visible`` and ``available``
        require. A category the mod defines is updated with ``category_icon``
        and ``path``, and a category no one defines is created. A category
        of the game or a base mod only gets the decisions added (as with
        ``create_decision()``); pass ``category_icon`` to give it a
        definition of the mod's own, which replaces theirs. Each step is a
        dict with at least ``id`` unless
        ``prefix`` is supplied (the id is then ``<prefix>_<n>``, counting
        from 1). Common keys are ``icon``, ``cost``, ``days_remove``,
        ``fire_only_once``, ``visible``, ``available``, ``complete_effect``,
        ``remove_effect``, ``ai_will_do``, ``flag``, ``event`` (with
        ``event_days``), ``clear_flags``, ``loc_name`` and ``loc_desc``. A
        string step is treated as ``{"id": step}``.

        Args:
            icon: The icon of steps that name none.
            final_event: A country event the last step fires.
            overwrite: Replace existing decisions of these ids; without it an
                existing decision is updated in place.

        Returns:
            The decisions, in step order.

        Raises:
            TypeError: A step has a key that is not one of the keys above;
                nothing changed.
            ValueError: ``steps`` is empty, a step has neither an id nor a
                prefix, an id is not valid, or a step exists in another
                category.
            AlreadyExistsError: One id is in ``steps`` twice.
        """
        if not steps:
            raise ValueError("create_decision_chain() requires at least one step")

        # Work out and check every step before the first change, so a call
        # that raises leaves nothing staged.
        planned: list[tuple[str, dict[str, Any], Mapping[str, Any]]] = []
        planned_ids: set[str] = set()
        previous_flag = ""
        for index, raw_step in enumerate(steps, start=1):
            step = {"id": raw_step} if isinstance(raw_step, str) else dict(raw_step)
            _check_field_names(
                "create_decision_chain", step, _CHAIN_STEP_KEYS, where=f" in step {index}"
            )
            decision_id = str(step.get("id") or (f"{prefix}_{index}" if prefix else ""))
            if not decision_id:
                raise ValueError("Decision chain steps need an id or a prefix")
            decision_id = require_script_id(decision_id, label="decision ID")
            if decision_id in planned_ids:
                raise AlreadyExistsError(
                    f"create_decision_chain(): decision '{decision_id}' is in this call twice."
                )
            planned_ids.add(decision_id)
            existing = self.get_decision(decision_id, include_vanilla=False, default=None)
            if existing is None and not overwrite:
                self._refuse_inherited("decision", decision_id)
            if existing is not None and not overwrite and existing.category != category_id:
                raise ValueError(
                    f"Decision '{decision_id}' is in category '{existing.category}', "
                    f"not '{category_id}'; recreate it to move it."
                )
            flag = quote(str(step.get("flag") or f"{decision_id}_done"))
            visible = _join_script_parts(
                [
                    str(step.get("visible") or ""),
                    f"has_country_flag = {previous_flag}" if previous_flag else "",
                    f"NOT = {{ has_country_flag = {flag} }}",
                ]
            )
            available = _join_script_parts(
                [
                    str(step.get("available") or ""),
                    f"has_country_flag = {previous_flag}" if previous_flag else "",
                ]
            )
            event_id = step.get("event")
            effect_parts = [
                str(step.get("complete_effect") or ""),
                effects.schedule_country_event(str(event_id), int(step.get("event_days") or 0))
                if event_id
                else "",
                effects.schedule_country_event(final_event)
                if final_event and index == len(steps)
                else "",
                f"set_country_flag = {flag}",
            ]
            clear_flags = step.get("clear_flags") or []
            effect_parts.extend(f"clr_country_flag = {quote(str(item))}" for item in clear_flags)
            create_kwargs: dict[str, Any] = {
                "icon": str(step.get("icon") or icon),
                "cost": step.get("cost", 0),
                "days_remove": step.get("days_remove"),
                "fire_only_once": step.get("fire_only_once", True),
                "visible": visible,
                "available": available,
                "complete_effect": _join_script_parts(effect_parts),
                "remove_effect": str(step.get("remove_effect") or ""),
                "ai_will_do": str(step.get("ai_will_do") or ""),
                "overwrite": overwrite,
            }
            if path is not None and (overwrite or existing is None):
                # An existing decision is updated in place; ``path`` moves it
                # with its category (``ensure_decision_category`` below).
                create_kwargs["path"] = path
            planned.append((decision_id, create_kwargs, step))
            previous_flag = flag

        self._category_for_new_decisions(category_id, icon=category_icon, path=path)
        decisions: list[Decision] = []
        for decision_id, create_kwargs, planned_step in planned:
            if overwrite:
                decision = self.create_decision(category_id, decision_id, **create_kwargs)
            else:
                create_kwargs.pop("overwrite")
                decision = self.ensure_decision(category_id, decision_id, **create_kwargs)
            if planned_step.get("loc_name"):
                self.set_loc(decision_id, str(planned_step["loc_name"]))
            if planned_step.get("loc_desc"):
                self.set_loc(f"{decision_id}_desc", str(planned_step["loc_desc"]))
            decisions.append(decision)
        return decisions

    def create_mission(
        self,
        category_id: str,
        decision_id: str,
        *,
        days_mission_timeout: int,
        activation: str = "",
        available: str = "",
        complete_effect: str = "",
        timeout_effect: str = "",
        cancel_trigger: str = "",
        cancel_effect: str = "",
        is_good: bool | None = None,
        selectable_mission: bool | None = None,
        loc_name: str = "",
        loc_desc: str = "",
        **fields: Unpack[MissionOptions],
    ) -> Decision:
        """Create a mission: a timed decision that starts when ``activation``
        holds, completes with ``complete_effect`` once ``available`` holds, and
        otherwise runs ``timeout_effect`` after ``days_mission_timeout`` days.

        Missions are never paid for, so ``cost``/``custom_cost_trigger`` are
        rejected. Other ``create_decision`` keywords pass through.
        ``loc_name`` and ``loc_desc`` set the English name and description.
        Staged until ``save()``.

        Returns:
            The new mission.

        Raises:
            TypeError: A keyword is not one of ``create_decision()``'s; nothing changed.
            TypeError: ``days_mission_timeout`` is not a whole number.
            ValueError: ``days_mission_timeout`` is not positive, or a cost
                was given.
            AlreadyExistsError: The id exists and ``overwrite`` is false.
            NotFoundError: With a game install configured, the category is
                not defined.
        """
        kwargs: dict[str, Any] = dict(fields)
        if isinstance(days_mission_timeout, bool) or not isinstance(days_mission_timeout, int):
            raise TypeError("days_mission_timeout must be an int number of days")
        if days_mission_timeout <= 0:
            raise ValueError("days_mission_timeout must be positive")
        if kwargs.get("cost") or kwargs.get("custom_cost_trigger"):
            raise ValueError("Missions cannot have cost or custom_cost_trigger")
        _check_field_names(
            "create_mission",
            kwargs,
            _typed_dict_keys(MissionOptions) | {"cost", "custom_cost_trigger"},
        )
        decision = self.create_decision(
            category_id,
            decision_id,
            days_mission_timeout=days_mission_timeout,
            activation=activation,
            available=available,
            complete_effect=complete_effect,
            timeout_effect=timeout_effect,
            cancel_trigger=cancel_trigger,
            cancel_effect=cancel_effect,
            is_good=is_good,
            selectable_mission=selectable_mission,
            **kwargs,
        )
        if loc_name:
            self.set_loc(decision_id, loc_name)
        if loc_desc:
            self.set_loc(f"{decision_id}_desc", loc_desc)
        return decision

    def create_targeted_decision(
        self,
        category_id: str,
        decision_id: str,
        *,
        target_trigger: str = "",
        targets: Sequence[str] | str = (),
        target_array: str = "",
        target_root_trigger: str = "",
        state_target: bool | None = None,
        targets_dynamic: bool | None = None,
        target_non_existing: bool | None = None,
        on_map_mode: str = "",
        loc_name: str = "",
        loc_desc: str = "",
        **fields: Unpack[TargetedDecisionOptions],
    ) -> Decision:
        """Create a targeted decision with one instance per country or state.

        ``target_trigger`` filters candidates (``FROM`` is the target);
        ``targets``/``target_array`` restrict the candidate list and
        ``state_target=True`` targets states. At least one targeting field is
        required. Other ``create_decision`` keywords pass through.
        ``loc_name`` and ``loc_desc`` set the English name and description.
        Staged until ``save()``.

        Returns:
            The new decision.

        Raises:
            TypeError: A keyword is not one of ``create_decision()``'s; nothing changed.
            ValueError: No targeting field is set, or a target is not valid.
            AlreadyExistsError: The id exists and ``overwrite`` is false.
            NotFoundError: With a game install configured, the category is
                not defined.
        """
        kwargs: dict[str, Any] = dict(fields)
        _check_field_names(
            "create_targeted_decision", kwargs, _typed_dict_keys(TargetedDecisionOptions)
        )
        if not (target_trigger.strip() or targets or target_array or state_target):
            raise ValueError(
                "A targeted decision needs target_trigger, targets, target_array, "
                "or state_target=True"
            )
        decision = self.create_decision(
            category_id,
            decision_id,
            target_trigger=target_trigger,
            targets=targets,
            target_array=target_array,
            target_root_trigger=target_root_trigger,
            state_target=state_target,
            targets_dynamic=targets_dynamic,
            target_non_existing=target_non_existing,
            on_map_mode=on_map_mode,
            **kwargs,
        )
        if loc_name:
            self.set_loc(decision_id, loc_name)
        if loc_desc:
            self.set_loc(f"{decision_id}_desc", loc_desc)
        return decision

    def create_recovery_decision(
        self,
        category_id: str,
        decision_id: str,
        *,
        effect: str,
        visible: str = "",
        available: str = "",
        hidden: bool = False,
        icon: str = "",
        cost: int = 0,
        days_remove: int | None = None,
        fire_only_once: bool = True,
        loc_name: str = "",
        loc_desc: str = "",
        path: str | Path | None = None,
        overwrite: bool = False,
    ) -> Decision:
        """Create a repair/migration decision for already-started saves.

        The decision runs ``effect`` once (unless ``fire_only_once`` is
        false). ``hidden=True`` without ``visible`` hides it from the player
        (``visible = { always = no }``). A missing category is created (or,
        with ``path``, created or moved there). ``loc_name`` and ``loc_desc``
        set the English name and description. Staged until ``save()``.

        Raises:
            AlreadyExistsError: The decision exists and ``overwrite`` is false.
            ValueError: An id is not valid, or ``path`` is outside the mod.
        """
        # The decision is checked before the category changes, so a call
        # that raises leaves nothing staged.
        self._require_new_decision_id(decision_id, overwrite=overwrite)
        self._category_for_new_decisions(category_id, path=path)
        visible_body = visible or ("always = no" if hidden else "")
        decision = self.create_decision(
            category_id,
            decision_id,
            icon=icon,
            cost=cost,
            days_remove=days_remove,
            fire_only_once=fire_only_once,
            visible=visible_body,
            available=available,
            complete_effect=effect,
            path=path,
            overwrite=overwrite,
        )
        if loc_name:
            self.set_loc(decision_id, loc_name)
        if loc_desc:
            self.set_loc(f"{decision_id}_desc", loc_desc)
        return decision
