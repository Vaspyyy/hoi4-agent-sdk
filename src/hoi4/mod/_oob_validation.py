"""Order-of-battle validation against the effective game data."""

from __future__ import annotations

from ..oob import (
    EquipmentVariant,
    OrderOfBattle,
    find_equipment_variants,
)
from ..tags import (
    load_all_tags,
)
from ..types import (
    State,
    ValidationError,
)
from ._catalogs import CatalogsLayer
from ._countries import CountriesLayer
from ._states import StatesLayer


class OobValidationLayer(StatesLayer, CountriesLayer, CatalogsLayer):
    """Order-of-battle validation against the effective game data."""

    def _validate_oob_context(
        self,
        oob: OrderOfBattle,
        country_tag: str,
    ) -> list[ValidationError]:
        errors: list[ValidationError] = []
        known_units = self._known_sub_unit_types()
        known_equipment = self._known_equipment_ids()
        known_tags = set(load_all_tags(self._base_content_root, self.mod_root))
        known_tags.update(self._countries)
        variants_by_tag: dict[str, tuple[EquipmentVariant, ...]] = {}

        def variants_for(tag: str) -> tuple[EquipmentVariant, ...]:
            if tag not in variants_by_tag:
                try:
                    variants_by_tag[tag] = find_equipment_variants(
                        self.get_country(tag).raw_history
                    )
                except (KeyError, OSError, ValueError):
                    variants_by_tag[tag] = ()
            return variants_by_tag[tag]

        def variant_available(
            variant: EquipmentVariant,
        ) -> bool:
            required = set(oob.required_dlc)
            excluded = set(oob.excluded_dlc)
            return bool(
                not (set(variant.required_dlc) & excluded)
                and not (set(variant.excluded_dlc) & required)
                and set(variant.required_dlc) <= required
                and set(variant.excluded_dlc) <= excluded
            )

        def has_matching_variant(
            equipment_type: str,
            version_name: str,
            *,
            owner: str,
            creator: str,
        ) -> bool:
            candidate_tags = tuple(
                dict.fromkeys(
                    tag for tag in (creator, owner, country_tag) if tag
                )
            )
            return any(
                variant.name == version_name
                and variant.equipment_type == equipment_type
                and variant_available(variant)
                for tag in candidate_tags
                for variant in variants_for(tag)
            )

        reported_legacy_mtg_mismatch = False
        reported_ungated_mtg_oob = False
        for template in oob.templates:
            for battalion in (*template.battalions, *template.support):
                if known_units and battalion.unit_type not in known_units:
                    errors.append(
                        ValidationError(
                            message=(
                                f"OOB '{oob.name}' template '{template.name}' uses "
                                f"unknown sub-unit type '{battalion.unit_type}'"
                            ),
                            severity="error",
                            code="unknown_oob_unit_type",
                            country_tag=country_tag,
                            file_path=str(oob.path) if oob.path is not None else None,
                        )
                    )

        province_to_state: dict[int, State] = {}
        for state_id in sorted(set(self._state_ids) | set(self._states)):
            try:
                state = self.get_state(state_id)
            except (KeyError, OSError, ValueError):
                continue
            for province_id in state.provinces:
                province_to_state[province_id] = state
        province_types = self._effective_province_types()
        for division in oob.divisions:
            location_state = province_to_state.get(division.location)
            label = division.name or "<ordered name>"
            if location_state is None or (
                province_types
                and division.location not in province_types
            ):
                errors.append(
                    ValidationError(
                        message=(
                            f"OOB '{oob.name}' division '{label}' uses unknown "
                            f"province {division.location}"
                        ),
                        severity="error",
                        code="unknown_oob_province",
                        country_tag=country_tag,
                        state_id=(
                            location_state.id
                            if location_state is not None
                            else None
                        ),
                        file_path=str(oob.path) if oob.path is not None else None,
                    )
                )
                continue
            province_type = province_types.get(division.location)
            if province_type is not None and province_type != "land":
                errors.append(
                    ValidationError(
                        message=(
                            f"OOB '{oob.name}' division '{label}' is placed in "
                            f"{province_type} province {division.location}"
                        ),
                        severity="error",
                        code="oob_water_location",
                        country_tag=country_tag,
                        state_id=location_state.id,
                        file_path=str(oob.path) if oob.path is not None else None,
                    )
                )
            if location_state.owner != country_tag:
                errors.append(
                    ValidationError(
                        message=(
                            f"OOB '{oob.name}' division '{label}' is placed in "
                            f"province {division.location}, owned by {location_state.owner} "
                            f"instead of {country_tag}"
                        ),
                        severity="error",
                        code="oob_location_not_owned",
                        country_tag=country_tag,
                        state_id=location_state.id,
                        file_path=str(oob.path) if oob.path is not None else None,
                    )
                )
        def validate_owned_location(
            location: int,
            *,
            label: str,
            code_unknown: str,
            code_owner: str,
            allow_state_id: bool = False,
        ) -> None:
            location_state: State | None = None
            if allow_state_id:
                try:
                    location_state = self.get_state(location)
                except (KeyError, OSError, ValueError):
                    location_state = None
            if location_state is None:
                location_state = province_to_state.get(location)
            if location_state is None:
                errors.append(
                    ValidationError(
                        message=(
                            f"OOB '{oob.name}' {label} uses unknown location {location}"
                        ),
                        severity="error",
                        code=code_unknown,
                        country_tag=country_tag,
                        file_path=str(oob.path) if oob.path is not None else None,
                    )
                )
            elif location_state.owner != country_tag:
                errors.append(
                    ValidationError(
                        message=(
                            f"OOB '{oob.name}' {label} is at {location}, owned by "
                            f"{location_state.owner} instead of {country_tag}"
                        ),
                        severity="error",
                        code=code_owner,
                        country_tag=country_tag,
                        state_id=location_state.id,
                        file_path=str(oob.path) if oob.path is not None else None,
                    )
                )

        for fleet in oob.fleets:
            validate_owned_location(
                fleet.naval_base,
                label=f"fleet '{fleet.name}' naval base",
                code_unknown="unknown_oob_naval_location",
                code_owner="oob_naval_location_not_owned",
            )
            for task_force in fleet.task_forces:
                validate_owned_location(
                    task_force.location,
                    label=f"task force '{task_force.name}' location",
                    code_unknown="unknown_oob_naval_location",
                    code_owner="oob_naval_location_not_owned",
                )
                for ship in task_force.ships:
                    if known_units and ship.definition not in known_units:
                        errors.append(
                            ValidationError(
                                message=(
                                    f"OOB '{oob.name}' ship '{ship.name}' uses "
                                    f"unknown definition '{ship.definition}'"
                                ),
                                severity="error",
                                code="unknown_oob_ship_definition",
                                country_tag=country_tag,
                                file_path=(
                                    str(oob.path) if oob.path is not None else None
                                ),
                            )
                        )
                    for equipment in ship.equipment:
                        if (
                            known_equipment
                            and equipment.equipment_type not in known_equipment
                        ):
                            errors.append(
                                ValidationError(
                                    message=(
                                        f"OOB '{oob.name}' ship '{ship.name}' uses "
                                        "unknown equipment "
                                        f"'{equipment.equipment_type}'"
                                    ),
                                    severity="error",
                                    code="unknown_oob_equipment",
                                    country_tag=country_tag,
                                    file_path=(
                                        str(oob.path)
                                        if oob.path is not None
                                        else None
                                    ),
                                )
                            )
                        is_mtg_hull = equipment.equipment_type.startswith(
                            "ship_hull_"
                        )
                        mtg_required = "Man the Guns" in oob.required_dlc
                        mtg_excluded = "Man the Guns" in oob.excluded_dlc
                        if not is_mtg_hull and not mtg_excluded:
                            code = (
                                "mtg_naval_oob_uses_legacy_equipment"
                                if mtg_required
                                else "legacy_naval_oob_without_dlc_fallback"
                            )
                            if not reported_legacy_mtg_mismatch:
                                errors.append(
                                    ValidationError(
                                        message=(
                                            f"OOB '{oob.name}' uses legacy naval "
                                            f"equipment '{equipment.equipment_type}' "
                                            "when Man the Guns may be active. HOI4 "
                                            "will skip these ships because no proper "
                                            "equipment variant exists. Assign a "
                                            "legacy naval OOB with excluded_dlc="
                                            "('Man the Guns',) and a hull-based OOB "
                                            "with required_dlc=('Man the Guns',)."
                                        ),
                                        severity="error",
                                        code=code,
                                        country_tag=country_tag,
                                        file_path=(
                                            str(oob.path)
                                            if oob.path is not None
                                            else None
                                        ),
                                    )
                                )
                                reported_legacy_mtg_mismatch = True
                        if is_mtg_hull and not mtg_required:
                            if not reported_ungated_mtg_oob:
                                errors.append(
                                    ValidationError(
                                        message=(
                                            f"OOB '{oob.name}' uses Man the Guns hull "
                                            "equipment but is not gated with "
                                            "required_dlc=('Man the Guns',). Add the "
                                            "gate and a legacy naval fallback."
                                        ),
                                        severity="error",
                                        code="ungated_mtg_naval_oob",
                                        country_tag=country_tag,
                                        file_path=(
                                            str(oob.path)
                                            if oob.path is not None
                                            else None
                                        ),
                                    )
                                )
                                reported_ungated_mtg_oob = True
                        if is_mtg_hull and not equipment.version_name:
                            errors.append(
                                ValidationError(
                                    message=(
                                        f"OOB '{oob.name}' ship '{ship.name}' uses "
                                        f"'{equipment.equipment_type}' without a "
                                        "version_name matching a country-history "
                                        "create_equipment_variant definition."
                                    ),
                                    severity="error",
                                    code="missing_mtg_ship_variant_name",
                                    country_tag=country_tag,
                                    file_path=(
                                        str(oob.path)
                                        if oob.path is not None
                                        else None
                                    ),
                                )
                            )
                        elif is_mtg_hull:
                            if not has_matching_variant(
                                equipment.equipment_type,
                                equipment.version_name,
                                owner=equipment.owner,
                                creator=equipment.creator,
                            ):
                                errors.append(
                                    ValidationError(
                                        message=(
                                            f"OOB '{oob.name}' ship '{ship.name}' "
                                            f"references {equipment.equipment_type} "
                                            f"variant '{equipment.version_name}', but "
                                            "no compatible create_equipment_variant "
                                            "definition was found in the owner or "
                                            "creator country history."
                                        ),
                                        severity="error",
                                        code="missing_mtg_equipment_variant",
                                        country_tag=country_tag,
                                        file_path=(
                                            str(oob.path)
                                            if oob.path is not None
                                            else None
                                        ),
                                    )
                                )
                        for owner in (equipment.owner, equipment.creator):
                            if owner and owner not in known_tags:
                                errors.append(
                                    ValidationError(
                                        message=(
                                            f"OOB '{oob.name}' ship '{ship.name}' "
                                            f"uses unknown equipment owner '{owner}'"
                                        ),
                                        severity="error",
                                        code="unknown_oob_equipment_owner",
                                        country_tag=country_tag,
                                        file_path=(
                                            str(oob.path)
                                            if oob.path is not None
                                            else None
                                        ),
                                    )
                                )
        for wing in oob.air_wings:
            validate_owned_location(
                wing.location,
                label=f"air wing '{wing.equipment_type}' location",
                code_unknown="unknown_oob_air_location",
                code_owner="oob_air_location_not_owned",
                allow_state_id=True,
            )
            if known_equipment and wing.equipment_type not in known_equipment:
                errors.append(
                    ValidationError(
                        message=(
                            f"OOB '{oob.name}' air wing uses unknown equipment "
                            f"'{wing.equipment_type}'"
                        ),
                        severity="error",
                        code="unknown_oob_equipment",
                        country_tag=country_tag,
                        file_path=str(oob.path) if oob.path is not None else None,
                    )
                )
            is_bba_airframe = "airframe" in wing.equipment_type
            bba_required = "By Blood Alone" in oob.required_dlc
            if is_bba_airframe and not bba_required:
                errors.append(
                    ValidationError(
                        message=(
                            f"OOB '{oob.name}' uses By Blood Alone airframe "
                            f"equipment '{wing.equipment_type}' but is not gated "
                            "with required_dlc=('By Blood Alone',). Add the gate "
                            "and a legacy air OOB fallback."
                        ),
                        severity="error",
                        code="ungated_bba_air_oob",
                        country_tag=country_tag,
                        file_path=str(oob.path) if oob.path is not None else None,
                    )
                )
            if is_bba_airframe and not wing.version_name:
                errors.append(
                    ValidationError(
                        message=(
                            f"OOB '{oob.name}' air wing uses "
                            f"'{wing.equipment_type}' without a version_name "
                            "matching a country-history "
                            "create_equipment_variant definition."
                        ),
                        severity="error",
                        code="missing_bba_air_variant_name",
                        country_tag=country_tag,
                        file_path=str(oob.path) if oob.path is not None else None,
                    )
                )
            elif is_bba_airframe and not has_matching_variant(
                wing.equipment_type,
                wing.version_name,
                owner=wing.owner,
                creator=wing.creator,
            ):
                errors.append(
                    ValidationError(
                        message=(
                            f"OOB '{oob.name}' air wing references "
                            f"{wing.equipment_type} variant "
                            f"'{wing.version_name}', but no compatible "
                            "create_equipment_variant definition was found in "
                            "the owner or creator country history."
                        ),
                        severity="error",
                        code="missing_bba_equipment_variant",
                        country_tag=country_tag,
                        file_path=str(oob.path) if oob.path is not None else None,
                    )
                )
            for owner in (wing.owner, wing.creator):
                if owner and owner not in known_tags:
                    errors.append(
                        ValidationError(
                            message=(
                                f"OOB '{oob.name}' air wing uses unknown equipment "
                                f"owner '{owner}'"
                            ),
                            severity="error",
                            code="unknown_oob_equipment_owner",
                            country_tag=country_tag,
                            file_path=str(oob.path) if oob.path is not None else None,
                        )
                    )
        return errors
