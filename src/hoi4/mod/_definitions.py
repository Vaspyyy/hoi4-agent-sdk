"""Script imports and the keyed ``common/`` definition API."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

from ..definitions import DefinitionKind, ScriptDefinition
from ..ideas import read_ideas_file, scan_idea_ids_file
from ..layers import relative_content_path
from ..parser import (
    PdxNode,
)
from ..patching import (
    set_block,
)
from ..paths import (
    resolve_mod_output_path,
)
from ..types import (
    Idea,
    ValidationError,
)
from ._core import CoreLayer
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

    def create_scripted_trigger(
        self, trigger_id: str, body: str, *, path: str | Path | None = None,
        overwrite: bool = False,
    ) -> Path:
        """Queue a named scripted trigger, preserving sibling definitions.

        ``body`` contains no outer braces. Syntax is checked immediately;
        installed-game domain/scope correctness remains the caller's responsibility.
        Like other mutations, this participates in preview, transactions and save.
        """
        from ..parser import parse_pdx
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", trigger_id):
            raise ValueError("Invalid scripted trigger identifier")
        relative = relative_content_path(path or "common/scripted_triggers/00_generated_triggers.txt")
        if relative.parent != Path("common/scripted_triggers") or relative.suffix != ".txt":
            raise ValueError("Scripted triggers must use a common/scripted_triggers/*.txt path")
        parsed_request = parse_pdx(f"{trigger_id} = {{\n{body}\n}}")
        definitions = [node for node in parsed_request.children if not node.is_comment]
        if len(definitions) != 1 or definitions[0].key != trigger_id or not definitions[0].is_block():
            raise ValueError("Scripted trigger body must not escape its enclosing block")
        requested = definitions[0]

        def remember(text: str, index: dict[str, PdxNode]) -> None:
            # Keep only a few recent immutable text versions when appending
            # hundreds of definitions; avoid retaining quadratic history.
            if text not in self._script_trigger_index_cache and len(self._script_trigger_index_cache) >= 8:
                self._script_trigger_index_cache.pop(next(iter(self._script_trigger_index_cache)))
            self._script_trigger_index_cache[text] = index

        def index_for(text: str) -> dict[str, PdxNode]:
            if text not in self._script_trigger_index_cache:
                index: dict[str, PdxNode] = {}
                for node in parse_pdx(text).children:
                    if node.key is not None:
                        index.setdefault(node.key, node)
                remember(text, index)
            return self._script_trigger_index_cache[text]

        def structure(node: PdxNode) -> tuple:
            return (node.key, node.value, node.operator, node.block, node.quoted,
                    tuple(structure(child) for child in node.children if not child.is_comment))
        target = resolve_mod_output_path(self.mod_root, relative)
        source = self.content_source(relative)
        text = self._script_file_updates.get(target)
        if text is None:
            # What is read here is what the staged file builds on.
            text = self._keep_source(source, read_text(source)) if source else ""
        current_index = index_for(text)
        existing = current_index.get(trigger_id)
        if existing is not None and not overwrite:
            raise ValueError(f"Scripted trigger {trigger_id!r} already exists")
        for other_relative, other_source in self.content_files("common/scripted_triggers").items():
            if Path(other_relative) == relative:
                continue
            other_target = resolve_mod_output_path(self.mod_root, other_relative)
            other_text = self._script_file_updates.get(other_target)
            if other_text is None:
                other_text = other_source.read_text(encoding="utf-8-sig")
            if trigger_id in index_for(other_text):
                raise ValueError(f"Scripted trigger {trigger_id!r} already exists in {other_relative}")
        for other_target, other_text in self._script_file_updates.items():
            if (other_target.parent == target.parent and other_target != target
                    and trigger_id in index_for(other_text)):
                raise ValueError(f"Scripted trigger {trigger_id!r} already queued in another file")
        # Collision checks still run for an otherwise unchanged request.
        if existing is not None and structure(existing) == structure(requested):
            return target
        if existing is None:
            fragment = set_block("", trigger_id, body)
            newline = "\r\n" if "\r\n" in text else "\n"
            fragment = fragment.replace("\n", newline)
            updated = text + (newline if text and not text.endswith("\n") else "") + fragment + newline
        else:
            updated = set_block(text, trigger_id, body)
        if updated == text:
            return target
        updated_index = dict(current_index)
        updated_index[trigger_id] = requested
        remember(updated, updated_index)
        self._script_file_updates[target] = updated
        self._dirty.add("scripted_triggers")
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

    def list_definitions(self, kind: str | DefinitionKind) -> list[ScriptDefinition]:
        """List effective definitions of one domain, including pending edits.

        Inherited base-game and base-mod files are read through the configured
        content layers; files overridden by the mod are hidden.
        """

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

    def get_definition(self, kind: str | DefinitionKind, definition_id: str) -> ScriptDefinition | None:
        """Return the first effective definition with ``definition_id``."""

        for definition in self.list_definitions(kind):
            if definition.id == definition_id:
                return definition
        return None

    def _queue_definition_text(self, relative: Path, text: str, original: str) -> Path:
        target = resolve_mod_output_path(self.mod_root, relative)
        if text == original and target not in self._script_file_updates:
            return target
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
    ) -> Path:
        """Queue one keyed definition in a supported ``common/`` domain.

        ``body`` is the definition interior without its outer braces (and, for
        entry-style domains such as scripted localisation, without the
        identifier field). New definitions go to ``path`` or the domain's
        generated file; container wrappers such as ``technologies = { }`` are
        created or reused automatically. Identifiers must be unique across the
        effective domain. With ``overwrite=True`` an existing definition is
        patched in its current file, copying an inherited file into the mod
        first. Unrelated bytes of every touched file are preserved.
        """

        from ..definitions import (
            check_definition_body,
            get_definition_kind,
            in_domain,
            upsert_definition,
        )
        resolved = get_definition_kind(kind)
        body = check_definition_body(resolved, definition_id, body)
        existing = self.get_definition(resolved, definition_id)
        if existing is not None:
            if not overwrite:
                raise ValueError(
                    f"{resolved.name} {definition_id!r} already exists in {existing.relative_path}"
                )
            relative = Path(existing.relative_path)
            if path is not None and relative_content_path(path) != relative:
                raise ValueError(
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
        return self._queue_definition_text(relative, updated, original)

    def update_definition(self, kind: str | DefinitionKind, definition_id: str, body: str) -> Path:
        """Replace an existing definition's body in its current file."""

        from ..definitions import get_definition_kind
        resolved = get_definition_kind(kind)
        if self.get_definition(resolved, definition_id) is None:
            raise KeyError(f"Unknown {resolved.name}: {definition_id}")
        return self.create_definition(resolved, definition_id, body, overwrite=True)

    def delete_definition(self, kind: str | DefinitionKind, definition_id: str) -> Path:
        """Remove a definition from its file, preserving every sibling."""

        from ..definitions import get_definition_kind, remove_definition
        resolved = get_definition_kind(kind)
        existing = self.get_definition(resolved, definition_id)
        if existing is None:
            raise KeyError(f"Unknown {resolved.name}: {definition_id}")
        relative = Path(existing.relative_path)
        original = self._definition_texts(resolved)[existing.relative_path][1]
        return self._queue_definition_text(
            relative, remove_definition(original, resolved, definition_id), original,
        )

    def _validate_definitions(self) -> list[ValidationError]:
        """Report duplicate mod definitions and character traits nobody defines."""

        from ..definitions import DEFINITION_KINDS
        issues: list[ValidationError] = []
        for kind in DEFINITION_KINDS.values():
            directory = self.mod_root / kind.directory
            if not directory.is_dir() and not any(
                path.is_relative_to(directory) for path in self._script_file_updates
            ):
                continue
            seen: dict[str, str] = {}
            for definition in self.list_definitions(kind):
                if definition.id not in seen:
                    seen[definition.id] = definition.relative_path
                    continue
                first = seen[definition.id]
                if not (definition.source_path.is_relative_to(self.mod_root)
                        or self.mod_root.joinpath(first).exists()):
                    continue
                issues.append(ValidationError(
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
            definition.id
            for kind in ("country_leader_trait", "unit_leader_trait")
            for definition in self.list_definitions(kind)
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
                        issues.append(ValidationError(
                            message=(f"Character {character.id!r} uses trait {trait!r}, "
                                     "which no country_leader or unit_leader file defines"),
                            severity="warning",
                            code="unknown_character_trait",
                            file_path=str(path),
                        ))
        return issues

    def create_scripted_effect(
        self, effect_id: str, body: str, *, path: str | Path | None = None,
        overwrite: bool = False,
    ) -> Path:
        """Queue a named scripted effect (``common/scripted_effects``)."""

        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", effect_id):
            raise ValueError("Invalid scripted effect identifier")
        return self.create_definition(
            "scripted_effect", effect_id, body, path=path, overwrite=overwrite,
        )

    def create_scripted_localisation(
        self,
        name: str,
        entries: Sequence[tuple[str, str]],
        *,
        path: str | Path | None = None,
        overwrite: bool = False,
    ) -> Path:
        """Queue a ``defined_text`` usable as ``[Root.GetName]`` or ``[GetName]``.

        ``entries`` are ``(trigger_body, localisation_key)`` pairs evaluated in
        order; an empty trigger is an unconditional fallback and belongs last.
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

    def create_country_leader_trait(
        self, trait_id: str, body: str, *, path: str | Path | None = None,
        overwrite: bool = False,
    ) -> Path:
        """Queue a country-leader or advisor trait in ``common/country_leader``."""

        return self.create_definition(
            "country_leader_trait", trait_id, body, path=path, overwrite=overwrite,
        )

    def create_unit_leader_trait(
        self, trait_id: str, body: str, *, path: str | Path | None = None,
        overwrite: bool = False,
    ) -> Path:
        """Queue an army/navy commander trait in ``common/unit_leader``."""

        return self.create_definition(
            "unit_leader_trait", trait_id, body, path=path, overwrite=overwrite,
        )

    def create_technology(
        self, technology_id: str, body: str, *, path: str | Path | None = None,
        overwrite: bool = False,
    ) -> Path:
        """Queue a technology inside a ``technologies = { }`` container.

        The new identifier is immediately known to ``set_technology`` and
        tech-bonus validation in this ``Mod`` instance.
        """

        return self.create_definition(
            "technology", technology_id, body, path=path, overwrite=overwrite,
        )

    def create_sub_unit(
        self, sub_unit_id: str, body: str, *, path: str | Path | None = None,
        overwrite: bool = False,
    ) -> Path:
        """Queue a battalion/company type inside ``sub_units = { }``."""

        return self.create_definition(
            "sub_unit", sub_unit_id, body, path=path, overwrite=overwrite,
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
    ) -> Path:
        """Queue an ``ai_strategy`` plan from ``(type, id, value)`` entries.

        For example ``[("antagonize", "FRA", 100), ("role_ratio", "armor", 15)]``;
        use an empty id for strategy types that take none.
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
    ) -> Path:
        """Queue an opinion modifier for ``add_opinion_modifier``."""

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
    ) -> Path:
        """Queue a named static modifier (``common/modifiers``)."""

        from ..definitions import render_scalar_mapping
        if not modifiers:
            raise ValueError("A static modifier needs at least one modifier")
        return self.create_definition(
            "static_modifier", modifier_id, render_scalar_mapping(modifiers),
            path=path, overwrite=overwrite,
        )
