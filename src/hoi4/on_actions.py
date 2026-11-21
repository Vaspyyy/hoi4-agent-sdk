"""
On-action system - read, create, and write HOI4 on_actions.

The SDK intentionally models on_actions conservatively: it preserves common
event lists and effect blocks, which is enough for startup scheduling hooks
without forcing agents to manually write common/on_actions files.

Reading is a query of the syntax tree in :mod:`hoi4.syntax`. A hook may repeat
``events``, ``random_events`` and ``effect`` blocks (the game runs all of
them); the model joins them in source order. Writing compares the model with
the hook's source text and touches only the blocks whose content changed, so
comments, unknown keys and repeated blocks of unchanged fields stay
byte-identical. A changed field is written into its first block and its other
blocks are removed.
"""

from __future__ import annotations

from pathlib import Path

from .patching import (
    AssignmentSpan,
    append_assignment,
    block_bodies_equivalent,
    dedent_block_body,
    replace_assignment,
    replace_assignment_body,
    set_block,
)
from .script import normalize_block_body
from .syntax import Assignment, Block, Scalar, parse
from .textio import read_text, write_text
from .types import OnAction


def load_on_actions_file(path: Path) -> list[OnAction]:
    actions: list[OnAction] = []
    occurrences: dict[str, int] = {}
    for container in _containers(parse(read_text(path)).root):
        for item in _action_items(container):
            block = item.block
            assert block is not None
            source_occurrence = occurrences.get(item.name, 0)
            occurrences[item.name] = source_occurrence + 1
            effect, events, random_events = _read_fields(block)
            actions.append(
                OnAction(
                    id=item.name,
                    effect=effect,
                    events=events,
                    random_events=random_events,
                    path=path,
                    raw_block=block.body.strip(),
                    source_path=path,
                    source_occurrence=source_occurrence,
                )
            )
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
        for line in _render_random_events(action.random_events).splitlines():
            parts.append(f"\t\t\t{line}")
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
        roots = [
            _span(item)
            for item in parse(original).root.assignments("on_actions")
            if item.op == "=" and type(item.value) is Block
        ]
        if not roots:
            return _patch_actions_body(actions, original, {}, append=True)
        occurrences: dict[str, int] = {}
        bodies: list[str] = []
        for index, span in enumerate(roots):
            assert span.body_start is not None and span.body_end is not None
            body = original[span.body_start : span.body_end]
            bodies.append(
                _patch_actions_body(actions, body, occurrences, append=index == len(roots) - 1)
            )
        text = original
        for span, body in reversed(list(zip(roots, bodies))):
            text = replace_assignment_body(text, span, body)
        return text
    parts = ["on_actions = {"]
    for action in actions:
        parts.append(serialize_on_action(action))
        parts.append("")
    parts.append("}")
    parts.append("")
    return "\n".join(parts)


