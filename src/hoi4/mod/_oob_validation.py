"""Order-of-battle validation against the effective game data.

How the game uses an OOB file, and so what these checks assume:

- A country loads the OOB files its history names (``oob``, ``set_oob``,
  ``set_naval_oob``, ``set_air_oob``) only when it exists at the scenario
  start. A releasable country that owns no state never loads them.
- Division templates belong to the country, not to one file. A division may
  use a template from an OOB the same country loaded earlier, or one that
  script creates with a ``division_template = { name = ... }`` effect.
- Units may start outside the country's own territory (British divisions in
  Egypt, a Japanese fleet in a Chinese port), so that is only advice.
- Man the Guns hulls and By Blood Alone airframes only exist with their DLC,
  and the variant a ship or wing names may come from any of several
  ``if``/``else`` branches of the owner or creator history.
"""

from __future__ import annotations

import dataclasses
from collections import defaultdict
from typing import Iterable

from ..oob import (
    OOBReference,
    OrderOfBattle,
    load_oob_file,
    validate_oob,
)
from ..syntax import parse
from ..tags import (
    load_all_tags,
)
from ..types import (
    State,
    ValidationError,
)
from ._countries import CountriesLayer
from ._equipment import EquipmentLayer, _ScriptCondition
from ._states import StatesLayer

_MAN_THE_GUNS = "Man the Guns"
_BY_BLOOD_ALONE = "By Blood Alone"

# Script folders whose effects may create a division template at runtime.
_TEMPLATE_SCRIPT_DIRS = (
    "common/scripted_effects",
    "common/national_focus",
    "common/decisions",
    "common/on_actions",
    "events",
)


@dataclasses.dataclass
class _VariantOccurrence:
    """One ``create_equipment_variant`` of a country history."""

    name: str
    equipment_type: str
    condition: _ScriptCondition
    date: tuple[int, ...]


@dataclasses.dataclass
class _OobCheckData:
    """Game data the OOB checks share, built once per validation run."""

    states: dict[int, State]
    province_states: dict[int, State]
    province_types: dict[int, str]
    sub_unit_types: set[str]
    equipment_ids: set[str]
    known_tags: set[str]
    scenario_tags: set[str]
    script_templates: set[str]
    variants: dict[str, list[_VariantOccurrence]] = dataclasses.field(
        default_factory=dict
    )


@dataclasses.dataclass
class _OobLines:
    """Line numbers of the items of one OOB file, in reading order."""

    templates: list[int] = dataclasses.field(default_factory=list)
    divisions: list[int] = dataclasses.field(default_factory=list)
    fleets: list[int] = dataclasses.field(default_factory=list)
    task_forces: dict[tuple[int, int], int] = dataclasses.field(default_factory=dict)
    ships: dict[tuple[int, int, int], int] = dataclasses.field(default_factory=dict)
    air_wings: list[int] = dataclasses.field(default_factory=list)

    @staticmethod
    def at(values: list[int], index: int) -> int | None:
        return values[index] if 0 <= index < len(values) else None


def _date_key(date: str) -> tuple[int, ...]:
    parts = date.split(".")
    return tuple(int(part) for part in parts if part.isdigit())


def _division_template_names(text: str) -> frozenset[str]:
    """The ``name`` of every ``division_template`` block anywhere in a script."""

    # A cheap filter before parsing; the tree decides what counts.
    if "division_template" not in text:
        return frozenset()
    names: set[str] = set()
    for item, _ in parse(text).root.walk():
        if item.name == "division_template" and item.block is not None:
            name = item.block.scalar("name")
            if name:
                names.add(name)
    return frozenset(names)


