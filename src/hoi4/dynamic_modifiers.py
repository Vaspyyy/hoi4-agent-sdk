"""Source-preserving support for HOI4 ``common/dynamic_modifiers`` files."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ._mapping_patch import patch_scalar_entries
from .patching import (
    append_assignment,
    replace_assignment,
    replace_assignment_body,
    set_block,
    set_scalar,
    top_level_assignments,
)
from .script import pdx_value
from .syntax import Block, Scalar, parse
from .textio import read_text

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


def load_dynamic_modifiers_text(text: str, path: Path | None = None) -> list[DynamicModifier]:
    """Read every ``id = { ... }`` definition at the top level of a file's text."""

    modifiers: list[DynamicModifier] = []
    for item in parse(text).root.assignments():
        if item.op != "=" or type(item.value) is not Block:
            continue
        modifiers.append(_parse_dynamic_modifier(item.key.value, item.value, path))
    return modifiers


def load_dynamic_modifiers_file(path: Path) -> list[DynamicModifier]:
    return load_dynamic_modifiers_text(read_text(path), path)


def _parse_dynamic_modifier(
    modifier_id: str,
    block: Block,
    path: Path | None,
) -> DynamicModifier:
    icon = ""
    enable = ""
    remove_trigger = ""
    attacker_modifier: bool | None = None
    modifier: dict[str, str | int | float | bool] = {}
    for item in block.assignments():
        if item.op != "=":
            continue
        value = item.value
        if type(value) is Block:
            if item.key.value == "enable":
                enable = value.body.strip()
            elif item.key.value == "remove_trigger":
                remove_trigger = value.body.strip()
            continue
        if type(value) is not Scalar:
            continue
        key = item.key.value
        if key == "icon":
            icon = value.value.strip()
        elif key == "attacker_modifier":
            attacker_modifier = value.value.strip().lower() in {"yes", "true", "1"}
        else:
            modifier[key] = _coerce_scalar(value.value)
    return DynamicModifier(
        id=modifier_id,
        icon=icon,
        enable=enable,
        remove_trigger=remove_trigger,
        attacker_modifier=attacker_modifier,
        modifier=modifier,
        path=path,
        raw_block=block.body,
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
    return patch_scalar_entries(body, values, skip=_RESERVED_SCALARS)


def _coerce_scalar(value: str) -> str | int | float | bool:
    text = value.strip()
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


def _wrap_block(key: str, body: str) -> str:
    clean = body.strip("\n")
    if not clean.strip():
        return f"{key} = {{ }}"
    inner = "\n".join(f"\t{line}" for line in clean.splitlines())
    return f"{key} = {{\n{inner}\n}}"
