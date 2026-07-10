"""
Decision system - read, create, and write HOI4 decisions.

Decision files live in common/decisions/*.txt. The SDK models categories and
their nested decisions while preserving common trigger/effect blocks as raw
Paradox script strings.
"""

from __future__ import annotations

import re
from pathlib import Path

from .parser import find_assignment_block, iter_assignment_blocks, strip_comments
from .patching import (
    append_assignment,
    replace_assignment,
    set_block,
    set_scalar,
    top_level_assignments,
)
from .types import Decision, DecisionCategory


SCALAR_RE = re.compile(r"\b([A-Za-z0-9_]+)\s*=\s*([^\s{}#]+)")


def _extract_block(text: str, key: str) -> str:
    match = find_assignment_block(text, key)
    return match[0].strip() if match else ""


def _extract_scalar(text: str, key: str) -> str:
    clean = strip_comments(text)
    m = re.search(rf"\b{re.escape(key)}\s*=\s*([^\s{{}}#]+)", clean)
    return m.group(1) if m else ""


def _extract_int(text: str, key: str) -> int | None:
    raw = _extract_scalar(text, key)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _extract_bool(text: str, key: str) -> bool | None:
    raw = _extract_scalar(text, key)
    if raw == "yes":
        return True
    if raw == "no":
        return False
    return None


def _brace_depth_at(text: str, pos: int) -> int:
    depth = 0
    in_quote = False
    i = 0
    while i < min(pos, len(text)):
        c = text[i]
        if c == "\\" and in_quote:
            i += 2
            continue
        if c == '"':
            in_quote = not in_quote
        elif c == "#" and not in_quote:
            while i < min(pos, len(text)) and text[i] != "\n":
                i += 1
            continue
        elif not in_quote and c == "{":
            depth += 1
        elif not in_quote and c == "}":
            depth -= 1
        i += 1
    return depth


def _looks_like_category(body: str) -> bool:
    decision_keys = {
        "available",
        "visible",
        "complete_effect",
        "remove_effect",
        "days_remove",
        "fire_only_once",
        "cost",
    }
    clean = strip_comments(body)
    return bool(iter_assignment_blocks(body, "available")) or any(
        re.search(rf"\b{re.escape(key)}\s*=", clean) for key in decision_keys
    )


def load_decisions_file(path: Path) -> list[DecisionCategory]:
    txt = path.read_text(encoding="utf-8", errors="ignore")
    categories: list[DecisionCategory] = []
    for span in top_level_assignments(txt):
        if not span.is_block or span.body_start is None or span.body_end is None:
            continue
        category_id = span.key
        category_body = txt[span.body_start : span.body_end]
        category = DecisionCategory(
            id=category_id,
            icon=_extract_scalar(category_body, "icon"),
            allowed=_extract_block(category_body, "allowed"),
            visible=_extract_block(category_body, "visible"),
            path=path,
            raw_block=category_body.strip(),
        )
        for decision_id, decision_body in _iter_top_level_decision_blocks(category_body):
            decision = _parse_decision(decision_id, category_id, decision_body, path)
            category.decisions.append(decision)
        categories.append(category)
    return categories


