"""Character, role and instance authoring."""

from __future__ import annotations

from typing import Any, Unpack

import copy
import dataclasses
from pathlib import Path

from ..fields import CharacterFields, CharacterInstanceFields, CharacterRoleFields
from ..characters import (
    AdvisorRole,
    Character,
    CharacterInstance,
    CharacterRole,
    load_characters_file,
    role_key,
)
from ..paths import (
    require_country_tag,
    require_script_id,
)
from ..textio import read_text
from ..errors import AlreadyExistsError, NotFoundError, not_found
from ._countries import CountriesLayer
from ._shared import MISSING, _D, _Missing, _check_field_names, _check_model, _check_model_lists
from ._localisation import LocalisationLayer


def _require_character_lookup_id(value: str) -> str:
    """Validate an id to look up: any script word, including non-ASCII letters."""

    text = str(value)
    if not text or any(
        character.isspace() or not character.isprintable() or character in '{}=<>#"/\\'
        for character in text
    ):
        return require_script_id(text, label="character ID")
    return text


def _missing_role(
    character: Character, role_type: str, occurrence: int | None, count: int
) -> NotFoundError:
    which = "" if occurrence is None else f" occurrence {occurrence}"
    return NotFoundError(
        f"Character '{character.id}' has no '{role_type}' role{which} "
        f"({count} '{role_type}' roles).",
        kind="character role",
        id=role_type,
    )


