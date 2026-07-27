"""Source-preserving support for HOI4 ``common/dynamic_modifiers`` files."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .patching import (
    append_assignment,
    replace_assignment,
    replace_assignment_body,
    scalar_values_equivalent,
    set_block,
    set_scalar,
    top_level_assignments,
)
from .script import pdx_value


_RESERVED_SCALARS = {"icon", "attacker_modifier"}


@dataclass
class DynamicModifier:
    id: str
    icon: str = ""
    enable: str = ""
    remove_trigger: str = ""
    attacker_modifier: bool | None = None
    modifier: dict[str, str | int | float | bool] = field(default_factory=dict)
    path: Path | None = None
    raw_block: str = ""
    touched_fields: set[str] = field(default_factory=set)


def load_dynamic_modifiers_file(path: Path) -> list[DynamicModifier]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    modifiers: list[DynamicModifier] = []
    for span in top_level_assignments(text):
        if not span.is_block or span.body_start is None or span.body_end is None:
            continue
        body = text[span.body_start : span.body_end]
        modifiers.append(_parse_dynamic_modifier(span.key, body, path))
    return modifiers


def _parse_dynamic_modifier(
    modifier_id: str,
    body: str,
    path: Path,
) -> DynamicModifier:
    icon = ""
    enable = ""
    remove_trigger = ""
    attacker_modifier: bool | None = None
    modifier: dict[str, str | int | float | bool] = {}
    for span in top_level_assignments(body):
        if span.is_block and span.body_start is not None and span.body_end is not None:
            block = body[span.body_start : span.body_end]
            if span.key == "enable":
                enable = block.strip()
            elif span.key == "remove_trigger":
                remove_trigger = block.strip()
            continue
        value = body[span.value_start : span.value_end].strip()
        if span.key == "icon":
            icon = _unquote(value)
        elif span.key == "attacker_modifier":
            attacker_modifier = value.lower() in {"yes", "true", "1"}
        else:
            modifier[span.key] = _coerce_scalar(value)
    return DynamicModifier(
        id=modifier_id,
        icon=icon,
        enable=enable,
        remove_trigger=remove_trigger,
        attacker_modifier=attacker_modifier,
        modifier=modifier,
        path=path,
        raw_block=body,
    )


def serialize_dynamic_modifier(modifier: DynamicModifier) -> str:
    lines: list[str] = []
    if modifier.icon:
        lines.append(f"icon = {pdx_value(modifier.icon)}")
    if modifier.enable:
        lines.append(_wrap_block("enable", modifier.enable))
    if modifier.remove_trigger:
        lines.append(_wrap_block("remove_trigger", modifier.remove_trigger))
    if modifier.attacker_modifier is not None:
        lines.append(
            f"attacker_modifier = {pdx_value(modifier.attacker_modifier)}"
        )
    lines.extend(
        f"{key} = {pdx_value(value)}" for key, value in modifier.modifier.items()
    )
    return _wrap_block(modifier.id, "\n".join(lines))


def serialize_dynamic_modifiers_file(
    modifiers: list[DynamicModifier],
    original: str = "",
) -> str:
    if not original:
        rendered = "\n\n".join(
            serialize_dynamic_modifier(modifier) for modifier in modifiers
        )
        return rendered + ("\n" if rendered else "")

    remaining = list(modifiers)
    text = original
    for span in sorted(
        top_level_assignments(original),
        key=lambda item: item.start,
        reverse=True,
    ):
        if not span.is_block or span.body_start is None or span.body_end is None:
            continue
        match_index = next(
            (index for index, item in enumerate(remaining) if item.id == span.key),
            None,
        )
        if match_index is None:
            text = replace_assignment(text, span, None)
            continue
        modifier = remaining.pop(match_index)
        if modifier.raw_block and not modifier.touched_fields:
            continue
        body = original[span.body_start : span.body_end]
        patched = _patch_dynamic_modifier_body(body, modifier)
        text = replace_assignment_body(text, span, patched)

    for modifier in remaining:
        text = append_assignment(text, serialize_dynamic_modifier(modifier))
    return text


def _patch_dynamic_modifier_body(
    body: str,
    modifier: DynamicModifier,
) -> str:
    patched = body
    touched = modifier.touched_fields
    if "icon" in touched:
        patched = set_scalar(
            patched,
            "icon",
            pdx_value(modifier.icon) if modifier.icon else None,
        )
    if "enable" in touched:
        patched = set_block(patched, "enable", modifier.enable or None)
    if "remove_trigger" in touched:
        patched = set_block(
            patched,
            "remove_trigger",
            modifier.remove_trigger or None,
        )
    if "attacker_modifier" in touched:
        patched = set_scalar(
            patched,
            "attacker_modifier",
            (
                pdx_value(modifier.attacker_modifier)
                if modifier.attacker_modifier is not None
                else None
            ),
        )
    if "modifier" in touched:
        patched = _patch_modifier_scalars(patched, modifier.modifier)
    unknown = touched - {
        "icon",
        "enable",
        "remove_trigger",
        "attacker_modifier",
        "modifier",
    }
    if unknown:
        raise ValueError(f"Unknown dynamic modifier fields: {sorted(unknown)}")
    return patched


def _patch_modifier_scalars(
    body: str,
    values: dict[str, str | int | float | bool],
) -> str:
    spans = [
        span
        for span in top_level_assignments(body)
        if not span.is_block and span.key not in _RESERVED_SCALARS
    ]
    first_by_key = {span.key: span for span in spans[::-1]}
    remaining = dict(values)
    result = body
    for span in reversed(spans):
        if span.key not in values:
            result = replace_assignment(result, span, None)
            continue
        if span != first_by_key[span.key]:
            result = replace_assignment(result, span, None)
            continue
        rendered = pdx_value(values[span.key])
        current = result[span.value_start : span.value_end]
        if not scalar_values_equivalent(current, rendered):
            result = (
                result[: span.value_start] + rendered + result[span.value_end :]
            )
        remaining.pop(span.key, None)
    for key, value in remaining.items():
        result = append_assignment(result, f"{key} = {pdx_value(value)}")
    return result


def _coerce_scalar(value: str) -> str | int | float | bool:
    text = _unquote(value)
    if text == "yes":
        return True
    if text == "no":
        return False
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return text


def _unquote(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return text


def _wrap_block(key: str, body: str) -> str:
    clean = body.strip("\n")
    if not clean.strip():
        return f"{key} = {{ }}"
    inner = "\n".join(f"\t{line}" for line in clean.splitlines())
    return f"{key} = {{\n{inner}\n}}"
