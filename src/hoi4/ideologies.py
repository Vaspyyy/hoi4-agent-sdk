"""Ideology definitions with source-preserving reads and writes.

HOI4 ideology files live in ``common/ideologies`` and wrap definitions in an
``ideologies = { ... }`` block.  This module deliberately keeps the original
body of every definition so editing one property does not rewrite unrelated
rules, comments, ordering, or syntax added by a newer game version.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .parser import PdxNode, find_assignment_block, parse_pdx
from .patching import (
    append_assignment,
    assignment_spans,
    replace_assignment,
    replace_assignment_body,
    set_block,
    set_scalar,
)
from .patching import top_level_assignments
from .script import pdx_value
from .structured_patching import patch_scalar_mapping


VANILLA_AI_BEHAVIORS = ("democratic", "communist", "fascist", "neutral")


@dataclass
class SubIdeology:
    """A subtype inside an ideology's ``types`` block."""

    name: str
    can_be_randomly_selected: bool = True
    raw_block: str = field(default="", compare=False)


@dataclass
class Ideology:
    """Structured ideology fields plus its untouched Paradox source body."""

    id: str
    color: tuple[int, ...] = (128, 128, 128)
    types: list[SubIdeology] = field(default_factory=list)
    rules: dict[str, str] = field(default_factory=dict)
    modifiers: dict[str, str] = field(default_factory=dict)
    hidden_modifiers: dict[str, str] = field(default_factory=dict)
    faction_modifiers: dict[str, str] = field(default_factory=dict)
    dynamic_faction_names: list[str] = field(default_factory=list)
    ai_behavior: str = ""
    ai_ideology_wanted_units_factor: str | int | float | None = "1.0"
    ai_give_core_state_control_threshold: str | int | float | None = "0"
    war_impact_on_world_tension: str | int | float | None = "0.25"
    faction_impact_on_world_tension: str | int | float | None = "0.1"
    can_host_government_in_exile: bool = False
    can_collaborate: bool = False
    effects: list[str] = field(default_factory=list)
    is_vanilla: bool = False
    path: Path | None = None
    raw_block: str = ""
    touched_fields: set[str] = field(default_factory=set)

    @property
    def touched(self) -> bool:
        return bool(self.touched_fields)


def load_ideologies_file(path: Path, *, is_vanilla: bool = False) -> list[Ideology]:
    """Load every ideology from one file, retaining duplicate definitions."""

    text = path.read_text(encoding="utf-8", errors="ignore")
    match = find_assignment_block(text, "ideologies")
    if match is None:
        return []
    body = match[0]
    ideologies: list[Ideology] = []
    for span in top_level_assignments(body):
        if not span.is_block or span.body_start is None or span.body_end is None:
            continue
        ideology_body = body[span.body_start : span.body_end]
        ideology = _parse_ideology(span.key, ideology_body)
        ideology.is_vanilla = is_vanilla
        ideology.path = path
        ideologies.append(ideology)
    return ideologies


def load_ideologies(root: Path, *, is_vanilla: bool = False) -> list[Ideology]:
    """Load all ideology files beneath a game or mod root."""

    directory = root / "common" / "ideologies"
    if not directory.is_dir():
        return []
    result: list[Ideology] = []
    for path in sorted(directory.glob("*.txt")):
        result.extend(load_ideologies_file(path, is_vanilla=is_vanilla))
    return result


def _parse_ideology(ideology_id: str, body: str) -> Ideology:
    root = parse_pdx(body)
    color = _parse_color(root.get_block("color"))
    ai_behavior = next(
        (
            behavior
            for behavior in VANILLA_AI_BEHAVIORS
            if root.get_value(f"ai_{behavior}").lower() == "yes"
        ),
        "",
    )
    return Ideology(
        id=ideology_id,
        color=color,
        types=_parse_types(body),
        rules=_parse_mapping(root.get_block("rules")),
        modifiers=_parse_mapping(root.get_block("modifiers")),
        hidden_modifiers=_parse_hidden_modifiers(root.get_block("modifiers")),
        faction_modifiers=_parse_mapping(root.get_block("faction_modifiers")),
        dynamic_faction_names=_parse_bare_values(root.get_block("dynamic_faction_names")),
        ai_behavior=ai_behavior,
        ai_ideology_wanted_units_factor=root.get_value(
            "ai_ideology_wanted_units_factor", "1.0"
        ),
        ai_give_core_state_control_threshold=root.get_value(
            "ai_give_core_state_control_threshold", "0"
        ),
        war_impact_on_world_tension=root.get_value("war_impact_on_world_tension", "0.25"),
        faction_impact_on_world_tension=root.get_value(
            "faction_impact_on_world_tension", "0.1"
        ),
        can_host_government_in_exile=_as_bool(
            root.get_value("can_host_government_in_exile", "no")
        ),
        can_collaborate=_as_bool(root.get_value("can_collaborate", "no")),
        effects=_parse_bare_values(root.get_block("effects")),
        raw_block=body,
    )


