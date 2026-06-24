"""
State management - read, modify, and write HOI4 state files.

States live in history/states/*.txt. Each file defines one state with
an owner, cores, manpower, victory points, and province assignments.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Optional

from .parser import PdxNode, parse_pdx, serialize_pdx
from .types import State

STATE_ID_RE = re.compile(r"\bid\s*=\s*(\d+)")
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


def ensure_state_in_mod(mod_root: Path, hoi4_install: Optional[Path], state_id: int) -> Optional[Path]:
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


def read_state(state_file: Path) -> State:
    txt = state_file.read_text(encoding="utf-8", errors="ignore")
    root = parse_pdx(txt)

    state_block = None
    for child in root.children:
        if child.key == "state":
            state_block = child
            break
    if state_block is None:
        sid_m = STATE_ID_RE.search(txt)
        return State(id=int(sid_m.group(1)) if sid_m else 0, path=state_file)

    state_id = state_block.get_int("id", 0)
    state_obj = State(id=state_id, path=state_file)

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

    prov_block = state_block.get_block("provinces")
    if prov_block:
        state_obj.provinces = [
            int(c.value) for c in prov_block.children if c.value and c.value.isdigit()
        ]

    history = state_block.get_block("history")
    if history:
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


def serialize_state(state: State) -> str:
    root = PdxNode()
    state_block = PdxNode(key="state")
    root.children.append(state_block)

    state_block.children.append(PdxNode(key="id", value=str(state.id)))

    if state.name:
        state_block.children.append(PdxNode(key="name", value=state.name))

    state_block.children.append(PdxNode(
        key="manpower", value=state.manpower or "0",
    ))

    state_block.children.append(PdxNode(
        key="state_category", value=state.state_category,
    ))

    if state.is_demilitarized_zone:
        state_block.children.append(PdxNode(key="is_demilitarized_zone", value="yes"))

    if state.buildings_max_level_factor and state.buildings_max_level_factor != "1.0":
        state_block.children.append(PdxNode(
            key="buildings_max_level_factor", value=state.buildings_max_level_factor,
        ))

    prov_block = PdxNode(key="provinces")
    for pid in state.provinces:
        prov_block.children.append(PdxNode(key=None, value=str(pid)))
    state_block.children.append(prov_block)

    history = PdxNode(key="history")
    if state.owner:
        history.children.append(PdxNode(key="owner", value=state.owner))
    for core in state.cores:
        history.children.append(PdxNode(key="add_core_of", value=core))
    if state.victory_points:
        vp_block = PdxNode(key="victory_points")
        for part in state.victory_points.split():
            vp_block.children.append(PdxNode(key=None, value=part))
        history.children.append(vp_block)
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
    path.write_text(serialize_state(state), encoding="utf-8")
    return path


def patch_state_owner(state_text: str, tag: str, add_core: bool = True) -> str:
    root = parse_pdx(state_text)

    state_block = None
    for child in root.children:
        if child.key == "state":
            state_block = child
            break
    if state_block is None:
        return state_text

    history = state_block.get_block("history")
    if history is None:
        history = PdxNode(key="history")
        state_block.add_child(history)

    owner_node = history.find("owner")
    if owner_node:
        owner_node.value = tag
    else:
        history.add_child(PdxNode(key="owner", value=tag))

    if add_core:
        has_core = any(c.key == "add_core_of" and c.value == tag for c in history.children)
        if not has_core:
            history.add_child(PdxNode(key="add_core_of", value=tag))

    return serialize_pdx(root)


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
        out.append({"id": sid, "owner": owner_m.group(1) if owner_m else None})
    return out
