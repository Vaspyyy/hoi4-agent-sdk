"""
State management - read, modify, and write HOI4 state files.

States live in history/states/*.txt. Each file defines one state with
an owner, cores, manpower, victory points, and province assignments.
"""

from __future__ import annotations

import re
import shutil
import textwrap
from pathlib import Path
from typing import Optional, Sequence

from .parser import PdxNode, parse_pdx, serialize_pdx
from .patching import AssignmentSpan, replace_assignment, top_level_assignments
from .types import State

STATE_ID_RE = re.compile(r"\bid\s*=\s*(\d+)")
STATE_NAME_RE = re.compile(r"\bname\s*=\s*\"?([^\"\s}]+)\"?")
OWNER_RE = re.compile(r"\bowner\s*=\s*([A-Z0-9]{3})")


def find_state_file(states_dir: Path, state_id: int) -> Optional[Path]:
    if not states_dir.is_dir():
        return None
    for f in states_dir.glob(f"{state_id} *.txt"):
        return f
    for f in sorted(states_dir.glob("*.txt")):
        txt = f.read_text(encoding="utf-8", errors="ignore")
        if re.search(rf"\bid\s*=\s*{state_id}\b", txt):
            return f
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
    txt = text if text is not None else state_file.read_text(encoding="utf-8", errors="ignore")
    root = parse_pdx(txt)

    state_block = None
    for child in root.children:
        if child.key == "state":
            state_block = child
            break
    if state_block is None:
        sid_m = STATE_ID_RE.search(txt)
        return State(id=int(sid_m.group(1)) if sid_m else 0, path=state_file, raw_text=txt)

    state_id = state_block.get_int("id", 0)
    state_obj = State(
        id=state_id,
        path=state_file,
        raw_text=txt,
        source_path=state_file,
    )

    name_node = state_block.find("name")
    if name_node and name_node.value:
        state_obj.name = name_node.value

    cat_node = state_block.find("state_category")
    if cat_node and cat_node.value:
        state_obj.state_category = cat_node.value

    dz_node = state_block.find("is_demilitarized_zone")
    if dz_node and dz_node.value:
        state_obj.is_demilitarized_zone = dz_node.value == "yes"

    mp_node = state_block.find("manpower")
    if mp_node and mp_node.value:
        state_obj.manpower = mp_node.value

    bldg_node = state_block.find("buildings_max_level_factor")
    if bldg_node and bldg_node.value:
        state_obj.buildings_max_level_factor = bldg_node.value

    local_supplies_node = state_block.find("local_supplies")
    if local_supplies_node and local_supplies_node.value:
        state_obj.local_supplies = local_supplies_node.value

    resources_block = state_block.get_block("resources")
    if resources_block:
        for child in resources_block.children:
            if child.key and child.value is not None:
                state_obj.resources[child.key] = _coerce_scalar(child.value)

    state_span = _find_direct_assignment(txt, "state", block=True)
    state_body = _assignment_body(txt, state_span)

    prov_block = state_block.get_block("provinces")
    if prov_block:
        state_obj.provinces = [
            int(c.value) for c in prov_block.children if c.value and c.value.isdigit()
        ]

    history = state_block.get_block("history")
    if history:
        history_span = _find_direct_assignment(state_body, "history", block=True)
        history_body = _assignment_body(state_body, history_span)
        if history_span is not None:
            state_obj.history = textwrap.dedent(history_body).strip()

        # Buildings are legal in the history block, not at state scope.  A
        # depth-insensitive search used to read dated history buildings here
        # and later serialize them as a new state-level block.
        buildings_span = _find_direct_assignment(history_body, "buildings", block=True)
        if buildings_span is not None:
            state_obj.buildings = textwrap.dedent(
                _assignment_body(history_body, buildings_span)
            ).strip()

        owner_node = history.find("owner")
        if owner_node and owner_node.value:
            state_obj.owner = owner_node.value

        state_obj.cores = [c.value for c in history.find_all("add_core_of") if c.value]

        vp_parts: list[str] = []
        for vp in history.find_all("victory_points"):
            if vp.is_block():
                vals = [c.value for c in vp.children if c.value]
                vp_parts.extend(vals)
            elif vp.value:
                vp_parts.append(vp.value)
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


def _assignment_indent(text: str, span: AssignmentSpan) -> str:
    line_start = text.rfind("\n", 0, span.start) + 1
    return text[line_start : span.start]


def _infer_assignment_indent(text: str, fallback: str = "\t") -> str:
    assignments = top_level_assignments(text)
    if not assignments:
        return fallback
    return _assignment_indent(text, assignments[0])