def _iter_top_level_decision_blocks(category_body: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    for span in top_level_assignments(category_body):
        if not span.is_block or span.body_start is None or span.body_end is None:
            continue
        key = span.key
        if key in {"allowed", "visible", "picture", "icon"}:
            continue
        body = category_body[span.body_start : span.body_end]
        if not _looks_like_category(body):
            continue
        blocks.append((key, body))
    return blocks


def _parse_decision(decision_id: str, category_id: str, body: str, path: Path) -> Decision:
    return Decision(
        id=decision_id,
        category=category_id,
        icon=_extract_scalar(body, "icon"),
        cost=_extract_int(body, "cost"),
        days_remove=_extract_int(body, "days_remove"),
        fire_only_once=_extract_bool(body, "fire_only_once"),
        available=_extract_block(body, "available"),
        visible=_extract_block(body, "visible"),
        complete_effect=_extract_block(body, "complete_effect"),
        remove_effect=_extract_block(body, "remove_effect"),
        ai_will_do=_extract_block(body, "ai_will_do"),
        path=path,
        raw_block=body.strip(),
    )


def serialize_decision(decision: Decision) -> str:
    if decision.raw_block:
        body = decision.raw_block
        if decision.touched:
            body = set_scalar(body, "icon", decision.icon or None)
            body = set_scalar(body, "cost", None if decision.cost is None else str(decision.cost))
            body = set_scalar(
                body,
                "days_remove",
                None if decision.days_remove is None else str(decision.days_remove),
            )
            body = set_scalar(
                body,
                "fire_only_once",
                None
                if decision.fire_only_once is None
                else ("yes" if decision.fire_only_once else "no"),
            )
            for key in ["visible", "available", "complete_effect", "remove_effect", "ai_will_do"]:
                value = getattr(decision, key)
                body = set_block(body, key, value or None)
        return f"\t{decision.id} = {{\n{_indent(body, 2)}\n\t}}"
    lines = [f"\t{decision.id} = {{"]
    if decision.icon:
        lines.append(f"\t\ticon = {decision.icon}")
    if decision.cost is not None:
        lines.append(f"\t\tcost = {decision.cost}")
    if decision.days_remove is not None:
        lines.append(f"\t\tdays_remove = {decision.days_remove}")
    if decision.fire_only_once is not None:
        lines.append(f"\t\tfire_only_once = {'yes' if decision.fire_only_once else 'no'}")
    for key in ["visible", "available", "complete_effect", "remove_effect", "ai_will_do"]:
        body = getattr(decision, key)
        if body:
            lines.append(f"\t\t{key} = {{")
            for line in body.strip().split("\n"):
                lines.append(f"\t\t\t{line.strip()}")
            lines.append("\t\t}")
    lines.append("\t}")
    return "\n".join(lines)


def serialize_decisions_file(categories: list[DecisionCategory], original: str = "") -> str:
    if original:
        text = original
        current = {category.id: category for category in categories}
        for span in sorted(top_level_assignments(text), key=lambda item: item.start, reverse=True):
            if not span.is_block:
                continue
            category = current.pop(span.key, None)
            if category is None and span.key not in {candidate.id for candidate in categories}:
                # Treat blocks that parse as categories as deleted; leave unrelated blocks.
                if (
                    span.body_start is None
                    or span.body_end is None
                    or not _iter_top_level_decision_blocks(text[span.body_start : span.body_end])
                ):
                    continue
            text = replace_assignment(
                text, span, None if category is None else _serialize_category(category)
            )
        for category in current.values():
            text = append_assignment(text, _serialize_category(category))
        return text
    parts: list[str] = []
    for category in categories:
        parts.append(_serialize_category(category))
        parts.append("")
    return "\n".join(parts)


def _serialize_category(category: DecisionCategory) -> str:
    if category.raw_block:
        body = category.raw_block
        if category.touched:
            body = set_scalar(body, "icon", category.icon or None)
            body = set_block(body, "allowed", category.allowed or None)
            body = set_block(body, "visible", category.visible or None)
        current = {decision.id: decision for decision in category.decisions}
        decision_spans = {
            span.key: span
            for span in top_level_assignments(body)
            if span.is_block
            and span.body_start is not None
            and span.body_end is not None
            and _looks_like_category(body[span.body_start : span.body_end])
        }
        for decision_id, span in sorted(
            decision_spans.items(), key=lambda item: item[1].start, reverse=True
        ):
            decision = current.pop(decision_id, None)
            body = replace_assignment(
                body, span, None if decision is None else serialize_decision(decision).strip()
            )
        for decision in current.values():
            body = append_assignment(body, serialize_decision(decision).strip())
        return f"{category.id} = {{\n{_indent(body, 1)}\n}}"
    parts = [f"{category.id} = {{"]
    if category.icon:
        parts.append(f"\ticon = {category.icon}")
    for key in ["allowed", "visible"]:
        body = getattr(category, key)
        if body:
            parts.append(f"\t{key} = {{")
            parts.extend(f"\t\t{line.strip()}" for line in body.strip().splitlines())
            parts.append("\t}")
    for decision in category.decisions:
        parts.append(serialize_decision(decision))
        parts.append("")
    parts.append("}")
    return "\n".join(parts)


def write_decisions_file(path: Path, categories: list[DecisionCategory]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize_decisions_file(categories), encoding="utf-8")
    return path


def _indent(text: str, count: int) -> str:
    prefix = "\t" * count
    return "\n".join(prefix + line.rstrip() for line in text.strip().splitlines())
