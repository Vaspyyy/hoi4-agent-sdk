"""Script imports and the keyed ``common/`` definition API."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

from ..definitions import DefinitionKind, ScriptDefinition
from ..ideas import read_ideas_file, scan_idea_ids_file
from ..layers import relative_content_path
from ..paths import (
    resolve_mod_output_path,
)
from ..types import (
    Idea,
    ValidationIssue,
)
from ..errors import AlreadyExistsError, NotFoundError, already_exists, not_found
from ._core import CoreLayer
from ._shared import MISSING, _D, _Missing
from ..textio import decode_text, read_text


class DefinitionsLayer(CoreLayer):
    """Script imports and the keyed ``common/`` definition API."""

    def import_script_file(
        self, source_path: str | Path, relative_path: str | Path, *,
        overwrite: bool = False,
    ) -> Path:
        """Stage an unchanged, syntax-checked support script in this mod.

        Supports idea/law, scripted trigger/effect, AI strategy/naval goals, and
        source-derived technology, equipment and doctrine files, plus every
        ``DEFINITION_KINDS`` directory. Modeled
        country/state/event domains must use their facade authoring APIs. Imports
        participate in preview, transactions and atomic save, and never modify
        the source. Imported ideas are immediately available to validation and
        subsequent ``update_idea`` calls on this same instance.

        Args:
            source_path: The file to copy, anywhere on disk.
            relative_path: Where it goes inside the mod, such as
                ``"common/scripted_effects/my_effects.txt"``.
            overwrite: Replace a file that is already there or staged.

        Returns:
            The path the file will be written to on ``save()``.

        Raises:
            FileNotFoundError: ``source_path`` does not exist.
            FileExistsError: The target exists and ``overwrite`` is false.
            ParseError: The source is not valid script.
            ValueError: The target folder is not supported, a file is not a
                ``.txt`` file, or an id in the file is defined elsewhere or
                twice.
        """
        from ..definitions import DEFINITION_KINDS
        from ..parser import parse_pdx

        relative = relative_content_path(relative_path)
        supported = {
            Path("common/ideas"), Path("common/national_ideas"),
            Path("common/scripted_triggers"), Path("common/scripted_effects"),
            Path("common/ai_strategy"), Path("common/ai_strategy_plans"),
            Path("common/ai_navy/goals"), Path("common/doctrines/folders"),
            Path("common/technologies"), Path("common/units/equipment"),
            Path("common/special_projects/projects"),
            Path("common/ai_templates"), Path("common/ai_equipment"),
            Path("common/unit_medals"), Path("common/collections"),
            Path("common/resistance_compliance_modifiers"), Path("common/raids"), Path("common/raids/categories"),
            Path("common/operations"), Path("common/operation_phases"),
            Path("common/intelligence_agencies"), Path("common/intelligence_agency_upgrades"),
            Path("common/military_industrial_organization/organizations"),
            *(Path(kind.directory) for kind in DEFINITION_KINDS.values()),
            Path("common/bop"), Path("common/continuous_focus"), Path("common/occupation_laws"),
            Path("common/terrain"), Path("common/units/names_railway_guns"),
        }
        support_trees = (
            Path("common/factions"), Path("common/peace_conference"), Path("common/doctrines"),
        )
        permitted = relative.parent in supported or any(relative.parent.is_relative_to(root) for root in support_trees)
        if relative.suffix != ".txt" or not permitted:
            raise ValueError("Import requires a supported common support-script *.txt path")
        source = Path(source_path).expanduser().resolve(strict=True)
        if not source.is_file() or source.suffix != ".txt":
            raise ValueError("Source must be a .txt file")
        source_bytes = source.read_bytes()
        text = decode_text(source_bytes)
        parsed = parse_pdx(text)
        target = resolve_mod_output_path(self.mod_root, relative)
        if (target.exists() or target in self._script_file_updates) and not overwrite:
            raise FileExistsError(f"Script target already exists: {relative}")
        ideas: list[Idea] = []
        container = ""
        idea_domain = relative.parent in {Path("common/ideas"), Path("common/national_ideas")}
        if idea_domain:
            ideas, container = read_ideas_file(source)
            ids = [idea.id for idea in ideas]
            if len(ids) != len(set(ids)):
                raise ValueError("Imported idea file contains duplicate IDs")
            for idea in ideas:
                existing = self._ideas.get(idea.id)
                if existing is not None and existing.path != target:
                    raise ValueError(f"Imported idea {idea.id!r} conflicts with an already loaded definition")
            for directory in ("common/ideas", "common/national_ideas"):
                for other_relative, other_source in self.content_files(directory).items():
                    if Path(other_relative) == relative or other_source.suffix != ".txt":
                        continue
                    overlap = set(ids) & scan_idea_ids_file(other_source)
                    if overlap:
                        raise ValueError(f"Imported idea IDs conflict with {other_relative}: {sorted(overlap)}")
        elif relative.parent in {Path("common/scripted_triggers"), Path("common/scripted_effects")}:
            ids = [node.key for node in parsed.children if node.key is not None]
            if len(ids) != len(set(ids)):
                raise ValueError("Imported script contains duplicate IDs")
            candidates = {
                resolve_mod_output_path(self.mod_root, rel): path.read_text(encoding="utf-8-sig")
                for rel, path in self.content_files(relative.parent).items()
                if path.suffix == ".txt"
            }
            candidates.update({path: body for path, body in self._script_file_updates.items()
                               if path.parent == target.parent})
            for other_target, body in candidates.items():
                if other_target != target:
                    other_ids = {node.key for node in parse_pdx(body).children}
                    if set(ids) & other_ids:
                        raise ValueError(f"Imported script IDs conflict with {other_target.name}")
        # Mutation starts only after every parse/path/collision check succeeds.
        if idea_domain:
            replaced = {key for key, idea in self._ideas.items() if idea.path == target}
            for key in replaced:
                self._ideas.pop(key)
            self._dirty_ideas.difference_update(replaced)
            self._dirty_idea_files.discard(target)
            for idea in ideas:
                idea.path = target
                self._ideas[idea.id] = idea
            self._idea_file_containers[target] = container
            self._original_files[target] = text
        # The file this import replaces, as it is now.
        self._note_target(target)
        self._script_file_updates[target] = text
        self._format_donors[target] = source_bytes
        self._scan_cache.clear()
        self._equipment_unlock_cache = None
        self._dirty.add("script_files")
        return target

    # ── Keyed common/ definitions ─────────────────────────────

    def definition_kinds(self) -> dict[str, DefinitionKind]:
        """Return the ``common/`` domains supported by the definition APIs."""

        from ..definitions import DEFINITION_KINDS
        return dict(DEFINITION_KINDS)

    def _definition_texts(self, kind: DefinitionKind) -> dict[str, tuple[Path, str, bool]]:
        """Map effective relative files of ``kind`` to (source, text, pending)."""

        from ..definitions import in_domain
        files: dict[str, tuple[Path, str, bool]] = {}
        for relative, source in self.content_files(kind.directory).items():
            if not in_domain(kind, Path(relative)):
                continue
            target = resolve_mod_output_path(self.mod_root, relative)
            pending = self._script_file_updates.get(target)
            if pending is not None:
                files[relative] = (target, pending, True)
                continue
            try:
                # What is read here is what an edit of the file builds on.
                files[relative] = (source, self._keep_source(source, read_text(source)), False)
            except (OSError, UnicodeDecodeError):
                continue
        for target, text in self._script_file_updates.items():
            if not target.is_relative_to(self.mod_root):
                continue
            relative_path = target.relative_to(self.mod_root)
            if in_domain(kind, relative_path) and str(relative_path) not in files:
                files[str(relative_path)] = (target, text, True)
        return dict(sorted(files.items()))

    def list_definitions(self, kind: str | DefinitionKind) -> list[str]:
        """List the ids of the effective definitions of one domain, sorted.

        Pending edits count. Inherited base-game and base-mod files are read
        through the configured content layers; files overridden by the mod
        are hidden. ``get_definition(kind, id)`` returns one of them.

        Raises:
            ValueError: ``kind`` is not a definition kind.
        """

        return sorted({definition.id for definition in self._effective_definitions(kind)})

    def _effective_definitions(self, kind: str | DefinitionKind) -> list[ScriptDefinition]:
        """Every effective definition of one domain in load order, duplicates included."""

        from ..definitions import get_definition_kind, read_definitions
        resolved = get_definition_kind(kind)
        result: list[ScriptDefinition] = []
        for relative, (source, text, pending) in self._definition_texts(resolved).items():
            try:
                result.extend(read_definitions(
                    text, resolved, relative_path=relative, source_path=source, pending=pending,
                ))
            except ValueError:
                continue
        return result

    def get_definition(
        self,
        kind: str | DefinitionKind,
        definition_id: str,
        *,
        default: _D | _Missing = MISSING,
    ) -> ScriptDefinition | _D:
        """Return the first effective definition of ``kind`` with ``definition_id``.

        Args:
            default: Returned instead of raising ``NotFoundError`` when no
                effective definition has this id.

        Raises:
            NotFoundError: No effective definition of that kind has this id
                and no default was given.
            ValueError: ``kind`` is not a definition kind.
        """

        from ..definitions import get_definition_kind
        resolved = get_definition_kind(kind)
        for definition in self._effective_definitions(resolved):
            if definition.id == definition_id:
                return definition
        if default is not MISSING:
            return default
        raise self._definition_not_found(resolved, definition_id)

    def _definition_not_found(self, kind: DefinitionKind, definition_id: str) -> NotFoundError:
        return not_found(
            kind.name.replace("_", " "),
            definition_id,
            self.list_definitions(kind),
        )

    def _queue_definition_text(self, relative: Path, text: str, original: str) -> Path:
        target = resolve_mod_output_path(self.mod_root, relative)
        if text == original and target not in self._script_file_updates:
            return target
        # Usually read (and noted) by ``_definition_texts``; a file of
        # another kind's folder at an explicit path is noted here.
        self._note_target(target)
        self._script_file_updates[target] = text
        self._scan_cache.clear()
        self._equipment_unlock_cache = None
        self._dirty.add("script_files")
        return target

    def create_definition(
        self,
        kind: str | DefinitionKind,
        definition_id: str,
        body: str,
        *,
        path: str | Path | None = None,
        overwrite: bool = False,
    ) -> ScriptDefinition:
        """Queue one keyed definition in a supported ``common/`` domain.

        ``body`` is the definition interior without its outer braces (and, for
        entry-style domains such as scripted localisation, without the
        identifier field). New definitions go to ``path`` or the domain's
        generated file; container wrappers such as ``technologies = { }`` are
        created or reused automatically. Identifiers must be unique across the
        effective domain. With ``overwrite=True`` an existing definition is
        patched in its current file, copying an inherited file into the mod
        first. Unrelated bytes of every touched file are preserved.

        Kinds include ``scripted_effect``, ``scripted_trigger``,
        ``country_leader_trait``, ``unit_leader_trait``, ``technology`` and
        ``sub_unit``; ``definition_kinds()`` lists them all.

        Returns:
            The queued definition (``source_path`` is the file it goes to).

        Raises:
            AlreadyExistsError: The id exists and ``overwrite`` is false.
            ValueError: ``kind`` is unknown, the path is outside the domain's
                folder, or the body escapes its block.
        """

        from ..definitions import (
            check_definition_body,
            get_definition_kind,
            in_domain,
            upsert_definition,
        )
        resolved = get_definition_kind(kind)
        body = check_definition_body(resolved, definition_id, body)
        existing = self.get_definition(resolved, definition_id, default=None)
        if existing is not None:
            if not overwrite:
                raise already_exists(
                    resolved.name.replace("_", " "),
                    definition_id,
                    hint=f"It is in {existing.relative_path}; pass overwrite=True or "
                    "call update_definition() to change it.",
                )
            copies = sum(
                1
                for definition in self._effective_definitions(resolved)
                if definition.id == definition_id
            )
            if copies > 1:
                raise ValueError(
                    f"{resolved.name} {definition_id!r} is defined {copies} times; "
                    "remove the extra definitions before overwriting it"
                )
            relative = Path(existing.relative_path)
            if path is not None and relative_content_path(path) != relative:
                raise AlreadyExistsError(
                    f"{resolved.name} {definition_id!r} already exists in {existing.relative_path}; "
                    "update it there or delete it first"
                )
        else:
            relative = relative_content_path(path or resolved.default_path)
            if not in_domain(resolved, relative):
                raise ValueError(
                    f"{resolved.name} definitions must use a {resolved.directory}/*.txt path"
                )
        texts = self._definition_texts(resolved)
        original = texts[str(relative)][1] if str(relative) in texts else ""
        updated = upsert_definition(original, resolved, definition_id, body)
        from ..parser import parse_pdx
        parse_pdx(updated.lstrip("﻿"))
        self._queue_definition_text(relative, updated, original)
        return self.get_definition(resolved, definition_id)

    def update_definition(
        self, kind: str | DefinitionKind, definition_id: str, body: str
    ) -> ScriptDefinition:
        """Replace an existing definition's body in its current file.

        Returns:
            The updated definition.

        Raises:
            NotFoundError: No effective definition of that kind has this id.
        """

        from ..definitions import get_definition_kind
        resolved = get_definition_kind(kind)
        self.get_definition(resolved, definition_id)
        return self.create_definition(resolved, definition_id, body, overwrite=True)

    def delete_definition(self, kind: str | DefinitionKind, definition_id: str) -> None:
        """Remove a definition from its file, preserving every sibling.

        A definition inherited from the game or a base mod is removed by
        writing an override of its file.

        Raises:
            NotFoundError: No effective definition of that kind has this id.
        """

        from ..definitions import get_definition_kind, remove_definition
        resolved = get_definition_kind(kind)
        existing = self.get_definition(resolved, definition_id)
        relative = Path(existing.relative_path)
        original = self._definition_texts(resolved)[existing.relative_path][1]
        self._queue_definition_text(
            relative, remove_definition(original, resolved, definition_id), original,
        )

    def _validate_definitions(self) -> list[ValidationIssue]:
        """Report duplicate mod definitions and character traits nobody defines."""

        from ..definitions import DEFINITION_KINDS
        issues: list[ValidationIssue] = []
        for kind in DEFINITION_KINDS.values():
            directory = self.mod_root / kind.directory
            if not directory.is_dir() and not any(
                path.is_relative_to(directory) for path in self._script_file_updates
            ):
                continue
            seen: dict[str, str] = {}
            for definition in self._effective_definitions(kind):
                if definition.id not in seen:
                    seen[definition.id] = definition.relative_path
                    continue
                first = seen[definition.id]
                if not (definition.source_path.is_relative_to(self.mod_root)
                        or self.mod_root.joinpath(first).exists()):
                    continue
                issues.append(ValidationIssue(
                    message=(f"Duplicate {kind.name} {definition.id!r} in "
                             + (f"{first} (twice)" if first == definition.relative_path
                                else f"{first} and {definition.relative_path}")),
                    severity="error",
                    code="duplicate_definition_id",
                    file_path=str(definition.source_path),
                    related_file_path=first,
                ))
        if self.hoi4_install is None:
            return issues
        known_traits = {
            definition_id
            for kind in ("country_leader_trait", "unit_leader_trait")
            for definition_id in self.list_definitions(kind)
        }
        if not known_traits:
            return issues
        for character in self._characters.values():
            path = character.path
            if path is None or not Path(path).is_relative_to(self.mod_root):
                continue
            roles = list(character.roles)
            for instance in getattr(character, "instances", []):
                roles.extend(instance.roles)
            for role in roles:
                for trait in getattr(role, "traits", []):
                    if trait not in known_traits:
                        issues.append(ValidationIssue(
                            message=(f"Character {character.id!r} uses trait {trait!r}, "
                                     "which no country_leader or unit_leader file defines"),
                            severity="warning",
                            code="unknown_character_trait",
                            file_path=str(path),
                        ))
        return issues

    def create_scripted_localisation(
        self,
        name: str,
        entries: Sequence[tuple[str, str]],
        *,
        path: str | Path | None = None,
        overwrite: bool = False,
    ) -> ScriptDefinition:
        """Queue a ``defined_text`` usable as ``[Root.GetName]`` or ``[GetName]``.

        ``entries`` are ``(trigger_body, localisation_key)`` pairs evaluated in
        order; an empty trigger is an unconditional fallback and belongs last.
        Staged until ``save()``.

        Returns:
            The staged definition.

        Raises:
            ValueError: ``name`` is not a valid name, an entry other than the
                last has no trigger, or ``path`` is not in the domain's folder.
            AlreadyExistsError: ``name`` exists and ``overwrite`` is false.
        """

        from ..definitions import render_scripted_localisation
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError("Invalid scripted localisation name")
        if any(not trigger.strip() for trigger, _ in entries[:-1]):
            raise ValueError("Only the last scripted localisation entry may omit its trigger")
        return self.create_definition(
            "scripted_localisation", name, render_scripted_localisation(entries),
            path=path, overwrite=overwrite,
        )

    def create_ai_strategy(
        self,
        plan_id: str,
        strategies: Sequence[tuple[str, str, int]],
        *,
        allowed: str = "",
        enable: str = "",
        abort: str = "",
        extra: str = "",
        path: str | Path | None = None,
        overwrite: bool = False,
    ) -> ScriptDefinition:
        """Queue an ``ai_strategy`` plan from ``(type, id, value)`` entries.

        For example ``[("antagonize", "FRA", 100), ("role_ratio", "armor", 15)]``;
        use an empty id for strategy types that take none. ``allowed``,
        ``enable`` and ``abort`` are trigger bodies; ``extra`` is added to
        the plan as it is. Staged until ``save()``.

        Returns:
            The staged definition.

        Raises:
            AlreadyExistsError: ``plan_id`` exists and ``overwrite`` is false.
            ValueError: ``path`` is not in the domain's folder, or a body
                escapes its block.
        """

        from ..definitions import render_ai_strategy
        return self.create_definition(
            "ai_strategy", plan_id,
            render_ai_strategy(strategies, allowed=allowed, enable=enable, abort=abort, extra=extra),
            path=path, overwrite=overwrite,
        )

    def create_opinion_modifier(
        self,
        modifier_id: str,
        *,
        value: int | float,
        decay: int | float | None = None,
        trade: bool = False,
        target: bool = False,
        days: int | None = None,
        months: int | None = None,
        years: int | None = None,
        min_trust: int | float | None = None,
        max_trust: int | float | None = None,
        path: str | Path | None = None,
        overwrite: bool = False,
    ) -> ScriptDefinition:
        """Queue an opinion modifier for ``add_opinion_modifier``; staged until ``save()``.

        Options left at ``None`` or ``False`` are not written.

        Returns:
            The staged definition.

        Raises:
            AlreadyExistsError: ``modifier_id`` exists and ``overwrite`` is false.
            ValueError: ``path`` is not in the domain's folder.
        """

        from ..definitions import render_scalar_mapping
        fields: dict[str, object] = {"value": value}
        optional: dict[str, object | None] = {
            "decay": decay, "days": days, "months": months, "years": years,
            "min_trust": min_trust, "max_trust": max_trust,
        }
        fields.update({key: item for key, item in optional.items() if item is not None})
        if trade:
            fields["trade"] = True
        if target:
            fields["target"] = True
        return self.create_definition(
            "opinion_modifier", modifier_id, render_scalar_mapping(fields),
            path=path, overwrite=overwrite,
        )

    def create_static_modifier(
        self,
        modifier_id: str,
        modifiers: dict[str, str | int | float | bool],
        *,
        path: str | Path | None = None,
        overwrite: bool = False,
    ) -> ScriptDefinition:
        """Queue a named static modifier (``common/modifiers``); staged until ``save()``.

        Args:
            modifiers: The modifier values by name, such as
                ``{"stability_factor": 0.1}``.

        Returns:
            The staged definition.

        Raises:
            ValueError: ``modifiers`` is empty, or ``path`` is not in the
                domain's folder.
            AlreadyExistsError: ``modifier_id`` exists and ``overwrite`` is false.
        """

        from ..definitions import render_scalar_mapping
        if not modifiers:
            raise ValueError("A static modifier needs at least one modifier")
        return self.create_definition(
            "static_modifier", modifier_id, render_scalar_mapping(modifiers),
            path=path, overwrite=overwrite,
        )