def _oob_lines(oob: OrderOfBattle) -> _OobLines:
    """Find the line of each template, division, fleet, ship and wing."""

    lines = _OobLines()
    if not oob.raw_text:
        return lines
    doc = parse(oob.raw_text)

    def line(offset: int) -> int:
        return doc.line_col(offset)[0]

    for item in doc.root.assignments("division_template"):
        if item.op == "=" and item.block is not None:
            lines.templates.append(line(item.start))
    units = doc.root.block("units")
    if units is not None:
        fleet_index = 0
        for item in units.assignments():
            if item.op != "=" or item.block is None:
                continue
            if item.name == "division":
                lines.divisions.append(line(item.start))
            elif item.name == "fleet":
                lines.fleets.append(line(item.start))
                task_force_index = 0
                for task_force in item.block.assignments("task_force"):
                    if task_force.op != "=" or task_force.block is None:
                        continue
                    lines.task_forces[(fleet_index, task_force_index)] = line(
                        task_force.start
                    )
                    ship_index = 0
                    for ship in task_force.block.assignments("ship"):
                        if ship.op != "=" or ship.block is None:
                            continue
                        lines.ships[(fleet_index, task_force_index, ship_index)] = (
                            line(ship.start)
                        )
                        ship_index += 1
                    task_force_index += 1
                fleet_index += 1
    air = doc.root.block("air_wings")
    if air is not None:
        for base in air.assignments():
            if not base.name.isdigit() or base.block is None:
                continue
            for wing in base.block.assignments():
                if wing.block is not None and wing.name != "ace":
                    lines.air_wings.append(line(wing.start))
    return lines