def write_on_actions_file(path: Path, actions: list[OnAction]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text(path, serialize_on_actions_file(actions))
    return path


# ── reading ─────────────────────────────────────────────────────


def _containers(root: Block) -> list[Block]:
    """The ``on_actions = { ... }`` blocks of a file, or the file itself without one."""

    roots = [
        item.value
        for item in root.assignments("on_actions")
        if item.op == "=" and type(item.value) is Block
    ]
    return roots or [root]


def _action_items(container: Block) -> list[Assignment]:
    return [
        item
        for item in container.assignments()
        if item.op == "=" and type(item.value) is Block
    ]


def _field_blocks(block: Block, key: str) -> list[Assignment]:
    return [
        item for item in block.assignments(key) if item.op == "=" and type(item.value) is Block
    ]


def _read_fields(block: Block) -> tuple[str, list[str], list[str]]:
    effects = [
        dedent_block_body(item.value.body)
        for item in _field_blocks(block, "effect")
        if type(item.value) is Block
    ]
    effect = "\n".join(body for body in effects if body)
    return effect, _read_list(block, "events"), _read_list(block, "random_events")


def _read_list(block: Block, key: str) -> list[str]:
    """Every entry of every top-level ``key = { ... }``.

    A weighted entry ``10 = example.1`` reads as ``"10", "example.1"``.
    """

    values: list[str] = []
    for item in _field_blocks(block, key):
        assert type(item.value) is Block
        for entry in item.value.items:
            if type(entry) is Scalar:
                values.append(entry.value)
            elif type(entry) is Assignment and type(entry.value) is Scalar:
                values.extend((entry.name, entry.value.value))
    return values


def _span(item: Assignment) -> AssignmentSpan:
    block = item.block
    assert block is not None
    return AssignmentSpan(
        item.name, item.start, item.end, item.value.start, item.value.end, True,
        block.body_start, block.body_end, item.op,
    )


# ── patching ────────────────────────────────────────────────────


def _render_random_events(values: list[str]) -> str:
    """Render a flattened ``random_events`` list, restoring ``weight = event`` pairs.

    Event IDs are never plain numbers, so a number followed by another entry is
    a weight.
    """

    lines: list[str] = []
    index = 0
    while index < len(values):
        value = values[index]
        if _is_number(value) and index + 1 < len(values):
            lines.append(f"{value} = {values[index + 1]}")
            index += 2
        else:
            lines.append(value)
            index += 1
    return "\n".join(lines)


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def _set_field(body: str, key: str, value: str | None) -> str:
    """Write ``value`` into the first ``key`` block and drop the hook's other ones."""

    spans = [_span(item) for item in _field_blocks(parse(body).root, key)]
    for span in reversed(spans[1:]):
        body = replace_assignment(body, span, None)
    return set_block(body, key, value)


def _patch_on_action_body(action: OnAction, body: str) -> str:
    effect, events, random_events = _read_fields(parse(body).root)
    if action.events != events:
        body = _set_field(body, "events", "\n".join(action.events) or None)
    if action.random_events != random_events:
        body = _set_field(
            body, "random_events", _render_random_events(action.random_events) or None
        )
    if not block_bodies_equivalent(effect, action.effect):
        body = _set_field(body, "effect", normalize_block_body(action.effect) or None)
    return body


def _is_source_occurrence(action: OnAction) -> bool:
    return (
        action.source_path is not None
        and action.path is not None
        and action.source_path.resolve(strict=False) == action.path.resolve(strict=False)
        and action.source_occurrence >= 0
    )


def _patch_actions_body(
    actions: list[OnAction],
    body: str,
    occurrences: dict[str, int],
    *,
    append: bool,
) -> str:
    """Patch the hooks of one container body.

    ``occurrences`` counts hooks per ID across the containers already patched,
    so the n-th ``on_startup`` of the file maps to the model's n-th occurrence.
    """

    source_actions = {
        (action.id, action.source_occurrence): action
        for action in actions
        if _is_source_occurrence(action)
    }
    matches: list[tuple[AssignmentSpan, OnAction | None]] = []
    for item in _action_items(parse(body).root):
        source_occurrence = occurrences.get(item.name, 0)
        occurrences[item.name] = source_occurrence + 1
        matches.append((_span(item), source_actions.get((item.name, source_occurrence))))

    for span, action in reversed(matches):
        if action is None:
            body = replace_assignment(body, span, None)
        else:
            assert span.body_start is not None and span.body_end is not None
            action_body = body[span.body_start : span.body_end]
            body = replace_assignment_body(
                body, span, _patch_on_action_body(action, action_body)
            )
    if append:
        for action in actions:
            if not _is_source_occurrence(action):
                body = append_assignment(body, serialize_on_action(action).strip())
    return body


def _indent(text: str, count: int) -> str:
    prefix = "\t" * count
    return "\n".join(f"{prefix}{line.rstrip()}" for line in text.strip().splitlines())
