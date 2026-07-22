"""Dynamic country ideas with lossless, field-level editing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .parser import find_assignment_block, parse_pdx
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


@dataclass
class DynamicIdea:
    id: str
    potential: str = ""
    available: str = ""
    modifier: dict[str, str | int | float | bool] = field(default_factory=dict)
    path: Path | None = None
    raw_block: str = ""
    touched_fields: set[str] = field(default_factory=set)


@dataclass
class DynamicIdeaGroup:
    name: str
    ideas: list[DynamicIdea] = field(default_factory=list)
    path: Path | None = None
    raw_block: str = ""
    touched_fields: set[str] = field(default_factory=set)


def load_dynamic_ideas_file(path: Path) -> DynamicIdeaGroup | None:
    text = path.read_text(encoding="utf-8", errors="ignore")
    match = find_assignment_block(text, "dynamic_country_ideas")
    if match is None:
        return None
    body = match[0]
    name = ""
    ideas: list[DynamicIdea] = []
    for span in top_level_assignments(body):
        if span.key == "name" and not span.is_block:
            name = _unquote(body[span.value_start : span.value_end])
            continue
        if not span.is_block or span.body_start is None or span.body_end is None:
            continue
        idea_body = body[span.body_start : span.body_end]
        ideas.append(_parse_dynamic_idea(span.key, idea_body, path))
    return DynamicIdeaGroup(name=name, ideas=ideas, path=path, raw_block=body)


def load_dynamic_ideas(root: Path) -> list[DynamicIdeaGroup]:
    directory = root / "common" / "national_ideas"
    if not directory.is_dir():
        return []
    groups: list[DynamicIdeaGroup] = []
    for path in sorted(directory.glob("*.txt")):
        group = load_dynamic_ideas_file(path)
        if group is not None:
            groups.append(group)
    return groups


def _parse_dynamic_idea(idea_id: str, body: str, path: Path) -> DynamicIdea:
    potential = _direct_block_body(body, "potential")
    available = _direct_block_body(body, "available")
    modifier_body = _direct_block_body(body, "modifier")
    modifier: dict[str, str | int | float | bool] = {}
    if modifier_body is not None:
        root = parse_pdx(modifier_body)
        for child in root.children:
            if child.key is None or child.value is None:
                continue
            modifier[child.key] = _coerce_scalar(child.value)
    return DynamicIdea(
        id=idea_id,
        potential=potential or "",
        available=available or "",
        modifier=modifier,
        path=path,
        raw_block=body,
    )


def serialize_dynamic_idea(idea: DynamicIdea, indent: int = 0) -> str:
    body = idea.raw_block
    fields = idea.touched_fields
    if not body:
        fields = {"potential", "available", "modifier"}
    for field_name in sorted(fields):
        if field_name in {"potential", "available"}:
            body = set_block(body, field_name, getattr(idea, field_name) or None)
        elif field_name == "modifier":
            body = _patch_modifier(body, idea.modifier)
        else:
            raise ValueError(f"Unknown dynamic idea field: {field_name}")
    return _wrap_block(idea.id, body, indent)


def serialize_dynamic_ideas_file(
    group: DynamicIdeaGroup,
    *,
    original: str = "",
) -> str:
    """Serialize one ``dynamic_country_ideas`` container."""

    if not original:
        body = f"name = {pdx_value(group.name)}\n"
        for idea in group.ideas:
            body += serialize_dynamic_idea(idea, 0) + "\n"
        return _wrap_block("dynamic_country_ideas", body.rstrip(), 0) + "\n"

    outer = find_assignment_block(original, "dynamic_country_ideas")
    if outer is None:
        rendered = serialize_dynamic_ideas_file(group)
        return append_assignment(original, rendered)
    body, start, end = outer
    open_brace = original.find("{", start, end)
    if open_brace < 0:
        raise ValueError("Malformed dynamic_country_ideas block")

    if "name" in group.touched_fields:
        body = set_scalar(body, "name", pdx_value(group.name))

    remaining = list(group.ideas)
    spans = top_level_assignments(body)
    for span in sorted(spans, key=lambda item: item.start, reverse=True):
        if span.key == "name" or not span.is_block:
            continue
        match_index = next((i for i, item in enumerate(remaining) if item.id == span.key), None)
        if match_index is None:
            body = replace_assignment(body, span, None)
            continue
        idea = remaining.pop(match_index)
        if idea.raw_block and not idea.touched_fields:
            continue
        if span.body_start is None or span.body_end is None:
            continue
        patched = idea.raw_block
        if patched:
            for field_name in sorted(idea.touched_fields):
                if field_name in {"potential", "available"}:
                    patched = set_block(patched, field_name, getattr(idea, field_name) or None)
                elif field_name == "modifier":
                    patched = _patch_modifier(patched, idea.modifier)
                else:
                    raise ValueError(f"Unknown dynamic idea field: {field_name}")
            body = body[: span.body_start] + patched + body[span.body_end :]
        else:
            body = replace_assignment(body, span, serialize_dynamic_idea(idea, 0))
    for idea in remaining:
        body = append_assignment(body, serialize_dynamic_idea(idea, 0))
    return original[: open_brace + 1] + body + original[end - 1 :]


def remove_dynamic_ideas_container(original: str) -> str:
    """Remove only the dynamic-ideas container from a mixed source file."""

    span = next(
        (
            item
            for item in top_level_assignments(original)
            if item.key == "dynamic_country_ideas"
        ),
        None,
    )
    if span is None:
        return original
    return replace_assignment(original, span, None)


def _direct_block_body(text: str, key: str) -> str | None:
    for span in top_level_assignments(text):
        if span.key == key and span.is_block and span.body_start is not None and span.body_end is not None:
            return text[span.body_start : span.body_end]
    return None


def _patch_modifier(
    body: str,
    modifier: dict[str, str | int | float | bool],
) -> str:
    spans = assignment_spans(body, "modifier")
    if not spans:
        rendered = "\n".join(f"{key} = {pdx_value(value)}" for key, value in modifier.items())
        return set_block(body, "modifier", rendered or None)
    span = spans[0]
    if not span.is_block or span.body_start is None or span.body_end is None:
        return body
    if not modifier:
        return set_block(body, "modifier", None)
    current = body[span.body_start : span.body_end]
    return replace_assignment_body(body, span, patch_scalar_mapping(current, modifier))


def _coerce_scalar(value: str) -> str | int | float | bool:
    if value == "yes":
        return True
    if value == "no":
        return False
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def _wrap_block(key: str, body: str, indent: int) -> str:
    prefix = "\t" * indent
    clean = body.strip("\n")
    if not clean.strip():
        return f"{prefix}{key} = {{ }}"
    inner = "\n".join(prefix + "\t" + line for line in clean.splitlines())
    return f"{prefix}{key} = {{\n{inner}\n{prefix}}}"