def _parse_color(node: PdxNode | None) -> tuple[int, ...]:
    if node is None:
        return (128, 128, 128)
    values = _parse_bare_values(node)
    try:
        return tuple(int(item) for item in values)
    except (TypeError, ValueError):
        # Retain invalidity in the model so validation can report it instead of
        # silently replacing malformed source with a plausible default.
        return ()


def _parse_mapping(node: PdxNode | None) -> dict[str, str]:
    if node is None:
        return {}
    return {
        child.key: child.value
        for child in node.children
        if child.key is not None and child.value is not None and not child.is_comment
    }


def _parse_hidden_modifiers(node: PdxNode | None) -> dict[str, str]:
    if node is None:
        return {}
    hidden = node.get_block("hidden_modifier")
    return _parse_mapping(hidden)


def _parse_bare_values(node: PdxNode | None) -> list[str]:
    if node is None:
        return []
    return [
        child.value
        for child in node.children
        if child.key is None and child.value is not None and not child.is_comment
    ]


def _parse_types(source: str) -> list[SubIdeology]:
    spans = assignment_spans(source, "types")
    if not spans:
        return []
    types_span = spans[0]
    if (
        not types_span.is_block
        or types_span.body_start is None
        or types_span.body_end is None
    ):
        return []
    types_body = source[types_span.body_start : types_span.body_end]
    result: list[SubIdeology] = []
    for child in top_level_assignments(types_body):
        if not child.is_block or child.body_start is None or child.body_end is None:
            continue
        raw_block = types_body[child.body_start : child.body_end]
        parsed = parse_pdx(raw_block)
        result.append(
            SubIdeology(
                name=child.key,
                can_be_randomly_selected=(
                    parsed.get_value("can_be_randomly_selected", "yes").lower() != "no"
                ),
                raw_block=raw_block,
            )
        )
    return result


def serialize_ideology(ideology: Ideology, indent: int = 0) -> str:
    """Render one ideology, patching only explicitly touched fields when possible."""

    if ideology.raw_block:
        body = ideology.raw_block
        for field_name in sorted(ideology.touched_fields):
            body = _patch_field(body, ideology, field_name)
        return _wrap_block(ideology.id, body, indent)

    body = ""
    for field_name in (
        "types",
        "dynamic_faction_names",
        "color",
        "war_impact_on_world_tension",
        "faction_impact_on_world_tension",
        "rules",
        "can_host_government_in_exile",
        "can_collaborate",
        "modifiers",
        "faction_modifiers",
        "ai_behavior",
        "ai_ideology_wanted_units_factor",
        "ai_give_core_state_control_threshold",
        "effects",
    ):
        body = _patch_field(body, ideology, field_name)
    return _wrap_block(ideology.id, body.rstrip(), indent)


def _patch_field(body: str, ideology: Ideology, field_name: str) -> str:
    if field_name == "color":
        return set_block(body, "color", " ".join(str(value) for value in ideology.color))
    if field_name == "types":
        return _patch_types_assignment(body, ideology.types)
    if field_name in {"rules", "faction_modifiers"}:
        return _patch_mapping_assignment(body, field_name, getattr(ideology, field_name))
    if field_name in {"modifiers", "hidden_modifiers"}:
        spans = assignment_spans(body, "modifiers")
        if not spans or not spans[0].is_block:
            modifier_body = _mapping_body(ideology.modifiers)
            if ideology.hidden_modifiers:
                hidden = _wrap_block(
                    "hidden_modifier", _mapping_body(ideology.hidden_modifiers), 0
                )
                modifier_body = "\n".join(part for part in (modifier_body, hidden) if part)
            return set_block(body, "modifiers", modifier_body or None)
        span = spans[0]
        if span.body_start is None or span.body_end is None:
            return body
        modifier_body = body[span.body_start : span.body_end]
        modifier_body = patch_scalar_mapping(modifier_body, ideology.modifiers)
        modifier_body = _patch_mapping_assignment(
            modifier_body, "hidden_modifier", ideology.hidden_modifiers
        )
        return replace_assignment_body(body, span, modifier_body)
    if field_name in {"dynamic_faction_names", "effects"}:
        values = getattr(ideology, field_name)
        return set_block(body, field_name, " ".join(values) or None)
    if field_name == "ai_behavior":
        for behavior in VANILLA_AI_BEHAVIORS:
            value = "yes" if ideology.ai_behavior == behavior else None
            body = set_scalar(body, f"ai_{behavior}", value)
        return body
    if field_name in {"can_host_government_in_exile", "can_collaborate"}:
        value = "yes" if getattr(ideology, field_name) else None
        return set_scalar(body, field_name, value)
    if field_name in {
        "ai_ideology_wanted_units_factor",
        "ai_give_core_state_control_threshold",
        "war_impact_on_world_tension",
        "faction_impact_on_world_tension",
    }:
        raw_value = getattr(ideology, field_name)
        value = None if raw_value is None else pdx_value(raw_value)
        return set_scalar(body, field_name, value)
    raise ValueError(f"Unknown ideology field: {field_name}")


