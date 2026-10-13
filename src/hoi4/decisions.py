"""
Decision system - read, create, and write HOI4 decisions.

Decision files live in common/decisions/*.txt. Category metadata lives
separately in common/decisions/categories/*.txt. The SDK models both while
preserving common trigger/effect blocks as raw Paradox script strings.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from ._scalar import needs_quotes
from .parser import (
    ParseError,
    TokenType,
    find_assignment_block,
    iter_assignment_blocks,
    strip_comments,
    tokenize,
)
from .patching import (
    AssignmentSpan,
    append_assignment,
    dedent_block_body,
    replace_assignment,
    replace_assignment_body,
    set_block,
    set_scalar,
    top_level_assignments,
)
from .types import Decision, DecisionCategory


SCALAR_RE = re.compile(r"\b([A-Za-z0-9_]+)\s*=\s*([^\s{}#]+)")

# Field kinds: "int", "bool", "str" (scalar, quoted when needed), "atom" (bare
# scalar), "list" (``key = { A B }``), and "block" (raw Paradox script).
# Legacy fields keep their historical readers; the mission/targeted/general
# fields are read from top-level assignments only, so nested keys such as an
# ``ai_will_do = { modifier = { ... } }`` never leak into ``Decision.modifier``.
_LEGACY_FIELDS: dict[str, str] = {
    "icon": "atom",
    "cost": "int",
    "days_remove": "int",
    "fire_only_once": "bool",
    "available": "block",
    "visible": "block",
    "complete_effect": "block",
    "remove_effect": "block",
    "ai_will_do": "block",
}
_EXTENDED_FIELDS: dict[str, str] = {
    "days_mission_timeout": "int",
    "activation": "block",
    "timeout_effect": "block",
    "cancel_trigger": "block",
    "cancel_effect": "block",
    "is_good": "bool",
    "selectable_mission": "bool",
    "target_trigger": "block",
    "targets": "list",
    "target_array": "str",
    "target_root_trigger": "block",
    "state_target": "bool",
    "targets_dynamic": "bool",
    "target_non_existing": "bool",
    "on_map_mode": "str",
    "allowed": "block",
    "days_re_enable": "int",
    "war_with_on_remove": "str",
    "custom_cost_trigger": "block",
    "custom_cost_text": "str",
    "cancel_if_not_visible": "bool",
    "modifier": "block",
    "remove_trigger": "block",
    "highlight_states": "block",
    "priority": "int",
}
DECISION_FIELD_KINDS: dict[str, str] = {**_LEGACY_FIELDS, **_EXTENDED_FIELDS}

# Render order for new decisions: scalars first, then trigger/effect blocks.
# The legacy fields keep their historical relative order.
_RENDER_ORDER: tuple[str, ...] = (
    "icon",
    "priority",
    "cost",
    "custom_cost_text",
    "days_remove",
    "days_re_enable",
    "days_mission_timeout",
    "fire_only_once",
    "is_good",
    "selectable_mission",
    "cancel_if_not_visible",
    "state_target",
    "targets",
    "target_array",
    "targets_dynamic",
    "target_non_existing",
    "on_map_mode",
    "war_with_on_remove",
    "allowed",
    "visible",
    "available",
    "target_root_trigger",
    "target_trigger",
    "activation",
    "highlight_states",
    "cancel_trigger",
    "remove_trigger",
    "custom_cost_trigger",
    "complete_effect",
    "remove_effect",
    "timeout_effect",
    "cancel_effect",
    "modifier",
    "ai_will_do",
)
assert set(_RENDER_ORDER) == set(DECISION_FIELD_KINDS)


def _extract_block(text: str, key: str) -> str:
    match = find_assignment_block(text, key)
    return dedent_block_body(match[0]) if match else ""


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


def _first_spans(body: str) -> dict[str, AssignmentSpan]:
    spans: dict[str, AssignmentSpan] = {}
    try:
        for span in top_level_assignments(body):
            spans.setdefault(span.key, span)
    except ParseError:
        return {}
    return spans


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return value


def _parse_atom_list(body: str) -> list[str]:
    """Read ``A B "C"``; anything structured is not a plain list."""
    values: list[str] = []
    try:
        tokens = tokenize(body)
    except (ParseError, ValueError):
        return []
    for token in tokens:
        if token.type in {TokenType.COMMENT, TokenType.EOF}:
            continue
        if token.type not in {TokenType.IDENT, TokenType.STRING, TokenType.NUMBER}:
            return []
        values.append(token.value)
    return values


def _top_level_value(body: str, span: AssignmentSpan | None, kind: str) -> Any:
    if kind == "block":
        if span is None or span.body_start is None or span.body_end is None:
            return ""
        return dedent_block_body(body[span.body_start : span.body_end])
    if kind == "list":
        if span is None or span.body_start is None or span.body_end is None:
            return []
        return _parse_atom_list(body[span.body_start : span.body_end])
    if span is None or span.is_block:
        return "" if kind in {"str", "atom"} else None
    raw = body[span.value_start : span.value_end]
    if kind == "int":
        try:
            return int(_unquote(raw))
        except ValueError:
            return None
    if kind == "bool":
        value = _unquote(raw)
        return True if value == "yes" else False if value == "no" else None
    return _unquote(raw)


_LEGACY_READERS: dict[str, Callable[[str, str], Any]] = {
    "atom": _extract_scalar,
    "int": _extract_int,
    "bool": _extract_bool,
    "block": _extract_block,
}


def _read_decision_fields(body: str) -> dict[str, Any]:
    values: dict[str, Any] = {
        key: _LEGACY_READERS[kind](body, key) for key, kind in _LEGACY_FIELDS.items()
    }
    spans = _first_spans(body)
    for key, kind in _EXTENDED_FIELDS.items():
        values[key] = _top_level_value(body, spans.get(key), kind)
    return values


def _quote_if_needed(value: str) -> str:
    if needs_quotes(value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def _normalized_targets(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return value.split()
    return [str(item) for item in value]


def _render_scalar(kind: str, value: Any) -> str | None:
    if kind == "int":
        return None if value is None else str(value)
    if kind == "bool":
        return None if value is None else ("yes" if value else "no")
    text = "" if value is None else str(value)
    if not text:
        return None
    return text if kind == "atom" else _quote_if_needed(text)


def _render_list(value: Any) -> str | None:
    items = _normalized_targets(value)
    return " ".join(_quote_if_needed(item) for item in items) if items else None


def _model_value(decision: Decision, key: str) -> Any:
    value = getattr(decision, key)
    if DECISION_FIELD_KINDS[key] == "list":
        return _normalized_targets(value)
    return value


def decision_has_mission_timeout(decision: Decision) -> bool:
    """Whether the decision is a mission, counting unmodeled source spellings.

    ``days_mission_timeout = @constant`` cannot be read as an int, but it is
    preserved on save, so it still makes the decision a mission.
    """
    if decision.days_mission_timeout is not None:
        return True
    if not decision.raw_block:
        return False
    span = _first_spans(decision.raw_block).get("days_mission_timeout")
    return (
        span is not None
        and _top_level_value(decision.raw_block, span, "int") is None
    )


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
        # Missions and targeted decisions need not use any key above.
        "days_mission_timeout",
        "activation",
        "timeout_effect",
        "target_trigger",
        "selectable_mission",
    }
    clean = strip_comments(body)
    return bool(iter_assignment_blocks(body, "available")) or any(
        re.search(rf"\b{re.escape(key)}\s*=", clean) for key in decision_keys
    )


def _extract_category_metadata(body: str) -> dict[str, str]:
    """Read only direct category fields, never fields inside decisions."""
    metadata: dict[str, str] = {}
    for span in top_level_assignments(body):
        if span.key in metadata:
            continue
        if span.key == "icon" and not span.is_block:
            metadata[span.key] = body[span.value_start : span.value_end]
        elif (
            span.key in {"allowed", "visible"}
            and span.is_block
            and span.body_start is not None
            and span.body_end is not None
        ):
            metadata[span.key] = dedent_block_body(body[span.body_start : span.body_end])
    return metadata


def load_decisions_file(path: Path) -> list[DecisionCategory]:
    txt = path.read_text(encoding="utf-8", errors="ignore")
    categories: list[DecisionCategory] = []
    for span in top_level_assignments(txt):
        if not span.is_block or span.body_start is None or span.body_end is None:
            continue
        category_id = span.key
        category_body = txt[span.body_start : span.body_end]
        metadata = _extract_category_metadata(category_body)
        category = DecisionCategory(
            id=category_id,
            icon=metadata.get("icon", ""),
            allowed=metadata.get("allowed", ""),
            visible=metadata.get("visible", ""),
            path=path,
            raw_block=category_body.strip(),
        )
        for decision_id, decision_body in _iter_top_level_decision_blocks(category_body):
            decision = _parse_decision(decision_id, category_id, decision_body, path)
            category.decisions.append(decision)
        categories.append(category)
    return categories


def load_decision_categories_file(path: Path) -> list[DecisionCategory]:
    """Load category metadata from ``common/decisions/categories``."""

    txt = path.read_text(encoding="utf-8", errors="ignore")
    categories: list[DecisionCategory] = []
    for span in top_level_assignments(txt):
        if not span.is_block or span.body_start is None or span.body_end is None:
            continue
        body = txt[span.body_start : span.body_end]
        metadata = _extract_category_metadata(body)
        categories.append(
            DecisionCategory(
                id=span.key,
                icon=metadata.get("icon", ""),
                allowed=metadata.get("allowed", ""),
                visible=metadata.get("visible", ""),
                definition_path=path,
                definition_raw_block=body.strip(),
            )
        )
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
        path=path,
        raw_block=body.strip(),
        **_read_decision_fields(body),
    )


def serialize_decision(decision: Decision) -> str:
    if decision.raw_block:
        body = _patch_decision_body(decision, decision.raw_block)
        return f"\t{decision.id} = {{\n{_indent(body, 2)}\n\t}}"
    lines = [f"\t{decision.id} = {{"]
    for key in _RENDER_ORDER:
        kind = DECISION_FIELD_KINDS[key]
        value = getattr(decision, key)
        if kind == "block":
            if value:
                lines.append(f"\t\t{key} = {{")
                for line in value.strip().split("\n"):
                    lines.append(f"\t\t\t{line.strip()}")
                lines.append("\t\t}")
            continue
        rendered = _render_list(value) if kind == "list" else _render_scalar(kind, value)
        if rendered is not None:
            lines.append(
                f"\t\t{key} = {{ {rendered} }}" if kind == "list" else f"\t\t{key} = {rendered}"
            )
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
            if category is None:
                text = replace_assignment(text, span, None)
            elif category.raw_block and span.body_start is not None and span.body_end is not None:
                category_body = text[span.body_start : span.body_end]
                text = replace_assignment_body(
                    text, span, _patch_category_body(category, category_body)
                )
            else:
                text = replace_assignment(text, span, _serialize_category(category))
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
        body = _patch_category_body(category, category.raw_block)
        return f"{category.id} = {{\n{_indent(body, 1)}\n}}"
    parts = [f"{category.id} = {{"]
    for decision in category.decisions:
        parts.append(serialize_decision(decision))
        parts.append("")
    parts.append("}")
    return "\n".join(parts)


def _patch_decision_body(decision: Decision, body: str) -> str:
    if not decision.touched:
        return body
    # Patch only fields whose model value differs from what this source reads
    # as. Spellings the model cannot represent (``cost = @constant``, a
    # ``priority = { ... }`` block) therefore survive edits to other fields.
    current = _read_decision_fields(body)
    for key in _RENDER_ORDER:
        kind = DECISION_FIELD_KINDS[key]
        value = _model_value(decision, key)
        if value == current[key]:
            continue
        if kind == "block":
            body = set_block(body, key, value or None)
        elif kind == "list":
            body = set_block(body, key, _render_list(value))
        else:
            body = set_scalar(body, key, _render_scalar(kind, value))
    return body


def _patch_category_body(category: DecisionCategory, body: str) -> str:
    # Migrate invalid pre-0.4.2 output by removing presentation metadata from
    # the decision-content file. It is rendered through the category file.
    body = set_scalar(body, "icon", None)
    body = set_block(body, "allowed", None)
    body = set_block(body, "visible", None)
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
        if decision is None:
            body = replace_assignment(body, span, None)
        elif span.body_start is not None and span.body_end is not None and decision.raw_block:
            decision_body = body[span.body_start : span.body_end]
            body = replace_assignment_body(
                body, span, _patch_decision_body(decision, decision_body)
            )
        else:
            body = replace_assignment(body, span, serialize_decision(decision).strip())
    for decision in current.values():
        body = append_assignment(body, serialize_decision(decision).strip())
    return body


def serialize_decision_categories_file(
    categories: list[DecisionCategory], original: str = ""
) -> str:
    """Serialize category metadata without decision definitions."""

    if original:
        text = original
        current = {category.id: category for category in categories}
        for span in sorted(
            top_level_assignments(text), key=lambda item: item.start, reverse=True
        ):
            if not span.is_block:
                continue
            category = current.pop(span.key, None)
            if category is None:
                text = replace_assignment(text, span, None)
            elif span.body_start is not None and span.body_end is not None:
                body = text[span.body_start : span.body_end]
                text = replace_assignment_body(
                    text, span, _patch_category_definition_body(category, body)
                )
        for category in current.values():
            text = append_assignment(text, _serialize_category_definition(category))
        return text
    parts = [_serialize_category_definition(category) for category in categories]
    return "\n\n".join(parts) + ("\n" if parts else "")


def _serialize_category_definition(category: DecisionCategory) -> str:
    if category.definition_raw_block:
        body = _patch_category_definition_body(
            category, category.definition_raw_block
        )
        return f"{category.id} = {{\n{_indent(body, 1)}\n}}"
    parts = [f"{category.id} = {{"]
    if category.icon:
        parts.append(f"\ticon = {category.icon}")
    for key in ("allowed", "visible"):
        body = getattr(category, key)
        if body:
            parts.append(f"\t{key} = {{")
            parts.extend(f"\t\t{line.strip()}" for line in body.strip().splitlines())
            parts.append("\t}")
    parts.append("}")
    return "\n".join(parts)


def _patch_category_definition_body(category: DecisionCategory, body: str) -> str:
    if not category.touched:
        return body
    body = set_scalar(body, "icon", category.icon or None)
    body = set_block(body, "allowed", category.allowed or None)
    body = set_block(body, "visible", category.visible or None)
    return body


def write_decisions_file(path: Path, categories: list[DecisionCategory]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize_decisions_file(categories), encoding="utf-8")
    return path


def _indent(text: str, count: int) -> str:
    prefix = "\t" * count
    return "\n".join(prefix + line.rstrip() for line in text.strip().splitlines())
