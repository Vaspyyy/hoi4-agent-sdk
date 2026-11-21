"""Ideology definitions with source-preserving reads and writes.

HOI4 ideology files live in ``common/ideologies`` and wrap definitions in an
``ideologies = { ... }`` block.  This module deliberately keeps the original
body of every definition so editing one property does not rewrite unrelated
rules, comments, ordering, or syntax added by a newer game version.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ._mapping_patch import patch_scalar_entries, scalar_entries
from .patching import (
    AssignmentSpan,
    append_assignment,
    assignment_spans,
    replace_assignment,
    replace_assignment_body,
    set_block,
    set_scalar,
    top_level_assignments,
)
from .script import pdx_value
from .syntax import Assignment, Block, parse, quote, require_entry, require_key
from .textio import read_text

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


def _is_block_assignment(item: object) -> bool:
    return type(item) is Assignment and item.op == "=" and type(item.value) is Block


def _containers(root: Block) -> list[Assignment]:
    """Return the top-level ``ideologies = { ... }`` blocks."""

    return [
        item
        for item in root.items
        if type(item) is Assignment
        and _is_block_assignment(item)
        and item.key.value == "ideologies"
    ]


def _definitions(container: Assignment) -> list[Assignment]:
    assert type(container.value) is Block
    return [
        item
        for item in container.value.items
        if type(item) is Assignment and _is_block_assignment(item)
    ]


def load_ideologies_text(
    text: str, path: Path | None = None, *, is_vanilla: bool = False
) -> list[Ideology]:
    """Load every ideology from one file's text, retaining duplicate definitions."""

    ideologies: list[Ideology] = []
    for container in _containers(parse(text).root):
        for item in _definitions(container):
            assert type(item.value) is Block
            ideology = _parse_ideology(item.key.value, item.value)
            ideology.is_vanilla = is_vanilla
            ideology.path = path
            ideologies.append(ideology)
    return ideologies


def load_ideologies_file(path: Path, *, is_vanilla: bool = False) -> list[Ideology]:
    """Load every ideology from one file, retaining duplicate definitions."""

    return load_ideologies_text(read_text(path), path, is_vanilla=is_vanilla)


def load_ideologies(root: Path, *, is_vanilla: bool = False) -> list[Ideology]:
    """Load all ideology files beneath a game or mod root."""

    directory = root / "common" / "ideologies"
    if not directory.is_dir():
        return []
    result: list[Ideology] = []
    for path in sorted(directory.glob("*.txt")):
        result.extend(load_ideologies_file(path, is_vanilla=is_vanilla))
    return result


def _parse_ideology(ideology_id: str, block: Block) -> Ideology:
    ai_behavior = next(
        (
            behavior
            for behavior in VANILLA_AI_BEHAVIORS
            if (block.scalar(f"ai_{behavior}") or "").lower() == "yes"
        ),
        "",
    )
    modifiers = _sub_block(block, "modifiers")
    return Ideology(
        id=ideology_id,
        color=_parse_color(block.block("color")),
        types=_parse_types(block),
        rules=_parse_mapping(_sub_block(block, "rules")),
        modifiers=_parse_mapping(modifiers),
        hidden_modifiers=_parse_mapping(
            _sub_block(modifiers, "hidden_modifier") if modifiers is not None else None
        ),
        faction_modifiers=_parse_mapping(_sub_block(block, "faction_modifiers")),
        dynamic_faction_names=_parse_bare_values(_sub_block(block, "dynamic_faction_names")),
        ai_behavior=ai_behavior,
        ai_ideology_wanted_units_factor=block.scalar("ai_ideology_wanted_units_factor", "1.0"),
        ai_give_core_state_control_threshold=block.scalar(
            "ai_give_core_state_control_threshold", "0"
        ),
        war_impact_on_world_tension=block.scalar("war_impact_on_world_tension", "0.25"),
        faction_impact_on_world_tension=block.scalar("faction_impact_on_world_tension", "0.1"),
        can_host_government_in_exile=_as_bool(
            block.scalar("can_host_government_in_exile", "no") or "no"
        ),
        can_collaborate=_as_bool(block.scalar("can_collaborate", "no") or "no"),
        effects=_parse_bare_values(_sub_block(block, "effects")),
        raw_block=block.body,
    )


def _sub_block(block: Block, key: str) -> Block | None:
    for item in block.assignments(key):
        if item.op == "=" and type(item.value) is Block:
            return item.value
    return None


def _parse_color(block: Block | None) -> tuple[int, ...]:
    if block is None:
        return (128, 128, 128)
    values = _parse_bare_values(block)
    try:
        return tuple(int(item) for item in values)
    except (TypeError, ValueError):
        # Retain invalidity in the model so validation can report it instead of
        # silently replacing malformed source with a plausible default.
        return ()


def _parse_mapping(block: Block | None) -> dict[str, str]:
    if block is None:
        return {}
    return {
        item.key.value: item.scalar
        for item in block.assignments()
        if item.op == "=" and item.scalar is not None
    }


