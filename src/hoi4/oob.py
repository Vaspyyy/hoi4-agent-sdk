"""Land, naval, and air OOB models with source-preserving serialization."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, TypeVar

from .patching import (
    AssignmentSpan,
    append_assignment,
    replace_assignment,
    replace_assignment_body,
    set_block,
    set_scalar,
    top_level_assignments,
)
from .script import pdx_string, pdx_value
from .types import ValidationError

_T = TypeVar("_T")
AssignedOOBKind = Literal["land", "naval", "air"]
OOBKind = Literal["land", "naval", "air", "mixed"]
_OOB_ASSIGNMENT_KEYS: dict[str, AssignedOOBKind] = {
    "oob": "land",
    "set_oob": "land",
    "set_naval_oob": "naval",
    "set_air_oob": "air",
}


@dataclass(frozen=True)
class OOBReference:
    """One country-history OOB assignment and its date/DLC conditions."""

    name: str
    kind: AssignedOOBKind
    required_dlc: tuple[str, ...] = ()
    excluded_dlc: tuple[str, ...] = ()
    date: str = ""


@dataclass(frozen=True)
class EquipmentVariant:
    """A country-history ``create_equipment_variant`` definition."""

    name: str
    equipment_type: str
    name_group: str = ""
    parent_version: int = 0
    allow_without_tech: bool = False
    obsolete: bool = False
    mark_older_equipment_obsolete: bool = False
    role_icon_index: int | str | None = None
    upgrades: dict[str, int] = field(default_factory=dict)
    modules: dict[str, str] = field(default_factory=dict)
    model: str = ""
    icon: str = ""
    design_team: str = ""
    required_dlc: tuple[str, ...] = ()
    excluded_dlc: tuple[str, ...] = ()


@dataclass(frozen=True)
class Battalion:
    unit_type: str
    x: int
    y: int


@dataclass
class DivisionTemplate:
    name: str
    battalions: list[Battalion] = field(default_factory=list)
    support: list[Battalion] = field(default_factory=list)
    division_names_group: str = ""
    priority: int | None = None
    raw_block: str = ""
    touched_fields: set[str] = field(default_factory=set, repr=False)
    source_index: int = -1


@dataclass
class DivisionUnit:
    division_template: str
    location: int
    name: str = ""
    name_order: int | None = None
    start_experience_factor: float | None = None
    start_equipment_factor: float | None = None
    raw_block: str = ""
    touched_fields: set[str] = field(default_factory=set, repr=False)
    source_index: int = -1


@dataclass
class ShipEquipment:
    equipment_type: str
    amount: int = 1
    owner: str = ""
    creator: str = ""
    version_name: str = ""
    raw_block: str = ""
    touched_fields: set[str] = field(default_factory=set, repr=False)
    source_index: int = -1


@dataclass
class Ship:
    name: str
    definition: str
    equipment: list[ShipEquipment] = field(default_factory=list)
    pride_of_the_fleet: bool = False
    raw_block: str = ""
    touched_fields: set[str] = field(default_factory=set, repr=False)
    source_index: int = -1


@dataclass
class TaskForce:
    name: str
    location: int
    ships: list[Ship] = field(default_factory=list)
    raw_block: str = ""
    touched_fields: set[str] = field(default_factory=set, repr=False)
    source_index: int = -1


@dataclass
class Fleet:
    name: str
    naval_base: int
    task_forces: list[TaskForce] = field(default_factory=list)
    raw_block: str = ""
    touched_fields: set[str] = field(default_factory=set, repr=False)
    source_index: int = -1


@dataclass
class AirWing:
    location: int
    equipment_type: str
    amount: int
    owner: str = ""
    creator: str = ""
    version_name: str = ""
    raw_block: str = ""
    touched_fields: set[str] = field(default_factory=set, repr=False)
    source_index: int = -1
    source_location_index: int = -1


@dataclass
class OrderOfBattle:
    name: str
    country_tag: str = ""
    templates: list[DivisionTemplate] = field(default_factory=list)
    divisions: list[DivisionUnit] = field(default_factory=list)
    fleets: list[Fleet] = field(default_factory=list)
    air_wings: list[AirWing] = field(default_factory=list)
    kind: OOBKind = "land"
    required_dlc: tuple[str, ...] = ()
    excluded_dlc: tuple[str, ...] = ()
    path: Path | None = None
    raw_text: str = ""
    touched_fields: set[str] = field(default_factory=set, repr=False)


def load_oob_file(
    path: Path,
    *,
    name: str | None = None,
    country_tag: str = "",
    kind: OOBKind | None = None,
    required_dlc: tuple[str, ...] = (),
    excluded_dlc: tuple[str, ...] = (),
) -> OrderOfBattle:
    text = path.read_text(encoding="utf-8-sig", errors="ignore")
    templates: list[DivisionTemplate] = []
    template_index = 0
    for span in top_level_assignments(text):
        if (
            span.key != "division_template"
            or not span.is_block
            or span.body_start is None
            or span.body_end is None
        ):
            continue
        body = text[span.body_start : span.body_end]
        templates.append(_parse_template(body, template_index))
        template_index += 1

    divisions: list[DivisionUnit] = []
    fleets: list[Fleet] = []
    units = _block_span(text, "units")
    if units is not None and units.body_start is not None and units.body_end is not None:
        units_body = text[units.body_start : units.body_end]
        division_index = 0
        fleet_index = 0
        for span in top_level_assignments(units_body):
            if not span.is_block or span.body_start is None or span.body_end is None:
                continue
            body = units_body[span.body_start : span.body_end]
            if span.key == "division":
                divisions.append(_parse_division(body, division_index))
                division_index += 1
            elif span.key == "fleet":
                fleets.append(_parse_fleet(body, fleet_index))
                fleet_index += 1
    air_wings = _parse_air_wings(text)
    inferred_tag = country_tag or _infer_country_tag(path.stem)
    return OrderOfBattle(
        name=name or path.stem,
        country_tag=inferred_tag,
        templates=templates,
        divisions=divisions,
        fleets=fleets,
        air_wings=air_wings,
        kind=kind or _infer_oob_kind(templates, divisions, fleets, air_wings),
        required_dlc=required_dlc,
        excluded_dlc=excluded_dlc,
        path=path,
        raw_text=text,
    )


def find_oob_references(history: str) -> tuple[OOBReference, ...]:
    """Find land, naval, and air OOB assignments in country history."""

    result: list[OOBReference] = []
    for body, span, required_dlc, excluded_dlc, date in _conditioned_assignments(
        history
    ):
        if span.is_block:
            continue
        kind = _OOB_ASSIGNMENT_KEYS.get(span.key.lower())
        if kind is not None:
            result.append(
                OOBReference(
                    name=_unquote(body[span.value_start : span.value_end]),
                    kind=kind,
                    required_dlc=required_dlc,
                    excluded_dlc=excluded_dlc,
                    date=date,
                )
            )
    return tuple(result)


def find_equipment_variants(history: str) -> tuple[EquipmentVariant, ...]:
    """Find equipment variants in country history, retaining DLC conditions."""

    result: list[EquipmentVariant] = []
    for body, span, required_dlc, excluded_dlc, _date in _conditioned_assignments(
        history
    ):
        if (
            span.key != "create_equipment_variant"
            or not span.is_block
            or span.body_start is None
            or span.body_end is None
        ):
            continue
        child = body[span.body_start : span.body_end]
        role_icon_index = _optional_int(child, "role_icon_index")
        result.append(
            EquipmentVariant(
                name=_scalar_value(child, "name"),
                equipment_type=_scalar_value(child, "type"),
                name_group=_scalar_value(child, "name_group"),
                parent_version=_optional_int(child, "parent_version") or 0,
                allow_without_tech=(
                    _optional_bool(child, "allow_without_tech") or False
                ),
                obsolete=_optional_bool(child, "obsolete") or False,
                mark_older_equipment_obsolete=(
                    _optional_bool(child, "mark_older_equipment_obsolete")
                    or False
                ),
                role_icon_index=(
                    role_icon_index
                    if role_icon_index is not None
                    else _scalar_value(child, "role_icon_index") or None
                ),
                upgrades={
                    key: int(value)
                    for key, value in _scalar_mapping(
                        _block_value(child, "upgrades")
                    ).items()
                    if value.lstrip("-").isdigit()
                },
                modules=_scalar_mapping(_block_value(child, "modules")),
                model=_scalar_value(child, "model"),
                icon=_scalar_value(child, "icon"),
                design_team=_scalar_value(child, "design_team"),
                required_dlc=required_dlc,
                excluded_dlc=excluded_dlc,
            )
        )
    return tuple(result)


def serialize_equipment_variant(variant: EquipmentVariant) -> str:
    """Serialize one variant, including optional DLC gating."""

    if not variant.name.strip():
        raise ValueError("Equipment variant name cannot be empty")
    if not variant.equipment_type.strip():
        raise ValueError("Equipment variant type cannot be empty")
    lines = [
        f"name = {pdx_string(variant.name)}",
        f"type = {pdx_value(variant.equipment_type)}",
    ]
    if variant.name_group:
        lines.append(f"name_group = {pdx_value(variant.name_group)}")
    if variant.parent_version:
        lines.append(f"parent_version = {variant.parent_version}")
    if variant.allow_without_tech:
        lines.append("allow_without_tech = yes")
    if variant.obsolete:
        lines.append("obsolete = yes")
    if variant.mark_older_equipment_obsolete:
        lines.append("mark_older_equipment_obsolete = yes")
    if variant.role_icon_index is not None:
        lines.append(f"role_icon_index = {pdx_value(variant.role_icon_index)}")
    if variant.upgrades:
        body = "\n".join(
            f"{name} = {level}" for name, level in variant.upgrades.items()
        )
        lines.append(_wrap_block("upgrades", body, 0))
    if variant.modules:
        body = "\n".join(
            f"{slot} = {pdx_value(module)}"
            for slot, module in variant.modules.items()
        )
        lines.append(_wrap_block("modules", body, 0))
    for key, value in (
        ("model", variant.model),
        ("icon", variant.icon),
        ("design_team", variant.design_team),
    ):
        if value:
            lines.append(f"{key} = {pdx_value(value)}")
    effect = _wrap_block("create_equipment_variant", "\n".join(lines), 0)
    return conditional_history_effect(
        effect,
        required_dlc=variant.required_dlc,
        excluded_dlc=variant.excluded_dlc,
    )


def serialize_oob_assignment(reference: OOBReference) -> str:
    """Serialize one country-history OOB assignment."""

    key = {
        "land": "set_oob",
        "naval": "set_naval_oob",
        "air": "set_air_oob",
    }.get(reference.kind)
    if key is None:
        raise ValueError("A mixed OOB cannot be assigned to country history")
    effect = f"{key} = {pdx_string(reference.name)}"
    effect = conditional_history_effect(
        effect,
        required_dlc=reference.required_dlc,
        excluded_dlc=reference.excluded_dlc,
    )
    return _wrap_block(reference.date, effect, 0) if reference.date else effect


def remove_oob_reference(
    history: str,
    reference: OOBReference,
) -> tuple[str, bool]:
    """Remove one exact OOB assignment without re-rendering surrounding history."""

    changed = False

    def scalar_matches(
        fragment: str,
        span: AssignmentSpan,
        required_dlc: tuple[str, ...],
        excluded_dlc: tuple[str, ...],
        date: str,
    ) -> bool:
        kind = _OOB_ASSIGNMENT_KEYS.get(span.key.lower())
        return bool(
            kind == reference.kind
            and not span.is_block
            and _unquote(fragment[span.value_start : span.value_end])
            == reference.name
            and required_dlc == reference.required_dlc
            and excluded_dlc == reference.excluded_dlc
            and date == reference.date
        )

    def rewrite_if(
        fragment: str,
        required_dlc: tuple[str, ...],
        excluded_dlc: tuple[str, ...],
        date: str,
    ) -> str:
        nonlocal changed
        found_required, found_excluded = _dlc_conditions(
            _block_value(fragment, "limit")
        )
        positive_required = _merge_unique(required_dlc, found_required)
        positive_excluded = _merge_unique(excluded_dlc, found_excluded)
        negative_required = _merge_unique(required_dlc, found_excluded)
        negative_excluded = _merge_unique(excluded_dlc, found_required)
        result = fragment
        for span in reversed(top_level_assignments(fragment)):
            key = span.key.lower()
            if key == "limit":
                continue
            if not span.is_block:
                if scalar_matches(
                    fragment,
                    span,
                    positive_required,
                    positive_excluded,
                    date,
                ):
                    result = replace_assignment(result, span, None)
                    changed = True
                continue
            if span.body_start is None or span.body_end is None:
                continue
            child = fragment[span.body_start : span.body_end]
            if key == "else":
                updated = rewrite(child, negative_required, negative_excluded, date)
            elif key == "if":
                updated = rewrite_if(
                    child,
                    positive_required,
                    positive_excluded,
                    date,
                )
            else:
                updated = rewrite(
                    child,
                    positive_required,
                    positive_excluded,
                    span.key if _DATE_KEY_RE.fullmatch(span.key) else date,
                )
            if updated != child:
                result = replace_assignment_body(result, span, updated)
        return result

    def rewrite(
        fragment: str,
        required_dlc: tuple[str, ...],
        excluded_dlc: tuple[str, ...],
        date: str,
    ) -> str:
        nonlocal changed
        result = fragment
        for span in reversed(top_level_assignments(fragment)):
            if not span.is_block:
                if scalar_matches(
                    fragment,
                    span,
                    required_dlc,
                    excluded_dlc,
                    date,
                ):
                    result = replace_assignment(result, span, None)
                    changed = True
                continue
            if span.body_start is None or span.body_end is None:
                continue
            child = fragment[span.body_start : span.body_end]
            updated = (
                rewrite_if(child, required_dlc, excluded_dlc, date)
                if span.key.lower() == "if"
                else rewrite(
                    child,
                    required_dlc,
                    excluded_dlc,
                    span.key if _DATE_KEY_RE.fullmatch(span.key) else date,
                )
            )
            if updated != child:
                result = replace_assignment_body(result, span, updated)
        return result

    return rewrite(history, (), (), ""), changed


def conditional_history_effect(
    effect: str,
    *,
    required_dlc: tuple[str, ...] = (),
    excluded_dlc: tuple[str, ...] = (),
) -> str:
    """Wrap a history effect in an explicit DLC condition when requested."""

    required = _merge_unique((), required_dlc)
    excluded = _merge_unique((), excluded_dlc)
    if set(required) & set(excluded):
        overlap = sorted(set(required) & set(excluded))
        raise ValueError(f"DLC cannot be both required and excluded: {overlap}")
    if not required and not excluded:
        return effect
    conditions = [f"has_dlc = {pdx_string(name)}" for name in required]
    conditions.extend(
        f"NOT = {{ has_dlc = {pdx_string(name)} }}" for name in excluded
    )
    return _wrap_block(
        "if",
        _wrap_block("limit", "\n".join(conditions), 0) + "\n" + effect,
        0,
    )


def serialize_oob(oob: OrderOfBattle) -> str:
    text = oob.raw_text
    if not text:
        parts = [serialize_division_template(template) for template in oob.templates]
        if oob.divisions:
            unit_parts = [
                serialize_division_unit(division, indent=1)
                for division in oob.divisions
            ]
        else:
            unit_parts = []
        unit_parts.extend(serialize_fleet(fleet, indent=1) for fleet in oob.fleets)
        if unit_parts:
            units_body = "\n\n".join(unit_parts)
            parts.append(f"units = {{\n{units_body}\n}}")
        if oob.air_wings:
            parts.append(_serialize_air_wings(oob.air_wings))
        return "\n\n".join(parts) + ("\n" if parts else "")
    if not oob.touched_fields:
        return text
    if "templates" in oob.touched_fields:
        text = _patch_templates(text, oob.templates)
    if "divisions" in oob.touched_fields:
        text = _patch_divisions(text, oob.divisions)
    if "fleets" in oob.touched_fields:
        text = _patch_fleets(text, oob.fleets)
    if "air_wings" in oob.touched_fields:
        text = _patch_air_wings(text, oob.air_wings)
    unknown = oob.touched_fields - {
        "templates",
        "divisions",
        "fleets",
        "air_wings",
    }
    if unknown:
        raise ValueError(f"Unknown OOB fields: {sorted(unknown)}")
    return text


def serialize_division_template(
    template: DivisionTemplate,
    *,
    indent: int = 0,
) -> str:
    body = _patch_template_body(template.raw_block, template)
    if not template.raw_block:
        body = _patch_template_body("", template, all_fields=True)
    return _wrap_block("division_template", body, indent)


def serialize_division_unit(
    division: DivisionUnit,
    *,
    indent: int = 0,
) -> str:
    body = _patch_division_body(division.raw_block, division)
    if not division.raw_block:
        body = _patch_division_body("", division, all_fields=True)
    return _wrap_block("division", body, indent)


def serialize_ship_equipment(
    equipment: ShipEquipment,
    *,
    indent: int = 0,
) -> str:
    body = _patch_ship_equipment_body(
        equipment.raw_block,
        equipment,
        all_fields=not equipment.raw_block,
    )
    return _wrap_block(equipment.equipment_type, body, indent)


def serialize_ship(ship: Ship, *, indent: int = 0) -> str:
    body = _patch_ship_body(
        ship.raw_block,
        ship,
        all_fields=not ship.raw_block,
    )
    return _wrap_block("ship", body, indent)


def serialize_task_force(task_force: TaskForce, *, indent: int = 0) -> str:
    body = _patch_task_force_body(
        task_force.raw_block,
        task_force,
        all_fields=not task_force.raw_block,
    )
    return _wrap_block("task_force", body, indent)


def serialize_fleet(fleet: Fleet, *, indent: int = 0) -> str:
    body = _patch_fleet_body(
        fleet.raw_block,
        fleet,
        all_fields=not fleet.raw_block,
    )
    return _wrap_block("fleet", body, indent)


def validate_oob(oob: OrderOfBattle) -> list[ValidationError]:
    errors: list[ValidationError] = []
    inferred_kind = _infer_oob_kind(
        oob.templates,
        oob.divisions,
        oob.fleets,
        oob.air_wings,
    )
    if oob.kind == "mixed" or inferred_kind == "mixed":
        errors.append(
            _issue(
                oob,
                (
                    "mixes land, naval, or air content in one file. Split it "
                    "into separately assigned land, naval, and air OOBs"
                ),
                "mixed_oob_kinds",
                severity="warning",
            )
        )
    elif inferred_kind != oob.kind and not (
        inferred_kind == "land" and not any(
            (oob.templates, oob.divisions, oob.fleets, oob.air_wings)
        )
    ):
        errors.append(
            _issue(
                oob,
                f"is declared as {oob.kind} but contains {inferred_kind} content",
                "oob_kind_mismatch",
            )
        )
    template_names: set[str] = set()
    for template in oob.templates:
        if not template.name:
            errors.append(
                _issue(oob, "Division template has no name", "oob_template_name")
            )
        elif template.name in template_names:
            errors.append(
                _issue(
                    oob,
                    f"Duplicate division template name '{template.name}'",
                    "duplicate_oob_template",
                )
            )
        template_names.add(template.name)
        for label, battalions in (
            ("regiments", template.battalions),
            ("support", template.support),
        ):
            positions: set[tuple[int, int]] = set()
            for battalion in battalions:
                if not battalion.unit_type:
                    errors.append(
                        _issue(
                            oob,
                            f"Template '{template.name}' contains an empty {label} unit type",
                            "invalid_oob_battalion",
                        )
                    )
                position = (battalion.x, battalion.y)
                if position in positions:
                    errors.append(
                        _issue(
                            oob,
                            (
                                f"Template '{template.name}' repeats {label} grid "
                                f"position {position}"
                            ),
                            "duplicate_oob_grid_position",
                        )
                    )
                positions.add(position)
                if not (0 <= battalion.x <= 4 and 0 <= battalion.y <= 4):
                    errors.append(
                        _issue(
                            oob,
                            (
                                f"Template '{template.name}' uses out-of-range "
                                f"{label} position {position}"
                            ),
                            "invalid_oob_grid_position",
                        )
                    )
    for division in oob.divisions:
        if division.division_template not in template_names:
            errors.append(
                _issue(
                    oob,
                    (
                        f"Division '{division.name or '<ordered name>'}' references "
                        f"unknown template '{division.division_template}'"
                    ),
                    "unknown_oob_template",
                )
            )
        if division.location <= 0:
            errors.append(
                _issue(
                    oob,
                    f"Division '{division.name or '<ordered name>'}' has invalid location",
                    "invalid_oob_location",
                )
            )
        for field_name in (
            "start_experience_factor",
            "start_equipment_factor",
        ):
            value = getattr(division, field_name)
            if value is not None and not 0 <= value <= 1:
                errors.append(
                    _issue(
                        oob,
                        (
                            f"Division '{division.name or '<ordered name>'}' "
                            f"{field_name} must be between 0 and 1"
                        ),
                        "invalid_oob_factor",
                    )
                )
    fleet_names: set[str] = set()
    ship_names: set[str] = set()
    for fleet in oob.fleets:
        task_force_names: set[str] = set()
        if not fleet.name:
            errors.append(_issue(oob, "Fleet has no name", "oob_fleet_name"))
        elif fleet.name in fleet_names:
            errors.append(
                _issue(
                    oob,
                    f"Duplicate fleet name '{fleet.name}'",
                    "duplicate_oob_fleet",
                )
            )
        fleet_names.add(fleet.name)
        if fleet.naval_base <= 0:
            errors.append(
                _issue(
                    oob,
                    f"Fleet '{fleet.name or '<unnamed>'}' has invalid naval base",
                    "invalid_oob_naval_base",
                )
            )
        for task_force in fleet.task_forces:
            if not task_force.name:
                errors.append(
                    _issue(
                        oob,
                        f"Fleet '{fleet.name}' has an unnamed task force",
                        "oob_task_force_name",
                    )
                )
            elif task_force.name in task_force_names:
                errors.append(
                    _issue(
                        oob,
                        (
                            f"Fleet '{fleet.name}' repeats task force "
                            f"'{task_force.name}'"
                        ),
                        "duplicate_oob_task_force",
                    )
                )
            task_force_names.add(task_force.name)
            if task_force.location <= 0:
                errors.append(
                    _issue(
                        oob,
                        f"Task force '{task_force.name}' has invalid location",
                        "invalid_oob_naval_location",
                    )
                )
            for ship in task_force.ships:
                if not ship.name:
                    errors.append(_issue(oob, "Ship has no name", "oob_ship_name"))
                elif ship.name in ship_names:
                    errors.append(
                        _issue(
                            oob,
                            f"Duplicate ship name '{ship.name}'",
                            "duplicate_oob_ship",
                        )
                    )
                ship_names.add(ship.name)
                if not ship.definition:
                    errors.append(
                        _issue(
                            oob,
                            f"Ship '{ship.name}' has no definition",
                            "invalid_oob_ship_definition",
                        )
                    )
                if not ship.equipment:
                    errors.append(
                        _issue(
                            oob,
                            f"Ship '{ship.name}' has no equipment",
                            "missing_oob_ship_equipment",
                        )
                    )
                equipment_types: set[str] = set()
                for equipment in ship.equipment:
                    if not equipment.equipment_type or equipment.amount <= 0:
                        errors.append(
                            _issue(
                                oob,
                                f"Ship '{ship.name}' has invalid equipment",
                                "invalid_oob_ship_equipment",
                            )
                        )
                    if equipment.equipment_type in equipment_types:
                        errors.append(
                            _issue(
                                oob,
                                (
                                    f"Ship '{ship.name}' repeats equipment type "
                                    f"'{equipment.equipment_type}'"
                                ),
                                "duplicate_oob_ship_equipment",
                            )
                        )
                    equipment_types.add(equipment.equipment_type)
    air_keys: set[tuple[int, str, str]] = set()
    for wing in oob.air_wings:
        if wing.location <= 0:
            errors.append(
                _issue(oob, "Air wing has invalid location", "invalid_oob_air_location")
            )
        if not wing.equipment_type or wing.amount <= 0:
            errors.append(
                _issue(oob, "Air wing has invalid equipment", "invalid_oob_air_equipment")
            )
        identity = (wing.location, wing.equipment_type, wing.owner)
        if identity in air_keys:
            errors.append(
                _issue(
                    oob,
                    (
                        "Duplicate air wing equipment "
                        f"'{wing.equipment_type}' at province {wing.location}"
                    ),
                    "duplicate_oob_air_wing",
                )
            )
        air_keys.add(identity)
    return errors


def _parse_template(body: str, source_index: int) -> DivisionTemplate:
    return DivisionTemplate(
        name=_scalar_value(body, "name"),
        battalions=_parse_battalions(_block_value(body, "regiments")),
        support=_parse_battalions(_block_value(body, "support")),
        division_names_group=_scalar_value(body, "division_names_group"),
        priority=_optional_int(body, "priority"),
        raw_block=body,
        source_index=source_index,
    )


def _parse_division(body: str, source_index: int) -> DivisionUnit:
    name_order: int | None = None
    division_name = _block_value(body, "division_name")
    if division_name:
        name_order = _optional_int(division_name, "name_order")
    return DivisionUnit(
        name=_scalar_value(body, "name"),
        name_order=name_order,
        location=_optional_int(body, "location") or 0,
        division_template=_scalar_value(body, "division_template"),
        start_experience_factor=_optional_float(body, "start_experience_factor"),
        start_equipment_factor=_optional_float(body, "start_equipment_factor"),
        raw_block=body,
        source_index=source_index,
    )


def _parse_fleet(body: str, source_index: int) -> Fleet:
    task_forces: list[TaskForce] = []
    for span in top_level_assignments(body):
        if (
            span.key == "task_force"
            and span.is_block
            and span.body_start is not None
            and span.body_end is not None
        ):
            task_forces.append(
                _parse_task_force(
                    body[span.body_start : span.body_end],
                    len(task_forces),
                )
            )
    return Fleet(
        name=_scalar_value(body, "name"),
        naval_base=_optional_int(body, "naval_base") or 0,
        task_forces=task_forces,
        raw_block=body,
        source_index=source_index,
    )


def _parse_task_force(body: str, source_index: int) -> TaskForce:
    ships: list[Ship] = []
    for span in top_level_assignments(body):
        if (
            span.key == "ship"
            and span.is_block
            and span.body_start is not None
            and span.body_end is not None
        ):
            ships.append(
                _parse_ship(body[span.body_start : span.body_end], len(ships))
            )
    return TaskForce(
        name=_scalar_value(body, "name"),
        location=_optional_int(body, "location") or 0,
        ships=ships,
        raw_block=body,
        source_index=source_index,
    )


def _parse_ship(body: str, source_index: int) -> Ship:
    equipment: list[ShipEquipment] = []
    equipment_body = _block_value(body, "equipment")
    for span in top_level_assignments(equipment_body):
        if not span.is_block or span.body_start is None or span.body_end is None:
            continue
        child = equipment_body[span.body_start : span.body_end]
        equipment.append(
            ShipEquipment(
                equipment_type=span.key,
                amount=_optional_int(child, "amount") or 0,
                owner=_scalar_value(child, "owner"),
                creator=_scalar_value(child, "creator"),
                version_name=_scalar_value(child, "version_name"),
                raw_block=child,
                source_index=len(equipment),
            )
        )
    return Ship(
        name=_scalar_value(body, "name"),
        definition=_scalar_value(body, "definition"),
        equipment=equipment,
        pride_of_the_fleet=_optional_bool(body, "pride_of_the_fleet") or False,
        raw_block=body,
        source_index=source_index,
    )


def _parse_air_wings(text: str) -> list[AirWing]:
    outer = _block_span(text, "air_wings")
    if outer is None or outer.body_start is None or outer.body_end is None:
        return []
    body = text[outer.body_start : outer.body_end]
    result: list[AirWing] = []
    location_index = 0
    for location_span in top_level_assignments(body):
        if (
            not location_span.key.isdigit()
            or not location_span.is_block
            or location_span.body_start is None
            or location_span.body_end is None
        ):
            continue
        location = int(location_span.key)
        location_body = body[location_span.body_start : location_span.body_end]
        for equipment_span in top_level_assignments(location_body):
            if (
                not equipment_span.is_block
                or equipment_span.body_start is None
                or equipment_span.body_end is None
            ):
                continue
            equipment_body = location_body[
                equipment_span.body_start : equipment_span.body_end
            ]
            result.append(
                AirWing(
                    location=location,
                    equipment_type=equipment_span.key,
                    amount=_optional_int(equipment_body, "amount") or 0,
                    owner=_scalar_value(equipment_body, "owner"),
                    creator=_scalar_value(equipment_body, "creator"),
                    version_name=_scalar_value(equipment_body, "version_name"),
                    raw_block=equipment_body,
                    source_index=len(result),
                    source_location_index=location_index,
                )
            )
        location_index += 1
    return result


def _parse_battalions(body: str) -> list[Battalion]:
    result: list[Battalion] = []
    for span in top_level_assignments(body):
        if not span.is_block or span.body_start is None or span.body_end is None:
            continue
        child = body[span.body_start : span.body_end]
        x = _optional_int(child, "x")
        y = _optional_int(child, "y")
        if x is None or y is None:
            continue
        result.append(Battalion(span.key, x, y))
    return result


def _patch_template_body(
    body: str,
    template: DivisionTemplate,
    *,
    all_fields: bool = False,
) -> str:
    fields = (
        {"name", "battalions", "support", "division_names_group", "priority"}
        if all_fields
        else template.touched_fields
    )
    if "name" in fields:
        body = set_scalar(body, "name", pdx_string(template.name))
    if "division_names_group" in fields:
        body = set_scalar(
            body,
            "division_names_group",
            template.division_names_group or None,
        )
    if "battalions" in fields:
        body = set_block(
            body,
            "regiments",
            _serialize_battalions(template.battalions) or None,
        )
    if "support" in fields:
        body = set_block(
            body,
            "support",
            _serialize_battalions(template.support) or None,
        )
    if "priority" in fields:
        body = set_scalar(
            body,
            "priority",
            str(template.priority) if template.priority is not None else None,
        )
    unknown = fields - {
        "name",
        "battalions",
        "support",
        "division_names_group",
        "priority",
    }
    if unknown:
        raise ValueError(f"Unknown division template fields: {sorted(unknown)}")
    return body


def _patch_division_body(
    body: str,
    division: DivisionUnit,
    *,
    all_fields: bool = False,
) -> str:
    fields = (
        {
            "name",
            "name_order",
            "location",
            "division_template",
            "start_experience_factor",
            "start_equipment_factor",
        }
        if all_fields
        else division.touched_fields
    )
    if "name" in fields:
        body = set_scalar(body, "name", pdx_string(division.name) if division.name else None)
    if "name_order" in fields:
        if division.name_order is None:
            body = set_block(body, "division_name", None)
        else:
            current = _block_value(body, "division_name")
            current = set_scalar(current, "is_name_ordered", "yes")
            current = set_scalar(current, "name_order", str(division.name_order))
            body = set_block(body, "division_name", current)
    if "location" in fields:
        body = set_scalar(body, "location", str(division.location))
    if "division_template" in fields:
        body = set_scalar(
            body,
            "division_template",
            pdx_string(division.division_template),
        )
    for field_name in ("start_experience_factor", "start_equipment_factor"):
        if field_name in fields:
            value = getattr(division, field_name)
            body = set_scalar(
                body,
                field_name,
                pdx_value(value) if value is not None else None,
            )
    unknown = fields - {
        "name",
        "name_order",
        "location",
        "division_template",
        "start_experience_factor",
        "start_equipment_factor",
    }
    if unknown:
        raise ValueError(f"Unknown division unit fields: {sorted(unknown)}")
    return body


def _patch_ship_equipment_body(
    body: str,
    equipment: ShipEquipment,
    *,
    all_fields: bool = False,
) -> str:
    fields = (
        {"amount", "owner", "creator", "version_name"}
        if all_fields
        else equipment.touched_fields
    )
    if "amount" in fields:
        body = set_scalar(body, "amount", str(equipment.amount))
    for field_name in ("owner", "creator"):
        if field_name in fields:
            value = getattr(equipment, field_name)
            body = set_scalar(body, field_name, pdx_value(value) if value else None)
    if "version_name" in fields:
        body = set_scalar(
            body,
            "version_name",
            pdx_string(equipment.version_name) if equipment.version_name else None,
        )
    unknown = fields - {"amount", "owner", "creator", "version_name"}
    if unknown:
        raise ValueError(f"Unknown ship equipment fields: {sorted(unknown)}")
    return body


def _patch_ship_body(
    body: str,
    ship: Ship,
    *,
    all_fields: bool = False,
) -> str:
    fields = (
        {"name", "definition", "equipment", "pride_of_the_fleet"}
        if all_fields
        else ship.touched_fields
    )
    if "name" in fields:
        body = set_scalar(body, "name", pdx_string(ship.name))
    if "definition" in fields:
        body = set_scalar(body, "definition", pdx_value(ship.definition))
    if "pride_of_the_fleet" in fields:
        body = set_scalar(
            body,
            "pride_of_the_fleet",
            "yes" if ship.pride_of_the_fleet else None,
        )
    if "equipment" in fields:
        current = _block_value(body, "equipment")
        current = _patch_repeated_blocks(
            current,
            ship.equipment,
            key=lambda item: item.equipment_type,
            render=lambda item: serialize_ship_equipment(item),
            patch=lambda child, item: _patch_ship_equipment_body(child, item),
            existing_keys={
                span.key
                for span in top_level_assignments(current)
                if span.is_block
            },
        )
        body = set_block(body, "equipment", current or None)
    unknown = fields - {"name", "definition", "equipment", "pride_of_the_fleet"}
    if unknown:
        raise ValueError(f"Unknown ship fields: {sorted(unknown)}")
    return body


def _patch_task_force_body(
    body: str,
    task_force: TaskForce,
    *,
    all_fields: bool = False,
) -> str:
    fields = (
        {"name", "location", "ships"} if all_fields else task_force.touched_fields
    )
    if "name" in fields:
        body = set_scalar(body, "name", pdx_string(task_force.name))
    if "location" in fields:
        body = set_scalar(body, "location", str(task_force.location))
    if "ships" in fields:
        body = _patch_repeated_blocks(
            body,
            task_force.ships,
            key=lambda _item: "ship",
            render=lambda item: serialize_ship(item),
            patch=lambda child, item: _patch_ship_body(child, item),
            existing_keys={"ship"},
        )
    unknown = fields - {"name", "location", "ships"}
    if unknown:
        raise ValueError(f"Unknown task force fields: {sorted(unknown)}")
    return body


def _patch_fleet_body(
    body: str,
    fleet: Fleet,
    *,
    all_fields: bool = False,
) -> str:
    fields = (
        {"name", "naval_base", "task_forces"}
        if all_fields
        else fleet.touched_fields
    )
    if "name" in fields:
        body = set_scalar(body, "name", pdx_string(fleet.name))
    if "naval_base" in fields:
        body = set_scalar(body, "naval_base", str(fleet.naval_base))
    if "task_forces" in fields:
        body = _patch_repeated_blocks(
            body,
            fleet.task_forces,
            key=lambda _item: "task_force",
            render=lambda item: serialize_task_force(item),
            patch=lambda child, item: _patch_task_force_body(child, item),
            existing_keys={"task_force"},
        )
    unknown = fields - {"name", "naval_base", "task_forces"}
    if unknown:
        raise ValueError(f"Unknown fleet fields: {sorted(unknown)}")
    return body


def _patch_air_wing_body(
    body: str,
    wing: AirWing,
    *,
    all_fields: bool = False,
) -> str:
    fields = (
        {"amount", "owner", "creator", "version_name"}
        if all_fields
        else wing.touched_fields
    )
    if "amount" in fields:
        body = set_scalar(body, "amount", str(wing.amount))
    for field_name in ("owner", "creator"):
        if field_name in fields:
            value = getattr(wing, field_name)
            body = set_scalar(body, field_name, pdx_value(value) if value else None)
    if "version_name" in fields:
        body = set_scalar(
            body,
            "version_name",
            pdx_string(wing.version_name) if wing.version_name else None,
        )
    unknown = fields - {"amount", "owner", "creator", "version_name"}
    if unknown:
        raise ValueError(f"Unknown air wing fields: {sorted(unknown)}")
    return body


def _patch_templates(
    text: str,
    templates: list[DivisionTemplate],
) -> str:
    source = {
        template.source_index: template
        for template in templates
        if template.source_index >= 0
    }
    spans = [
        span
        for span in top_level_assignments(text)
        if span.key == "division_template"
        and span.is_block
        and span.body_start is not None
        and span.body_end is not None
    ]
    for source_index, span in reversed(list(enumerate(spans))):
        template = source.get(source_index)
        if template is None:
            text = replace_assignment(text, span, None)
            continue
        assert span.body_start is not None
        assert span.body_end is not None
        body = text[span.body_start : span.body_end]
        text = replace_assignment_body(
            text,
            span,
            _patch_template_body(body, template),
        )
    for template in templates:
        if template.source_index < 0:
            text = append_assignment(text, serialize_division_template(template))
    return text


def _patch_divisions(
    text: str,
    divisions: list[DivisionUnit],
) -> str:
    units = _block_span(text, "units")
    if units is None or units.body_start is None or units.body_end is None:
        units_body = "\n\n".join(
            serialize_division_unit(division, indent=0)
            for division in divisions
        )
        return set_block(text, "units", units_body or None)
    units_body = text[units.body_start : units.body_end]
    source = {
        division.source_index: division
        for division in divisions
        if division.source_index >= 0
    }
    spans = [
        span
        for span in top_level_assignments(units_body)
        if span.key == "division"
        and span.is_block
        and span.body_start is not None
        and span.body_end is not None
    ]
    for source_index, span in reversed(list(enumerate(spans))):
        division = source.get(source_index)
        if division is None:
            units_body = replace_assignment(units_body, span, None)
            continue
        assert span.body_start is not None
        assert span.body_end is not None
        body = units_body[span.body_start : span.body_end]
        units_body = replace_assignment_body(
            units_body,
            span,
            _patch_division_body(body, division),
        )
    for division in divisions:
        if division.source_index < 0:
            units_body = append_assignment(
                units_body,
                serialize_division_unit(division),
            )
    return replace_assignment_body(text, units, units_body)


def _patch_fleets(text: str, fleets: list[Fleet]) -> str:
    units = _block_span(text, "units")
    if units is None or units.body_start is None or units.body_end is None:
        units_body = "\n\n".join(serialize_fleet(fleet) for fleet in fleets)
        return set_block(text, "units", units_body or None)
    units_body = text[units.body_start : units.body_end]
    units_body = _patch_repeated_blocks(
        units_body,
        fleets,
        key=lambda _item: "fleet",
        render=lambda item: serialize_fleet(item),
        patch=lambda child, item: _patch_fleet_body(child, item),
        existing_keys={"fleet"},
    )
    return replace_assignment_body(text, units, units_body)


def _serialize_air_wings(air_wings: list[AirWing]) -> str:
    grouped: dict[int, list[AirWing]] = {}
    for wing in air_wings:
        grouped.setdefault(wing.location, []).append(wing)
    locations: list[str] = []
    for location, wings in grouped.items():
        children = "\n\n".join(
            _wrap_block(
                wing.equipment_type,
                _patch_air_wing_body(
                    wing.raw_block,
                    wing,
                    all_fields=not wing.raw_block,
                ),
                1,
            )
            for wing in wings
        )
        locations.append(_wrap_block(str(location), children, 0))
    return _wrap_block("air_wings", "\n\n".join(locations), 0)


def _patch_air_wings(text: str, air_wings: list[AirWing]) -> str:
    span = _block_span(text, "air_wings")
    rendered = _serialize_air_wings(air_wings)
    rendered_span = _block_span(rendered, "air_wings")
    rendered_body = (
        rendered[rendered_span.body_start : rendered_span.body_end]
        if rendered_span is not None
        and rendered_span.body_start is not None
        and rendered_span.body_end is not None
        else ""
    )
    if span is None:
        return append_assignment(text, rendered) if air_wings else text
    return replace_assignment_body(text, span, rendered_body) if air_wings else replace_assignment(text, span, None)


def _patch_repeated_blocks(
    body: str,
    items: list[_T],
    *,
    key: Callable[[_T], str],
    render: Callable[[_T], str],
    patch: Callable[[str, _T], str],
    existing_keys: set[str] | None = None,
) -> str:
    item_keys = {key(item) for item in items}
    source = {
        getattr(item, "source_index"): item
        for item in items
        if getattr(item, "source_index") >= 0
    }
    spans = [
        span
        for span in top_level_assignments(body)
        if span.is_block
        and span.body_start is not None
        and span.body_end is not None
        and span.key in (existing_keys if existing_keys is not None else item_keys)
    ]
    for source_index, span in reversed(list(enumerate(spans))):
        item = source.get(source_index)
        if item is None:
            body = replace_assignment(body, span, None)
            continue
        if span.key != key(item):
            body = replace_assignment(body, span, render(item))
            continue
        assert span.body_start is not None
        assert span.body_end is not None
        child = body[span.body_start : span.body_end]
        body = replace_assignment_body(body, span, patch(child, item))
    for item in items:
        if getattr(item, "source_index") < 0:
            body = append_assignment(body, render(item))
    return body


def _serialize_battalions(battalions: list[Battalion]) -> str:
    return "\n".join(
        f"{battalion.unit_type} = {{ x = {battalion.x} y = {battalion.y} }}"
        for battalion in battalions
    )


def _block_span(body: str, key: str) -> AssignmentSpan | None:
    return next(
        (
            span
            for span in top_level_assignments(body)
            if span.key == key
            and span.is_block
            and span.body_start is not None
            and span.body_end is not None
        ),
        None,
    )


def _block_value(body: str, key: str) -> str:
    span = _block_span(body, key)
    if span is None or span.body_start is None or span.body_end is None:
        return ""
    return body[span.body_start : span.body_end]


def _scalar_value(body: str, key: str) -> str:
    for span in top_level_assignments(body):
        if span.key == key and not span.is_block:
            return _unquote(body[span.value_start : span.value_end])
    return ""


def _optional_int(body: str, key: str) -> int | None:
    value = _scalar_value(body, key)
    try:
        return int(value)
    except ValueError:
        return None


def _optional_float(body: str, key: str) -> float | None:
    value = _scalar_value(body, key)
    try:
        return float(value)
    except ValueError:
        return None


def _optional_bool(body: str, key: str) -> bool | None:
    value = _scalar_value(body, key).lower()
    if value in {"yes", "true", "1"}:
        return True
    if value in {"no", "false", "0"}:
        return False
    return None


def _unquote(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return text


def _scalar_mapping(body: str) -> dict[str, str]:
    return {
        span.key: _unquote(body[span.value_start : span.value_end])
        for span in top_level_assignments(body)
        if not span.is_block
    }


def _dlc_conditions(body: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    required: list[str] = []
    excluded: list[str] = []

    def walk(fragment: str, negated: bool = False) -> None:
        for span in top_level_assignments(fragment):
            if not span.is_block:
                if span.key.lower() == "has_dlc":
                    value = _unquote(fragment[span.value_start : span.value_end])
                    (excluded if negated else required).append(value)
                continue
            if span.body_start is None or span.body_end is None:
                continue
            walk(
                fragment[span.body_start : span.body_end],
                negated ^ (span.key.upper() == "NOT"),
            )

    walk(body)
    return _merge_unique((), required), _merge_unique((), excluded)


def _conditioned_assignments(
    body: str,
    required_dlc: tuple[str, ...] = (),
    excluded_dlc: tuple[str, ...] = (),
    date: str = "",
) -> list[
    tuple[str, AssignmentSpan, tuple[str, ...], tuple[str, ...], str]
]:
    """Flatten assignments while retaining simple ``IF``/``ELSE`` DLC gates."""

    result: list[
        tuple[str, AssignmentSpan, tuple[str, ...], tuple[str, ...], str]
    ] = []
    for span in top_level_assignments(body):
        if not span.is_block or span.body_start is None or span.body_end is None:
            result.append((body, span, required_dlc, excluded_dlc, date))
            continue
        child = body[span.body_start : span.body_end]
        if span.key.lower() != "if":
            result.append((body, span, required_dlc, excluded_dlc, date))
            result.extend(
                _conditioned_assignments(
                    child,
                    required_dlc,
                    excluded_dlc,
                    span.key if _DATE_KEY_RE.fullmatch(span.key) else date,
                )
            )
            continue

        found_required, found_excluded = _dlc_conditions(
            _block_value(child, "limit")
        )
        positive_required = _merge_unique(required_dlc, found_required)
        positive_excluded = _merge_unique(excluded_dlc, found_excluded)
        negative_required = _merge_unique(required_dlc, found_excluded)
        negative_excluded = _merge_unique(excluded_dlc, found_required)

        positive = child
        else_bodies: list[str] = []
        ignored = [
            item
            for item in top_level_assignments(child)
            if item.key.lower() in {"limit", "else"}
        ]
        for item in ignored:
            if (
                item.key.lower() == "else"
                and item.is_block
                and item.body_start is not None
                and item.body_end is not None
            ):
                else_bodies.append(child[item.body_start : item.body_end])
        for item in reversed(ignored):
            positive = replace_assignment(positive, item, None)
        result.extend(
            _conditioned_assignments(
                positive,
                positive_required,
                positive_excluded,
                date,
            )
        )
        for else_body in else_bodies:
            result.extend(
                _conditioned_assignments(
                    else_body,
                    negative_required,
                    negative_excluded,
                    date,
                )
            )
    return result


_DATE_KEY_RE = re.compile(r"\d{1,4}\.\d{1,2}\.\d{1,2}(?:\.\d{1,2})?")


def _merge_unique(
    first: tuple[str, ...],
    second: tuple[str, ...] | list[str],
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            (*first, *(value.strip() for value in second if value.strip()))
        )
    )


def _infer_oob_kind(
    templates: list[DivisionTemplate],
    divisions: list[DivisionUnit],
    fleets: list[Fleet],
    air_wings: list[AirWing],
) -> OOBKind:
    populated: set[AssignedOOBKind] = set()
    if templates or divisions:
        populated.add("land")
    if fleets:
        populated.add("naval")
    if air_wings:
        populated.add("air")
    if len(populated) > 1:
        return "mixed"
    if populated:
        return next(iter(populated))
    return "land"


def _wrap_block(key: str, body: str, indent: int) -> str:
    prefix = "\t" * indent
    inner_prefix = "\t" * (indent + 1)
    inner = "\n".join(
        inner_prefix + line.rstrip() for line in body.strip().splitlines()
    )
    if inner:
        return f"{prefix}{key} = {{\n{inner}\n{prefix}}}"
    return f"{prefix}{key} = {{\n{prefix}}}"


def _infer_country_tag(name: str) -> str:
    prefix = name.split("_", 1)[0]
    return (
        prefix
        if len(prefix) == 3
        and prefix.isalnum()
        and prefix.upper() == prefix
        else ""
    )


def _issue(
    oob: OrderOfBattle,
    message: str,
    code: str,
    *,
    severity: str = "error",
) -> ValidationError:
    return ValidationError(
        message=f"OOB '{oob.name}': {message}",
        severity=severity,
        code=code,
        country_tag=oob.country_tag or None,
        file_path=str(oob.path) if oob.path is not None else None,
    )
