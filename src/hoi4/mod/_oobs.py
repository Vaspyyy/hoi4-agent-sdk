"""Orders of battle, equipment variants and their country assignment."""

from __future__ import annotations

import copy
import functools
import re
from pathlib import Path
from typing import Any, Literal, Protocol, TypeVar, cast
from collections.abc import Sequence

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
)
from ..syntax import parse, repair
from ..textio import read_text
from ..types import (
    Country,
)
from ..errors import AlreadyExistsError, NotFoundError, ReadOnlyError, not_found
from ._countries import CountriesLayer
from ._shared import MISSING, _D, _Missing, _check_model, _check_model_lists
from ._equipment import EquipmentLayer


class _SourceItem(Protocol):
    source_index: int
    raw_block: str


_Item = TypeVar("_Item", bound=_SourceItem)


def _inherit_sources(replacements: Sequence[_Item], existing: Sequence[_Item]) -> None:
    """Give each fresh replacement the source block of the item at its position.

    An item that already points at a source block keeps it. A source block that
    another replacement already points at is never handed out a second time, so
    removing one item and adding another in the same call cannot make the new
    item overwrite a kept one. Which fields get written is decided later, by
    comparing each item with what its source block says.
    """

    claimed = {item.source_index for item in replacements if item.source_index >= 0}
    for index, item in enumerate(replacements):
        if index >= len(existing) or item.source_index >= 0 or item.raw_block:
            continue
        source = existing[index]
        if source.source_index < 0 or source.source_index in claimed:
            continue
        item.source_index = source.source_index
        item.raw_block = source.raw_block
        if isinstance(item, AirWing) and isinstance(source, AirWing):
            item.source_location_index = source.source_location_index
        claimed.add(source.source_index)


def _matching_source(item: _Item, existing: Sequence[_Item]) -> _Item | None:
    """The loaded item that ``item`` was read from, if any."""

    if item.source_index < 0:
        return None
    return next(
        (source for source in existing if source.source_index == item.source_index),
        None,
    )


