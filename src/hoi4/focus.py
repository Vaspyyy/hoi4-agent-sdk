"""
Focus Tree System

Read, modify, and write HOI4 national focus trees.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .parser import find_assignment_block, iter_assignment_blocks, strip_comments
from .types import Focus, FocusTree

FOCUS_ID_RE = re.compile(r"\bid\s*=\s*([A-Za-z0-9_\-]+)")
ICON_RE = re.compile(r"\bicon\s*=\s*([A-Za-z0-9_\-]+)")
X_RE = re.compile(r"\bx\s*=\s*(-?\d+)")
Y_RE = re.compile(r"\by\s*=\s*(-?\d+)")
COST_RE = re.compile(r"\bcost\s*=\s*(\d+)")
FOCUS_REF_RE = re.compile(r"\bfocus\s*=\s*([A-Za-z0-9_\-]+)")
TREE_ID_RE = re.compile(r"\bid\s*=\s*(\S+)")
TAG_RE = re.compile(r"\btag\s*=\s*([A-Z0-9]{3})")
RELATIVE_POSITION_RE = re.compile(r"\brelative_position_id\s*=\s*([A-Za-z0-9_\-]+)")
WAR_TARGET_RE = re.compile(r"\bwill_lead_to_war_with\s*=\s*([A-Z0-9]{3})")


def load_focus_tree(path: Path) -> Optional[FocusTree]:
    txt = path.read_text(encoding="utf-8", errors="ignore")

    tree_match = find_assignment_block(txt, "focus_tree")
    if tree_match:
        return _parse_wrapped_tree(txt, path)

    if not iter_assignment_blocks(txt, "focus"):
        return None

    return _parse_bare_focuses(txt, path)


def _parse_wrapped_tree(txt: str, path: Path) -> FocusTree:
    match = find_assignment_block(txt, "focus_tree")
    if match is None:
        return FocusTree(id=path.stem, path=path)
    tree_block = match[0]

    clean_tree_block = strip_comments(tree_block)
    tree_id_m = TREE_ID_RE.search(clean_tree_block)
    tree_id = tree_id_m.group(1) if tree_id_m else path.stem
    tag_m = TAG_RE.search(clean_tree_block)
    country_tag = tag_m.group(1) if tag_m else ""

    focuses = _extract_focuses(tree_block)
    tree = FocusTree(id=tree_id, country_tag=country_tag, focuses=focuses, path=path)
    default = _extract_bool(tree_block, "default")
    if default is not None:
        tree.default = default
    tree.continuous_focus_position = _extract_block_content(tree_block, "continuous_focus_position")
    tree.shared_focuses = _extract_scalar_all(tree_block, "shared_focus")
    return tree


def _parse_bare_focuses(txt: str, path: Path) -> FocusTree:
    focuses = _extract_focuses(txt)
    return FocusTree(id=path.stem, focuses=focuses, path=path)


def _extract_focuses(text: str) -> list[Focus]:
    focuses: list[Focus] = []
    for chunk, _, _ in iter_assignment_blocks(text, "focus"):
        focus = _parse_focus_block(chunk)
        if focus:
            focuses.append(focus)
    return focuses


def _parse_focus_block(chunk: str) -> Focus | None:
    clean_chunk = strip_comments(chunk)
    mid = FOCUS_ID_RE.search(clean_chunk)
    if not mid:
        return None
    fid = mid.group(1)

    icon_m = ICON_RE.search(clean_chunk)
    x_m = X_RE.search(clean_chunk)
    y_m = Y_RE.search(clean_chunk)
    cost_m = COST_RE.search(clean_chunk)
    rel_m = RELATIVE_POSITION_RE.search(clean_chunk)
    war_m = WAR_TARGET_RE.search(clean_chunk)

    prereqs = _extract_ref_groups(chunk, "prerequisite")
    mutex = _extract_ref_groups(chunk, "mutually_exclusive")
    completion_reward = _extract_block_content(chunk, "completion_reward")
    available = _extract_block_content(chunk, "available")
    bypass = _extract_block_content(chunk, "bypass")
    select_effect = _extract_block_content(chunk, "select_effect")
    complete_tooltip = _extract_block_content(chunk, "complete_tooltip")
    allow_branch = _extract_block_content(chunk, "allow_branch")
    ai_will_do = _extract_block_content(chunk, "ai_will_do")

    return Focus(
        id=fid,
        icon=icon_m.group(1) if icon_m else "GFX_goal_generic_construct_civilian",
        x=int(x_m.group(1)) if x_m else 0,
        y=int(y_m.group(1)) if y_m else 0,
        cost=int(cost_m.group(1)) if cost_m else 10,
        prerequisites=prereqs,
        mutually_exclusive=mutex,
        relative_position_id=rel_m.group(1) if rel_m else "",
        search_filters=_extract_bare_block_values(chunk, "search_filters"),
        completion_reward=completion_reward,
        available=available,
        bypass=bypass,
        select_effect=select_effect,
        complete_tooltip=complete_tooltip,
        allow_branch=allow_branch,
        ai_will_do=ai_will_do,
        cancel_if_invalid=_extract_bool(chunk, "cancel_if_invalid"),
        continue_if_invalid=_extract_bool(chunk, "continue_if_invalid"),
        available_if_capitulated=_extract_bool(chunk, "available_if_capitulated"),
        will_lead_to_war_with=war_m.group(1) if war_m else "",
        raw_block=chunk.strip(),
    )


def _extract_ref_groups(chunk: str, block_name: str) -> list[list[str]]:
    groups: list[list[str]] = []
    for block_text, _, _ in iter_assignment_blocks(chunk, block_name):
        refs = [rm.group(1) for rm in FOCUS_REF_RE.finditer(strip_comments(block_text))]
        if refs:
            groups.append(refs)
    return groups


def _extract_block_content(chunk: str, block_name: str) -> str:
    match = find_assignment_block(chunk, block_name)
    if not match:
        return ""
    return match[0].strip()


def _extract_scalar_all(chunk: str, key: str) -> list[str]:
    clean = strip_comments(chunk)
    return re.findall(rf"\b{re.escape(key)}\s*=\s*([^\s#]+)", clean)


def _extract_bool(chunk: str, key: str) -> bool | None:
    clean = strip_comments(chunk)
    m = re.search(rf"\b{re.escape(key)}\s*=\s*(yes|no)\b", clean)
    if not m:
        return None
    return m.group(1) == "yes"


def _extract_bare_block_values(chunk: str, block_name: str) -> list[str]:
    body = _extract_block_content(chunk, block_name)
    if not body:
        return []
    return [item for item in strip_comments(body).split() if item]


def serialize_focus_tree(tree: FocusTree) -> str:
    parts: list[str] = []
    if tree.country_tag:
        parts.append("focus_tree = {")
        parts.append(f"\tid = {tree.id}")
        parts.append("")
        parts.append("\tcountry = {")
        parts.append("\t\tfactor = 0")
        parts.append("\t\tmodifier = {")
        parts.append("\t\t\tadd = 10")
        parts.append(f"\t\t\ttag = {tree.country_tag}")
        parts.append("\t\t}")
        parts.append("\t}")
        parts.append("")
    else:
        parts.append("focus_tree = {")
        parts.append(f"\tid = {tree.id}")
        parts.append("")

    if tree.default is not None:
        parts.append(f"\tdefault = {'yes' if tree.default else 'no'}")
        parts.append("")
    if tree.continuous_focus_position:
        parts.append("\tcontinuous_focus_position = {")
        for line in tree.continuous_focus_position.strip().split("\n"):
            parts.append(f"\t\t{line.strip()}")
        parts.append("\t}")
        parts.append("")
    for shared_focus in tree.shared_focuses:
        parts.append(f"\tshared_focus = {shared_focus}")
    if tree.shared_focuses:
        parts.append("")

    for focus in tree.focuses:
        parts.extend(_serialize_focus(focus))

    parts.append("}")
    parts.append("")
    return "\n".join(parts)


def _serialize_focus(focus: Focus) -> list[str]:
    if focus.raw_block and not focus.touched:
        lines = ["\tfocus = {"]
        for line in focus.raw_block.strip().split("\n"):
            lines.append(f"\t\t{line.rstrip()}")
        lines.append("\t}")
        lines.append("")
        return lines

    lines = ["\tfocus = {"]
    lines.append(f"\t\tid = {focus.id}")
    lines.append(f"\t\ticon = {focus.icon}")
    lines.append(f"\t\tx = {focus.x}")
    lines.append(f"\t\ty = {focus.y}")
    if focus.relative_position_id:
        lines.append(f"\t\trelative_position_id = {focus.relative_position_id}")
    lines.append(f"\t\tcost = {focus.cost}")

    for group in focus.prerequisites:
        lines.append("\t\tprerequisite = {")
        for ref in group:
            lines.append(f"\t\t\tfocus = {ref}")
        lines.append("\t\t}")

    for group in focus.mutually_exclusive:
        lines.append("\t\tmutually_exclusive = {")
        for ref in group:
            lines.append(f"\t\t\tfocus = {ref}")
        lines.append("\t\t}")

    if focus.search_filters:
        lines.append(f"\t\tsearch_filters = {{ {' '.join(focus.search_filters)} }}")

    for key, value in [
        ("cancel_if_invalid", focus.cancel_if_invalid),
        ("continue_if_invalid", focus.continue_if_invalid),
        ("available_if_capitulated", focus.available_if_capitulated),
    ]:
        if value is not None:
            lines.append(f"\t\t{key} = {'yes' if value else 'no'}")

    if focus.will_lead_to_war_with:
        lines.append(f"\t\twill_lead_to_war_with = {focus.will_lead_to_war_with}")

    if focus.available:
        lines.append("\t\tavailable = {")
        for line in focus.available.strip().split("\n"):
            lines.append(f"\t\t\t{line.strip()}")
        lines.append("\t\t}")

    if focus.bypass:
        lines.append("\t\tbypass = {")
        for line in focus.bypass.strip().split("\n"):
            lines.append(f"\t\t\t{line.strip()}")
        lines.append("\t\t}")

    if focus.select_effect:
        lines.append("\t\tselect_effect = {")
        for line in focus.select_effect.strip().split("\n"):
            lines.append(f"\t\t\t{line.strip()}")
        lines.append("\t\t}")

    if focus.completion_reward:
        lines.append("\t\tcompletion_reward = {")
        for line in focus.completion_reward.strip().split("\n"):
            lines.append(f"\t\t\t{line.strip()}")
        lines.append("\t\t}")

    if focus.complete_tooltip:
        lines.append("\t\tcomplete_tooltip = {")
        for line in focus.complete_tooltip.strip().split("\n"):
            lines.append(f"\t\t\t{line.strip()}")
        lines.append("\t\t}")

    if focus.allow_branch:
        lines.append("\t\tallow_branch = {")
        for line in focus.allow_branch.strip().split("\n"):
            lines.append(f"\t\t\t{line.strip()}")
        lines.append("\t\t}")

    if focus.ai_will_do:
        lines.append("\t\tai_will_do = {")
        for line in focus.ai_will_do.strip().split("\n"):
            lines.append(f"\t\t\t{line.strip()}")
        lines.append("\t\t}")

    lines.append("\t}")
    lines.append("")
    return lines


def write_focus_tree(tree: FocusTree, mod_root: Path) -> Path:
    tag = tree.country_tag or tree.id.replace("_focus", "").upper()
    filename = f"{tag}_focus.txt"
    out_path = tree.path or mod_root / "common" / "national_focus" / filename
    out_path.parent.mkdir(parents=True, exist_ok=True)
    content = serialize_focus_tree(tree)
    out_path.write_text(content, encoding="utf-8")
    return out_path
