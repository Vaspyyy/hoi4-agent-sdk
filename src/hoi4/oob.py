"""Land order-of-battle models with source-preserving HOI4 serialization."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

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
class OrderOfBattle:
    name: str
    country_tag: str = ""
    templates: list[DivisionTemplate] = field(default_factory=list)
    divisions: list[DivisionUnit] = field(default_factory=list)
    path: Path | None = None
    raw_text: str = ""
    touched_fields: set[str] = field(default_factory=set, repr=False)


def load_oob_file(
    path: Path,
    *,
    name: str | None = None,
    country_tag: str = "",
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
    units = _block_span(text, "units")
    if units is not None and units.body_start is not None and units.body_end is not None:
        units_body = text[units.body_start : units.body_end]
        division_index = 0
        for span in top_level_assignments(units_body):
            if (
                span.key != "division"
                or not span.is_block
                or span.body_start is None
                or span.body_end is None
            ):
                continue
            body = units_body[span.body_start : span.body_end]
            divisions.append(_parse_division(body, division_index))
            division_index += 1
    inferred_tag = country_tag or _infer_country_tag(path.stem)
    return OrderOfBattle(
        name=name or path.stem,
        country_tag=inferred_tag,
        templates=templates,
        divisions=divisions,
        path=path,
        raw_text=text,
    )


def serialize_oob(oob: OrderOfBattle) -> str:
    text = oob.raw_text
    if not text:
        parts = [serialize_division_template(template) for template in oob.templates]
        if oob.divisions:
            units_body = "\n\n".join(
                serialize_division_unit(division, indent=1)
                for division in oob.divisions
            )
            parts.append(f"units = {{\n{units_body}\n}}")
        return "\n\n".join(parts) + ("\n" if parts else "")
    if not oob.touched_fields:
        return text
    if "templates" in oob.touched_fields:
        text = _patch_templates(text, oob.templates)
    if "divisions" in oob.touched_fields:
        text = _patch_divisions(text, oob.divisions)
    unknown = oob.touched_fields - {"templates", "divisions"}
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


def validate_oob(oob: OrderOfBattle) -> list[ValidationError]:
    errors: list[ValidationError] = []
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


def _unquote(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return text


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


def _issue(oob: OrderOfBattle, message: str, code: str) -> ValidationError:
    return ValidationError(
        message=f"OOB '{oob.name}': {message}",
        severity="error",
        code=code,
        country_tag=oob.country_tag or None,
        file_path=str(oob.path) if oob.path is not None else None,
    )