def _parse_bare_values(block: Block | None) -> list[str]:
    if block is None:
        return []
    return [scalar.value for scalar in block.values()]


def _parse_types(block: Block) -> list[SubIdeology]:
    types = _sub_block(block, "types")
    if types is None:
        return []
    result: list[SubIdeology] = []
    for child in types.assignments():
        if child.op != "=" or type(child.value) is not Block:
            continue
        subtype = child.value
        result.append(
            SubIdeology(
                name=child.key.value,
                can_be_randomly_selected=(
                    (subtype.scalar("can_be_randomly_selected", "yes") or "yes").lower() != "no"
                ),
                raw_block=subtype.body,
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
        modifier_body = patch_scalar_entries(modifier_body, ideology.modifiers)
        modifier_body = _patch_mapping_assignment(
            modifier_body, "hidden_modifier", ideology.hidden_modifiers
        )
        return replace_assignment_body(body, span, modifier_body)
    if field_name in {"dynamic_faction_names", "effects"}:
        values = getattr(ideology, field_name)
        return set_block(body, field_name, " ".join(quote(value) for value in values) or None)
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
    """Serialize a complete ideology file while retaining its surrounding text.

    Only touched fields of touched ideologies, removed ideologies and appended
    new ones change; every other byte of ``original`` is kept.
    """

    if not original:
        rendered = "\n".join(serialize_ideology(item, 1) for item in ideologies)
        return f"ideologies = {{\n{rendered}\n}}\n"

    containers = _containers(parse(original).root)
    if not containers:
        rendered = "\n".join(serialize_ideology(item, 1) for item in ideologies)
        return append_assignment(original, f"ideologies = {{\n{rendered}\n}}")

    remaining = list(ideologies)
    # Per container: (definition, "remove" | "replace" | "body", text).
    edits: list[list[tuple[Assignment, str, str]]] = [[] for _ in containers]
    for index, container in enumerate(containers):
        for item in _definitions(container):
            match_index = next(
                (i for i, ideology in enumerate(remaining) if ideology.id == item.key.value),
                None,
            )
            if match_index is None:
                edits[index].append((item, "remove", ""))
                continue
            ideology = remaining.pop(match_index)
            if ideology.raw_block and not ideology.touched_fields:
                continue
            assert type(item.value) is Block
            current = item.value.body
            if ideology.raw_block:
                patched = current
                for field_name in sorted(ideology.touched_fields):
                    patched = _patch_field(patched, ideology, field_name)
                if patched != current:
                    edits[index].append((item, "body", patched))
            else:
                edits[index].append((item, "replace", serialize_ideology(ideology, 0)))

    result = original
    for index in reversed(range(len(containers))):
        appended = remaining if index == len(containers) - 1 else []
        if not edits[index] and not appended:
            continue
        container = containers[index]
        block = container.value
        assert type(block) is Block
        base = block.body_start
        body = original[block.body_start : block.body_end]
        for item, action, text in sorted(edits[index], key=lambda edit: edit[0].start, reverse=True):
            value = item.value
            assert type(value) is Block
            span = AssignmentSpan(
                item.key.value, item.start - base, value.end - base,
                value.start - base, value.end - base, True,
                value.body_start - base, value.body_end - base,
            )
            if action == "remove":
                body = replace_assignment(body, span, None)
            elif action == "replace":
                body = replace_assignment(body, span, text)
            else:
                body = replace_assignment_body(body, span, text)
        for ideology in appended:
            body = append_assignment(body, serialize_ideology(ideology, 0))
        result = result[: block.body_start] + body + result[block.body_end :]
    return result


def _mapping_body(values: dict[str, str]) -> str:
    return "\n".join(
        require_entry(key, value, f"the entry '{key}'") for key, value in values.items()
    )


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
    current_value = parse(subtype.raw_block).root.scalar("can_be_randomly_selected", "yes")
    current = (current_value or "yes").lower() != "no"
    if current == subtype.can_be_randomly_selected:
        return subtype.raw_block
    value = "yes" if subtype.can_be_randomly_selected else "no"
    return set_scalar(subtype.raw_block, "can_be_randomly_selected", value)


def _serialize_subideology(subtype: SubIdeology) -> str:
    return _wrap_block(
        require_key(subtype.name, "a sub-ideology name"), _patch_subideology_body(subtype), 0
    )


def _patch_mapping_assignment(body: str, key: str, values: dict[str, str]) -> str:
    spans = assignment_spans(body, key)
    if not spans:
        return set_block(body, key, _mapping_body(values) or None)
    span = spans[0]
    if not span.is_block or span.body_start is None or span.body_end is None:
        return set_block(body, key, _mapping_body(values) or None)
    current = body[span.body_start : span.body_end]
    if not values:
        return body if not scalar_entries(current) else set_block(body, key, None)
    patched = patch_scalar_entries(current, values)
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
