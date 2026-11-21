"""
State management - read, modify, and write HOI4 state files.

States live in history/states/*.txt. Each file defines one state with
an owner, cores, manpower, victory points, and province assignments.
"""

from __future__ import annotations

import re
import shutil
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from .map_editing import list_ints, patch_int_list_body
from .parser import PdxNode, parse_pdx, serialize_pdx
from .patching import (
    AssignmentSpan,
    block_bodies_equivalent,
    replace_assignment,
    scalar_values_equivalent,
    set_block,
    top_level_assignments,
)
from .syntax import Assignment, Block, parse, require_value, require_well_formed
from .textio import encode_text, read_text
from .types import State


def _state_assignment(block: Block) -> Assignment | None:
    """Return the file's top-level ``state = { ... }`` statement."""

    for item in block.assignments("state"):
        if item.op == "=" and item.block is not None:
            return item
    return None


def _int_or_none(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def state_id_of(text: str) -> int | None:
    """Return the ``id`` of the state a state file defines, or ``None``.

    Only the ``id`` directly inside the top-level ``state`` block counts: an
    ``id`` in a comment or in a nested block does not identify the state.
    """

    state = _state_assignment(parse(text).root)
    if state is None or state.block is None:
        return None
    return _int_or_none(state.block.scalar("id"))


def _filename_state_id(path: Path) -> int | None:
    digits = ""
    for char in path.stem.lstrip():
        if not char.isdigit():
            break
        digits += char
    return int(digits) if digits else None


def find_state_file(states_dir: Path, state_id: int) -> Optional[Path]:
    """Return the file in ``states_dir`` whose state block has ``id = state_id``.

    Files whose name starts with the ID are checked first, but the file's own
    ``id`` always decides, because file names are not authoritative.
    """

    if not states_dir.is_dir():
        return None
    paths = sorted(states_dir.glob("*.txt"))
    likely = [path for path in paths if _filename_state_id(path) == state_id]
    others = [path for path in paths if _filename_state_id(path) != state_id]
    for path in likely + others:
        if state_id_of(read_text(path)) == state_id:
            return path
    return None


def ensure_state_in_mod(
    mod_root: Path, hoi4_install: Optional[Path], state_id: int
) -> Optional[Path]:
    mod_states = mod_root / "history" / "states"
    mod_states.mkdir(parents=True, exist_ok=True)

    f = find_state_file(mod_states, state_id)
    if f:
        return f

    if not hoi4_install:
        return None
    vanilla_states = hoi4_install / "history" / "states"
    vf = find_state_file(vanilla_states, state_id)
    if not vf:
        return None

    dst = mod_states / vf.name
    shutil.copy2(vf, dst)
    return dst


def read_state(state_file: Path, text: str | None = None) -> State:
    txt = text if text is not None else read_text(state_file)
    state_assignment = _state_assignment(parse(txt).root)
    state_block = state_assignment.block if state_assignment is not None else None
    if state_block is None:
        return State(id=0, path=state_file, raw_text=txt)

    state_obj = State(
        id=_int_or_none(state_block.scalar("id")) or 0,
        path=state_file,
        raw_text=txt,
        source_path=state_file,
    )

    name = state_block.scalar("name")
    if name:
        state_obj.name = name

    category = state_block.scalar("state_category")
    if category:
        state_obj.state_category = category

    demilitarized = state_block.scalar("is_demilitarized_zone")
    if demilitarized:
        state_obj.is_demilitarized_zone = demilitarized == "yes"

    state_obj.impassable = state_block.scalar("impassable") == "yes"

    manpower = state_block.scalar("manpower")
    if manpower:
        state_obj.manpower = manpower

    factor = state_block.scalar("buildings_max_level_factor")
    if factor:
        state_obj.buildings_max_level_factor = factor

    local_supplies = state_block.scalar("local_supplies")
    if local_supplies:
        state_obj.local_supplies = local_supplies

    resources = state_block.block("resources")
    if resources is not None:
        for item in resources.assignments():
            value = item.scalar
            if value is not None:
                state_obj.resources[item.name] = _coerce_scalar(value)

    provinces = state_block.block("provinces")
    if provinces is not None:
        state_obj.provinces = [
            int(scalar.value) for scalar in provinces.values() if scalar.value.isdigit()
        ]

    history = state_block.block("history")
    if history is not None:
        state_obj.history = textwrap.dedent(history.body).strip()

        # Buildings are legal in the history block, not at state scope, and
        # dated history blocks have their own: only the direct child counts.
        buildings = history.block("buildings")
        if buildings is not None:
            state_obj.buildings = textwrap.dedent(buildings.body).strip()

        owner = history.scalar("owner")
        if owner:
            state_obj.owner = owner

        state_obj.cores = [
            item.scalar for item in history.assignments("add_core_of") if item.scalar
        ]

        vp_parts: list[str] = []
        for item in history.assignments("victory_points"):
            block = item.block
            if block is not None:
                vp_parts.extend(scalar.value for scalar in block.values())
            elif item.scalar:
                vp_parts.append(item.scalar)
        if vp_parts:
            state_obj.victory_points = " ".join(vp_parts)

    return state_obj


def _coerce_scalar(value: str) -> str | int | float:
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


# ── Source-preserving edits ─────────────────────────────────────────────
#
# Every edit below works on the text of one block body and replaces only the
# span that changed. New statements are inserted on their own line, indented
# like their siblings (or one step deeper than the line that opens the block),
# using the file's own indentation step.


_DATE_KEY_RE = re.compile(r"\d{1,4}\.\d{1,2}\.\d{1,2}(?:\.\d{1,2})?")


@dataclass(frozen=True)
class _Layout:
    """Indentation of one block: of the line that opens it, and one step."""

    outer: str
    unit: str

    @property
    def inner(self) -> str:
        return self.outer + self.unit


def _indent_unit(text: str) -> str:
    """Return the file's indentation step: a tab, or its smallest space indent."""

    smallest: int | None = None
    for line in text.split("\n"):
        if not line[:1].isspace() or not line.strip():
            continue
        if line[0] == "\t":
            return "\t"
        width = len(line) - len(line.lstrip(" "))
        if width and (smallest is None or width < smallest):
            smallest = width
    return " " * smallest if smallest else "\t"


def _own_line_indent(text: str, offset: int, *, file_start: bool = False) -> str | None:
    """Return the indentation before ``offset`` if nothing else precedes it on its line.

    In a block body the text before the first line break belongs to the line
    that opens the block, so it only counts when ``file_start`` is true.
    """

    line_start = text.rfind("\n", 0, offset) + 1
    if line_start == 0 and not file_start:
        return None
    prefix = text[line_start:offset]
    return prefix if not prefix.strip() else None


def _item_indent(body: str, layout: _Layout) -> str:
    """Return the indentation of statements inside a block body."""

    for item in parse(body).root.items:
        indent = _own_line_indent(body, item.start)
        if indent is not None:
            return indent
    return layout.inner


def _child_layout(body: str, item: Assignment | AssignmentSpan, layout: _Layout) -> _Layout:
    """Return the layout of a block that is a statement of ``body``."""

    indent = _own_line_indent(body, item.start)
    return _Layout(indent if indent is not None else _item_indent(body, layout), layout.unit)


def _continue_lines(statement: str, indent: str) -> str:
    lines = statement.split("\n")
    return lines[0] + "".join("\n" + (indent + line if line else "") for line in lines[1:])


def _render_block(key: str, body: str, unit: str) -> str:
    """Render ``key = { ... }`` relative to its own line's indentation."""

    cleaned = textwrap.dedent(body).strip()
    if not cleaned:
        return f"{key} = {{ }}"
    lines = "\n".join(unit + line.rstrip() if line.strip() else "" for line in cleaned.splitlines())
    return f"{key} = {{\n{lines}\n}}"


def _line_end_after(body: str, offset: int) -> int | None:
    line_end = body.find("\n", offset)
    return None if line_end < 0 else line_end


def _insert_statement(
    body: str,
    statement: str,
    layout: _Layout,
    *,
    after_keys: Sequence[str] = (),
    first: bool = False,
    before_dated: bool = False,
) -> str:
    """Insert ``statement`` into a block body on a line of its own.

    It goes after the last statement with the first of ``after_keys`` that is
    present, else before the first statement (``first``) or the first dated
    history block (``before_dated``), else at the end. A block that was empty
    or written on one line is spread over lines, so the result is always
    cleanly indented.
    """

    items = parse(body).root.items
    if not items:
        return "\n" + layout.inner + _continue_lines(statement, layout.inner) + "\n" + layout.outer
    if "\n" not in body:
        # ``history = { owner = X }``: one statement per line from now on.
        body = "".join("\n" + layout.inner + item.raw for item in items) + "\n" + layout.outer
        items = parse(body).root.items
    inner = _item_indent(body, layout)
    rendered = _continue_lines(statement, inner)
    for key in after_keys:
        anchors = [item for item in items if type(item) is Assignment and item.name == key]
        if not anchors:
            continue
        position = _line_end_after(body, anchors[-1].end)
        if position is not None:
            return body[:position] + "\n" + inner + rendered + body[position:]
        break
    targets: list[int] = []
    if first:
        targets.append(items[0].start)
    if before_dated:
        targets.extend(
            item.start
            for item in items
            if type(item) is Assignment and _DATE_KEY_RE.fullmatch(item.name)
        )
    for target in targets:
        if _own_line_indent(body, target) is not None:
            line_start = body.rfind("\n", 0, target) + 1
            return body[:line_start] + inner + rendered + "\n" + body[line_start:]
    content = body.rstrip(" \t\n")
    trailing = body[len(content):]
    if "\n" in trailing:
        return content + "\n" + inner + rendered + trailing
    return content + "\n" + inner + rendered + "\n" + layout.outer


def _find_direct_assignment(
    text: str,
    key: str,
    *,
    block: bool | None = None,
) -> AssignmentSpan | None:
    for span in top_level_assignments(text):
        if span.key == key and (block is None or span.is_block is block):
            return span
    return None


def _assignment_body(text: str, span: AssignmentSpan | None) -> str:
    if span is None or span.body_start is None or span.body_end is None:
        return ""
    return text[span.body_start : span.body_end]


def _replace_body(text: str, span: AssignmentSpan, body: str) -> str:
    assert span.body_start is not None and span.body_end is not None
    return text[: span.body_start] + body + text[span.body_end :]


def _scalar_text(value: str | int | float) -> str:
    """Spell a state property for script; numbers may be ``int`` or ``float``."""

    if isinstance(value, bool):
        raise TypeError(f"Expected a number or text, got {value!r}")
    if isinstance(value, float):
        # ``repr`` is the shortest exact spelling; avoid exponents like 1e-05.
        text = repr(value)
        if "e" in text or "E" in text:
            text = format(value, "f").rstrip("0")
            text = text + "0" if text.endswith(".") else text
        return text
    return str(value).strip()


def _quote_like(existing: str, value: str) -> str:
    if existing.startswith('"') and existing.endswith('"'):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _set_direct_scalar(
    text: str,
    key: str,
    value: str | None,
    layout: _Layout,
    *,
    after_keys: Sequence[str] = (),
    first: bool = False,
    before_dated: bool = False,
) -> str:
    spans = [
        span for span in top_level_assignments(text) if span.key == key and not span.is_block
    ]
    if value is None:
        result = text
        for span in reversed(spans):
            result = replace_assignment(result, span, None)
        return result
    # A value of several words, or one with a brace, would not stay the value.
    require_value(value, f"the value of '{key}'")
    if spans:
        span = spans[0]
        existing = text[span.value_start : span.value_end]
        replacement = _quote_like(existing, value)
        if replacement == existing:
            return text
        return text[: span.value_start] + replacement + text[span.value_end :]
    return _insert_statement(
        text, f"{key} = {value}", layout,
        after_keys=after_keys, first=first, before_dated=before_dated,
    )


def _set_direct_block(
    text: str,
    key: str,
    body: str | None,
    layout: _Layout,
    *,
    after_keys: Sequence[str] = (),
    before_dated: bool = False,
) -> str:
    """Set the body of ``key = { ... }``.

    An existing block is edited statement by statement (``patching.set_block``),
    so its comments and unchanged statements keep their bytes. Raises
    :class:`~hoi4.syntax.ParseError` for a body with a stray or missing brace.
    """

    named = [span for span in top_level_assignments(text) if span.key == key]
    spans = [span for span in named if span.is_block]
    if body is None:
        result = text
        for span in reversed(spans):
            result = replace_assignment(result, span, None)
        return result
    cleaned = textwrap.dedent(body).strip()
    # A body with a stray or missing brace would change the script around it.
    require_well_formed(cleaned, f"the new body of '{key}'")
    if not spans:
        return _insert_statement(
            text, _render_block(key, body, layout.unit), layout,
            after_keys=after_keys, before_dated=before_dated,
        )
    span = spans[0]
    current = _assignment_body(text, span)
    if not cleaned:
        return text if not current.strip() else _replace_body(text, span, " ")
    if named[0] is span and span.tag is None:
        return set_block(text, key, cleaned)
    if block_bodies_equivalent(current, body):
        return text
    child = _child_layout(text, span, layout)
    lines = "".join(
        "\n" + (child.inner + line.rstrip() if line.strip() else "")
        for line in cleaned.splitlines()
    )
    return _replace_body(text, span, lines + "\n" + child.outer)


def _victory_point_pairs(value: str) -> list[tuple[str, str]]:
    values = value.split()
    if len(values) % 2:
        raise ValueError("Victory points require province/value pairs")
    return list(zip(values[::2], values[1::2]))


def _victory_point_spans(text: str) -> list[AssignmentSpan]:
    return [span for span in top_level_assignments(text) if span.key == "victory_points"]


def _victory_point_block_values(text: str, span: AssignmentSpan) -> list[str]:
    if not span.is_block:
        return []
    return [scalar.value for scalar in parse(_assignment_body(text, span)).root.values()]


def _has_non_pair_victory_points(text: str) -> bool:
    return any(len(_victory_point_block_values(text, span)) != 2
               for span in _victory_point_spans(text))


def _set_victory_point_pairs(text: str, value: str, layout: _Layout) -> str:
    pairs = _victory_point_pairs(value)
    spans = _victory_point_spans(text)
    result = text
    for index in range(len(spans) - 1, -1, -1):
        span = spans[index]
        if index >= len(pairs):
            result = replace_assignment(result, span, None)
        elif _victory_point_block_values(text, span) != list(pairs[index]):
            province, points = pairs[index]
            result = replace_assignment(result, span, f"victory_points = {{ {province} {points} }}")
    for province, points in pairs[len(spans):]:
        result = _insert_statement(
            result,
            f"victory_points = {{ {province} {points} }}",
            layout,
            after_keys=("victory_points",),
            before_dated=True,
        )
    return result


def _set_repeated_scalars(
    text: str,
    key: str,
    values: Sequence[str],
    layout: _Layout,
    *,
    after_keys: Sequence[str] = (),
) -> str:
    spans = [
        span for span in top_level_assignments(text) if span.key == key and not span.is_block
    ]
    for value in values:
        require_value(value, f"a value of '{key}'")
    result = text
    for index in range(len(spans) - 1, -1, -1):
        span = spans[index]
        if index < len(values):
            existing = result[span.value_start : span.value_end]
            replacement = _quote_like(existing, values[index])
            result = result[: span.value_start] + replacement + result[span.value_end :]
        else:
            result = replace_assignment(result, span, None)
    for value in values[len(spans) :]:
        result = _insert_statement(
            result,
            f"{key} = {value}",
            layout,
            after_keys=(key, *after_keys),
            before_dated=True,
        )
    return result


def _set_resources(
    text: str,
    original: dict[str, str | int | float],
    updated: dict[str, str | int | float],
    layout: _Layout,
) -> str:
    """Change only the resource lines that differ, inside the existing block."""

    span = _find_direct_assignment(text, "resources", block=True)
    if not updated:
        return _set_direct_block(text, "resources", None, layout)
    if span is None:
        return _set_direct_block(
            text, "resources", _render_resources(updated), layout,
            after_keys=("buildings_max_level_factor", "state_category", "manpower"),
        )
    body = _assignment_body(text, span)
    child = _child_layout(text, span, layout)
    for key in original:
        if key not in updated:
            body = _set_direct_scalar(body, key, None, child)
    for key, value in updated.items():
        if key not in original or not scalar_values_equivalent(
            _scalar_text(original[key]), _scalar_text(value)
        ):
            body = _set_direct_scalar(body, key, _scalar_text(value), child)
    return _replace_body(text, span, body)


def _render_resources(resources: dict[str, str | int | float]) -> str:
    return "\n".join(f"{key} = {_scalar_text(value)}" for key, value in resources.items())


def _render_new_state(state: State) -> str:
    """Render a state that has no source text (or none with a state block)."""

    root = PdxNode()
    state_block = PdxNode(key="state")
    root.children.append(state_block)

    state_block.children.append(PdxNode(key="id", value=str(state.id)))

    if state.name:
        state_block.children.append(PdxNode(key="name", value=state.name))

    state_block.children.append(
        PdxNode(key="manpower", value=_scalar_text(state.manpower) or "0")
    )
    state_block.children.append(PdxNode(key="state_category", value=state.state_category))

    if state.impassable:
        state_block.children.append(PdxNode(key="impassable", value="yes"))

    if state.is_demilitarized_zone:
        state_block.children.append(PdxNode(key="is_demilitarized_zone", value="yes"))

    factor = _scalar_text(state.buildings_max_level_factor)
    if factor and not scalar_values_equivalent(factor, "1.0"):
        state_block.children.append(PdxNode(key="buildings_max_level_factor", value=factor))
    local_supplies = _scalar_text(state.local_supplies)
    if local_supplies:
        state_block.children.append(PdxNode(key="local_supplies", value=local_supplies))
    if state.resources:
        _replace_resources(state_block, state.resources)

    prov_block = PdxNode(key="provinces")
    for pid in state.provinces:
        prov_block.children.append(PdxNode(key=None, value=str(pid)))
    state_block.children.append(prov_block)

    # A body with a stray or missing brace would change the state around it.
    require_well_formed(state.history, "the history of a new state")
    require_well_formed(state.buildings, "the buildings of a new state")
    history = PdxNode(key="history")
    if state.history:
        parsed_history = parse_pdx(f"history = {{\n{state.history}\n}}")
        if parsed_history.children and parsed_history.children[0].is_block():
            history = parsed_history.children[0]
    if state.owner:
        history.set_value("owner", state.owner)
    if state.cores:
        history.remove("add_core_of")
        for core in state.cores:
            history.children.append(PdxNode(key="add_core_of", value=core))
    if state.victory_points:
        history.remove("victory_points")
        for province, points in _victory_point_pairs(state.victory_points):
            vp_block = PdxNode(key="victory_points", block=True)
            vp_block.children.extend([PdxNode(value=province), PdxNode(value=points)])
            history.children.append(vp_block)
    if state.buildings:
        _replace_block(history, "buildings", state.buildings)
    state_block.children.append(history)

    return serialize_pdx(root)


def _patch_existing_state(state: State) -> str:
    original = read_state(state.path or Path("<memory-state>"), text=state.raw_text)
    raw = state.raw_text
    state_assignment = _state_assignment(parse(raw).root)
    if state_assignment is None or state_assignment.block is None:
        return _render_new_state(state)
    state_block = state_assignment.block
    state_body = state_block.body
    outer = _own_line_indent(raw, state_assignment.start, file_start=True)
    layout = _Layout(outer or "", _indent_unit(raw))

    scalar_fields = {
        "id": str(state.id),
        "name": state.name,
        "manpower": _scalar_text(state.manpower) or "0",
        "state_category": state.state_category,
        "local_supplies": _scalar_text(state.local_supplies),
        "buildings_max_level_factor": _scalar_text(state.buildings_max_level_factor),
    }
    original_scalars = {
        "id": str(original.id),
        "name": original.name,
        "manpower": _scalar_text(original.manpower) or "0",
        "state_category": original.state_category,
        "local_supplies": _scalar_text(original.local_supplies),
        "buildings_max_level_factor": _scalar_text(original.buildings_max_level_factor),
    }
    placement: dict[str, tuple[str, ...]] = {
        "id": (),
        "name": ("id",),
        "manpower": ("name", "id"),
        "state_category": ("manpower", "name", "id"),
    }
    for key, value in scalar_fields.items():
        # Compare by meaning: ``2000`` given for a file's ``2000.0`` is no change.
        if value == original_scalars[key] or (
            value and original_scalars[key]
            and scalar_values_equivalent(value, original_scalars[key])
        ):
            continue
        rendered_value: str | None = value
        if key in {"local_supplies", "buildings_max_level_factor"} and not value:
            rendered_value = None
        state_body = _set_direct_scalar(
            state_body, key, rendered_value, layout,
            after_keys=placement.get(key, ("state_category", "manpower")),
            first=key == "id",
        )

    if state.is_demilitarized_zone != original.is_demilitarized_zone:
        state_body = _set_direct_scalar(
            state_body,
            "is_demilitarized_zone",
            "yes" if state.is_demilitarized_zone else None,
            layout,
            after_keys=("state_category", "manpower"),
        )
    if state.impassable != original.impassable:
        state_body = _set_direct_scalar(
            state_body, "impassable", "yes" if state.impassable else None, layout,
            after_keys=("state_category", "manpower"),
        )
    if state.resources != original.resources:
        state_body = _set_resources(state_body, original.resources, state.resources, layout)
    if state.provinces != original.provinces:
        state_body = _set_state_provinces(state_body, original.provinces, state.provinces, layout)

    history_replaced = state.history.strip() != original.history.strip()
    if history_replaced:
        state_body = _set_direct_block(state_body, "history", state.history or None, layout)

    history_span = _find_direct_assignment(state_body, "history", block=True)
    # Victory points are rewritten only when their value changed, or when the
    # caller passed them explicitly and the file combines several pairs in one
    # block. An edit of another field keeps the file's spelling.
    victory_points_changed = state.victory_points.split() != original.victory_points.split()
    rewrite_victory_points = victory_points_changed or (
        state._victory_points_requested
        and _has_non_pair_victory_points(_assignment_body(state_body, history_span))
    )
    history_changed = history_replaced or rewrite_victory_points or any(
        (
            state.owner != original.owner,
            state.cores != original.cores,
            state.buildings.strip() != original.buildings.strip(),
        )
    )
    if history_span is None and history_changed:
        state_body = _set_direct_block(state_body, "history", "", layout)
        history_span = _find_direct_assignment(state_body, "history", block=True)
    if history_span is not None:
        history_layout = _child_layout(state_body, history_span, layout)
        history_body = _assignment_body(state_body, history_span)
        if history_replaced or state.owner != original.owner:
            history_body = _set_direct_scalar(
                history_body, "owner", state.owner or None, history_layout, first=True,
            )
        if history_replaced or state.cores != original.cores:
            history_body = _set_repeated_scalars(
                history_body, "add_core_of", state.cores, history_layout, after_keys=("owner",),
            )
        if history_replaced or rewrite_victory_points:
            history_body = _set_victory_point_pairs(
                history_body, state.victory_points, history_layout,
            )
        if history_replaced or state.buildings.strip() != original.buildings.strip():
            history_body = _set_direct_block(
                history_body, "buildings", state.buildings or None, history_layout,
                before_dated=True,
            )
        state_body = _replace_body(state_body, history_span, history_body)

    if state_body == state_block.body:
        return raw
    return raw[: state_block.body_start] + state_body + raw[state_block.body_end :]


def _set_state_provinces(
    state_body: str,
    original: Sequence[int],
    updated: Sequence[int],
    layout: _Layout,
) -> str:
    """Remove/append province atoms in place; re-render only for reorders."""
    span = _find_direct_assignment(state_body, "provinces", block=True)
    if span is not None and span.body_start is not None and span.body_end is not None:
        body = state_body[span.body_start : span.body_end]
        if list_ints(body) == list(original):
            patched = patch_int_list_body(body, original, updated)
            if patched is not None:
                return state_body[: span.body_start] + patched + state_body[span.body_end :]
    return _set_direct_block(
        state_body,
        "provinces",
        " ".join(str(province) for province in updated),
        layout,
        before_dated=False,
        after_keys=("resources", "local_supplies", "buildings_max_level_factor", "state_category"),
    )


def _replace_block(block: PdxNode, key: str, body: str) -> None:
    parsed = parse_pdx(f"{key} = {{\n{body}\n}}")
    if not parsed.children:
        return
    new_node = parsed.children[0]
    for i, child in enumerate(block.children):
        if child.key == key:
            block.children[i] = new_node
            return
    block.children.append(new_node)


def _replace_resources(block: PdxNode, resources: dict[str, str | int | float]) -> None:
    if not resources:
        return
    node = PdxNode(key="resources")
    for key, value in resources.items():
        node.children.append(PdxNode(key=key, value=str(value)))
    for i, child in enumerate(block.children):
        if child.key == "resources":
            block.children[i] = node
            return
    block.children.append(node)


def serialize_state(state: State) -> str:
    if state.raw_text:
        return _patch_existing_state(state)
    return _render_new_state(state)


def write_state(mod_root: Path, state: State) -> Path:
    states_dir = mod_root / "history" / "states"
    states_dir.mkdir(parents=True, exist_ok=True)

    if state.path:
        path = state.path
    else:
        existing = find_state_file(states_dir, state.id)
        path = existing or states_dir / f"{state.id} - {state.name or state.id}.txt"

    path.parent.mkdir(parents=True, exist_ok=True)
    content = serialize_state(state)
    _write_like_existing(path, content)
    state.raw_text = content
    return path


def _write_like_existing(path: Path, content: str) -> None:
    """Write ``content``, keeping an existing file's encoding and line endings."""

    original = path.read_bytes() if path.is_file() else None
    path.write_bytes(encode_text(content, like=original))


def patch_state_owner(state_text: str, tag: str, add_core: bool = True) -> str:
    state = read_state(Path("<memory-state>"), text=state_text)
    state.owner = tag
    if add_core and tag not in state.cores:
        state.cores.append(tag)
    return serialize_state(state)


def patch_state_history_owner_cores(
    state_file: Path,
    *,
    owner: str | None = None,
    add_cores: list[str] | None = None,
    remove_cores: list[str] | None = None,
) -> None:
    """Patch owner/core lines in-place without reserializing unrelated state data."""
    text = read_text(state_file)
    patched = patch_state_history_owner_cores_text(
        text,
        owner=owner,
        add_cores=add_cores,
        remove_cores=remove_cores,
    )
    _write_like_existing(state_file, patched)


def patch_state_history_owner_cores_text(
    text: str,
    *,
    owner: str | None = None,
    add_cores: list[str] | None = None,
    remove_cores: list[str] | None = None,
) -> str:
    """Patch only top-level owner/core assignments in the state's history block."""
    state = read_state(Path("<memory-state>"), text=text)
    removed = {tag.upper() for tag in (remove_cores or [])}
    state.cores = [core for core in state.cores if core.upper() not in removed]
    for core in add_cores or []:
        tag = core.upper()
        if tag not in state.cores:
            state.cores.append(tag)
    if owner is not None:
        state.owner = owner.upper()
    return serialize_state(state)


def build_state_index(states_dir: Path) -> list[dict]:
    """Return one entry per state file, identified by the file's own ``id``."""

    out: list[dict] = []
    if not states_dir.is_dir():
        return out
    for f in sorted(states_dir.glob("*.txt")):
        state_assignment = _state_assignment(parse(read_text(f)).root)
        block = state_assignment.block if state_assignment is not None else None
        if block is None:
            continue
        sid = _int_or_none(block.scalar("id"))
        if sid is None:
            continue
        history = block.block("history")
        owner = history.scalar("owner") if history is not None else None
        name = block.scalar("name") or ""
        filename_name = _state_name_from_filename(f, sid)
        out.append(
            {
                "id": sid,
                "name": name,
                "display_name": filename_name or name,
                "file_name": filename_name,
                "owner": owner or None,
                "path": str(f),
            }
        )
    return out


def _state_name_from_filename(path: Path, state_id: int) -> str:
    stem = path.stem
    name = re.sub(rf"^\s*{state_id}\s*[-_\s]*", "", stem).strip()
    return name