def _inherit_fleet_sources(
    replacements: list[Fleet],
    existing: list[Fleet],
) -> None:
    """Carry source coordinates into explicit naval replacements."""

    _inherit_sources(replacements, existing)
    for fleet in replacements:
        source_fleet = _matching_source(fleet, existing)
        if source_fleet is None:
            continue
        _inherit_sources(fleet.task_forces, source_fleet.task_forces)
        for task_force in fleet.task_forces:
            source_force = _matching_source(task_force, source_fleet.task_forces)
            if source_force is None:
                continue
            _inherit_sources(task_force.ships, source_force.ships)
            for ship in task_force.ships:
                source_ship = _matching_source(ship, source_force.ships)
                if source_ship is not None:
                    _inherit_sources(ship.equipment, source_ship.equipment)


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
    for wing in oob.air_wings:
        if not wing.owner:
            wing.owner = oob.country_tag


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
        """Return the sorted names of the mod's orders of battle, and the game's with ``include_vanilla``."""
        if include_vanilla and self._base_content is not None:
            inherited = self._base_content.glob("history/units", "*.txt")
            return sorted(set(self._oobs) | {
                path.stem
                for relative, path in inherited.items()
                if not self._oob_file_hidden(Path(relative))
            })
        return sorted(self._oobs)

    def _oob_file_hidden(self, relative: Path) -> bool:
        """Whether the mod hides an inherited OOB file (on disk, staged or by replace_path)."""

        return self._mod_hides_inherited(relative) or (
            bool(self._dirty) and self._file_is_staged(self.mod_root / relative)
        )

    def get_oob(
        self,
        name: str,
        *,
        include_vanilla: bool = True,
        default: _D | _Missing = MISSING,
    ) -> OrderOfBattle | _D:
        """Return the OOB ``name`` (``history/units/<name>.txt``).

        ``include_vanilla`` also looks at the game's and base mods' files.

        Args:
            default: Returned instead of raising ``NotFoundError`` when no
                OOB file has this name.

        Raises:
            NotFoundError: No OOB file has this name and no default was given.
            ValueError: ``name`` is not a valid OOB name.
        """
        if name is None:
            raise TypeError("OOB name cannot be None")
        name = require_script_id(name, label="OOB name")
        cached = self._oobs.get(name)
        if cached is not None:
            return cached
        candidates = [self.mod_root / "history" / "units" / f"{name}.txt"]
        if include_vanilla and self._base_content is not None:
            relative = Path("history/units") / f"{name}.txt"
            inherited = self._base_content.file(relative)
            if inherited is not None and not self._oob_file_hidden(relative):
                candidates.append(inherited)
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
            if not path.resolve(strict=False).is_relative_to(self.mod_root):
                inherited_oob = self._inherited_copy(
                    "oob", name,
                    functools.partial(load_oob_file,
                        path, name=name, country_tag=country_tag,
                        kind=reference.kind if reference is not None else None,
                        required_dlc=reference.required_dlc if reference is not None else (),
                        excluded_dlc=reference.excluded_dlc if reference is not None else (),
                    ),
                )
                assert inherited_oob is not None
                return inherited_oob
            oob = load_oob_file(
                path,
                name=name,
                country_tag=country_tag,
                kind=reference.kind if reference is not None else None,
                required_dlc=(reference.required_dlc if reference is not None else ()),
                excluded_dlc=(reference.excluded_dlc if reference is not None else ()),
            )
            self._oobs[name] = oob
            # Not one of the OOBs the section loaded: a mod file that is new
            # or that the loader refused.
            # It is only this lookup's cache, so save() and discard() load
            # the section again (and with it the loader's diagnostics).
            self._reload_on_discard.add("oobs")
            if path.resolve(strict=False).is_relative_to(self.mod_root):
                text = read_text(path)
                self._original_files.setdefault(path, text)
                self._note_read(path, text)
            return oob
        if default is not MISSING:
            return default
        raise not_found("OOB", name, self.list_oobs(include_vanilla=include_vanilla))

    def create_oob(
        self,
        name: str,
        tag: str,
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
        """Create an order of battle file (``history/units/<name>.txt``); staged until ``save()``.

        One file holds land, naval or air content, not a mix. The mod keeps
        copies of the given objects.

        Args:
            name: The file name without ``.txt``.
            tag: The country it belongs to.
            kind: ``"auto"`` takes the kind from the content (land when empty).
            required_dlc: Assign it only when these DLCs are active.
            excluded_dlc: Assign it only when these DLCs are not active.
            assign: Also add it to the country's history (``oob``,
                ``set_naval_oob`` or ``set_air_oob``).
            overwrite: Replace an OOB of this name.
            path: The output file. Without it, ``history/units/<name>.txt``.

        Returns:
            The new order of battle.

        Raises:
            TypeError: ``templates``, ``divisions``, ``fleets`` or ``air_wings`` holds something of another class; nothing changed.
            AlreadyExistsError: The name exists and ``overwrite`` is false.
            NotFoundError: With a game install configured, no one defines
                ``tag``; or ``assign`` is true and the country is unknown.
            ValueError: The name or tag is not valid, the content mixes
                kinds or does not match ``kind``, a DLC is both required and
                excluded, or the assignment conflicts with the country's
                other OOBs.
        """
        name = require_script_id(name, label="OOB name")
        lists: dict[str, Any] = {
            "templates": templates, "divisions": divisions, "fleets": fleets,
            "air_wings": air_wings,
        }
        _check_model_lists("create_oob", OrderOfBattle, lists)
        templates, divisions = lists["templates"], lists["divisions"]
        fleets, air_wings = lists["fleets"], lists["air_wings"]
        country_tag = self._require_country_reference(tag)
        existing = self._oobs.get(name)
        if existing is not None and not overwrite:
            raise AlreadyExistsError(
                f"OOB '{name}' already exists. Use overwrite=True only for "
                "deliberate replacement."
            )
        target = self._output_path(path or Path("history") / "units" / f"{name}.txt")
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
            for index, division in enumerate(oob.divisions):
                if index < len(existing.divisions):
                    division.source_index = existing.divisions[index].source_index
                    division.raw_block = existing.divisions[index].raw_block
            _inherit_fleet_sources(oob.fleets, existing.fleets)
            for index, wing in enumerate(oob.air_wings):
                if index < len(existing.air_wings):
                    source = existing.air_wings[index]
                    wing.source_index = source.source_index
                    wing.source_location_index = source.source_location_index
                    wing.raw_block = source.raw_block
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
    ) -> OrderOfBattle:
        """Replace selected collections and assignment data of an OOB.

        A game OOB is copied into the mod first. ``assign=True`` also adds
        the history reference of ``country_tag``.

        Returns:
            The updated OOB.

        Raises:
            TypeError: ``templates``, ``divisions``, ``fleets`` or ``air_wings`` holds something of another class; nothing changed.
            NotFoundError: No OOB file has this name.
            ValueError: The update would mix land, naval and air content, or
                the kind does not match the content.
        """
        oob = self.get_oob(name)
        lists: dict[str, Any] = {
            "templates": templates, "divisions": divisions, "fleets": fleets,
            "air_wings": air_wings,
        }
        _check_model_lists("update_oob", OrderOfBattle, lists, none_is_unset=True)
        templates, divisions = lists["templates"], lists["divisions"]
        fleets, air_wings = lists["fleets"], lists["air_wings"]
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
            _inherit_sources(template_replacements, oob.templates)
            oob.templates = template_replacements
            oob.touched_fields.add("templates")
        if divisions is not None:
            division_replacements = copy.deepcopy(list(divisions))
            _inherit_sources(division_replacements, oob.divisions)
            oob.divisions = division_replacements
            oob.touched_fields.add("divisions")
        if fleets is not None:
            fleet_replacements = copy.deepcopy(list(fleets))
            _inherit_fleet_sources(fleet_replacements, oob.fleets)
            oob.fleets = fleet_replacements
            oob.touched_fields.add("fleets")
        if air_wings is not None:
            wing_replacements = copy.deepcopy(list(air_wings))
            _inherit_sources(wing_replacements, oob.air_wings)
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
                kind=oob.kind,
                required_dlc=oob.required_dlc,
                excluded_dlc=oob.excluded_dlc,
            )
        self._dirty_oobs.add(oob.name)
        if oob.path is not None:
            self._dirty_oob_files.add(oob.path)
        self._dirty.add("oobs")
        return oob

    def delete_oob(self, name: str, *, unassign: bool = True) -> None:
        """Stage the deletion of an OOB the mod defines.

        ``unassign`` also removes every country history reference to it.

        Raises:
            NotFoundError: No OOB file has this name.
            ReadOnlyError: Only the game or a base mod has it.
        """
        read_only = ReadOnlyError(f"Cannot delete game OOB '{name}'")
        try:
            oob = self.get_oob(name, include_vanilla=False)
        except NotFoundError:
            if self._base_content is not None and self._base_content.file(
                f"history/units/{name}.txt"
            ) is not None:
                raise read_only from None
            raise
        if oob.path is not None and not oob.path.resolve(strict=False).is_relative_to(
            self.mod_root
        ):
            raise read_only
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

    def _require_oob_reference(self, name: str) -> None:
        """Raise ``NotFoundError`` when the SDK knows no OOB file is called ``name``.

        The mod's OOBs (pending ones included) count, and with a configured
        game install the inherited ones too. Without a game install the OOB
        may be the game's, so it is accepted and ``validate()`` checks it.
        """
        if name in self._oobs or (self.mod_root / "history" / "units" / f"{name}.txt").is_file():
            return
        if self.hoi4_install is None or self._base_content is None:
            return
        inherited = self._base_content.glob("history/units", "*.txt")
        if not inherited or any(path.stem == name for path in inherited.values()):
            return
        raise not_found(
            "OOB",
            name,
            self.list_oobs(include_vanilla=True),
            plural="OOBs",
            hint="Create it with create_oob() first.",
        )

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
        """Assign an OOB with the engine's land/naval/air history effect.

        Raises:
            NotFoundError: No one defines the country (its history is where
                the assignment goes), or a game install is configured and no
                OOB file has this name.
            ValueError: The country already assigns another OOB of this kind
                for the same date and DLC conditions.
        """

        tag = self._require_known_country(tag)
        name = require_script_id(name, label="OOB name")
        self._require_oob_reference(name)
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
    ) -> None:
        """Remove one exact land, naval, or air OOB assignment.

        Raises:
            NotFoundError: The country, or that exact assignment, does not exist.
        """

        tag = self._require_known_country(tag)
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
            raise NotFoundError(
                f"Country '{tag}' has no {kind} OOB assignment of '{name}' with these "
                "date and DLC conditions.",
                kind="OOB assignment",
                id=name,
            )
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

    def create_equipment_variant(
        self,
        tag: str,
        variant: EquipmentVariant,
    ) -> EquipmentVariant:
        """Append a validated equipment variant to country history.

        Raises:
            TypeError: ``variant`` is not an ``EquipmentVariant``; nothing changed.
            NotFoundError: No one defines the country (its history is where
                the variant goes).
            ValueError: The variant is invalid or uses a chassis before it is
                unlocked.
        """

        _check_model("create_equipment_variant", variant, "variant", (EquipmentVariant,))
        country_tag = self._require_known_country(tag)
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
                raise AlreadyExistsError(
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
        relative = (
            self._base_content.relative(source.resolve(strict=False))
            if self._base_content is not None else None
        )
        if relative is None:
            raise ValueError(f"Cannot retarget OOB '{oob.name}' without a HOI4 install")
        target = self._output_path(relative)
        self._original_files.setdefault(
            target,
            self._read_noted_text(target)
            or read_text(source),
        )
        if parse(oob.raw_text).problems:
            # The game tolerates the inherited file's missing or stray braces;
            # the mod's own copy is patched and written out well formed.
            oob.raw_text = repair(oob.raw_text)
        oob.path = target
        self._oobs[oob.name] = oob
        self._dirty_oob_files.add(target)