def _append_direct_assignment(text: str, assignment: str, indent: str) -> str:
    content = text.rstrip()
    trailing = text[len(content) :]
    separator = "\n" if content and not content.endswith("\n") else ""
    return f"{content}{separator}{indent}{assignment.rstrip()}{trailing}"


def _quote_like(existing: str, value: str) -> str:
    if existing.startswith('"') and existing.endswith('"'):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _set_direct_scalar(
    text: str,
    key: str,
    value: str | None,
    *,
    fallback_indent: str = "\t",
) -> str:
    spans = [
        span for span in top_level_assignments(text) if span.key == key and not span.is_block
    ]
    if value is None:
        result = text
        for span in reversed(spans):
            result = replace_assignment(result, span, None)
        return result
    if spans:
        span = spans[0]
        existing = text[span.value_start : span.value_end]
        replacement = _quote_like(existing, value)
        return text[: span.value_start] + replacement + text[span.value_end :]
    indent = _infer_assignment_indent(text, fallback=fallback_indent)
    return _append_direct_assignment(text, f"{key} = {value}", indent)


def _set_direct_block(
    text: str,
    key: str,
    body: str | None,
    *,
    fallback_indent: str = "\t",
) -> str:
    span = _find_direct_assignment(text, key, block=True)
    if span is None and body is None:
        return text
    if span is not None and body is None:
        result = text
        for candidate in reversed(
            [
                item
                for item in top_level_assignments(result)
                if item.key == key and item.is_block
            ]
        ):
            result = replace_assignment(result, candidate, None)
        return result
    indent = (
        _assignment_indent(text, span)
        if span is not None
        else _infer_assignment_indent(text, fallback=fallback_indent)
    )
    cleaned = textwrap.dedent(body or "").strip()
    if cleaned:
        inner = "\n".join(f"{indent}\t{line.rstrip()}" for line in cleaned.splitlines())
        rendered = f"{key} = {{\n{inner}\n{indent}}}"
    else:
        rendered = f"{key} = {{ }}"
    if span is not None:
        return replace_assignment(text, span, rendered)
    return _append_direct_assignment(text, rendered, indent)


def _set_repeated_scalars(
    text: str,
    key: str,
    values: Sequence[str],
    *,
    fallback_indent: str = "\t\t",
) -> str:
    spans = [
        span for span in top_level_assignments(text) if span.key == key and not span.is_block
    ]
    result = text
    for index in range(len(spans) - 1, -1, -1):
        span = spans[index]
        if index < len(values):
            existing = result[span.value_start : span.value_end]
            replacement = _quote_like(existing, values[index])
            result = result[: span.value_start] + replacement + result[span.value_end :]
        else:
            result = replace_assignment(result, span, None)
    if len(values) > len(spans):
        indent = _infer_assignment_indent(result, fallback=fallback_indent)
        for value in values[len(spans) :]:
            result = _append_direct_assignment(result, f"{key} = {value}", indent)
    return result


def _render_resources(resources: dict[str, str | int | float]) -> str:
    return "\n".join(f"{key} = {value}" for key, value in resources.items())