class CharactersLayer(CountriesLayer, LocalisationLayer):
    """Character, role and instance authoring."""

    # ── Characters ──────────────────────────────────────────────

    def list_characters(
        self,
        tag: str | None = None,
        *,
        include_vanilla: bool = True,
    ) -> list[str]:
        """Return the sorted ids of the known characters, optionally of one country.

        Args:
            tag: Only characters of this country.
            include_vanilla: Also load and list the characters of the game
                and the base mods.

        Raises:
            ValueError: ``tag`` is not a valid country tag.
        """
        normalized = require_country_tag(tag) if tag is not None else None
        if include_vanilla and self._base_content is not None:
            self._load_vanilla_characters(normalized)
        return sorted(
            character_id
            for character_id, character in self._characters.items()
            if normalized is None or character.country_tag == normalized
        )

    def get_character(
        self,
        character_id: str,
        *,
        include_vanilla: bool = True,
        default: _D | _Missing = MISSING,
    ) -> Character | _D:
        """Return the character ``character_id``.

        ``include_vanilla`` also loads the game's and base mods' characters
        of the tag in the id's prefix.

        Args:
            default: Returned instead of raising ``NotFoundError`` when no
                character has this id.

        Raises:
            NotFoundError: No character has this id and no default was given.
            ValueError: ``character_id`` is not a valid id.
        """
        # Look up before validating: the game accepts ids the SDK would not
        # generate, such as ``COL_alfonso_lópez_pumarejo`` in vanilla.
        character = self._characters.get(str(character_id))
        if character is not None:
            return character
        character_id = _require_character_lookup_id(character_id)
        if include_vanilla and self._base_content is not None:
            prefix = character_id.split("_", 1)[0]
            tag = (
                prefix
                if len(prefix) == 3 and prefix.isalnum() and prefix.upper() == prefix
                else None
            )
            self._load_vanilla_characters(tag)
            character = self._characters.get(character_id)
        if character is None:
            if default is not MISSING:
                return default
            raise not_found("character", character_id, self._characters)
        return character

    def create_character(
        self,
        tag: str,
        character: Character,
        *,
        recruit: bool = True,
        overwrite: bool = False,
        path: str | Path | None = None,
    ) -> Character:
        """Add ``character`` to the mod as a character of ``tag``; staged until ``save()``.

        The mod keeps a copy, so later changes to ``character`` do not reach
        it. An advisor role without an ``idea_token`` gets the character id,
        and one without ``allowed`` gets ``original_tag = <tag>``. The name
        (or the id) goes into the English localisation as the character's
        name and description.

        Args:
            recruit: Add the character to the country's recruited characters.
                Pass false for a character that an event or focus unlocks.
            overwrite: Replace an existing character with this id.
            path: The output file. Without it, the existing character's mod
                file or ``common/characters/<tag>_characters.txt``.

        Returns:
            The character as stored in the mod.

        Raises:
            NotFoundError: ``recruit`` is true and no one defines ``tag``.
            AlreadyExistsError: The id exists and ``overwrite`` is false.
            ValueError: ``tag`` or the character id is not valid, or ``path``
                is outside the mod.
        """
        tag = require_country_tag(tag)
        _check_model("create_character", character, "character")
        character_id = require_script_id(character.id, label="character ID")
        if recruit and not self._is_known_country_tag(tag):
            raise not_found(
                "country",
                tag,
                self._country_tag_candidates(),
                plural="countries",
                hint=(
                    f"Cannot recruit character '{character_id}'. Create or load the "
                    "country first, or pass recruit=False for an event-unlocked definition."
                ),
            )
        existing = self._characters.get(character_id)
        if existing is not None and not overwrite:
            raise AlreadyExistsError(
                f"Character '{character_id}' already exists. "
                "Use overwrite=True only for deliberate replacement."
            )
        target = self._output_path(
            path
            or (
                existing.path
                if existing is not None
                and existing.path is not None
                and existing.path.resolve(strict=False).is_relative_to(self.mod_root)
                else Path("common") / "characters" / f"{tag}_characters.txt"
            ),
        )
        replacement = copy.deepcopy(character)
        replacement.id = character_id
        replacement.country_tag = tag
        replacement.path = target
        replacement.recruitment_expected = recruit
        if existing is not None:
            replacement.source_index = existing.source_index
            replacement.raw_block = existing.raw_block
            replacement.touched_fields.update(
                {"name", "portraits", "roles", "instances"}
            )
        else:
            replacement.source_index = -1
        self._prepare_character_roles(replacement, tag)
        self._characters[character_id] = replacement
        self._dirty_characters.add(character_id)
        self._dirty_character_files.add(target)
        self._dirty.add("characters")
        if recruit:
            self._set_character_recruited(tag, character_id, recruited=True)
        self._sync_character_loc(replacement)
        return replacement

    def update_character(self, character_id: str, **fields: Unpack[CharacterFields]) -> Character:
        """Patch a character's top-level ``name`` or ``portraits``.

        Roles and instances have their own methods. A character loaded from
        the game or a base mod is copied into the mod first.

        Returns:
            The updated character.

        Raises:
            NotFoundError: No character has this id.
            TypeError: A field name is unknown, or ``portraits`` holds something
                that is not a ``CharacterPortrait``; nothing changed.
        """
        kwargs: dict[str, Any] = dict(fields)
        character = self.get_character(character_id)
        if "country_tag" in kwargs:
            raise TypeError(
                "update_character() cannot update country_tag, even to the existing tag. "
                "Remove country_tag from this call; persistent character reassignment "
                "is not supported."
            )
        _check_field_names("update_character", dict(kwargs), {"name", "portraits"})
        _check_model_lists("update_character", Character, kwargs)
        self._materialize_character_override(character)
        for key, value in kwargs.items():
            setattr(character, key, copy.deepcopy(value))
        character.touched_fields.update(kwargs)
        self._dirty_characters.add(character.id)
        if character.path is not None:
            self._dirty_character_files.add(character.path)
        self._dirty.add("characters")
        self._sync_character_loc(character)
        return character

    def delete_character(
        self,
        character_id: str,
        *,
        remove_recruitment: bool = True,
    ) -> None:
        """Stage the deletion of a character and the localisation the mod holds for it.

        ``remove_recruitment`` also removes it from its country's
        ``recruit_character`` list. A character of the game or a base mod
        is deleted by writing an override of its file.

        Raises:
            NotFoundError: No character has this id.
        """
        character = self.get_character(character_id)
        self._materialize_character_override(character)
        self._characters.pop(character.id, None)
        if character.path is not None:
            self._dirty_character_files.add(character.path)
        self._dirty_characters.add(character.id)
        self._dirty.add("characters")
        if remove_recruitment and character.country_tag:
            self._set_character_recruited(
                character.country_tag,
                character.id,
                recruited=False,
            )
        self._delete_loc_all_languages(character.id)
        self._delete_loc_all_languages(f"{character.id}_desc")

    def add_character_instance(
        self,
        character_id: str,
        instance: CharacterInstance,
    ) -> CharacterInstance:
        """Append a copy of ``instance`` to a character; staged until ``save()``.

        A character of the game or a base mod is copied into the mod first.

        Returns:
            The added instance as stored in the mod.

        Raises:
            NotFoundError: No character has this id.
            ValueError: The character comes from the game but no game install
                is configured.
        """
        character = self.get_character(character_id)
        _check_model("add_character_instance", instance, "instance")
        self._materialize_character_override(character)
        new_instance = copy.deepcopy(instance)
        new_instance.source_occurrence = -1
        self._prepare_roles(
            new_instance.roles,
            character.country_tag,
            character.id,
        )
        character.instances.append(new_instance)
        character.touched_fields.add("instances")
        self._mark_character_dirty(character)
        return new_instance

    def update_character_instance(
        self,
        character_id: str,
        occurrence: int,
        **fields: Unpack[CharacterInstanceFields],
    ) -> CharacterInstance:
        """Patch ``allowed``, ``name`` or ``portraits`` of one instance (zero-based).

        Roles of an instance have their own methods.

        Returns:
            The updated instance.

        Raises:
            NotFoundError: The character or that instance does not exist.
            TypeError: A field name is unknown; nothing changed.
        """
        kwargs: dict[str, Any] = dict(fields)
        character = self.get_character(character_id)
        self._require_character_instance(character, occurrence)
        _check_field_names(
            "update_character_instance", dict(kwargs), {"allowed", "name", "portraits"}
        )
        _check_model_lists("update_character_instance", CharacterInstance, kwargs)
        self._materialize_character_override(character)
        instance = character.instances[occurrence]
        for key, value in kwargs.items():
            setattr(instance, key, copy.deepcopy(value))
        instance.touched_fields.update(kwargs)
        character.touched_fields.add("instances")
        self._mark_character_dirty(character)
        return instance

    def delete_character_instance(
        self,
        character_id: str,
        occurrence: int,
    ) -> None:
        """Remove one ``instance`` block (zero-based ``occurrence``) of a character.

        Raises:
            NotFoundError: The character or that instance does not exist.
        """
        character = self.get_character(character_id)
        self._require_character_instance(character, occurrence)
        self._materialize_character_override(character)
        character.instances.pop(occurrence)
        character.touched_fields.add("instances")
        self._mark_character_dirty(character)

    @staticmethod
    def _require_character_instance(character: Character, occurrence: int) -> None:
        if occurrence < 0 or occurrence >= len(character.instances):
            raise NotFoundError(
                f"Character '{character.id}' has no instance occurrence {occurrence} "
                f"({len(character.instances)} instances).",
                kind="character instance",
                id=occurrence,
            )

    def add_character_role(
        self,
        character_id: str,
        role: CharacterRole,
        *,
        instance_occurrence: int | None = None,
    ) -> CharacterRole:
        """Append a copy of ``role`` to a character or one of its instances; staged until ``save()``.

        A character of the game or a base mod is copied into the mod first.

        Args:
            instance_occurrence: Add the role to this instance (zero-based)
                instead of to the character itself.

        Returns:
            The added role as stored in the mod.

        Raises:
            NotFoundError: The character, or the chosen instance, does not exist.
            ValueError: The character comes from the game but no game install
                is configured.
        """
        character = self.get_character(character_id)
        self._materialize_character_override(character)
        roles, scope = self._character_role_scope(character, instance_occurrence)
        new_role = copy.deepcopy(role)
        new_role.source_occurrence = -1
        self._prepare_roles([new_role], character.country_tag, character.id)
        roles.append(new_role)
        scope.touched_fields.add("roles")
        if isinstance(scope, CharacterInstance):
            character.touched_fields.add("instances")
        self._mark_character_dirty(character)
        return new_role

    def update_character_role(
        self,
        character_id: str,
        role_type: str,
        *,
        occurrence: int | None = None,
        instance_occurrence: int | None = None,
        **fields: Unpack[CharacterRoleFields],
    ) -> CharacterRole:
        """Patch one role of a character or of one of its instances.

        When the character has several roles of ``role_type``, pass
        ``occurrence=`` (zero-based). ``instance_occurrence=`` picks an
        instance instead of the character's own roles.

        Returns:
            The updated role.

        Raises:
            NotFoundError: The character, the instance or the role does not exist.
            TypeError: A field name is unknown; nothing changed.
            ValueError: Several roles match and no ``occurrence`` was given.
        """
        kwargs: dict[str, Any] = dict(fields)
        if role_type not in {
            "country_leader",
            "advisor",
            "corps_commander",
            "field_marshal",
            "navy_leader",
        }:
            raise ValueError(f"Unsupported character role type: {role_type}")
        character = self.get_character(character_id)
        roles, scope = self._character_role_scope(character, instance_occurrence)
        matches = [role for role in roles if role_key(role) == role_type]
        if not matches:
            raise _missing_role(character, role_type, occurrence, 0)
        if occurrence is None:
            if len(matches) != 1:
                raise ValueError(
                    f"Character '{character_id}' has {len(matches)} '{role_type}' "
                    "roles; pass occurrence= to select one."
                )
            role = matches[0]
        else:
            if occurrence < 0 or occurrence >= len(matches):
                raise _missing_role(character, role_type, occurrence, len(matches))
            role = matches[occurrence]
        internal = {"raw_block", "touched_fields", "source_occurrence", "kind"}
        valid = {
            field.name for field in dataclasses.fields(role)
        } - internal
        _check_field_names("update_character_role", dict(kwargs), valid)
        self._materialize_character_override(character)
        for key, value in kwargs.items():
            setattr(role, key, copy.deepcopy(value))
        role.touched_fields.update(kwargs)
        scope.touched_fields.add("roles")
        if isinstance(scope, CharacterInstance):
            character.touched_fields.add("instances")
        self._mark_character_dirty(character)
        return role

    def remove_character_role(
        self,
        character_id: str,
        role_type: str,
        *,
        occurrence: int | None = None,
        instance_occurrence: int | None = None,
    ) -> None:
        """Remove one role of a character or of one of its instances.

        Raises:
            NotFoundError: The character, the instance or the role does not exist.
            ValueError: Several roles match and no ``occurrence`` was given.
        """
        character = self.get_character(character_id)
        roles, scope = self._character_role_scope(character, instance_occurrence)
        indexes = [
            index for index, role in enumerate(roles) if role_key(role) == role_type
        ]
        if not indexes:
            raise _missing_role(character, role_type, occurrence, 0)
        if occurrence is None:
            if len(indexes) != 1:
                raise ValueError(
                    f"Character '{character_id}' has {len(indexes)} '{role_type}' "
                    "roles; pass occurrence= to select one."
                )
            index = indexes[0]
        else:
            if occurrence < 0 or occurrence >= len(indexes):
                raise _missing_role(character, role_type, occurrence, len(indexes))
            index = indexes[occurrence]
        self._materialize_character_override(character)
        roles.pop(index)
        scope.touched_fields.add("roles")
        if isinstance(scope, CharacterInstance):
            character.touched_fields.add("instances")
        self._mark_character_dirty(character)

    def _character_role_scope(
        self,
        character: Character,
        instance_occurrence: int | None,
    ) -> tuple[
        list[CharacterRole],
        Character | CharacterInstance,
    ]:
        if instance_occurrence is None:
            return character.roles, character
        if instance_occurrence < 0 or instance_occurrence >= len(character.instances):
            raise NotFoundError(
                f"Character '{character.id}' has no instance occurrence "
                f"{instance_occurrence} ({len(character.instances)} instances).",
                kind="character instance",
                id=instance_occurrence,
            )
        instance = character.instances[instance_occurrence]
        return instance.roles, instance

    def _prepare_character_roles(self, character: Character, tag: str) -> None:
        self._prepare_roles(character.roles, tag, character.id)
        for instance in character.instances:
            self._prepare_roles(instance.roles, tag, character.id)

    @staticmethod
    def _prepare_roles(
        roles: list[CharacterRole],
        tag: str,
        character_id: str = "",
    ) -> None:
        occurrences: dict[str, int] = {}
        for role in roles:
            if isinstance(role, AdvisorRole):
                if not role.idea_token:
                    role.idea_token = character_id
                if not role.allowed and tag:
                    role.allowed = f"original_tag = {tag}"
            key = role_key(role)
            if role.source_occurrence < 0 and role.raw_block:
                role.source_occurrence = occurrences.get(key, 0)
            occurrences[key] = occurrences.get(key, 0) + 1

    def _set_character_recruited(
        self,
        tag: str,
        character_id: str,
        *,
        recruited: bool,
    ) -> None:
        if not recruited and not self._is_known_country_tag(tag):
            return  # no country, so no recruitment list to remove it from
        country = self.get_country(tag)
        self._materialize_country_override(country)
        values = list(country.recruited_characters)
        if country.leader and country.leader.character_id not in values:
            values.append(country.leader.character_id)
        if recruited and character_id not in values:
            values.append(character_id)
        if not recruited:
            values = [value for value in values if value != character_id]
        country.recruited_characters = values
        country.touched_fields.add("recruited_characters")
        self._dirty_countries.add(tag)
        self._dirty.add("countries")

    def _mark_character_dirty(self, character: Character) -> None:
        self._dirty_characters.add(character.id)
        if character.path is not None:
            self._dirty_character_files.add(character.path)
        self._dirty.add("characters")

    def _materialize_character_override(self, character: Character) -> None:
        source = character.path
        if source is None:
            target = self.mod_root / "common" / "characters" / (
                f"{character.country_tag or 'mod'}_characters.txt"
            )
            character.path = target
            self._dirty_character_files.add(target)
            return
        if source.resolve(strict=False).is_relative_to(self.mod_root):
            return
        relative = (
            self._base_content.relative(source.resolve(strict=False))
            if self._base_content is not None else None
        )
        if relative is None:
            raise ValueError(
                f"Cannot retarget character '{character.id}' without a HOI4 install"
            )
        target = self._output_path(relative)
        source_text = read_text(source)
        for item in load_characters_file(source, country_tag=character.country_tag):
            cached = self._characters.get(item.id)
            if cached is not None and cached.path == source:
                cached.path = target
        self._original_files.setdefault(
            target,
            self._read_noted_text(target) or source_text,
        )
        character.path = target
        self._dirty_character_files.add(target)

    def _load_vanilla_characters(self, tag: str | None) -> None:
        if self._base_content is None:
            return
        folder = "common/characters"
        if tag is None:
            paths = list(self._base_content.glob(folder, "*.txt").values())
        else:
            preferred = [
                self._base_content.file(f"{folder}/{tag}.txt"),
                self._base_content.file(f"{folder}/{tag}_characters.txt"),
            ]
            paths = [path for path in preferred if path is not None]
            if not paths:
                paths = list(self._base_content.glob(folder, f"{tag}*.txt").values())
        for path in paths:
            cache_key = f"character_file:{path}"
            if cache_key in self._scan_cache:
                continue
            try:
                for character in load_characters_file(path, country_tag=tag or ""):
                    self._characters.setdefault(character.id, character)
            except (OSError, ValueError):
                pass
            self._scan_cache[cache_key] = set()
            # Characters of the game or a base mod, cached by this lookup:
            # save() and discard() drop them with the section.
            self._reload_on_discard.add("characters")

    @staticmethod
    def _all_character_roles(character: Character) -> tuple[CharacterRole, ...]:
        return (
            *character.roles,
            *(role for instance in character.instances for role in instance.roles),
        )
