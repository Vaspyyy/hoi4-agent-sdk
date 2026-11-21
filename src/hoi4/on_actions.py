"""
On-action system - read, create, and write HOI4 on_actions.

The SDK intentionally models on_actions conservatively: it preserves common
event lists and effect blocks, which is enough for startup scheduling hooks
without forcing agents to manually write common/on_actions files.

Reading is a query of the syntax tree in :mod:`hoi4.syntax`. A hook may repeat
``events``, ``random_events`` and ``effect`` blocks (the game runs all of
them); the model joins them in source order. Writing compares the model with
the hook's source text and touches only what changed, so comments, unknown
keys and repeated blocks stay byte-identical. An event list is edited entry by
entry: an entry that stays keeps its block and its comment, a dropped one is
cut out, a changed weight is rewritten in place and a new entry goes into the
block of the entry before it. Only a reordered list, or a changed ``effect``,
is written into the first block with the other blocks removed.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from .patching import (
    AssignmentSpan,
    append_assignment,
    dedent_block_body,
    replace_assignment,
    replace_assignment_body,
    same_block_body,
    set_block,
    sync_body,
)
from .script import normalize_block_body
from .syntax import Assignment, Block, Scalar, parse, require_well_formed
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


def _require_entries(key: str, values: list[str]) -> None:
    """Refuse a list entry with a stray brace: it would change the script around it."""

    for value in values:
        require_well_formed(str(value), f"an entry of '{key}'")


def serialize_on_action(action: OnAction) -> str:
    if action.raw_block:
        body = _patch_on_action_body(action, action.raw_block)
        return f"\t{action.id} = {{\n{_indent(body, 2)}\n\t}}"
    _require_entries("events", action.events)
    _require_entries("random_events", action.random_events)
    require_well_formed(action.effect, f"the effect of on_action '{action.id}'")
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
    """Every entry of every top-level ``key = { ... }``. See ``_entry_values``."""

    values: list[str] = []
    for item in _field_blocks(block, key):
        assert type(item.value) is Block
        for entry in item.value.items:
            values.extend(_entry_values(entry, key == "random_events") or ())
    return values


def _entry_values(entry: object, weighted: bool) -> tuple[str, ...] | None:
    """How one entry of an ``events`` or ``random_events`` block reads.

    A bare id is one value. In ``random_events`` a weighted entry
    ``10 = example.1`` is two values, ``"10", "example.1"``. Any other
    assignment (``chance_to_fire = 25``) is one value that holds the whole
    statement, so it is never taken for a weight and never split. ``None``
    for a block, which the list cannot hold.
    """

    if type(entry) is Scalar:
        return (entry.value,)
    if type(entry) is Assignment and type(entry.value) is Scalar:
        if weighted and entry.op == "=" and _is_number(entry.name):
            return (entry.name, entry.value.value)
        return (f"{entry.name} {entry.op} {entry.value.raw}",)
    return None


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

    return "\n".join(" = ".join(entry) for entry in _weighted_entries(values))


def _weighted_entries(values: list[str]) -> list[tuple[str, ...]]:
    entries: list[tuple[str, ...]] = []
    index = 0
    while index < len(values):
        value = values[index]
        if (
            _is_number(value)
            and index + 1 < len(values)
            # A whole statement (``chance_to_fire = 25``) is not an event id.
            and not _is_statement(values[index + 1])
        ):
            entries.append((value, values[index + 1]))
            index += 2
        else:
            entries.append((value,))
            index += 1
    return entries


def _is_statement(value: str) -> bool:
    """Whether a list value is a whole ``key = value`` statement, not one word."""

    items = parse(value).root.items
    return len(items) != 1 or type(items[0]) is not Scalar


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


def _set_entries(body: str, key: str, wanted: list[tuple[str, ...]]) -> str:
    """Make the ``key`` blocks of a hook hold ``wanted``, entry by entry.

    An entry that stays keeps its block, spelling and comment. A weighted
    entry whose weight changed (``10 = a.1`` to ``20 = a.1``) is the same
    entry: only its weight is rewritten. A new entry goes into the block of
    the entry before it (a new first entry into the block of the first one
    that stays). Only a changed order cannot be expressed that way and
    rewrites the list into the first block.
    """

    rendered = "\n".join(" = ".join(entry) for entry in wanted)
    held: list[tuple[tuple[str, ...], int, Assignment | None]] = []
    for block_index, item in enumerate(_field_blocks(parse(body).root, key)):
        assert type(item.value) is Block
        for entry in item.value.items:
            values = _entry_values(entry, key == "random_events")
            if values is None:
                return _set_field(body, key, rendered or None)
            held.append(
                (values, block_index, entry if len(values) == 2 and type(entry) is Assignment else None)
            )

    # Which entries stay as they are, and which wanted entries are still open.
    open_entries = Counter(wanted)
    stays: list[tuple[str, ...] | None] = []
    for value, _, _ in held:
        if open_entries[value] > 0:
            open_entries[value] -= 1
            stays.append(value)
        else:
            stays.append(None)
    spare = [entry for entry in wanted if open_entries[entry] > 0]
    reweighted: list[tuple[int, int, str]] = []
    for index, (value, _, node) in enumerate(held):
        if stays[index] is not None or node is None:
            continue
        twin = next((entry for entry in spare if len(entry) == 2 and entry[1] == value[1]), None)
        if twin is not None and open_entries[twin] > 0:
            open_entries[twin] -= 1
            spare.remove(twin)
            stays[index] = twin
            reweighted.append((node.key.start, node.key.end, twin[0]))

    # Walking the wanted list, the staying entries have to come in source order.
    flat = [(value, block) for value, (_, block, _) in zip(stays, held) if value is not None]
    if not flat:
        return _set_field(body, key, rendered or None)
    per_block: dict[int, list[tuple[str, ...]]] = {}
    pointer = 0
    current = flat[0][1]
    for target in wanted:
        if pointer < len(flat) and target == flat[pointer][0]:
            current = flat[pointer][1]
            pointer += 1
        per_block.setdefault(current, []).append(target)
    if pointer != len(flat):
        return _set_field(body, key, rendered or None)

    patched = body
    for start, end, weight in sorted(reweighted, reverse=True):
        patched = patched[:start] + weight + patched[end:]
    blocks = _field_blocks(parse(patched).root, key)
    for block_index, item in reversed(list(enumerate(blocks))):
        span = _span(item)
        staying = per_block.get(block_index)
        if not staying:
            patched = replace_assignment(patched, span, None)
            continue
        assert span.body_start is not None and span.body_end is not None
        synced = sync_body(
            patched[span.body_start : span.body_end],
            "\n".join(" = ".join(entry) for entry in staying),
        )
        if synced is None:
            return _set_field(body, key, rendered or None)
        patched = replace_assignment_body(patched, span, synced)
    return patched


def _patch_on_action_body(action: OnAction, body: str) -> str:
    effect, events, random_events = _read_fields(parse(body).root)
    # Only a changed field is checked and written: an untouched one keeps its
    # bytes, whatever they are.
    if action.events != events:
        _require_entries("events", action.events)
        body = _set_entries(body, "events", [(event_id,) for event_id in action.events])
    if action.random_events != random_events:
        _require_entries("random_events", action.random_events)
        body = _set_entries(body, "random_events", _weighted_entries(action.random_events))
    if not same_block_body(effect, action.effect):
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
