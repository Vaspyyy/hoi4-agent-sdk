"""Creating, updating and deleting countries."""

from __future__ import annotations

from typing import Any, Unpack

from pathlib import Path

from ..fields import CountryFields
from ..characters import (
    Character,
    CharacterPortrait,
    CountryLeaderRole,
)
from ..countries import (
    _country_localisation_suffixes,
    country_file_paths,
    country_history_target,
    serialize_country_colors_file,
)
from ..paths import (
    require_country_tag,
    safe_file_stem,
)
from ..types import (
    Country,
    Leader,
)
from ..errors import AlreadyExistsError, ReadOnlyError, not_found
from ._characters import CharactersLayer
from ._shared import (
    _check_field_names,
    _check_fields,
    _set_fields,
    _settable_fields,
)


class CountryAuthoringLayer(CharactersLayer):
    """Creating, updating and deleting countries."""

    def create_country(
        self,
        tag: str,
        name: str,
        *,
        adjective: str = "",
        color: tuple[int, int, int] = (128, 128, 128),
        capital: int = 1,
        research_slots: int | None = None,
        ruling_party: str = "democratic",
        elections_allowed: bool = True,
        popularities: dict[str, int] | None = None,
        leader_name: str = "Leader",
        leader_ideology: str | None = None,
        ideas: list[str] | None = None,
        stability: str | int | float | None = None,
        war_support: str | int | float | None = None,
        technologies: dict[str, int] | None = None,
        oob: str = "",
        overwrite: bool = False,
        overwrite_vanilla: bool = False,
    ) -> Country:
        """Create a country with its tag, history, colours and leader; staged until ``save()``.

        The leader becomes a character ``<TAG>_leader_1`` with a country
        leader role and a portrait sprite ``GFX_portrait_<TAG>_leader_1``.
        The name and adjective go into the English localisation.

        Args:
            adjective: Defaults to ``name``.
            color: The map colour as ``(red, green, blue)``, each 0 to 255.
            capital: The id of the capital state.
            ruling_party: The ideology group in power, such as ``"democratic"``.
            popularities: Party popularity in percent, by ideology group.
            leader_ideology: The leader's sub-ideology. Defaults to the first
                one the ruling party has.
            ideas: Ideas (national spirits, laws) the country starts with.
            technologies: Technologies the country starts with, by id, with
                the value HOI4 writes (normally ``1``).
            oob: The name of the starting order of battle file.
            overwrite: Replace a country of this tag that the mod defines, or
                tag files on disk that no tag file registers.
            overwrite_vanilla: Allow a tag that the game or a base mod uses.

        Returns:
            The new country.

        Raises:
            AlreadyExistsError: The mod defines ``tag`` or the character
                ``<TAG>_leader_1`` and ``overwrite`` is false, or the game or
                a base mod defines ``tag`` and ``overwrite_vanilla`` is false.
                Nothing is staged then.
            FileExistsError: Files of ``tag`` are on disk although no tag
                file registers it, and ``overwrite`` is false.
            ValueError: ``tag`` is not a valid tag.
        """
        tag = require_country_tag(tag)
        existing = self._countries.get(tag)
        if existing is not None and not overwrite:
            raise AlreadyExistsError(
                f"Country tag '{tag}' already exists in the mod. "
                "Use overwrite=True only when intentionally replacing that mod country."
            )
        if existing is None and not overwrite:
            orphan_candidates = [
                self.mod_root / "common" / "countries" / f"{tag}.txt",
                self.mod_root / "common" / "characters" / f"{tag}_characters.txt",
                self.mod_root / "common" / "characters" / f"{tag}.txt",
            ]
            history_dir = self.mod_root / "history" / "countries"
            if history_dir.is_dir():
                orphan_candidates.extend(sorted(history_dir.glob(f"{tag}*.txt")))
            conflicts = [path for path in orphan_candidates if path.exists()]
            if conflicts:
                displayed = ", ".join(self._display_path(path) for path in conflicts)
                raise FileExistsError(
                    f"Country tag '{tag}' is not registered, but country files already "
                    f"exist and would be overwritten: {displayed}. Pass overwrite=True "
                    "only if replacing those orphaned files is intentional."
                )
        if tag in self._vanilla_tags and not overwrite_vanilla:
            raise AlreadyExistsError(
                f"Country tag '{tag}' is already used by vanilla HOI4. "
                "Choose an unused tag or pass overwrite_vanilla=True intentionally."
            )
        leader_id = f"{tag}_leader_1"
        if not overwrite and leader_id in self._characters:
            # Checked here, not only in create_character() below, so a call
            # that raises leaves nothing staged.
            raise AlreadyExistsError(
                f"Character '{leader_id}' already exists. "
                "Use overwrite=True only for deliberate replacement.",
                kind="character",
                id=leader_id,
            )
        if leader_ideology is None:
            leader_ideology = self._default_leader_ideology_for_party(ruling_party)

        def preserved_mod_path(path: Path | None) -> Path | None:
            if path is None:
                return None
            resolved = path.resolve(strict=False)
            if resolved.is_relative_to(self.mod_root):
                return resolved
            relative = (
                self._base_content.relative(resolved) if self._base_content is not None else None
            )
            return self._output_path(relative) if relative is not None else None

        leader = Leader(
            name=leader_name,
            character_id=leader_id,
            ideology=leader_ideology,
            portrait_slug="leader_1",
        )
        country = Country(
            tag=tag,
            name=name,
            adjective=adjective or name,
            color=color,
            capital=capital,
            research_slots=research_slots,
            ruling_party=ruling_party,
            elections_allowed=elections_allowed,
            popularities=popularities or {},
            leader=leader,
            ideas=ideas or [],
            stability=stability,
            war_support=war_support,
            technologies=dict(technologies or {}),
            oob=oob,
            recruited_characters=[leader.character_id],
            definition_path=(
                preserved_mod_path(existing.definition_path) if existing is not None else None
            ),
            history_path=(
                preserved_mod_path(existing.history_path) if existing is not None else None
            ),
            character_path=(
                preserved_mod_path(existing.character_path) if existing is not None else None
            ),
            touched_fields={"*"},
        )
        self._countries[tag] = country
        self._note_country_files(country)
        self._created_country_tags.add(tag)
        self._dirty.add("countries")
        self._dirty_countries.add(tag)
        self._sync_country_loc(country)
        leader_character = Character(
            id=leader.character_id,
            country_tag=tag,
            name=leader.name,
            portraits=[
                CharacterPortrait(
                    channel="civilian",
                    large=f"GFX_portrait_{tag}_{leader.portrait_slug}",
                )
            ],
            roles=[CountryLeaderRole(ideology=leader.ideology)],
        )
        self.create_character(
            tag,
            leader_character,
            recruit=False,
            overwrite=overwrite,
        )

        ideas_dir = self.mod_root / "common" / "ideas"
        if ideas_dir.exists():
            name_lower = safe_file_stem(name.lower(), fallback=tag.lower())
            candidates = [
                name_lower,
                tag.lower(),
            ]
            for stem in candidates:
                p = ideas_dir / f"{stem}.txt"
                if p.exists():
                    self._cached_idea_file[tag] = p
                    break

        return country

    def update_country(self, tag: str, **fields: Unpack[CountryFields]) -> Country:
        """Patch modeled country fields; ``leader_<field>`` sets the leader's fields.

        A country of the game or a base mod is copied into the mod first.

        Returns:
            The updated country.

        Raises:
            NotFoundError: No one defines ``tag``.
            TypeError: A field name is unknown; nothing changed.
            ValueError: ``tag`` is not a valid tag.
        """
        kwargs: dict[str, Any] = dict(fields)
        tag = require_country_tag(tag)
        country = self._countries.get(tag)
        if country is None and tag in self._vanilla_tags:
            country = self.get_country(tag)
        if country is None:
            raise not_found("country", tag, self._country_tag_candidates(), plural="countries")
        leader_kwargs = {}
        other_kwargs = {}
        for key, value in kwargs.items():
            if key.startswith("leader_"):
                if key == "leader_character_id":
                    raise ValueError("Leader character IDs are immutable")
                leader_kwargs[key.removeprefix("leader_")] = value
            else:
                other_kwargs[key] = value
        leader_fields = {
            f"leader_{name}"
            for name in _settable_fields(
                country.leader or Leader(name="Leader"), exclude={"character_id"}
            )
        }
        _check_fields("update_country", country, other_kwargs, extra=leader_fields)
        _check_field_names(
            "update_country",
            {f"leader_{key}": value for key, value in leader_kwargs.items()},
            leader_fields,
        )
        self._materialize_country_override(country)
        if other_kwargs:
            _set_fields(country, other_kwargs)
            if "color" in other_kwargs and "color_ui" not in other_kwargs:
                # A new map colour without its own UI colour: the UI follows it.
                country.color_ui = None
        if leader_kwargs:
            if country.leader is None:
                country.leader = Leader(name="Leader", character_id=f"{tag}_leader_1")
            _set_fields(country.leader, leader_kwargs)
        country.touched_fields.update(other_kwargs)
        country.touched_fields.update(f"leader_{key}" for key in leader_kwargs)
        if "technologies" in other_kwargs:
            self._materialize_country_history(country)
            self._synchronize_pending_country_technologies(country)
        if {"name", "adjective", "popularities", "ruling_party"} & set(
            other_kwargs
        ) or leader_kwargs:
            self._sync_country_loc(country)
        if leader_kwargs and country.leader is not None:
            self._sync_leader_character_model(country)
        self._note_country_files(country)
        self._dirty.add("countries")
        self._dirty_countries.add(tag)
        return country

    def _note_country_files(self, country: Country) -> None:
        """Note the files a save will write for ``country``, as they are now.

        They may be on disk although nothing has read them: files of a tag
        that no tag file registers, or the history file that a new name
        moves to (``TAG - Name.txt``). The save replaces them.
        """

        targets = country_file_paths(self.mod_root, country)
        # Both names the history file can get. Which one the save takes
        # depends on whether the file overrides an inherited one, and that
        # is asked again when the save renders: it can have changed by then.
        targets.extend(
            country_history_target(self.mod_root, country, keep_history_path=keep)
            for keep in (False, True)
        )
        for target in targets:
            self._note_target(target)

    def _sync_leader_character_model(self, country: Country) -> None:
        leader = country.leader
        if leader is None:
            return
        character = self._characters.get(leader.character_id)
        if character is None:
            return
        self._materialize_character_override(character)
        character.name = leader.name
        portrait_slug = (
            leader.portrait_slug
            or leader.character_id.removeprefix(country.tag + "_")
        )
        character.portraits = [
            CharacterPortrait(
                channel="civilian",
                large=f"GFX_portrait_{country.tag}_{portrait_slug}",
            )
        ]
        character.touched_fields.update({"name", "portraits"})
        roles = [
            role
            for role in character.roles
            if isinstance(role, CountryLeaderRole)
        ]
        if len(roles) == 1:
            roles[0].ideology = leader.ideology
            roles[0].touched_fields.add("ideology")
            character.touched_fields.add("roles")
        self._mark_character_dirty(character)

    def delete_country(self, tag: str) -> None:
        """Stage the deletion of a country the mod defines, with its generated localisation.

        Raises:
            NotFoundError: No one defines ``tag``.
            ReadOnlyError: The game or a base mod defines ``tag``; removing it
                needs a total-conversion tag replacement instead.
        """
        tag = require_country_tag(tag)
        read_only = ReadOnlyError(
            f"Cannot delete game country '{tag}' without an explicit total-conversion "
            "tag replacement strategy"
        )
        if tag not in self._countries:
            if self._is_known_country_tag(tag):
                raise read_only
            raise not_found("country", tag, self.list_countries(), plural="countries")
        country = self._countries[tag]
        if any(
            path is not None and not path.resolve(strict=False).is_relative_to(self.mod_root)
            for path in (country.definition_path, country.history_path, country.character_path)
        ):
            raise read_only
        del self._countries[tag]
        self._deleted_countries[tag] = country
        self._created_country_tags.discard(tag)
        loc_keys: list[str] = []
        for suffix in _country_localisation_suffixes(country):
            key = f"{tag}{suffix}"
            loc_keys.extend((key, f"{key}_DEF"))
        loc_keys.append(f"{tag}_ADJ")
        if country.leader:
            loc_keys.extend((country.leader.character_id, f"{country.leader.character_id}_desc"))
        for key in set(loc_keys):
            self._delete_loc_all_languages(key)
        self._dirty.add("countries")
        self._dirty_countries.add(tag)

    def rebuild_country_color_table(self) -> Path:
        """Stage a new ``common/countries/colors.txt`` with the colour of every loaded country.

        For a total conversion that replaces all country tags: the game can
        keep an inherited colour table even when the tag files are replaced.
        The table is built from ``list_countries()``, so call it after the
        country edits. Written on ``save()``.

        Returns:
            The path of the staged table.

        Raises:
            ValueError: The descriptor does not replace ``common/country_tags``,
                or no country is loaded.
        """
        from ..layers import replace_paths
        if Path("common/country_tags") not in replace_paths(self.mod_root):
            raise ValueError("Full color-table rebuilding requires explicit country-tag replacement")
        countries = {tag: self.get_country(tag) for tag in self.list_countries()}
        if not countries:
            raise ValueError("Cannot replace country colors with an empty registry")
        # The table this rebuild replaces, as it is now.
        target = self._output_path("common/countries/colors.txt")
        self._script_file_updates[target] = serialize_country_colors_file(
            "", {tag: country.color for tag, country in countries.items()})
        self._dirty.add("script_files")
        return target