def _patch_existing_state(state: State) -> str:
    original = read_state(state.path or Path("<memory-state>"), text=state.raw_text)
    state_span = _find_direct_assignment(state.raw_text, "state", block=True)
    if state_span is None:
        return state.raw_text
    state_body = _assignment_body(state.raw_text, state_span)

    scalar_fields = {
        "id": str(state.id),
        "name": state.name,
        "manpower": state.manpower or "0",
        "state_category": state.state_category,
        "local_supplies": state.local_supplies,
        "buildings_max_level_factor": state.buildings_max_level_factor,
    }
    original_scalars = {
        "id": str(original.id),
        "name": original.name,
        "manpower": original.manpower or "0",
        "state_category": original.state_category,
        "local_supplies": original.local_supplies,
        "buildings_max_level_factor": original.buildings_max_level_factor,
    }
    for key, value in scalar_fields.items():
        if value == original_scalars[key]:
            continue
        rendered_value: str | None = value
        if key in {"local_supplies", "buildings_max_level_factor"} and not value:
            rendered_value = None
        state_body = _set_direct_scalar(state_body, key, rendered_value)

    if state.is_demilitarized_zone != original.is_demilitarized_zone:
        state_body = _set_direct_scalar(
            state_body,
            "is_demilitarized_zone",
            "yes" if state.is_demilitarized_zone else None,
        )
    if state.resources != original.resources:
        state_body = _set_direct_block(
            state_body,
            "resources",
            _render_resources(state.resources) if state.resources else None,
        )
    if state.provinces != original.provinces:
        state_body = _set_direct_block(
            state_body,
            "provinces",
            " ".join(str(province) for province in state.provinces),
        )

    if state.history.strip() != original.history.strip():
        state_body = _set_direct_block(state_body, "history", state.history or None)

    history_span = _find_direct_assignment(state_body, "history", block=True)
    history_changed = any(
        (
            state.owner != original.owner,
            state.cores != original.cores,
            state.victory_points != original.victory_points,
            state.buildings.strip() != original.buildings.strip(),
        )
    )
    if history_span is None and history_changed:
        state_body = _set_direct_block(state_body, "history", "")
        history_span = _find_direct_assignment(state_body, "history", block=True)
    if history_span is not None:
        history_body = _assignment_body(state_body, history_span)
        if state.owner != original.owner:
            history_body = _set_direct_scalar(
                history_body,
                "owner",
                state.owner or None,
                fallback_indent="\t\t",
            )
        if state.cores != original.cores:
            history_body = _set_repeated_scalars(
                history_body,
                "add_core_of",
                state.cores,
            )
        if state.victory_points != original.victory_points:
            history_body = _set_direct_block(
                history_body,
                "victory_points",
                state.victory_points or None,
                fallback_indent="\t\t",
            )
        if state.buildings.strip() != original.buildings.strip():
            history_body = _set_direct_block(
                history_body,
                "buildings",
                state.buildings or None,
                fallback_indent="\t\t",
            )
        state_body = (
            state_body[: history_span.body_start]
            + history_body
            + state_body[history_span.body_end :]
        )

    return (
        state.raw_text[: state_span.body_start]
        + state_body
        + state.raw_text[state_span.body_end :]
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

    root = PdxNode()
    state_block = PdxNode(key="state")
    root.children.append(state_block)

    state_block.children.append(PdxNode(key="id", value=str(state.id)))

    if state.name:
        state_block.children.append(PdxNode(key="name", value=state.name))

    state_block.children.append(
        PdxNode(
            key="manpower",
            value=state.manpower or "0",
        )
    )

    state_block.children.append(
        PdxNode(
            key="state_category",
            value=state.state_category,
        )
    )

    if state.is_demilitarized_zone:
        state_block.children.append(PdxNode(key="is_demilitarized_zone", value="yes"))

    if state.buildings_max_level_factor and state.buildings_max_level_factor != "1.0":
        state_block.children.append(
            PdxNode(
                key="buildings_max_level_factor",
                value=state.buildings_max_level_factor,
            )
        )
    if state.local_supplies:
        state_block.children.append(PdxNode(key="local_supplies", value=state.local_supplies))
    if state.resources:
        _replace_resources(state_block, state.resources)

    prov_block = PdxNode(key="provinces")
    for pid in state.provinces:
        prov_block.children.append(PdxNode(key=None, value=str(pid)))
    state_block.children.append(prov_block)

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
        vp_block = PdxNode(key="victory_points")
        for part in state.victory_points.split():
            vp_block.children.append(PdxNode(key=None, value=part))
        history.children.append(vp_block)
    if state.buildings:
        _replace_block(history, "buildings", state.buildings)
    state_block.children.append(history)

    return serialize_pdx(root)


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
    path.write_text(content, encoding="utf-8")
    state.raw_text = content
    return path


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
    text = state_file.read_text(encoding="utf-8", errors="ignore")
    patched = patch_state_history_owner_cores_text(
        text,
        owner=owner,
        add_cores=add_cores,
        remove_cores=remove_cores,
    )
    state_file.write_text(patched, encoding="utf-8")


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
    out: list[dict] = []
    if not states_dir.is_dir():
        return out
    for f in sorted(states_dir.glob("*.txt")):
        txt = f.read_text(encoding="utf-8", errors="ignore")
        mid = STATE_ID_RE.search(txt)
        if not mid:
            continue
        sid = int(mid.group(1))
        owner_m = OWNER_RE.search(txt)
        name_m = STATE_NAME_RE.search(txt)
        filename_name = _state_name_from_filename(f, sid)
        out.append(
            {
                "id": sid,
                "name": name_m.group(1) if name_m else "",
                "display_name": filename_name or (name_m.group(1) if name_m else ""),
                "owner": owner_m.group(1) if owner_m else None,
                "path": str(f),
            }
        )
    return out


def _state_name_from_filename(path: Path, state_id: int) -> str:
    stem = path.stem
    name = re.sub(rf"^\s*{state_id}\s*[-_\s]*", "", stem).strip()
    return name