class OobValidationLayer(StatesLayer, CountriesLayer, EquipmentLayer):
    """Order-of-battle validation against the effective game data."""

    # ── Shared game data ───────────────────────────────────────

    def _oob_check_data(self) -> _OobCheckData:
        run = self._validation_run
        if run is not None:
            cached = run.get("oob_check_data")
            if isinstance(cached, _OobCheckData):
                return cached
        data = self._build_oob_check_data()
        if run is not None:
            run["oob_check_data"] = data
        return data

    def _build_oob_check_data(self) -> _OobCheckData:
        states: dict[int, State] = {}
        state_ids = (
            {entry["id"] for entry in self.state_index()}
            | set(self._state_ids)
            | set(self._states)
        ) - set(self._deleted_state_ids)
        for state_id in sorted(state_ids):
            try:
                states[state_id] = self.get_state(state_id)
            except (KeyError, OSError, ValueError):
                continue
        province_states: dict[int, State] = {}
        for state in states.values():
            for province_id in state.provinces:
                province_states[province_id] = state
        known_tags = set(load_all_tags(self._base_content_root, self.mod_root))
        known_tags.update(self._countries)
        return _OobCheckData(
            states=states,
            province_states=province_states,
            province_types=self._effective_province_types(),
            sub_unit_types=self._known_sub_unit_types(),
            equipment_ids=self._oob_equipment_ids(),
            known_tags=known_tags,
            scenario_tags={state.owner for state in states.values() if state.owner},
            script_templates=self._script_division_templates(),
        )

    def _oob_equipment_ids(self) -> set[str]:
        """Equipment ids, including those ``duplicate_archetypes`` derives.

        A duplicated archetype such as ``small_plane_cas_airframe`` (made from
        ``small_plane_airframe``) also gets one equipment for every equipment
        of the original archetype: ``small_plane_airframe_0`` gives
        ``small_plane_cas_airframe_0``.
        """

        cached = self._scan_cache.get("oob_equipment")
        if cached is not None:
            return set(cached)
        ids = set(self._known_equipment_ids())
        members: dict[str, set[str]] = defaultdict(set)
        duplicates: dict[str, str] = {}
        for _, text in self._effective_script_texts("common/units/equipment"):
            root = parse(text).root
            for container in root.assignments():
                if container.block is None:
                    continue
                if container.name == "equipments":
                    for item in container.block.assignments():
                        if item.block is None:
                            continue
                        ids.add(item.name)
                        archetype = item.block.scalar("archetype")
                        if archetype:
                            members[archetype].add(item.name)
                elif container.name == "duplicate_archetypes":
                    for item in container.block.assignments():
                        if item.block is None:
                            continue
                        ids.add(item.name)
                        archetype = item.block.scalar("archetype")
                        if archetype:
                            duplicates[item.name] = archetype
                        names = item.block.block("variant_name")
                        if names is not None:
                            ids.update(entry.name for entry in names.assignments())
        for duplicate, archetype in duplicates.items():
            for member in members.get(archetype, ()):
                if member.startswith(archetype) and member != archetype:
                    ids.add(duplicate + member[len(archetype) :])
        self._scan_cache["oob_equipment"] = set(ids)
        return ids

    def _script_division_templates(self) -> set[str]:
        """Template names that history or effect script creates at runtime."""

        names: set[str] = set()
        for country in self._countries.values():
            names |= _division_template_names(country.raw_history)
        for relative in _TEMPLATE_SCRIPT_DIRS:
            for _, found in self._effective_script_results(
                relative, "division_template_names", _division_template_names
            ):
                names |= found
        return names

    def _country_variant_occurrences(
        self,
        tag: str,
        data: _OobCheckData,
    ) -> list[_VariantOccurrence]:
        cached = data.variants.get(tag)
        if cached is not None:
            return cached
        result: list[_VariantOccurrence] = []
        try:
            history = self.get_country(tag).raw_history
        except (KeyError, OSError, ValueError):
            history = ""
        sections: list[tuple[str, tuple[int, ...]]] = [(history, ())]
        for item in parse(history).root.assignments():
            date = _date_key(item.name)
            # An unclosed block ends at the end of the file, for the game too.
            if len(date) >= 3 and item.block is not None:
                sections.append(
                    (history[item.block.body_start : item.block.body_end], date)
                )
        for text, date in sections:
            _, occurrences = self._history_equipment_operations(text)
            for occurrence in occurrences:
                required, excluded = occurrence.condition
                result.append(
                    _VariantOccurrence(
                        name=occurrence.variant.name,
                        equipment_type=occurrence.variant.equipment_type,
                        # A gate the checker cannot evaluate (a game rule, a
                        # flag) may hold: keep only the DLC part.
                        condition=(
                            tuple(atom for atom in required if atom[0] == "dlc"),
                            tuple(atom for atom in excluded if atom[0] == "dlc"),
                        ),
                        date=date,
                    )
                )
        data.variants[tag] = result
        return result

    def _load_oob_for_validation(self, name: str) -> OrderOfBattle | None:
        """The mod's or inherited OOB file, without adding it to the mod."""

        cached = self._oobs.get(name)
        if cached is not None:
            return cached
        roots = [self.mod_root]
        if self._base_content_root is not None:
            roots.append(self._base_content_root)
        for root in roots:
            path = root / "history" / "units" / f"{name}.txt"
            if path.is_file():
                return load_oob_file(path, name=name)
        return None

    # ── The validation phase ───────────────────────────────────

    def _validate_country_oobs(self) -> list[ValidationError]:
        """Check every OOB file and every country assignment of one."""

        data = self._oob_check_data()
        errors: list[ValidationError] = []
        references = {
            tag: self._country_oob_references(country)
            for tag, country in self._countries.items()
        }
        oobs: dict[str, OrderOfBattle] = {}
        unreadable: set[str] = set()

        def load(name: str, tag: str, kind: str) -> OrderOfBattle | None:
            if name in oobs:
                return oobs[name]
            if name in unreadable:
                return None
            try:
                oob = self._load_oob_for_validation(name)
            except (OSError, ValueError) as error:
                unreadable.add(name)
                errors.append(
                    ValidationError(
                        message=(
                            f"Could not read {kind} OOB '{name}' of country "
                            f"'{tag}': {error}"
                        ),
                        severity="error",
                        code="oob_load_error",
                        country_tag=tag,
                    )
                )
                return None
            if oob is not None:
                oobs[name] = oob
            return oob

        assignments: list[tuple[str, OOBReference, OrderOfBattle]] = []
        first_reference: dict[str, tuple[str, OOBReference]] = {}
        users: dict[str, set[str]] = defaultdict(set)
        country_templates: dict[str, set[str]] = defaultdict(set)
        for tag, country_references in references.items():
            country = self._countries[tag]
            for reference in country_references:
                oob = load(reference.name, tag, reference.kind)
                if oob is None:
                    if reference.name in unreadable:
                        continue
                    errors.append(
                        self._missing_oob_reference(
                            tag,
                            reference,
                            active=tag in data.scenario_tags,
                            file_path=(
                                str(country.history_path)
                                if country.history_path is not None
                                else None
                            ),
                        )
                    )
                    continue
                assignments.append((tag, reference, oob))
                first_reference.setdefault(oob.name, (tag, reference))
                users[oob.name].add(tag)
                country_templates[tag].update(
                    template.name for template in oob.templates
                )
        for name, oob in self._oobs.items():
            oobs.setdefault(name, oob)

        for name, oob in oobs.items():
            tags = users.get(name) or {oob.country_tag}
            known_templates = set(data.script_templates)
            for tag in tags:
                known_templates.update(country_templates.get(tag, ()))
            # The file's own checks run once, seen as its first user loads it.
            first = first_reference.get(name)
            context = (
                self._oob_assignment_copy(oob, first[0], first[1])
                if first is not None
                else self._oob_assignment_copy(oob, oob.country_tag, None)
            )
            errors.extend(validate_oob(context, known_templates=known_templates))
            errors.extend(self._validate_oob_contents(context, data))

        for tag, reference, oob in assignments:
            # A country that owns nothing at the start never loads the file.
            if tag not in data.scenario_tags:
                continue
            errors.extend(
                self._validate_oob_assignment(
                    self._oob_assignment_copy(oob, tag, reference),
                    tag,
                    reference,
                    data,
                )
            )
        return errors

    @staticmethod
    def _missing_oob_reference(
        tag: str,
        reference: OOBReference,
        *,
        active: bool,
        file_path: str | None,
    ) -> ValidationError:
        if active:
            return ValidationError(
                message=(
                    f"Country '{tag}' references missing {reference.kind} OOB "
                    f"'{reference.name}'"
                ),
                severity="error",
                code="missing_oob_reference",
                country_tag=tag,
                file_path=file_path,
            )
        return ValidationError(
            message=(
                f"Country '{tag}' references missing {reference.kind} OOB "
                f"'{reference.name}'. The country owns no state at the "
                "scenario start, so the game never loads it, but the "
                "reference breaks if the country ever starts on the map."
            ),
            severity="warning",
            code="unloaded_missing_oob_reference",
            country_tag=tag,
            file_path=file_path,
        )

    @staticmethod
    def _oob_assignment_copy(
        oob: OrderOfBattle,
        tag: str,
        reference: OOBReference | None,
    ) -> OrderOfBattle:
        """A view of ``oob`` as one country loads it; the original is untouched."""

        if reference is None:
            return dataclasses.replace(oob, country_tag=tag or oob.country_tag)
        return dataclasses.replace(
            oob,
            country_tag=tag,
            kind=reference.kind,
            required_dlc=reference.required_dlc,
            excluded_dlc=reference.excluded_dlc,
        )

    def _validate_oob_context(
        self,
        oob: OrderOfBattle,
        country_tag: str,
        reference: OOBReference | None = None,
        *,
        structure: bool = False,
    ) -> list[ValidationError]:
        """Check one OOB's contents and its use by ``country_tag``.

        ``structure`` also runs the file's own checks (``validate_oob``),
        with the templates the country gets from its other OOB files and
        from script.
        """

        data = self._oob_check_data()
        if reference is None:
            reference = OOBReference(
                oob.name,
                oob.kind if oob.kind != "mixed" else "land",
                oob.required_dlc,
                oob.excluded_dlc,
            )
        view = self._oob_assignment_copy(oob, country_tag, reference)
        findings: list[ValidationError] = []
        if structure:
            known_templates = set(data.script_templates)
            country = self._countries.get(country_tag)
            for other in self._country_oob_references(country) if country else ():
                if other.name == oob.name:
                    continue
                try:
                    loaded = self._load_oob_for_validation(other.name)
                except (OSError, ValueError):
                    continue
                if loaded is not None:
                    known_templates.update(t.name for t in loaded.templates)
            findings.extend(validate_oob(view, known_templates=known_templates))
        findings.extend(self._validate_oob_contents(view, data))
        findings.extend(
            self._validate_oob_assignment(view, country_tag, reference, data)
        )
        return findings

    # ── Checks of one file ─────────────────────────────────────

    def _validate_oob_contents(
        self,
        oob: OrderOfBattle,
        data: _OobCheckData,
    ) -> list[ValidationError]:
        """Checks that only depend on the file and the game data."""

        errors: list[ValidationError] = []
        lines = _oob_lines(oob)
        file_path = str(oob.path) if oob.path is not None else None
        tag = oob.country_tag or None

        def flag(
            message: str,
            code: str,
            line: int | None,
            *,
            state_id: int | None = None,
        ) -> None:
            errors.append(
                ValidationError(
                    message=f"OOB '{oob.name}' {message}",
                    severity="error",
                    code=code,
                    country_tag=tag,
                    state_id=state_id,
                    file_path=file_path,
                    line=line,
                )
            )

        units = data.sub_unit_types
        equipment_ids = data.equipment_ids
        for index, template in enumerate(oob.templates):
            line = lines.at(lines.templates, index)
            for battalion in (*template.battalions, *template.support):
                if units and battalion.unit_type and battalion.unit_type not in units:
                    flag(
                        f"template '{template.name}' uses unknown sub-unit type "
                        f"'{battalion.unit_type}'",
                        "unknown_oob_unit_type",
                        line,
                    )

        types = data.province_types
        for index, division in enumerate(oob.divisions):
            if division.location <= 0:
                continue  # validate_oob reports invalid_oob_location.
            line = lines.at(lines.divisions, index)
            label = division.name or "<ordered name>"
            state = data.province_states.get(division.location)
            province_type = types.get(division.location)
            if state is None or (types and province_type is None):
                flag(
                    f"division '{label}' uses unknown province {division.location}",
                    "unknown_oob_province",
                    line,
                    state_id=state.id if state is not None else None,
                )
            elif province_type is not None and province_type != "land":
                flag(
                    f"division '{label}' is placed in {province_type} province "
                    f"{division.location}",
                    "oob_water_location",
                    line,
                    state_id=state.id,
                )

        for fleet_index, fleet in enumerate(oob.fleets):
            fleet_line = lines.at(lines.fleets, fleet_index)
            if fleet.naval_base > 0 and fleet.naval_base not in data.province_states:
                flag(
                    f"fleet '{fleet.name}' naval base uses unknown location "
                    f"{fleet.naval_base}",
                    "unknown_oob_naval_location",
                    fleet_line,
                )
            for task_force_index, task_force in enumerate(fleet.task_forces):
                task_force_line = lines.task_forces.get(
                    (fleet_index, task_force_index), fleet_line
                )
                location = task_force.location
                # A task force may start in a port or at sea.
                if (
                    location > 0
                    and location not in data.province_states
                    and location not in types
                ):
                    flag(
                        f"task force '{task_force.name}' location uses unknown "
                        f"location {location}",
                        "unknown_oob_naval_location",
                        task_force_line,
                    )
                for ship_index, ship in enumerate(task_force.ships):
                    ship_line = lines.ships.get(
                        (fleet_index, task_force_index, ship_index), task_force_line
                    )
                    label = ship.name or "<ordered name>"
                    if units and ship.definition and ship.definition not in units:
                        flag(
                            f"ship '{label}' uses unknown definition "
                            f"'{ship.definition}'",
                            "unknown_oob_ship_definition",
                            ship_line,
                        )
                    for equipment in ship.equipment:
                        if (
                            equipment_ids
                            and equipment.equipment_type
                            and equipment.equipment_type not in equipment_ids
                        ):
                            flag(
                                f"ship '{label}' uses unknown equipment "
                                f"'{equipment.equipment_type}'",
                                "unknown_oob_equipment",
                                ship_line,
                            )
                        for owner in (equipment.owner, equipment.creator):
                            if owner and owner not in data.known_tags:
                                flag(
                                    f"ship '{label}' uses unknown equipment owner "
                                    f"'{owner}'",
                                    "unknown_oob_equipment_owner",
                                    ship_line,
                                )

        for index, wing in enumerate(oob.air_wings):
            line = lines.at(lines.air_wings, index)
            # Air wings sit at a state id; older files also use a province.
            if (
                wing.location > 0
                and wing.location not in data.states
                and wing.location not in data.province_states
            ):
                flag(
                    f"air wing '{wing.equipment_type}' location uses unknown "
                    f"location {wing.location}",
                    "unknown_oob_air_location",
                    line,
                )
            if (
                equipment_ids
                and wing.equipment_type
                and wing.equipment_type not in equipment_ids
            ):
                flag(
                    f"air wing uses unknown equipment '{wing.equipment_type}'",
                    "unknown_oob_equipment",
                    line,
                )
            for owner in (wing.owner, wing.creator):
                if owner and owner not in data.known_tags:
                    flag(
                        f"air wing uses unknown equipment owner '{owner}'",
                        "unknown_oob_equipment_owner",
                        line,
                    )
        return errors

    # ── Checks of one country's use of a file ──────────────────

    def _validate_oob_assignment(
        self,
        oob: OrderOfBattle,
        country_tag: str,
        reference: OOBReference,
        data: _OobCheckData,
    ) -> list[ValidationError]:
        """Checks that depend on which country loads the file, and when."""

        errors: list[ValidationError] = []
        lines = _oob_lines(oob)
        file_path = str(oob.path) if oob.path is not None else None
        required = set(reference.required_dlc)
        excluded = set(reference.excluded_dlc)

        def flag_use(
            message: str,
            code: str,
            line: int | None,
            *,
            severity: str = "error",
            state_id: int | None = None,
        ) -> None:
            errors.append(
                ValidationError(
                    message=f"OOB '{oob.name}' {message}",
                    severity=severity,
                    code=code,
                    country_tag=country_tag,
                    state_id=state_id,
                    file_path=file_path,
                    line=line,
                )
            )

        # Ownership is known for the scenario start only; a dated reference
        # (``1939.1.1 = { set_oob = ... }``) loads with later borders.
        check_owner = not reference.date

        def owner_advice(state: State | None, label: str, location: int, code: str, line: int | None) -> None:
            if not check_owner or state is None or state.owner == country_tag:
                return
            flag_use(
                f"{label} starts at {location}, owned by {state.owner}, not by "
                f"{country_tag}. The game allows this (for example units in a "
                "subject's or an ally's territory); check the location if it "
                "was not intended.",
                code,
                line,
                severity="info",
                state_id=state.id,
            )

        for index, division in enumerate(oob.divisions):
            owner_advice(
                data.province_states.get(division.location),
                f"division '{division.name or '<ordered name>'}'",
                division.location,
                "oob_location_not_owned",
                lines.at(lines.divisions, index),
            )
        for fleet_index, fleet in enumerate(oob.fleets):
            fleet_line = lines.at(lines.fleets, fleet_index)
            owner_advice(
                data.province_states.get(fleet.naval_base),
                f"fleet '{fleet.name}' naval base",
                fleet.naval_base,
                "oob_naval_location_not_owned",
                fleet_line,
            )
            for task_force_index, task_force in enumerate(fleet.task_forces):
                owner_advice(
                    data.province_states.get(task_force.location),
                    f"task force '{task_force.name}'",
                    task_force.location,
                    "oob_naval_location_not_owned",
                    lines.task_forces.get((fleet_index, task_force_index), fleet_line),
                )
        for index, wing in enumerate(oob.air_wings):
            owner_advice(
                data.states.get(wing.location)
                or data.province_states.get(wing.location),
                f"air wing '{wing.equipment_type}'",
                wing.location,
                "oob_air_location_not_owned",
                lines.at(lines.air_wings, index),
            )

        reported_legacy = False
        reported_ungated = False
        for fleet_index, fleet in enumerate(oob.fleets):
            for task_force_index, task_force in enumerate(fleet.task_forces):
                for ship_index, ship in enumerate(task_force.ships):
                    line = lines.ships.get(
                        (fleet_index, task_force_index, ship_index)
                    )
                    label = ship.name or "<ordered name>"
                    for equipment in ship.equipment:
                        is_hull = equipment.equipment_type.startswith("ship_hull_")
                        if not is_hull and _MAN_THE_GUNS not in excluded:
                            if not reported_legacy:
                                reported_legacy = True
                                flag_use(
                                    f"uses legacy naval equipment "
                                    f"'{equipment.equipment_type}' when Man the "
                                    "Guns may be active. HOI4 will skip these "
                                    "ships because no proper equipment variant "
                                    "exists. Assign a legacy naval OOB with "
                                    "excluded_dlc=('Man the Guns',) and a "
                                    "hull-based OOB with "
                                    "required_dlc=('Man the Guns',).",
                                    (
                                        "mtg_naval_oob_uses_legacy_equipment"
                                        if _MAN_THE_GUNS in required
                                        else "legacy_naval_oob_without_dlc_fallback"
                                    ),
                                    line,
                                )
                        if is_hull and _MAN_THE_GUNS not in required:
                            if not reported_ungated:
                                reported_ungated = True
                                flag_use(
                                    "uses Man the Guns hull equipment but is not "
                                    "gated with required_dlc=('Man the Guns',). "
                                    "Add the gate and a legacy naval fallback.",
                                    "ungated_mtg_naval_oob",
                                    line,
                                )
                        if not is_hull:
                            continue
                        if not equipment.version_name:
                            flag_use(
                                f"ship '{label}' uses "
                                f"'{equipment.equipment_type}' without a "
                                "version_name matching a country-history "
                                "create_equipment_variant definition.",
                                "missing_mtg_ship_variant_name",
                                line,
                            )
                        elif not self._oob_variant_available(
                            equipment.equipment_type,
                            equipment.version_name,
                            (equipment.creator, equipment.owner, country_tag),
                            reference,
                            _MAN_THE_GUNS,
                            data,
                        ):
                            flag_use(
                                f"ship '{label}' references "
                                f"{equipment.equipment_type} variant "
                                f"'{equipment.version_name}', but no "
                                "create_equipment_variant definition in the "
                                "owner or creator country history exists on "
                                "every DLC path that loads this OOB.",
                                "missing_mtg_equipment_variant",
                                line,
                            )

        for index, wing in enumerate(oob.air_wings):
            line = lines.at(lines.air_wings, index)
            if "airframe" not in wing.equipment_type:
                continue
            if _BY_BLOOD_ALONE not in required:
                flag_use(
                    f"uses By Blood Alone airframe equipment "
                    f"'{wing.equipment_type}' but is not gated with "
                    "required_dlc=('By Blood Alone',). Add the gate and a "
                    "legacy air OOB fallback.",
                    "ungated_bba_air_oob",
                    line,
                )
            if not wing.version_name:
                flag_use(
                    f"air wing uses '{wing.equipment_type}' without a "
                    "version_name matching a country-history "
                    "create_equipment_variant definition.",
                    "missing_bba_air_variant_name",
                    line,
                )
            elif not self._oob_variant_available(
                wing.equipment_type,
                wing.version_name,
                (wing.creator, wing.owner, country_tag),
                reference,
                _BY_BLOOD_ALONE,
                data,
            ):
                flag_use(
                    f"air wing references {wing.equipment_type} variant "
                    f"'{wing.version_name}', but no create_equipment_variant "
                    "definition in the owner or creator country history "
                    "exists on every DLC path that loads this OOB.",
                    "missing_bba_equipment_variant",
                    line,
                )
        return errors

    def _oob_variant_available(
        self,
        equipment_type: str,
        version_name: str,
        tags: Iterable[str],
        reference: OOBReference,
        dlc: str,
        data: _OobCheckData,
    ) -> bool:
        """Whether a matching variant exists wherever the OOB is loaded.

        The OOB loads on the DLC paths of its assignment, and its modular
        equipment only exists with ``dlc``. The matching variants, from any
        branch of the creator, owner or loading country history, must
        together cover all of those paths. A reference from a dated block
        also sees the variants of dated blocks up to its date.
        """

        date = _date_key(reference.date)
        conditions: list[_ScriptCondition] = []
        for tag in dict.fromkeys(tag for tag in tags if tag):
            for occurrence in self._country_variant_occurrences(tag, data):
                if (
                    occurrence.name == version_name
                    and occurrence.equipment_type == equipment_type
                    and (not occurrence.date or (date and occurrence.date <= date))
                ):
                    conditions.append(occurrence.condition)
        if not conditions:
            return False
        loads_on = self._dlc_script_condition(
            (*reference.required_dlc, dlc), reference.excluded_dlc
        )
        return self._script_conditions_are_covered(loads_on, conditions)
