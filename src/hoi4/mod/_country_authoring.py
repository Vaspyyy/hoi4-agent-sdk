"""Creating, updating and deleting countries."""

from __future__ import annotations

from collections.abc import Mapping
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
    country_color_tags,
    country_file_paths,
    country_history_target,
    has_start_politics,
    legacy_leader_details,
    remove_legacy_leader,
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
    _nullable_fields,
    _set_fields,
    _settable_fields,
)


def _check_country_values(method: str, values: Mapping[str, Any], *, allow_none: bool) -> None:
    """Refuse a colour, a list or a mapping of the wrong shape before anything changes.

    These values are only read when the country's files are written. A wrong
    shape used to raise in the middle of the call or later in ``preview()``,
    with the country already changed.
    """
    for name in ("popularities", "technologies"):
        if name not in values or (values[name] is None and allow_none):
            continue
        value = values[name]
        if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
            raise TypeError(
                f"{method}(): {name} must be a dict of names to numbers, "
                f"got {type(value).__name__}"
            )
    if "ideas" in values and not (values["ideas"] is None and allow_none):
        value = values["ideas"]
        if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
            raise TypeError(f"{method}(): ideas must be a list of idea ids, got {value!r}")
    for name in ("color", "color_ui"):
        if name not in values or (values[name] is None and (allow_none or name == "color_ui")):
            continue
        value = values[name]
        if (
            not isinstance(value, (tuple, list))
            or len(value) != 3
            or not all(
                isinstance(part, int) and not isinstance(part, bool) and 0 <= part <= 255
                for part in value
            )
        ):
            raise ValueError(
                f"{method}(): {name} must be three whole numbers from 0 to 255 "
                f"(red, green, blue), got {value!r}"
            )


