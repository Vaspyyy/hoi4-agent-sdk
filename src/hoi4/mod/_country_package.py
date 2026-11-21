"""Country package completeness validation."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Sequence

from ..characters import (
    AdvisorRole,
    ArmyCommanderRole,
    CountryLeaderRole,
)
from ..country_package import CountryPackageReport
from ..oob import (
    OrderOfBattle,
)
from ..parser import (
    ParseError,
    PdxNode,
    parse_pdx,
)
from ..paths import (
    require_country_tag,
)
from ..types import (
    Country,
    ValidationError,
)
from ..localisation import DEFAULT_LOCALISATION_LANGUAGE, parse_localization_dir
from ..syntax import parse
from ..validation import validate_country
from ._characters import CharactersLayer
from ._context import ContextLayer
from ._oob_validation import OobValidationLayer
from ._oobs import OobsLayer


class CountryPackageLayer(OobValidationLayer, ContextLayer, OobsLayer, CharactersLayer):
    """Country package completeness validation."""

    def _validate_countries(self, known_parties: set[str]) -> list[ValidationError]:
        """Check every country's history values and its variant unlocks."""

        errors: list[ValidationError] = []
        # Without base mods the mod's tables hold only its own files, so
        # also read the names the game itself ships.
        inherited: set[str] = set()
        if self._content_layers is None and self._base_content_root is not None:
            entries, _ = parse_localization_dir(
                self._base_content_root / "localisation" / DEFAULT_LOCALISATION_LANGUAGE
            )
            inherited = set(entries)

        def has_localisation(key: str) -> bool:
            return (
                self.get_loc(key) is not None
                or self._normalize_loc_key(key) in inherited
            )

        dynamic_tags = self._dynamic_country_tags()
        for country in list(self._countries.values()):
            errors.extend(
                validate_country(
                    country,
                    known_parties=known_parties,
                    has_localisation=has_localisation,
                    leader_ideologies=self._recruited_leader_ideologies(country),
                    dynamic_tag=country.tag in dynamic_tags,
                )
            )
            errors.extend(self._validate_equipment_variant_unlocks(country))
        return errors

    def _dynamic_country_tags(self) -> set[str]:
        """Tags declared after ``dynamic_tags = yes`` in a tag file."""

        tags: set[str] = set()
        for _, text in self._effective_script_texts("common/country_tags"):
            dynamic = False
            for item in parse(text).root.assignments():
                if item.name.lower() == "dynamic_tags":
                    dynamic = (item.scalar or "").lower() == "yes"
                elif dynamic:
                    tags.add(item.name)
        return tags

    def _recruited_leader_ideologies(self, country: Country) -> list[str] | None:
        """Ideologies of the recruited characters that can lead the country.

        ``None`` when a recruited character cannot be found, so the caller
        falls back to the one leader the country model knows.
        """

        recruited = country.recruited_characters
        if not recruited:
            return None
        ideologies: list[str] = []
        for character_id in recruited:
            try:
                character = self.get_character(character_id)
            except (KeyError, ValueError):
                return None
            ideologies.extend(
                role.ideology
                for role in self._all_character_roles(character)
                if isinstance(role, CountryLeaderRole) and role.ideology
            )
        return ideologies

    def _country_runtime_activation_evidence(
        self,
        tag: str,
    ) -> tuple[set[int], set[int], set[str], set[str]]:
        """Return runtime state transfers, cores, activators, and source labels."""

        scripts: list[tuple[str, str]] = []
        display_paths: dict[Path, str] = {}

        def add_script(label: str, body: str, path: Path | None) -> None:
            if not body.strip():
                return
            if path is not None and path not in display_paths:
                display_paths[path] = self._display_path(path)
            source = f"{display_paths[path]}:{label}" if path else label
            scripts.append((source, body))

        for tree in self._focus_trees.values():
            for focus in tree.focuses:
                add_script(
                    f"focus {focus.id} completion_reward",
                    focus.completion_reward,
                    tree.path,
                )
                add_script(
                    f"focus {focus.id} select_effect",
                    focus.select_effect,
                    tree.path,
                )
        for event in self._events.values():
            add_script(f"event {event.id} immediate", event.immediate, event.path)
            for index, option in enumerate(event.options):
                add_script(
                    f"event {event.id} option {index}",
                    option.effect,
                    event.path,
                )
        for decision in self._decisions.values():
            add_script(
                f"decision {decision.id} complete_effect",
                decision.complete_effect,
                decision.path,
            )
            add_script(
                f"decision {decision.id} remove_effect",
                decision.remove_effect,
                decision.path,
            )
            for effect_field in ("timeout_effect", "cancel_effect"):
                add_script(
                    f"decision {decision.id} {effect_field}",
                    getattr(decision, effect_field),
                    decision.path,
                )
        for occurrences in self._on_action_occurrences.values():
            for action in occurrences:
                add_script(
                    f"on_action {action.id} effect",
                    action.effect,
                    action.path,
                )

        transferred_states: set[int] = set()
        cored_states: set[int] = set()
        activators: set[str] = set()
        sources: set[str] = set()

        def scalar_int(node: PdxNode) -> int | None:
            if node.value is None:
                return None
            try:
                return int(node.value)
            except ValueError:
                return None

        def walk(
            node: PdxNode,
            *,
            in_country_scope: bool = False,
            state_scope: int | None = None,
        ) -> None:
            country_scope = in_country_scope
            nested_state_scope = state_scope
            if node.is_block():
                if node.key == tag:
                    country_scope = True
                    nested_state_scope = None
                elif node.key and node.key.isdigit():
                    nested_state_scope = int(node.key)
                    country_scope = False
            if country_scope and node.key == "transfer_state":
                state_id = scalar_int(node)
                if state_id is not None:
                    transferred_states.add(state_id)
            if (
                nested_state_scope is not None
                and node.key == "add_core_of"
                and node.value == tag
            ):
                cored_states.add(nested_state_scope)
            if node.key in {"release", "release_puppet", "puppet"} and node.value == tag:
                activators.add(node.key)
            if node.key == "create_subject" and node.is_block():
                if any(
                    child.key in {"subject", "target"} and child.value == tag
                    for child in node.children
                ):
                    activators.add("create_subject")
            for child in node.children:
                walk(
                    child,
                    in_country_scope=country_scope,
                    state_scope=nested_state_scope,
                )

        for source, body in scripts:
            before = (
                len(transferred_states),
                len(cored_states),
                len(activators),
            )
            if body not in self._runtime_activation_parse_cache:
                try:
                    self._runtime_activation_parse_cache[body] = parse_pdx(body)
                except ParseError:
                    # General script validation owns syntax diagnostics.
                    self._runtime_activation_parse_cache[body] = None
            root = self._runtime_activation_parse_cache[body]
            if root is None:
                continue
            # The walker only reads nodes. Exact body keys remain valid even
            # when effects change or an authoring transaction rolls back.
            walk(root)
            after = (
                len(transferred_states),
                len(cored_states),
                len(activators),
            )
            if after != before:
                sources.add(source)

        return transferred_states, cored_states, activators, sources

    def validate_country_package(
        self,
        tag: str,
        *,
        minimum_land_provinces: int = 2,
        allowed_state_ids: Sequence[int] = (),
        check_geography: bool = True,
        lifecycle: Literal["auto", "starting", "runtime"] = "auto",
    ) -> CountryPackageReport:
        """Validate one complete country package with lifecycle awareness.

        ``auto`` treats a country owning scenario-start states as ``starting``.
        A country with no starting territory is ``runtime`` only when loaded
        focus, event, decision, or on-action effects provide activation
        evidence. Otherwise its lifecycle remains unresolved and validation
        fails with a targeted remediation.

        A complete report proves structural authoring completeness only. It
        does not prove that runtime popularity thresholds or other dynamic
        trigger arithmetic are achievable in play.
        """

        tag = require_country_tag(tag)
        if not self._is_known_country_tag(tag):
            raise KeyError(f"Country '{tag}' is not defined")
        if lifecycle not in {"auto", "starting", "runtime"}:
            raise ValueError(
                "lifecycle must be 'auto', 'starting', or 'runtime'"
            )
        country = self.get_country(tag)
        findings: list[ValidationError] = []
        recruited = set(country.recruited_characters)
        roster = {
            character.id: character
            for character in self._characters.values()
            if character.country_tag == tag or character.id in recruited
        }

        def issue(
            code: str,
            message: str,
            *,
            file_path: str | None = None,
            state_id: int | None = None,
            severity: str = "error",
        ) -> None:
            findings.append(
                ValidationError(
                    message=message,
                    severity=severity,
                    code=code,
                    country_tag=tag,
                    file_path=file_path,
                    state_id=state_id,
                )
            )

        flag_paths = (
            self.mod_root / "gfx" / "flags" / f"{tag}.tga",
            self.mod_root / "gfx" / "flags" / "medium" / f"{tag}.tga",
            self.mod_root / "gfx" / "flags" / "small" / f"{tag}.tga",
        )
        missing_flags = [
            path.relative_to(self.mod_root).as_posix()
            for path in flag_paths
            if not path.is_file() and path not in self._pending_asset_writes
        ]
        if missing_flags:
            issue(
                "missing_country_flag",
                (
                    f"Country '{tag}' is missing required flag size(s): "
                    f"{', '.join(missing_flags)}. Import a source image with "
                    "import_flag_to_mod()."
                ),
            )

        requires_generated_names = False
        for reference in self._country_oob_references(country):
            try:
                if self.get_oob(reference.name).air_wings:
                    requires_generated_names = True
                    break
            except (KeyError, ValueError):
                continue
        if requires_generated_names:
            name_pool_tags = self._known_country_name_pool_tags()
            if tag not in name_pool_tags and "default" not in name_pool_tags:
                issue(
                    "missing_country_name_pool",
                    (
                        f"Country '{tag}' has no common/names entry. HOI4 will fail "
                        "to generate names for aces and other dynamic characters. "
                        "Call set_country_name_pool() with culturally appropriate "
                        "given names and surnames."
                    ),
                )

        undefined = sorted(recruited - set(roster))
        for character_id in undefined:
            issue(
                "undefined_recruited_character",
                (
                    f"Country '{tag}' recruits undefined character "
                    f"'{character_id}'. Create it or remove the recruitment entry."
                ),
            )

        for character in roster.values():
            if character.recruitment_expected is True and character.id not in recruited:
                issue(
                    "unrecruited_character",
                    (
                        f"Character '{character.id}' was created for immediate "
                        f"recruitment but is not recruited by country '{tag}'."
                    ),
                    file_path=str(character.path) if character.path else None,
                )

        recruited_characters = [
            character
            for character_id, character in roster.items()
            if character_id in recruited
        ]
        leader_ids = {
            character.id
            for character in recruited_characters
            if any(
                isinstance(role, CountryLeaderRole)
                for role in self._all_character_roles(character)
            )
        }
        if not leader_ids:
            issue(
                "missing_country_leader",
                (
                    f"Country '{tag}' has no recruited character with a "
                    "country_leader role."
                ),
            )

        advisor_ids = {
            character.id
            for character in recruited_characters
            if any(
                isinstance(role, AdvisorRole)
                and role.slot == "political_advisor"
                for role in self._all_character_roles(character)
            )
        }
        commander_ids = {
            character.id
            for character in recruited_characters
            if any(
                isinstance(role, ArmyCommanderRole)
                for role in self._all_character_roles(character)
            )
        }
        if len(advisor_ids) < 2:
            issue(
                "insufficient_political_advisors",
                (
                    f"Country '{tag}' has {len(advisor_ids)} recruited political "
                    "advisor(s); create and recruit at least 2."
                ),
            )
        if len(commander_ids) < 2:
            issue(
                "insufficient_military_commanders",
                (
                    f"Country '{tag}' has {len(commander_ids)} recruited army "
                    "commander(s); create and recruit at least 2."
                ),
            )

        sprite_textures = self._known_sprite_textures()
        visible_characters = [
            character
            for character in roster.values()
            if self._all_character_roles(character)
        ]
        for character in visible_characters:
            portraits = list(character.portraits)
            portraits.extend(
                portrait
                for instance in character.instances
                if instance.roles
                for portrait in instance.portraits
            )
            sprite_names = {
                sprite
                for portrait in portraits
                for sprite in (portrait.large, portrait.small)
                if sprite
            }
            roles = self._all_character_roles(character)
            if any(isinstance(role, AdvisorRole) for role in roles) and not any(
                portrait.channel == "civilian" and portrait.small
                for portrait in portraits
            ):
                issue(
                    "missing_character_small_portrait",
                    (
                        f"Advisor character '{character.id}' has no civilian small "
                        "portrait. Advisor cards require portraits.civilian.small "
                        "and a resolvable interface/*.gfx sprite."
                    ),
                    file_path=str(character.path) if character.path else None,
                )
            if not sprite_names:
                issue(
                    "missing_character_portrait",
                    (
                        f"Visible character '{character.id}' has no portrait. "
                        "Import one and declare its sprite with write_portrait_gfx()."
                    ),
                    file_path=str(character.path) if character.path else None,
                )
            for sprite_name in sorted(sprite_names):
                texture = sprite_textures.get(sprite_name)
                if texture is None:
                    issue(
                        "missing_character_portrait_gfx",
                        (
                            f"Character '{character.id}' portrait sprite "
                            f"'{sprite_name}' has no interface/*.gfx declaration."
                        ),
                        file_path=str(character.path) if character.path else None,
                    )
                    continue
                if not self._texture_exists(texture):
                    issue(
                        "missing_character_portrait_texture",
                        (
                            f"Character '{character.id}' portrait sprite "
                            f"'{sprite_name}' points to missing texture '{texture}'."
                        ),
                        file_path=str(character.path) if character.path else None,
                    )
            if character.id not in self._loc_entries:
                issue(
                    "missing_character_localization",
                    (
                        f"Character '{character.id}' is missing name "
                        f"localization key '{character.id}'."
                    ),
                    file_path=str(character.path) if character.path else None,
                )

        owned_states = [
            state for state in self._effective_states() if state.owner == tag
        ]
        owned_by_id = {state.id: state for state in owned_states}
        (
            runtime_state_ids,
            runtime_core_ids,
            runtime_activators,
            activation_sources,
        ) = self._country_runtime_activation_evidence(tag)
        resolved_lifecycle: str = lifecycle
        if lifecycle == "auto":
            if owned_states:
                resolved_lifecycle = "starting"
            elif runtime_state_ids or runtime_activators:
                resolved_lifecycle = "runtime"
            else:
                resolved_lifecycle = "unresolved"

        if resolved_lifecycle == "unresolved" or (
            resolved_lifecycle == "runtime"
            and not runtime_state_ids
            and not runtime_activators
        ):
            issue(
                "missing_country_activation",
                (
                    f"Country '{tag}' owns no scenario-start states and has no "
                    "runtime activation path. Assign starting territory and an "
                    "OOB, or add a focus/event/decision/on-action release path "
                    "that gives the country territory."
                ),
            )
        elif resolved_lifecycle == "runtime" and runtime_state_ids:
            if country.capital not in runtime_state_ids:
                issue(
                    "runtime_capital_not_assigned",
                    (
                        f"Runtime-created country '{tag}' receives states "
                        f"{sorted(runtime_state_ids)}, but not its declared "
                        f"capital state {country.capital}."
                    ),
                    state_id=country.capital,
                )
            elif (
                country.capital in runtime_state_ids
                and country.capital not in runtime_core_ids
            ):
                issue(
                    "runtime_capital_not_cored",
                    (
                        f"Runtime-created country '{tag}' receives capital state "
                        f"{country.capital}, but no runtime add_core_of = {tag} "
                        "was found for that state."
                    ),
                    state_id=country.capital,
                )

        matching_oobs = [
            oob
            for oob in self._oobs.values()
            if oob.country_tag == tag and oob.kind == "land"
        ]
        land_references = [
            reference
            for reference in self._country_oob_references(country)
            if reference.kind == "land"
        ]
        package_oob: OrderOfBattle | None = None
        if resolved_lifecycle == "starting":
            if land_references:
                try:
                    package_oob = self.get_oob(land_references[0].name)
                except KeyError:
                    issue(
                        "missing_oob_reference",
                        (
                            f"Country '{tag}' references missing OOB "
                            f"'{land_references[0].name}'."
                        ),
                    )
            elif len(matching_oobs) == 1:
                # Some scenarios deliberately load their sole tag-prefixed OOB
                # from an on-action rather than the country-history ``oob`` key.
                package_oob = matching_oobs[0]
            else:
                issue(
                    "missing_land_oob",
                    (
                        f"Starting country '{tag}' has no unambiguous land OOB. "
                        "Use create_oob(..., assign=True), or explicitly load one "
                        "tag-owned OOB from scenario script."
                    ),
                )
        if package_oob is not None:
            if not package_oob.templates or not package_oob.divisions:
                issue(
                    "empty_land_oob",
                    (
                        f"Country '{tag}' OOB '{package_oob.name}' must contain at "
                        "least one land template and one starting division."
                    ),
                    file_path=(
                        str(package_oob.path)
                        if package_oob.path
                        else None
                    ),
                )
            findings.extend(
                self._validate_oob_context(
                    package_oob,
                    tag,
                    land_references[0] if land_references else None,
                    structure=True,
                )
            )

        capital = owned_by_id.get(country.capital)
        if resolved_lifecycle == "starting":
            if not owned_states:
                issue(
                    "no_owned_territory",
                    (
                        f"Starting country '{tag}' owns no states. Assign "
                        "scenario-start territory before treating the package "
                        "as complete."
                    ),
                )
            if capital is None:
                issue(
                    "capital_not_owned",
                    (
                        f"Starting country '{tag}' capital state "
                        f"{country.capital} is not owned by {tag}."
                    ),
                    state_id=country.capital,
                )
            elif tag not in capital.cores:
                issue(
                    "capital_not_cored",
                    (
                        f"Starting country '{tag}' capital state "
                        f"{country.capital} is not cored by {tag}."
                    ),
                    state_id=country.capital,
                )

        if (
            resolved_lifecycle == "starting"
            and check_geography
            and capital is not None
        ):
            try:
                components = self.find_disconnected_states(
                    tag,
                    minimum_land_provinces=minimum_land_provinces,
                    allowed_state_ids=allowed_state_ids,
                )
            except (FileNotFoundError, RuntimeError):
                # Topology is intentionally optional; callers who require it
                # can invoke find_disconnected_states() directly.
                components = ()
            for component in components:
                issue(
                    "disconnected_country_territory",
                    (
                        f"Country '{tag}' owns a component disconnected from "
                        f"capital state {country.capital}: states "
                        f"{list(component.state_ids)}, "
                        f"{component.land_province_count} land province(s). "
                        "Fix the border or pass allowed_state_ids for a deliberate "
                        "island/overseas component."
                    ),
                    state_id=component.state_ids[0],
                    severity="warning",
                )
            try:
                enclosed = self.find_enclosed_foreign_states(
                    tag,
                    allowed_state_ids=allowed_state_ids,
                )
            except (FileNotFoundError, RuntimeError):
                enclosed = ()
            for component in enclosed:
                issue(
                    "enclosed_foreign_territory",
                    (
                        f"Country '{tag}' completely encloses foreign states "
                        f"{list(component.state_ids)} containing "
                        f"{component.land_province_count} land province(s). "
                        "This often means a state was missed during a border "
                        "transfer; fix it or explicitly allow the state."
                    ),
                    state_id=component.state_ids[0],
                    severity="warning",
                )

        return CountryPackageReport(
            tag=tag,
            findings=tuple(findings),
            lifecycle=resolved_lifecycle,
            advisor_count=len(advisor_ids),
            commander_count=len(commander_ids),
            character_count=len(roster),
            owned_state_count=len(owned_states),
            runtime_state_ids=tuple(sorted(runtime_state_ids)),
            activation_sources=tuple(sorted(activation_sources)),
        )
