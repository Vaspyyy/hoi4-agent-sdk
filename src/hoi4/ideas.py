"""
Ideas / National Spirits - read, create, and write HOI4 ideas.

Idea files live in common/national_ideas/*.txt and common/ideas/*.txt.
Each file contains a country_ideas block or ideas block with idea definitions.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .parser import extract_braced_block, find_assignment_block, strip_comments
from .patching import (
    append_assignment,
    replace_assignment,
    set_block,
    set_scalar,
    top_level_assignments,
)
from .script import pdx_string, pdx_value
from .types import Idea

KV_RE = re.compile(r"^\s*([a-zA-Z0-9_]+)\s*=\s*([^\n\r]+)", re.MULTILINE)
PICTURE_RE = re.compile(r"\bpicture\s*=\s*([^\n\r]+)")
ICON_RE = re.compile(r"\bicon\s*=\s*([^\n\r]+)")


VALID_CONTAINERS = ("country_ideas", "ideas")


def _extract_block(text: str, keyword: str) -> str:
    match = find_assignment_block(text, keyword)
    if not match:
        return ""
    return match[0]


def detect_ideas_container(text: str) -> str:
    matches = [
        (match[1], name)
        for name in VALID_CONTAINERS
        if (match := find_assignment_block(text, name)) is not None
    ]
    if matches:
        return min(matches)[1]
    return "country_ideas"


def _find_toplevel_blocks(text: str) -> list[tuple[str, str]]:
    return [
        (span.key, text[span.body_start : span.body_end])
        for span in top_level_assignments(text)
        if span.is_block and span.body_start is not None and span.body_end is not None
    ]


def _has_direct_property(text: str, keys: set[str]) -> bool:
    return any(span.key in keys for span in top_level_assignments(text))


def _is_idea_body(text: str) -> bool:
    return _has_direct_property(
        text,
        {
            "icon",
            "picture",
            "modifier",
            "research_bonus",
            "traits",
            "allowed",
            "allowed_civil_war",
            "ai_will_do",
            "cost",
            "visible",
        },
    )


def _parse_kv(text: str) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for key, value in KV_RE.findall(text):
        value = value.strip().strip('"')
        if value.lower() in ("yes", "no"):
            props[key.strip()] = value.lower() == "yes"
        elif "." in value:
            try:
                props[key.strip()] = float(value)
            except ValueError:
                props[key.strip()] = value
        else:
            try:
                props[key.strip()] = int(value)
            except ValueError:
                props[key.strip()] = value
    return props


def _parse_idea_body(idea_id: str, body: str) -> Idea:
    idea = Idea(id=idea_id.strip())
    pic = PICTURE_RE.search(body) or ICON_RE.search(body)
    if pic:
        idea.icon = pic.group(1).strip()
    modifier_body = _extract_block(body, "modifier")
    if modifier_body:
        idea.modifier = _parse_kv(modifier_body)
    allowed_body = _extract_block(body, "allowed")
    if allowed_body:
        idea.allowed = allowed_body.strip()
    research_body = _extract_block(body, "research_bonus")
    if research_body:
        idea.research_bonus = _parse_kv(research_body)
    traits_body = _extract_block(body, "traits")
    if traits_body:
        idea.traits = [item for item in strip_comments(traits_body).split() if item]
    ai_body = _extract_block(body, "ai_will_do")
    if ai_body:
        idea.ai_will_do = ai_body.strip()
    idea.raw_block = body.strip()
    return idea


def read_ideas_file(path: Path) -> tuple[list[Idea], str]:
    txt = path.read_text(encoding="utf-8", errors="ignore")
    container_name = detect_ideas_container(txt)
    container = _extract_block(txt, container_name)
    if not container:
        return [], container_name
    ideas: list[Idea] = []
    for block_id, block_body in _find_toplevel_blocks(container):
        if _is_idea_body(block_body):
            idea = _parse_idea_body(block_id, block_body)
            idea.path = path
            ideas.append(idea)
            continue
        for idea_id, idea_body in _find_toplevel_blocks(block_body):
            idea = _parse_idea_body(idea_id, idea_body)
            idea.category = block_id
            idea.category_raw_block = block_body.strip()
            idea.path = path
            ideas.append(idea)
    return ideas, container_name


def serialize_idea(idea: Idea, indent: int = 1) -> str:
    tab = "\t" * indent
    if idea.raw_block:
        body = idea.raw_block
        if idea.touched:
            body = set_scalar(body, "icon", idea.icon or None)
            body = set_scalar(body, "picture", None)
            body = set_block(body, "allowed", idea.allowed or None)
            body = set_block(
                body,
                "modifier",
                "\n".join(
                    f"{key} = {pdx_string(value) if isinstance(value, str) else pdx_value(value)}"
                    for key, value in idea.modifier.items()
                )
                or None,
            )
            body = set_block(
                body,
                "research_bonus",
                "\n".join(
                    f"{key} = {pdx_value(value)}" for key, value in idea.research_bonus.items()
                )
                or None,
            )
            body = set_block(body, "traits", " ".join(idea.traits) or None)
            body = set_block(body, "ai_will_do", idea.ai_will_do or None)
        return f"{tab}{idea.id} = {{\n{_indent(body, indent + 1)}\n{tab}}}"
    lines = [f"{tab}{idea.id} = {{"]
    lines.append(f"{tab}\ticon = {idea.icon}")
    if idea.allowed:
        lines.append(f"{tab}\tallowed = {{")
        for line in idea.allowed.strip().split("\n"):
            lines.append(f"{tab}\t\t{line.strip()}")
        lines.append(f"{tab}\t}}")
    if idea.modifier:
        lines.append(f"{tab}\tmodifier = {{")
        for key, value in idea.modifier.items():
            if isinstance(value, bool):
                lines.append(f"{tab}\t\t{key} = {'yes' if value else 'no'}")
            elif isinstance(value, float):
                lines.append(f"{tab}\t\t{key} = {value}")
            elif isinstance(value, int):
                lines.append(f"{tab}\t\t{key} = {value}")
            else:
                lines.append(f"{tab}\t\t{key} = {pdx_string(value)}")
        lines.append(f"{tab}\t}}")
    if idea.research_bonus:
        lines.append(f"{tab}\tresearch_bonus = {{")
        for key, value in idea.research_bonus.items():
            lines.append(f"{tab}\t\t{key} = {value}")
        lines.append(f"{tab}\t}}")
    if idea.ai_will_do:
        lines.append(f"{tab}\tai_will_do = {{")
        for line in idea.ai_will_do.strip().split("\n"):
            lines.append(f"{tab}\t\t{line.strip()}")
        lines.append(f"{tab}\t}}")
    if idea.traits:
        lines.append(f"{tab}\ttraits = {{ {' '.join(idea.traits)} }}")
    lines.append(f"{tab}}}")
    return "\n".join(lines)


def serialize_ideas_file(
    ideas: list[Idea],
    container_name: str = "country_ideas",
    original: str = "",
) -> str:
    uncategorized: list[Idea] = []
    categories: dict[str, list[Idea]] = {}
    for idea in ideas:
        if not idea.category:
            uncategorized.append(idea)
            continue
        categories.setdefault(idea.category, []).append(idea)
    if original:
        match = find_assignment_block(original, container_name)
        if match is not None:
            body = match[0]
            direct = {idea.id: idea for idea in uncategorized}
            remaining_categories = dict(categories)
            spans = top_level_assignments(body)
            for span in sorted(spans, key=lambda item: item.start, reverse=True):
                if not span.is_block or span.body_start is None or span.body_end is None:
                    continue
                span_body = body[span.body_start : span.body_end]
                replacement: str | None
                if _is_idea_body(span_body):
                    matched_idea = direct.pop(span.key) if span.key in direct else None
                    replacement = (
                        None
                        if matched_idea is None
                        else serialize_idea(matched_idea, indent=0).strip()
                    )
                else:
                    category_ideas = remaining_categories.pop(span.key, None)
                    if category_ideas is None:
                        nested = [
                            nested_span
                            for nested_span in top_level_assignments(span_body)
                            if nested_span.is_block
                            and nested_span.body_start is not None
                            and nested_span.body_end is not None
                        ]
                        if nested and all(
                            _is_idea_body(span_body[nested_span.body_start : nested_span.body_end])
                            for nested_span in nested
                        ):
                            body = replace_assignment(body, span, None)
                        continue
                    replacement = _serialize_category(span.key, category_ideas, indent=0)
                body = replace_assignment(body, span, replacement)
            for idea in direct.values():
                body = append_assignment(body, serialize_idea(idea, indent=0).strip())
            for category, category_ideas in remaining_categories.items():
                body = append_assignment(
                    body, _serialize_category(category, category_ideas, indent=0)
                )
            replacement = f"{container_name} = {{\n{_indent(body, 1)}\n}}"
            return original[: match[1]] + replacement + original[match[2] :]

    parts = [f"{container_name} = {{"]
    for idea in uncategorized:
        parts.append(serialize_idea(idea))
        parts.append("")
    for category, category_ideas in categories.items():
        parts.append(_serialize_category(category, category_ideas, indent=1))
        parts.append("")
    parts.append("}")
    parts.append("")
    return "\n".join(parts)


def _serialize_category(category: str, ideas: list[Idea], indent: int) -> str:
    prefix = "\t" * indent
    raw = next((idea.category_raw_block for idea in ideas if idea.category_raw_block), "")
    if raw:
        current = {idea.id: idea for idea in ideas}
        spans = {
            span.key: span
            for span in top_level_assignments(raw)
            if span.is_block
            and span.body_start is not None
            and span.body_end is not None
            and _is_idea_body(raw[span.body_start : span.body_end])
        }
        for idea_id, span in sorted(spans.items(), key=lambda item: item[1].start, reverse=True):
            idea = current.pop(idea_id, None)
            raw = replace_assignment(
                raw, span, None if idea is None else serialize_idea(idea, indent=0).strip()
            )
        for idea in current.values():
            raw = append_assignment(raw, serialize_idea(idea, indent=0).strip())
        return f"{prefix}{category} = {{\n{_indent(raw, indent + 1)}\n{prefix}}}"
    lines = [f"{prefix}{category} = {{"]
    for idea in ideas:
        lines.append(serialize_idea(idea, indent=indent + 1))
        lines.append("")
    lines.append(f"{prefix}}}")
    return "\n".join(lines)


def _indent(text: str, count: int) -> str:
    prefix = "\t" * count
    return "\n".join(prefix + line.rstrip() for line in text.strip().splitlines())


def write_ideas_file(path: Path, ideas: list[Idea], container_name: str = "country_ideas") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = serialize_ideas_file(ideas, container_name=container_name)
    path.write_text(content, encoding="utf-8")
    return path


def read_assigned_ideas(history_file: Path) -> list[str]:
    if not history_file.exists():
        return []
    text = history_file.read_text(encoding="utf-8", errors="ignore")
    assigned: list[str] = []
    removed: set[str] = set()

    for m in re.finditer(r"add_ideas\s*=\s*\{", text):
        start = m.end()
        try:
            block, _ = extract_braced_block(text, start)
        except ValueError:
            continue
        for im in re.finditer(r"\b([a-zA-Z][a-zA-Z0-9_]*)\b", block):
            word = im.group(1)
            if word.lower() not in ("yes", "no", "always", "and", "or", "not", "tag"):
                assigned.append(word)

    for m in re.finditer(r"remove_ideas\s*=\s*\{", text):
        start = m.end()
        try:
            block, _ = extract_braced_block(text, start)
        except ValueError:
            continue
        for im in re.finditer(r"\b([a-zA-Z][a-zA-Z0-9_]*)\b", block):
            removed.add(im.group(1))

    for m in re.finditer(r"remove_ideas\s*=\s*([a-zA-Z][a-zA-Z0-9_]*)", text):
        removed.add(m.group(1))

    return [idea for idea in dict.fromkeys(assigned) if idea not in removed]
