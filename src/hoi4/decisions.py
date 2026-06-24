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

    for m in re.finditer(r"(?m)^\s*([A-Za-z0-9_]+)\s*=\s*\{", txt):
        if _brace_depth_at(txt, m.start(1)) != 0:
            continue
        category_id = m.group(1)
        match = find_assignment_block(txt, category_id, m.start(1))
        if match is None or match[1] != m.start(1):
            continue
        category_body = match[0]
        category = DecisionCategory(
            id=category_id,
            icon=_extract_scalar(category_body, "icon"),
            allowed=_extract_block(category_body, "allowed"),
            visible=_extract_block(category_body, "visible"),
            path=path,
            raw_block=category_body.strip(),
        )
        for decision_body, _, _ in _iter_top_level_decision_blocks(category_body):
            decision_id = _decision_id_from_body(category_body, decision_body)
            decision = _parse_decision(decision_id, category_id, decision_body, path)
            category.decisions.append(decision)
        categories.append(category)
    return categories


def _iter_top_level_decision_blocks(category_body: str) -> list[tuple[str, int, int]]:
    blocks: list[tuple[str, int, int]] = []
    for m in re.finditer(r"(?m)^\s*([A-Za-z0-9_]+)\s*=\s*\{", category_body):
        if _brace_depth_at(category_body, m.start(1)) != 0:
            continue
        key = m.group(1)
        if key in {"allowed", "visible", "picture", "icon"}:
            continue
        match = find_assignment_block(category_body, key, m.start(1))
        if match is None or match[1] != m.start(1):
            continue
        if not _looks_like_category(match[0]):
            continue
        blocks.append(match)
    return blocks


def _decision_id_from_body(category_body: str, decision_body: str) -> str:
    for m in re.finditer(r"(?m)^\s*([A-Za-z0-9_]+)\s*=\s*\{", category_body):
        if _brace_depth_at(category_body, m.start(1)) != 0:
            continue
        match = find_assignment_block(category_body, m.group(1), m.start(1))
        if match and match[0] == decision_body:
            return m.group(1)
    return ""


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


def serialize_decisions_file(categories: list[DecisionCategory]) -> str:
    parts: list[str] = []
    for category in categories:
        parts.append(f"{category.id} = {{")
        if category.icon:
            parts.append(f"\ticon = {category.icon}")
        for key in ["allowed", "visible"]:
            body = getattr(category, key)
            if body:
                parts.append(f"\t{key} = {{")
                for line in body.strip().split("\n"):
                    parts.append(f"\t\t{line.strip()}")
                parts.append("\t}")
        for decision in category.decisions:
            parts.append(serialize_decision(decision))
            parts.append("")
        parts.append("}")
        parts.append("")
    return "\n".join(parts)


def write_decisions_file(path: Path, categories: list[DecisionCategory]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize_decisions_file(categories), encoding="utf-8")
    return path
