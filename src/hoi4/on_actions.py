"""
On-action system - read, create, and write HOI4 on_actions.

The SDK intentionally models on_actions conservatively: it preserves common
event lists and effect blocks, which is enough for startup scheduling hooks
without forcing agents to manually write common/on_actions files.
"""

from __future__ import annotations

from pathlib import Path

from .parser import TokenType, find_assignment_block, tokenize
from .patching import (
    AssignmentSpan,
    append_assignment,
    dedent_block_body,
    replace_assignment,
    replace_assignment_body,
    set_block,
    top_level_assignments,
)
from .script import normalize_block_body
from .types import OnAction


def load_on_actions_file(path: Path) -> list[OnAction]:
    txt = path.read_text(encoding="utf-8", errors="ignore")
    root = find_assignment_block(txt, "on_actions")
    body = root[0] if root else txt
    actions: list[OnAction] = []
    occurrences: dict[str, int] = {}
    for action_id, action_body in _top_level_blocks(body):
        source_occurrence = occurrences.get(action_id, 0)
        occurrences[action_id] = source_occurrence + 1
        action = OnAction(
            id=action_id,
            effect=_extract_block(action_body, "effect"),
            events=_extract_list(action_body, "events"),
            random_events=_extract_list(action_body, "random_events"),
            path=path,
            raw_block=action_body.strip(),
            source_path=path,
            source_occurrence=source_occurrence,
        )
        actions.append(action)
    return actions


def serialize_on_action(action: OnAction) -> str:
    if action.raw_block:
        body = _patch_on_action_body(action, action.raw_block)
        return f"\t{action.id} = {{\n{_indent(body, 2)}\n\t}}"
    parts = [f"\t{action.id} = {{"]
    if action.events:
        parts.append("\t\tevents = {")
        for event_id in action.events:
            parts.append(f"\t\t\t{event_id}")
        parts.append("\t\t}")
    if action.random_events:
        parts.append("\t\trandom_events = {")
        for event_id in action.random_events:
            parts.append(f"\t\t\t{event_id}")
        parts.append("\t\t}")
    effect = normalize_block_body(action.effect)
    if effect:
        parts.append("\t\teffect = {")
        for line in effect.splitlines():
            if line.strip():
                parts.append(f"\t\t\t{line.strip()}")
        parts.append("\t\t}")
    parts.append("\t}")
    return "\n".join(parts)


def serialize_on_actions_file(actions: list[OnAction], original: str = "") -> str:
    if original:
        root_span = next(
            (
                span
                for span in top_level_assignments(original)
                if span.key == "on_actions"
                and span.is_block
                and span.body_start is not None
                and span.body_end is not None
            ),
            None,
        )
        if root_span is not None:
            assert root_span.body_start is not None
            assert root_span.body_end is not None
            body = original[root_span.body_start : root_span.body_end]
            return replace_assignment_body(original, root_span, _patch_actions_body(actions, body))
        return _patch_actions_body(actions, original)
    parts = ["on_actions = {"]
    for action in actions:
        parts.append(serialize_on_action(action))
        parts.append("")
    parts.append("}")
    parts.append("")
    return "\n".join(parts)


def write_on_actions_file(path: Path, actions: list[OnAction]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize_on_actions_file(actions), encoding="utf-8")
    return path


def _extract_block(text: str, key: str) -> str:
    match = find_assignment_block(text, key)
    return dedent_block_body(match[0]) if match else ""


def _extract_list(text: str, key: str) -> list[str]:
    body = _extract_block(text, key)
    if not body:
        return []
    return [
        token.value
        for token in tokenize(body)
        if token.type in {TokenType.IDENT, TokenType.NUMBER, TokenType.STRING}
    ]


def _top_level_blocks(text: str) -> list[tuple[str, str]]:
    return [
        (span.key, text[span.body_start : span.body_end])
        for span in top_level_assignments(text)
        if span.is_block and span.body_start is not None and span.body_end is not None
    ]


def _patch_on_action_body(action: OnAction, body: str) -> str:
    if not action.touched:
        return body
    body = set_block(body, "events", "\n".join(action.events) or None)
    # The public list omits assignment operators in weighted random_events.
    # Keep the source block intact unless the caller changed that list.
    if action.random_events != _extract_list(body, "random_events"):
        body = set_block(body, "random_events", "\n".join(action.random_events) or None)
    body = set_block(body, "effect", action.effect or None)
    return body


def _patch_actions_body(actions: list[OnAction], body: str) -> str:
    spans = [span for span in top_level_assignments(body) if span.is_block]
    source_actions = {
        (action.id, action.source_occurrence): action
        for action in actions
        if action.source_path is not None
        and action.path is not None
        and action.source_path.resolve(strict=False) == action.path.resolve(strict=False)
        and action.source_occurrence >= 0
    }
    occurrences: dict[str, int] = {}
    matches: list[tuple[AssignmentSpan, OnAction | None]] = []
    for span in spans:
        source_occurrence = occurrences.get(span.key, 0)
        occurrences[span.key] = source_occurrence + 1
        matches.append((span, source_actions.get((span.key, source_occurrence))))

    for span, action in reversed(matches):
        if action is None:
            body = replace_assignment(body, span, None)
        elif span.body_start is not None and span.body_end is not None:
            action_body = body[span.body_start : span.body_end]
            body = replace_assignment_body(
                body, span, _patch_on_action_body(action, action_body)
            )
        else:
            body = replace_assignment(body, span, serialize_on_action(action).strip())
    for action in actions:
        is_source_occurrence = (
            action.source_path is not None
            and action.path is not None
            and action.source_path.resolve(strict=False) == action.path.resolve(strict=False)
            and action.source_occurrence >= 0
        )
        if is_source_occurrence:
            continue
        body = append_assignment(body, serialize_on_action(action).strip())
    return body


def _indent(text: str, count: int) -> str:
    prefix = "\t" * count
    return "\n".join(f"{prefix}{line.rstrip()}" for line in text.strip().splitlines())
