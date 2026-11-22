"""Creating, updating and deleting countries."""

from __future__ import annotations

from pathlib import Path

from ..characters import (
    Character,
    CharacterPortrait,
    CountryLeaderRole,
)
from ..countries import (
    _country_localisation_suffixes,
    country_file_paths,
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
from ._characters import CharactersLayer
from ._shared import (
    _set_fields,
)


class CountryAuthoringLayer(CharactersLayer):
    """Creating, updating and deleting countries."""

    def create_country(
        self,
        tag: str,
        name: str,
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
        allow_vanilla_override: bool = False,
    ) -> Country:
        tag = require_country_tag(tag)
        existing = self._countries.get(tag)
        if existing is not None and not overwrite:
            raise ValueError(
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
        if tag in self._vanilla_tags and not allow_vanilla_override:
            raise ValueError(
                f"Country tag '{tag}' is already used by vanilla HOI4. "
                "Choose an unused tag or pass allow_vanilla_override=True intentionally."
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
            character_id=f"{tag}_leader_1",
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
        # Files of this tag may be on disk although no tag file names them,
        # so the loader never read them. They are replaced: note them now.
        for country_file in country_file_paths(self.mod_root, country):
            self._note_target(country_file)
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

    def update_country(self, tag: str, **kwargs) -> bool:
        try:
            tag = require_country_tag(tag)
        except ValueError:
            return False
        country = self._countries.get(tag)
        if country is None and tag in self._vanilla_tags:
            country = self.get_country(tag)
        if country is None:
            return False
        self._materialize_country_override(country)
        leader_kwargs = {}
        other_kwargs = {}
        for key, value in kwargs.items():
            if key.startswith("leader_"):
                if key == "leader_character_id":
                    raise ValueError("Leader character IDs are immutable")
                leader_kwargs[key.removeprefix("leader_")] = value
            else:
                other_kwargs[key] = value
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
        self._dirty.add("countries")
        self._dirty_countries.add(tag)
        return True

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

    def delete_country(self, tag: str) -> bool:
        try:
            tag = require_country_tag(tag)
        except ValueError:
            return False
        if tag not in self._countries:
            return False
        country = self._countries.pop(tag)
        if any(
            path is not None and not path.resolve(strict=False).is_relative_to(self.mod_root)
            for path in (country.definition_path, country.history_path, country.character_path)
        ):
            self._countries[tag] = country
            raise ValueError(
                f"Cannot delete vanilla country '{tag}' without an explicit total-conversion "
                "tag replacement strategy"
            )
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
        return True

    def rebuild_country_color_table(self) -> Path:
        """Queue a complete table for a conversion replacing all country tags.

        Special engine registries can retain an inherited colors.txt even when
        ordinary country definitions are replaced. Rebuild from every effective
        country, never a partial caller-supplied subset. Call after country edits.
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