def serialize_ideologies_file(
    ideologies: list[Ideology],
    *,
    original: str = "",
) -> str:
    """Serialize a complete ideology file while retaining its surrounding text."""

    if not original:
        rendered = "\n".join(serialize_ideology(item, 1) for item in ideologies)
        return f"ideologies = {{\n{rendered}\n}}\n"

    match = find_assignment_block(original, "ideologies")
    if match is None:
        rendered = "\n".join(serialize_ideology(item, 1) for item in ideologies)
        return append_assignment(original, f"ideologies = {{\n{rendered}\n}}")

    body, start, end = match
    open_brace = original.find("{", start, end)
    if open_brace < 0:
        raise ValueError("Malformed ideologies block")
    remaining = list(ideologies)
    spans = top_level_assignments(body)
    for span in sorted(spans, key=lambda item: item.start, reverse=True):
        if not span.is_block or span.body_start is None or span.body_end is None:
            continue
        match_index = next((i for i, item in enumerate(remaining) if item.id == span.key), None)
        if match_index is None:
            body = replace_assignment(body, span, None)
            continue
        ideology = remaining.pop(match_index)
        if ideology.raw_block and not ideology.touched_fields:
            continue
        patched = ideology.raw_block
        if patched:
            for field_name in sorted(ideology.touched_fields):
                patched = _patch_field(patched, ideology, field_name)
            body = body[: span.body_start] + patched + body[span.body_end :]
        else:
            body = replace_assignment(body, span, serialize_ideology(ideology, 0))
    for ideology in remaining:
        body = append_assignment(body, serialize_ideology(ideology, 0))
    return original[: open_brace + 1] + body + original[end - 1 :]


def _mapping_body(values: dict[str, str]) -> str:
    return "\n".join(f"{key} = {value}" for key, value in values.items())


def _patch_types_assignment(body: str, subtypes: list[SubIdeology]) -> str:
    spans = assignment_spans(body, "types")
    if not spans:
        rendered = "\n".join(_serialize_subideology(subtype) for subtype in subtypes)
        return set_block(body, "types", rendered or None)
    types_span = spans[0]
    if (
        not types_span.is_block
        or types_span.body_start is None
        or types_span.body_end is None
    ):
        rendered = "\n".join(_serialize_subideology(subtype) for subtype in subtypes)
        return set_block(body, "types", rendered or None)
    if not subtypes:
        return set_block(body, "types", None)

    types_body = body[types_span.body_start : types_span.body_end]
    remaining = list(subtypes)
    child_spans = top_level_assignments(types_body)
    for child in sorted(child_spans, key=lambda item: item.start, reverse=True):
        if not child.is_block or child.body_start is None or child.body_end is None:
            continue
        match_index = next(
            (index for index, subtype in enumerate(remaining) if subtype.name == child.key),
            None,
        )
        if match_index is None:
            types_body = replace_assignment(types_body, child, None)
            continue
        subtype = remaining.pop(match_index)
        patched = _patch_subideology_body(subtype)
        types_body = replace_assignment_body(types_body, child, patched)
    for subtype in remaining:
        types_body = append_assignment(types_body, _serialize_subideology(subtype))
    return replace_assignment_body(body, types_span, types_body)


def _patch_subideology_body(subtype: SubIdeology) -> str:
    if not subtype.raw_block:
        return "" if subtype.can_be_randomly_selected else "can_be_randomly_selected = no"
    parsed = parse_pdx(subtype.raw_block)
    current = parsed.get_value("can_be_randomly_selected", "yes").lower() != "no"
    if current == subtype.can_be_randomly_selected:
        return subtype.raw_block
    value = "yes" if subtype.can_be_randomly_selected else "no"
    return set_scalar(subtype.raw_block, "can_be_randomly_selected", value)


def _serialize_subideology(subtype: SubIdeology) -> str:
    return _wrap_block(subtype.name, _patch_subideology_body(subtype), 0)


def _patch_mapping_assignment(body: str, key: str, values: dict[str, str]) -> str:
    spans = assignment_spans(body, key)
    if not spans:
        return set_block(body, key, _mapping_body(values) or None)
    span = spans[0]
    if not span.is_block or span.body_start is None or span.body_end is None:
        return set_block(body, key, _mapping_body(values) or None)
    if not values:
        return set_block(body, key, None)
    current = body[span.body_start : span.body_end]
    patched = patch_scalar_mapping(current, values)
    return replace_assignment_body(body, span, patched)


def _wrap_block(key: str, body: str, indent: int) -> str:
    prefix = "\t" * indent
    clean = body.strip("\n")
    if not clean.strip():
        return f"{prefix}{key} = {{ }}"
    inner = "\n".join(prefix + "\t" + line for line in clean.splitlines())
    return f"{prefix}{key} = {{\n{inner}\n{prefix}}}"


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"yes", "true", "1"}
