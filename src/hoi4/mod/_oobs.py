"""Orders of battle, equipment variants and their country assignment."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Literal, Sequence, cast

from ..oob import (
    AirWing,
    DivisionTemplate,
    DivisionUnit,
    EquipmentVariant,
    Fleet,
    OOBKind,
    OOBReference,
    OrderOfBattle,
    find_equipment_variants,
    load_oob_file,
    remove_oob_reference,
    serialize_equipment_variant,
    serialize_oob_assignment,
)
from ..patching import (
    append_assignment,
)
from ..paths import (
    require_country_tag,
    require_script_id,
    resolve_mod_output_path,
)
from ..syntax import parse, repair
from ..textio import read_text
from ..types import (
    Country,
)
from ._countries import CountriesLayer
from ._equipment import EquipmentLayer


def _inherit_fleet_sources(
    replacements: list[Fleet],
    existing: list[Fleet],
) -> None:
    """Carry source coordinates into explicit naval replacements."""

    for fleet_index, fleet in enumerate(replacements):
        if fleet_index >= len(existing):
            continue
        source_fleet = existing[fleet_index]
        if fleet.source_index < 0 and not fleet.raw_block:
            fleet.source_index = source_fleet.source_index
            fleet.raw_block = source_fleet.raw_block
        fleet.touched_fields.update({"name", "naval_base", "task_forces"})
        for force_index, task_force in enumerate(fleet.task_forces):
            if force_index >= len(source_fleet.task_forces):
                continue
            source_force = source_fleet.task_forces[force_index]
            if task_force.source_index < 0 and not task_force.raw_block:
                task_force.source_index = source_force.source_index
                task_force.raw_block = source_force.raw_block
            task_force.touched_fields.update({"name", "location", "ships"})
            for ship_index, ship in enumerate(task_force.ships):
                if ship_index >= len(source_force.ships):
                    continue
                source_ship = source_force.ships[ship_index]
                if ship.source_index < 0 and not ship.raw_block:
                    ship.source_index = source_ship.source_index
                    ship.raw_block = source_ship.raw_block
                ship.touched_fields.update(
                    {"name", "definition", "equipment", "pride_of_the_fleet"}
                )
                for equipment_index, equipment in enumerate(ship.equipment):
                    if equipment_index >= len(source_ship.equipment):
                        continue
                    source_equipment = source_ship.equipment[equipment_index]
                    if equipment.source_index < 0 and not equipment.raw_block:
                        equipment.source_index = source_equipment.source_index
                        equipment.raw_block = source_equipment.raw_block
                    equipment.touched_fields.update(
                        {"amount", "owner", "creator", "version_name"}
                    )


def _default_oob_equipment_owners(oob: OrderOfBattle) -> None:
    """Fill the engine-required equipment owner from the OOB country."""

    if not oob.country_tag:
        return
    for fleet in oob.fleets:
        for task_force in fleet.task_forces:
            for ship in task_force.ships:
                for equipment in ship.equipment:
                    if not equipment.owner:
                        equipment.owner = oob.country_tag
                        if equipment.raw_block:
                            equipment.touched_fields.add("owner")
    for wing in oob.air_wings:
        if not wing.owner:
            wing.owner = oob.country_tag
            if wing.raw_block:
                wing.touched_fields.add("owner")


def _oob_content_kind(
    templates: Sequence[DivisionTemplate],
    divisions: Sequence[DivisionUnit],
    fleets: Sequence[Fleet],
    air_wings: Sequence[AirWing],
) -> OOBKind | None:
    kinds: list[OOBKind] = []
    if templates or divisions:
        kinds.append("land")
    if fleets:
        kinds.append("naval")
    if air_wings:
        kinds.append("air")
    if len(kinds) > 1:
        return "mixed"
    return kinds[0] if kinds else None


class OobsLayer(CountriesLayer, EquipmentLayer):
    """Orders of battle, equipment variants and their country assignment."""

    # ── Land orders of battle ───────────────────────────────────

    def list_oobs(self, *, include_vanilla: bool = False) -> list[str]:
        if include_vanilla and self._base_content_root is not None:
            directory = self._base_content_root / "history" / "units"
            if directory.is_dir():
                return sorted(
                    set(self._oobs)
                    | {path.stem for path in directory.glob("*.txt")}
                )
        return sorted(self._oobs)

    def get_oob(
        self,
        name: str,
        *,
        include_vanilla: bool = True,
    ) -> OrderOfBattle:
        name = require_script_id(name, label="OOB name")
        cached = self._oobs.get(name)
        if cached is not None:
            return cached
        candidates = [self.mod_root / "history" / "units" / f"{name}.txt"]
        if include_vanilla and self._base_content_root is not None:
            candidates.append(
                self._base_content_root / "history" / "units" / f"{name}.txt"
            )
        country_tag = next(
            (
                country.tag
                for country in self._countries.values()
                if any(
                    reference.name == name
                    for reference in self._country_oob_references(country)
                )
            ),
            "",
        )
        reference = next(
            (
                reference
                for country in self._countries.values()
                for reference in self._country_oob_references(country)
                if reference.name == name
            ),
            None,
        )
        for path in candidates:
            if not path.is_file():
                continue
            oob = load_oob_file(
                path,
                name=name,
                country_tag=country_tag,
                kind=reference.kind if reference is not None else None,
                required_dlc=(reference.required_dlc if reference is not None else ()),
                excluded_dlc=(reference.excluded_dlc if reference is not None else ()),
            )
            self._oobs[name] = oob
            if path.resolve(strict=False).is_relative_to(self.mod_root):
                self._original_files.setdefault(
                    path,
                    read_text(path),
                )
            return oob
        raise KeyError(f"OOB '{name}' not found")

    def create_oob(
        self,
        name: str,
        country_tag: str,
        *,
        templates: Sequence[DivisionTemplate] = (),
        divisions: Sequence[DivisionUnit] = (),
        fleets: Sequence[Fleet] = (),
        air_wings: Sequence[AirWing] = (),
        kind: Literal["auto", "land", "naval", "air"] = "auto",
        required_dlc: Sequence[str] = (),
        excluded_dlc: Sequence[str] = (),
        assign: bool = True,
        overwrite: bool = False,
        path: str | Path | None = None,
    ) -> OrderOfBattle:
        name = require_script_id(name, label="OOB name")
        country_tag = require_country_tag(country_tag)
        existing = self._oobs.get(name)
        if existing is not None and not overwrite:
            raise ValueError(
                f"OOB '{name}' already exists. Use overwrite=True only for "
                "deliberate replacement."
            )
        target = resolve_mod_output_path(
            self.mod_root,
            path or Path("history") / "units" / f"{name}.txt",
        )
        inferred_kind = _oob_content_kind(
            templates,
            divisions,
            fleets,
            air_wings,
        )
        if inferred_kind == "mixed":
            raise ValueError(
                "New OOBs cannot mix land, naval, and air content. Split the "
                "content into separate create_oob() calls."
            )
        selected_kind: OOBKind = (
            inferred_kind or "land"
            if kind == "auto"
            else cast(OOBKind, kind)
        )
        if inferred_kind is not None and selected_kind != inferred_kind:
            raise ValueError(
                f"OOB kind '{selected_kind}' does not match its "
                f"{inferred_kind} content."
            )
        required = tuple(dict.fromkeys(value.strip() for value in required_dlc if value.strip()))
        excluded = tuple(dict.fromkeys(value.strip() for value in excluded_dlc if value.strip()))
        overlap = sorted(set(required) & set(excluded))
        if overlap:
            raise ValueError(f"DLC cannot be both required and excluded: {overlap}")
        oob = OrderOfBattle(
            name=name,
            country_tag=country_tag,
            templates=copy.deepcopy(list(templates)),
            divisions=copy.deepcopy(list(divisions)),
            fleets=copy.deepcopy(list(fleets)),
            air_wings=copy.deepcopy(list(air_wings)),
            kind=selected_kind,
            required_dlc=required,
            excluded_dlc=excluded,
            path=target,
            raw_text=existing.raw_text if existing is not None else "",
            touched_fields=(
                {"templates", "divisions", "fleets", "air_wings"}
                if existing is not None
                else set()
            ),
        )
        _default_oob_equipment_owners(oob)
        if existing is not None:
            for index, template in enumerate(oob.templates):
                if index < len(existing.templates):
                    template.source_index = existing.templates[index].source_index
                    template.raw_block = existing.templates[index].raw_block
                    template.touched_fields.update(
                        {
                            "name",
                            "battalions",
                            "support",
                            "division_names_group",
                            "priority",
                        }
                    )
            for index, division in enumerate(oob.divisions):
                if index < len(existing.divisions):
                    division.source_index = existing.divisions[index].source_index
                    division.raw_block = existing.divisions[index].raw_block
                    division.touched_fields.update(
                        {
                            "name",
                            "name_order",
                            "location",
                            "division_template",
                            "start_experience_factor",
                            "start_equipment_factor",
                        }
                    )
            _inherit_fleet_sources(oob.fleets, existing.fleets)
            for index, wing in enumerate(oob.air_wings):
                if index < len(existing.air_wings):
                    source = existing.air_wings[index]
                    wing.source_index = source.source_index
                    wing.source_location_index = source.source_location_index
                    wing.raw_block = source.raw_block
                    wing.touched_fields.update(
                        {"amount", "owner", "creator", "version_name"}
                    )
        if assign:
            self._check_country_oob_assignment(
                self.get_country(country_tag),
                OOBReference(
                    name,
                    cast(Literal["land", "naval", "air"], selected_kind),
                    required,
                    excluded,
                ),
            )
        self._oobs[name] = oob
        self._dirty_oobs.add(name)
        self._dirty_oob_files.add(target)
        self._dirty.add("oobs")
        if assign:
            assign_kind = cast(
                Literal["land", "naval", "air"],
                selected_kind,
            )
            self.assign_country_oob(
                country_tag,
                name,
                kind=assign_kind,
                required_dlc=required,
                excluded_dlc=excluded,
            )
        return oob

    def update_oob(
        self,
        name: str,
        *,
        templates: Sequence[DivisionTemplate] | None = None,
        divisions: Sequence[DivisionUnit] | None = None,
        fleets: Sequence[Fleet] | None = None,
        air_wings: Sequence[AirWing] | None = None,
        country_tag: str | None = None,
        kind: Literal["land", "naval", "air"] | None = None,
        required_dlc: Sequence[str] | None = None,
        excluded_dlc: Sequence[str] | None = None,
        assign: bool = False,
    ) -> bool:
        try:
            oob = self.get_oob(name)
        except (KeyError, ValueError):
            return False
        existing_content_kind = _oob_content_kind(
            oob.templates,
            oob.divisions,
            oob.fleets,
            oob.air_wings,
        )
        prospective_kind = _oob_content_kind(
            templates if templates is not None else oob.templates,
            divisions if divisions is not None else oob.divisions,
            fleets if fleets is not None else oob.fleets,
            air_wings if air_wings is not None else oob.air_wings,
        )
        selected_kind: OOBKind = kind or oob.kind
        if prospective_kind == "mixed" and existing_content_kind != "mixed":
            raise ValueError(
                "This update would mix land, naval, and air content. Split "
                "new content into separate OOB files."
            )
        if (
            prospective_kind not in {None, "mixed"}
            and selected_kind != prospective_kind
        ):
            raise ValueError(
                f"OOB kind '{selected_kind}' does not match its "
                f"{prospective_kind} content."
            )
        self._materialize_oob_override(oob)
        if templates is not None:
            template_replacements = copy.deepcopy(list(templates))
            for index, template in enumerate(template_replacements):
                if (
                    index < len(oob.templates)
                    and template.source_index < 0
                    and not template.raw_block
                ):
                    template.source_index = oob.templates[index].source_index
                    template.raw_block = oob.templates[index].raw_block
                    template.touched_fields.update(
                        {
                            "name",
                            "battalions",
                            "support",
                            "division_names_group",
                            "priority",
                        }
                    )
            oob.templates = template_replacements
            oob.touched_fields.add("templates")
        if divisions is not None:
            division_replacements = copy.deepcopy(list(divisions))
            for index, division in enumerate(division_replacements):
                if (
                    index < len(oob.divisions)
                    and division.source_index < 0
                    and not division.raw_block
                ):
                    division.source_index = oob.divisions[index].source_index
                    division.raw_block = oob.divisions[index].raw_block
                    division.touched_fields.update(
                        {
                            "name",
                            "name_order",
                            "location",
                            "division_template",
                            "start_experience_factor",
                            "start_equipment_factor",
                        }
                    )
            oob.divisions = division_replacements
            oob.touched_fields.add("divisions")
        if fleets is not None:
            fleet_replacements = copy.deepcopy(list(fleets))
            _inherit_fleet_sources(fleet_replacements, oob.fleets)
            oob.fleets = fleet_replacements
            oob.touched_fields.add("fleets")
        if air_wings is not None:
            wing_replacements = copy.deepcopy(list(air_wings))
            # Loaded entries keep their occurrences even when new wings precede them.
            claimed_sources = {
                wing.source_index for wing in wing_replacements if wing.source_index >= 0
            }
            for index, wing in enumerate(wing_replacements):
                if (
                    index < len(oob.air_wings)
                    and oob.air_wings[index].source_index not in claimed_sources
                    and wing.source_index < 0
                    and not wing.raw_block
                ):
                    source = oob.air_wings[index]
                    wing.source_index = source.source_index
                    claimed_sources.add(source.source_index)
                    wing.source_location_index = source.source_location_index
                    wing.raw_block = source.raw_block
                    wing.touched_fields.update(
                        {"amount", "owner", "creator", "version_name"}
                    )
            oob.air_wings = wing_replacements
            oob.touched_fields.add("air_wings")
        if country_tag is not None:
            oob.country_tag = require_country_tag(country_tag)
        if kind is not None:
            oob.kind = kind
        if required_dlc is not None:
            oob.required_dlc = tuple(
                dict.fromkeys(value.strip() for value in required_dlc if value.strip())
            )
        if excluded_dlc is not None:
            oob.excluded_dlc = tuple(
                dict.fromkeys(value.strip() for value in excluded_dlc if value.strip())
            )
        overlap = sorted(set(oob.required_dlc) & set(oob.excluded_dlc))
        if overlap:
            raise ValueError(f"DLC cannot be both required and excluded: {overlap}")
        _default_oob_equipment_owners(oob)
        if assign:
            if not oob.country_tag:
                raise ValueError("Assigning an OOB requires country_tag")
            if oob.kind == "mixed":
                raise ValueError(
                    "A mixed land/naval/air OOB cannot be assigned. Split it "
                    "into separate OOB files."
                )
            self.assign_country_oob(
                oob.country_tag,
                oob.name,
                kind=cast(Literal["land", "naval", "air"], oob.kind),
                required_dlc=oob.required_dlc,
                excluded_dlc=oob.excluded_dlc,
            )
        self._dirty_oobs.add(oob.name)
        if oob.path is not None:
            self._dirty_oob_files.add(oob.path)
        self._dirty.add("oobs")
        return True

    def delete_oob(self, name: str, *, unassign: bool = True) -> bool:
        try:
            oob = self.get_oob(name, include_vanilla=False)
        except (KeyError, ValueError):
            return False
        if oob.path is not None and not oob.path.resolve(strict=False).is_relative_to(
            self.mod_root
        ):
            raise ValueError(f"Cannot delete vanilla OOB '{name}'")
        self._oobs.pop(oob.name, None)
        self._dirty_oobs.add(oob.name)
        if oob.path is not None:
            self._dirty_oob_files.add(oob.path)
        self._dirty.add("oobs")
        if unassign:
            for country in self._countries.values():
                for reference in self._country_oob_references(country):
                    if reference.name == oob.name:
                        self.unassign_country_oob(
                            country.tag,
                            reference.name,
                            kind=reference.kind,
                            required_dlc=reference.required_dlc,
                            excluded_dlc=reference.excluded_dlc,
                            date=reference.date,
                        )
        return True

    def _assign_country_oob(self, tag: str, name: str) -> None:
        if name:
            self.assign_country_oob(tag, name, kind="land")
            return
        country = self.get_country(tag)
        for reference in self._country_oob_references(country):
            if reference.kind == "land":
                self.unassign_country_oob(
                    tag,
                    reference.name,
                    kind="land",
                    required_dlc=reference.required_dlc,
                    excluded_dlc=reference.excluded_dlc,
                )

    def assign_country_oob(
        self,
        tag: str,
        name: str,
        *,
        kind: Literal["land", "naval", "air"] = "land",
        required_dlc: Sequence[str] = (),
        excluded_dlc: Sequence[str] = (),
        date: str = "",
    ) -> None:
        """Assign an OOB with the engine's land/naval/air history effect."""

        tag = require_country_tag(tag)
        name = require_script_id(name, label="OOB name")
        country = self.get_country(tag)
        required = tuple(
            dict.fromkeys(value.strip() for value in required_dlc if value.strip())
        )
        excluded = tuple(
            dict.fromkeys(value.strip() for value in excluded_dlc if value.strip())
        )
        if date and re.fullmatch(r"\d{1,4}\.\d{1,2}\.\d{1,2}(?:\.\d{1,2})?", date) is None:
            raise ValueError(f"Invalid OOB assignment date: {date!r}")
        reference = OOBReference(name, kind, required, excluded, date)
        if not self._check_country_oob_assignment(country, reference):
            return
        self._materialize_country_override(country)
        if kind == "land" and not required and not excluded and not date:
            country.oob = name
            country.touched_fields.add("oob")
        else:
            self._materialize_country_history(country)
            country.raw_history = append_assignment(
                country.raw_history,
                serialize_oob_assignment(reference),
            )
        self._dirty_countries.add(tag)
        self._dirty.add("countries")

    def _check_country_oob_assignment(
        self,
        country: Country,
        reference: OOBReference,
    ) -> bool:
        """Reject conflicts without materializing country files; return whether new."""

        existing = self._country_oob_references(country)
        if reference in existing:
            return False
        conflict = next(
            (
                item
                for item in existing
                if item.kind == reference.kind
                and item.required_dlc == reference.required_dlc
                and item.excluded_dlc == reference.excluded_dlc
                and item.date == reference.date
            ),
            None,
        )
        if conflict is not None:
            raise ValueError(
                f"Country '{country.tag}' already assigns {reference.kind} OOB '{conflict.name}' "
                f"for date {reference.date or '<scenario start>'}, "
                f"required DLC {reference.required_dlc}, "
                f"and excluded DLC {reference.excluded_dlc}."
            )
        return True

    def unassign_country_oob(
        self,
        tag: str,
        name: str,
        *,
        kind: Literal["land", "naval", "air"] = "land",
        required_dlc: Sequence[str] = (),
        excluded_dlc: Sequence[str] = (),
        date: str = "",
    ) -> bool:
        """Remove one exact land, naval, or air OOB assignment."""

        tag = require_country_tag(tag)
        name = require_script_id(name, label="OOB name")
        country = self.get_country(tag)
        required = tuple(
            dict.fromkeys(value.strip() for value in required_dlc if value.strip())
        )
        excluded = tuple(
            dict.fromkeys(value.strip() for value in excluded_dlc if value.strip())
        )
        reference = OOBReference(name, kind, required, excluded, date)
        if reference not in self._country_oob_references(country):
            return False
        self._materialize_country_override(country)
        updated, removed = remove_oob_reference(country.raw_history, reference)
        if (
            kind == "land"
            and not required
            and not excluded
            and not date
            and country.oob == name
        ):
            country.oob = ""
            country.touched_fields.add("oob")
            removed = True
        if updated != country.raw_history:
            country.raw_history = updated
        if removed:
            self._dirty_countries.add(tag)
            self._dirty.add("countries")
        return removed

    def create_equipment_variant(
        self,
        country_tag: str,
        variant: EquipmentVariant,
    ) -> EquipmentVariant:
        """Append a validated equipment variant to country history."""

        country_tag = require_country_tag(country_tag)
        if not variant.name.strip():
            raise ValueError("Equipment variant name cannot be empty")
        if not variant.equipment_type.strip():
            raise ValueError("Equipment variant type cannot be empty")
        overlap = sorted(set(variant.required_dlc) & set(variant.excluded_dlc))
        if overlap:
            raise ValueError(f"DLC cannot be both required and excluded: {overlap}")
        known_equipment = self._known_equipment_ids()
        if known_equipment and variant.equipment_type not in known_equipment:
            raise ValueError(
                f"Unknown equipment type '{variant.equipment_type}' for variant "
                f"'{variant.name}'"
            )
        country = self.get_country(country_tag)
        unlocks = self._equipment_unlock_technologies().get(
            variant.equipment_type,
            (),
        )
        is_modular_chassis = (
            variant.equipment_type.startswith("ship_hull_")
            or "airframe" in variant.equipment_type
        )
        grants, _ = self._history_equipment_operations(country.raw_history)
        grants.extend(
            self._modeled_technology_grants_missing_from_history(country, grants)
        )
        if (
            is_modular_chassis
            and unlocks
            and not self._script_conditions_are_covered(
                self._dlc_script_condition(
                    variant.required_dlc,
                    variant.excluded_dlc,
                ),
                [
                    grant.condition
                    for grant in grants
                    if grant.technology in unlocks
                ],
            )
        ):
            raise ValueError(
                f"Equipment variant '{variant.name}' uses modular chassis "
                f"'{variant.equipment_type}' before it is unlocked on every matching "
                "DLC path. Grant one of these technologies in the same or a broader "
                f"DLC branch first: {', '.join(unlocks)}. "
                "allow_without_tech does not create the required chassis variant."
            )
        self._materialize_country_override(country)
        self._materialize_country_history(country)
        self._synchronize_pending_country_technologies(country)
        for existing in find_equipment_variants(country.raw_history):
            if (
                existing.name == variant.name
                and existing.equipment_type == variant.equipment_type
                and existing.required_dlc == variant.required_dlc
                and existing.excluded_dlc == variant.excluded_dlc
            ):
                raise ValueError(
                    f"Equipment variant '{variant.name}' for "
                    f"'{variant.equipment_type}' already exists in {country_tag} history"
                )
        country.raw_history = append_assignment(
            country.raw_history,
            serialize_equipment_variant(variant),
        )
        self._dirty_countries.add(country_tag)
        self._dirty.add("countries")
        return variant

    def _materialize_oob_override(self, oob: OrderOfBattle) -> None:
        source = oob.path
        if source is None:
            source = self.mod_root / "history" / "units" / f"{oob.name}.txt"
            oob.path = source
            self._dirty_oob_files.add(source)
            return
        if source.resolve(strict=False).is_relative_to(self.mod_root):
            return
        if self._base_content_root is None:
            raise ValueError(f"Cannot retarget OOB '{oob.name}' without a HOI4 install")
        target = resolve_mod_output_path(
            self.mod_root,
            source.resolve(strict=False).relative_to(self._base_content_root),
        )
        self._original_files.setdefault(
            target,
            self._read_current_text(target)
            or read_text(source),
        )
        if parse(oob.raw_text).problems:
            # The game tolerates the inherited file's missing or stray braces;
            # the mod's own copy is patched and written out well formed.
            oob.raw_text = repair(oob.raw_text)
        oob.path = target
        self._dirty_oob_files.add(target)
