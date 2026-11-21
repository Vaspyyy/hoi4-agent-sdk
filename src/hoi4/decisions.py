"""
Decision system - read, create, and write HOI4 decisions.

Decision files live in common/decisions/*.txt. Category metadata lives
separately in common/decisions/categories/*.txt. The SDK models both while
preserving common trigger/effect blocks as raw Paradox script strings.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

from ._scalar import needs_quotes
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
from .syntax import Scalar, parse, unquote
from .textio import read_text, write_text
from .types import Decision, DecisionCategory

# Keys a category block may carry for itself. Every other ``key = { ... }``
# directly inside a category block of a ``common/decisions`` file is a
# decision, the way the game reads it. (Category presentation belongs in
# ``common/decisions/categories``; pre-0.4.2 SDK output put ``icon``,
# ``allowed`` and ``visible`` here, see ``_patch_category_body``.)
_CATEGORY_METADATA_KEYS = frozenset(
    {
        "allowed",
        "visible",
        "picture",
        "icon",
        "priority",
        "visibility_type",
        "on_map_area",
        "custom_icon",
        "scripted_gui",
    }
)

# Field kinds: "int", "bool", "str" (scalar, quoted when needed), "atom" (bare
# scalar), "list" (``key = { A B }``), and "block" (raw Paradox script).
# Every field is read from the decision's own top-level assignments only, so a
# nested key such as ``complete_effect = { cost = 5 }`` or
# ``ai_will_do = { modifier = { ... } }`` never leaks into the model.
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


def _first_spans(body: str) -> dict[str, AssignmentSpan]:
    spans: dict[str, AssignmentSpan] = {}
    for span in top_level_assignments(body):
        spans.setdefault(span.key, span)
    return spans


def _parse_atom_list(body: str) -> list[str]:
    """Read ``A B "C"``; anything structured is not a plain list."""
    doc = parse(body)
    if doc.problems:
        return []
    values: list[str] = []
    for item in doc.root.items:
        if type(item) is not Scalar:
            return []
        values.append(item.value)
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
            return int(unquote(raw))
        except ValueError:
            return None
    if kind == "bool":
        value = unquote(raw)
        return True if value == "yes" else False if value == "no" else None
    return unquote(raw)


def _read_decision_fields(body: str) -> dict[str, Any]:
    spans = _first_spans(body)
    return {
        key: _top_level_value(body, spans.get(key), kind)
        for key, kind in DECISION_FIELD_KINDS.items()
    }


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


def decision_has_cost(decision: Decision) -> bool:
    """Whether the decision charges a cost, counting unmodeled spellings.

    ``cost = @constant`` cannot be read as an int but is preserved on save,
    so it still counts as a cost.
    """
    if decision.cost:
        return True
    if decision.cost is not None or not decision.raw_block:
        return False
    span = _first_spans(decision.raw_block).get("cost")
    return span is not None and _top_level_value(decision.raw_block, span, "int") is None


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


def _is_block_span(span: AssignmentSpan) -> bool:
    return (
        span.is_block
        and span.tag is None
        and span.body_start is not None
        and span.body_end is not None
    )


def _decision_spans(category_body: str) -> list[AssignmentSpan]:
    """Return the decisions directly inside a category body, in source order."""

    return [
        span
        for span in top_level_assignments(category_body)
        if _is_block_span(span) and span.key not in _CATEGORY_METADATA_KEYS
    ]


def _block_body(text: str, span: AssignmentSpan) -> str:
    assert span.body_start is not None and span.body_end is not None
    return text[span.body_start : span.body_end]


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
    """Load the categories of a ``common/decisions`` file with their decisions.

    A category block may appear more than once in one file: the game reopens
    the category. All its blocks become one :class:`DecisionCategory` holding
    the decisions of every block in source order.
    """

    txt = read_text(path)
    categories: dict[str, DecisionCategory] = {}
    bodies: dict[str, list[str]] = {}
    for span in top_level_assignments(txt):
        if not _is_block_span(span):
            continue
        category_id = span.key
        category_body = _block_body(txt, span)
        metadata = _extract_category_metadata(category_body)
        category = categories.get(category_id)
        if category is None:
            category = DecisionCategory(id=category_id, path=path)
            categories[category_id] = category
        category.icon = category.icon or metadata.get("icon", "")
        category.allowed = category.allowed or metadata.get("allowed", "")
        category.visible = category.visible or metadata.get("visible", "")
        bodies.setdefault(category_id, []).append(category_body.strip())
        for decision_span in _decision_spans(category_body):
            category.decisions.append(
                _parse_decision(
                    decision_span.key,
                    category_id,
                    _block_body(category_body, decision_span),
                    path,
                )
            )
    for category_id, category in categories.items():
        category.raw_block = "\n\n".join(body for body in bodies[category_id] if body)
    return list(categories.values())


def load_decision_categories_file(path: Path) -> list[DecisionCategory]:
    """Load category metadata from ``common/decisions/categories``."""

    txt = read_text(path)
    categories: list[DecisionCategory] = []
    for span in top_level_assignments(txt):
        if not _is_block_span(span):
            continue
        body = _block_body(txt, span)
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
    """Render a ``common/decisions`` file, patching ``original`` in place.

    Only the spans of decisions that changed are replaced. A category that
    appears in several blocks of ``original`` keeps every block; each decision
    is patched in the block that holds it, and new decisions go to the
    category's last block.
    """

    if original:
        current = {category.id: category for category in categories}
        blocks: dict[str, list[AssignmentSpan]] = {}
        for span in top_level_assignments(original):
            if _is_block_span(span):
                blocks.setdefault(span.key, []).append(span)
        edits: list[tuple[AssignmentSpan, str | None]] = []
        for category_id, spans in blocks.items():
            category = current.pop(category_id, None)
            if category is None:
                # A category that left this file (moved or replaced): remove
                # its blocks, but leave blocks that hold no decision alone.
                edits.extend(
                    (span, None)
                    for span in spans
                    if _decision_spans(_block_body(original, span))
                )
                continue
            pool = list(category.decisions)
            for index, span in enumerate(spans):
                body = _block_body(original, span)
                patched = _patch_category_body(
                    category, body, pool, append=index == len(spans) - 1
                )
                if patched != body:
                    edits.append((span, patched))
        text = original
        for span, new_body in sorted(edits, key=lambda edit: edit[0].start, reverse=True):
            if new_body is None:
                text = replace_assignment(text, span, None)
            else:
                text = replace_assignment_body(text, span, new_body)
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


def _standalone_decision(decision: Decision) -> str:
    """Render one decision with no outer indentation, ready to be placed in a block."""

    return textwrap.dedent(serialize_decision(decision)).strip()


def _patch_category_body(
    category: DecisionCategory,
    body: str,
    pool: list[Decision] | None = None,
    *,
    append: bool = True,
) -> str:
    """Patch one category block's body.

    ``pool`` holds the category's decisions not yet placed in an earlier block
    of the same category; each decision found here is taken from it. With
    ``append``, decisions still left in the pool are added at the end.
    """

    if pool is None:
        pool = list(category.decisions)
    if category.touched:
        # The game reads a category's presentation only from
        # ``common/decisions/categories``. When the category itself is edited,
        # presentation keys that sit in the decision-content file move there.
        # An edit to one of its decisions leaves them where they are.
        body = set_scalar(body, "icon", None)
        body = set_block(body, "allowed", None)
        body = set_block(body, "visible", None)
    placed: list[tuple[AssignmentSpan, Decision | None]] = []
    for span in _decision_spans(body):
        index = next(
            (i for i, decision in enumerate(pool) if decision.id == span.key), None
        )
        placed.append((span, None if index is None else pool.pop(index)))
    for span, decision in reversed(placed):
        if decision is None:
            body = replace_assignment(body, span, None)
        elif decision.raw_block:
            decision_body = _block_body(body, span)
            body = replace_assignment_body(
                body, span, _patch_decision_body(decision, decision_body)
            )
        else:
            body = replace_assignment(body, span, _standalone_decision(decision))
    if append:
        for decision in pool:
            body = append_assignment(body, _standalone_decision(decision))
        pool.clear()
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
            if not _is_block_span(span):
                continue
            category = current.pop(span.key, None)
            if category is None:
                text = replace_assignment(text, span, None)
            else:
                body = _block_body(text, span)
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
    # Patch only fields whose value differs from what the source reads as, so
    # setting a field to its current value (an empty ``visible = { }``
    # included) changes nothing.
    current = _extract_category_metadata(body)
    if category.icon != current.get("icon", ""):
        body = set_scalar(body, "icon", category.icon or None)
    for key in ("allowed", "visible"):
        value = getattr(category, key)
        if value != current.get(key, ""):
            body = set_block(body, key, value or None)
    return body


def write_decisions_file(path: Path, categories: list[DecisionCategory]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text(path, serialize_decisions_file(categories))
    return path


def _indent(text: str, count: int) -> str:
    prefix = "\t" * count
    return "\n".join(prefix + line.rstrip() for line in text.strip().splitlines())