class CountryAuthoringLayer(CharactersLayer):
    """Creating, updating and deleting countries."""

    def create_country(
        self,
        tag: str,
        name: str,
        *,
        adjective: str = "",
        color: tuple[int, int, int] | None = None,
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
                Without it a new tag is grey ``(128, 128, 128)`` and a tag of
                the game or a base mod keeps its colour.
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
                The new country takes the place of their files (the same
                definition and history file names) and starts from nothing:
                their history (technology per DLC, doctrines, old leader
                blocks) is not carried over, only the colour and the
                graphical culture are. To change such a country and
                keep the rest, use ``update_country()``.

        Returns:
            The new country.

        Raises:
            TypeError: ``ideas``, ``popularities`` or ``technologies`` is not a list or a dict as described; nothing changed.
            AlreadyExistsError: The mod defines ``tag`` or the character
                ``<TAG>_leader_1`` and ``overwrite`` is false, or the game or
                a base mod defines ``tag`` and ``overwrite_vanilla`` is false.
                Nothing is staged then.
            FileExistsError: Files of ``tag`` are on disk although no tag
                file registers it, and ``overwrite`` is false.
            ValueError: ``tag`` is not a valid tag.
        """
        tag = require_country_tag(tag)
        _check_country_values(
            "create_country",
            {"color": color, "popularities": popularities, "ideas": ideas,
             "technologies": technologies},
            allow_none=True,
        )
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
        inherited_cultures: tuple[str, str] | None = None
        inherited_color_ui: tuple[int, int, int] | None = None
        if existing is None and overwrite_vanilla and tag in self._vanilla_tags:
            # The game finds the country through the files its tag file and
            # history folder name: the new country takes their places, or it
            # would sit in files the game never reads (or reads twice).
            existing = self.get_country(tag)
            if color is None:
                color = existing.color
                inherited_color_ui = existing.color_ui
            inherited_cultures = (existing.graphical_culture, existing.graphical_culture_2d)
        if color is None:
            color = (128, 128, 128)
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
            # The leader's character goes to the mod's own character file of
            # the tag: a file of the game holds its whole roster, and a file
            # of that name in the mod would replace all of it.
            character_path=(
                existing.character_path.resolve(strict=False)
                if existing is not None
                and existing.character_path is not None
                and existing.character_path.resolve(strict=False).is_relative_to(self.mod_root)
                else self._output_path(self._default_character_file(tag))
            ),
            touched_fields={"*"},
        )
        if inherited_cultures is not None:
            # How the country's units and cities look is not politics: it
            # stays what the game gives the tag.
            country.graphical_culture, country.graphical_culture_2d = inherited_cultures
            country.color_ui = inherited_color_ui
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

    def _has_color_table_entry(self, tag: str) -> bool:
        """Whether a save writes this tag's colours to ``common/countries/colors.txt``.

        The same test as the save uses: a tag of the game or a base mod, or one
        the mod's own table already lists.
        """
        if tag in self._vanilla_tags:
            return True
        table = self._read_current_text(self.mod_root / "common" / "countries" / "colors.txt")
        return tag in country_color_tags(table)

    def update_country(self, tag: str, **fields: Unpack[CountryFields]) -> Country:
        """Patch modeled country fields; ``leader_<field>`` sets the leader's fields.

        A country of the game or a base mod is copied into the mod first.

        Returns:
            The updated country.

        Raises:
            NotFoundError: No one defines ``tag``.
            TypeError: A field name is unknown; nothing changed.
            ValueError: ``tag`` is not a valid tag, or ``color_ui`` is set for
                a country that is not in the colour table (a country the
                mod adds: the game uses its map colour).
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
        _check_country_values("update_country", other_kwargs, allow_none=False)
        leader_can_be_none = _nullable_fields(Leader) or frozenset()
        for key, value in leader_kwargs.items():
            if value is None and key not in leader_can_be_none:
                raise TypeError(
                    f"update_country(): leader_{key} cannot be None. Pass an empty value "
                    "of the field's own type to clear it."
                )
        if other_kwargs.get("color_ui") is not None and not self._has_color_table_entry(tag):
            raise ValueError(
                f"update_country(): color_ui of '{tag}' cannot be set: a save would not "
                "write it. A UI colour is written for a country of the game's colour "
                "table and for one the mod's saved common/countries/colors.txt lists. "
                "A country the mod adds takes its map colour from its own file, which "
                "has no color_ui."
            )
        frequency = other_kwargs.get("election_frequency")
        if frequency is not None and (isinstance(frequency, bool) or not isinstance(frequency, int)):
            raise TypeError(
                "update_country(): election_frequency is a number of months (an int) or "
                f"None, not {type(frequency).__name__}."
            )
        if "last_election" in other_kwargs and not isinstance(other_kwargs["last_election"], str):
            raise TypeError(
                'update_country(): last_election is a date string such as "1935.4.7", not '
                f"{type(other_kwargs['last_election']).__name__}."
            )
        if (
            {"last_election", "election_frequency"} & set(other_kwargs)
            and "ruling_party" not in other_kwargs
            and (
                not has_start_politics(country.raw_history)
                if country.raw_history
                # No history file at all (a dynamic tag of the game): a new
                # country of this session has politics in the file it gets.
                else tag not in self._created_country_tags
            )
        ):
            raise ValueError(
                f"update_country(): '{tag}' has no set_politics block at the start date "
                "to put the election in. Pass ruling_party (and elections_allowed) in "
                "the same call to create one."
            )
        self._materialize_country_override(country)
        if other_kwargs:
            _set_fields(country, other_kwargs)
            if "color" in other_kwargs and "color_ui" not in other_kwargs:
                # A new map colour without its own UI colour: the UI follows it.
                country.color_ui = None
        legacy: dict[str, Any] = {}
        if leader_kwargs:
            if country.leader is None:
                country.leader = Leader(name="Leader", character_id=f"{tag}_leader_1")
            elif not country.leader.character_id:
                # An old-style ``create_country_leader`` block becomes a
                # character: the block goes, the character is recruited.
                self._materialize_country_history(country)
                legacy = legacy_leader_details(country.raw_history, country.leader.name)
                country.raw_history = remove_legacy_leader(
                    country.raw_history, country.leader.name
                )
                country.leader.character_id = f"{tag}_leader_1"
                country.leader.portrait_slug = "leader_1"
                if country.leader.character_id not in country.recruited_characters:
                    country.recruited_characters.append(country.leader.character_id)
                country.touched_fields.add("recruited_characters")
            _set_fields(country.leader, leader_kwargs, method="update_country")
            leader_id = country.leader.character_id
            if leader_id and self.get_character(leader_id, default=None) is None:
                # No one defines the leader's character yet (an old-style
                # block, or a country without a leader): the character
                # itself is created, so a save writes it whoever writes the
                # character file, and the country recruits it.
                if leader_id not in country.recruited_characters:
                    country.recruited_characters.append(leader_id)
                country.touched_fields.add("recruited_characters")
                if not country.leader.portrait_slug:
                    country.leader.portrait_slug = leader_id.removeprefix(f"{tag}_")
                self.create_character(
                    tag,
                    Character(
                        id=country.leader.character_id,
                        country_tag=tag,
                        name=country.leader.name,
                        # An old-style block keeps its picture, traits and
                        # expiry; a portrait slug passed in this call wins.
                        portraits=[
                            CharacterPortrait(
                                channel="civilian",
                                large=(
                                    legacy["picture"]
                                    if "picture" in legacy and "portrait_slug" not in leader_kwargs
                                    else f"GFX_portrait_{tag}_{country.leader.portrait_slug}"
                                ),
                            )
                        ],
                        roles=[
                            CountryLeaderRole(
                                ideology=country.leader.ideology,
                                traits=list(legacy.get("traits", [])),
                                **({"expire": legacy["expire"]} if "expire" in legacy else {}),
                            )
                        ],
                    ),
                    recruit=False,
                )
        country.touched_fields.update(other_kwargs)
        country.touched_fields.update(f"leader_{key}" for key in leader_kwargs)
        if "technologies" in other_kwargs:
            self._materialize_country_history(country)
            self._synchronize_pending_country_technologies(country)
        if {"name", "adjective", "popularities", "ruling_party"} & set(
            other_kwargs
        ) or leader_kwargs:
            self._sync_country_loc(
                country,
                names="name" in other_kwargs,
                adjective=bool({"name", "adjective"} & set(other_kwargs)),
                leader=bool(leader_kwargs),
            )
        if leader_kwargs and country.leader is not None:
            self._sync_leader_character_model(
                country, portrait="portrait_slug" in leader_kwargs
            )
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

    def _sync_leader_character_model(self, country: Country, *, portrait: bool = True) -> None:
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
        character.touched_fields.add("name")
        # The portraits follow a new portrait slug; a change of the name or
        # the ideology leaves the leader's pictures as they are.
        if portrait or not character.portraits:
            character.portraits = [
                CharacterPortrait(
                    channel="civilian",
                    large=f"GFX_portrait_{country.tag}_{portrait_slug}",
                )
            ]
            character.touched_fields.add("portraits")
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
        # The characters of the country's own file go with it (the save
        # would otherwise write the file again right after deleting it),
        # and so do their names.
        removed_characters: list[str] = []
        if country.character_path is not None:
            for character_id, character in list(self._characters.items()):
                if character.path == country.character_path:
                    del self._characters[character_id]
                    self._dirty_characters.discard(character_id)
                    removed_characters.append(character_id)
            self._dirty_character_files.discard(country.character_path)
            self._reload_on_discard.add("characters")
        loc_keys: list[str] = []
        for suffix in _country_localisation_suffixes(country):
            key = f"{tag}{suffix}"
            loc_keys.extend((key, f"{key}_DEF"))
        loc_keys.append(f"{tag}_ADJ")
        if country.leader and country.leader.character_id:
            loc_keys.extend((country.leader.character_id, f"{country.leader.character_id}_desc"))
        for character_id in removed_characters:
            loc_keys.extend((character_id, f"{character_id}_desc"))
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
