"""
Focus Tree System

Read, modify, and write HOI4 national focus trees.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .parser import find_assignment_block, strip_comments
from .patching import (
    AssignmentSpan,
    append_assignment,
    assignment_spans,
    dedent_block_body,
    replace_assignment,
    replace_assignment_body,
    set_block,
    set_scalar,
    top_level_assignments,
)
from .paths import require_country_tag, resolve_mod_output_path
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


def load_focus_tree(path: Path, text: str | None = None) -> Optional[FocusTree]:
    trees = load_focus_trees(path, text=text)
    return trees[0] if trees else None


def load_focus_trees(path: Path, text: str | None = None) -> list[FocusTree]:
    txt = text if text is not None else path.read_text(encoding="utf-8", errors="ignore")
    wrapped = [
        txt[span.body_start : span.body_end]
        for span in top_level_assignments(txt)
        if span.key == "focus_tree"
        and span.is_block
        and span.body_start is not None
        and span.body_end is not None
    ]
    if wrapped:
        return [_parse_wrapped_tree(body, path) for body in wrapped]
    focuses = _extract_focuses(txt)
    return [_parse_bare_focuses(txt, path)] if focuses else []


def _parse_wrapped_tree(tree_block: str, path: Path) -> FocusTree:
    clean_tree_block = strip_comments(tree_block)
    tree_id_m = TREE_ID_RE.search(clean_tree_block)
    tree_id = tree_id_m.group(1) if tree_id_m else path.stem
    tag_m = None
    for span in top_level_assignments(tree_block):
        if (
            span.key == "country"
            and span.is_block
            and span.body_start is not None
            and span.body_end is not None
        ):
            selector = strip_comments(tree_block[span.body_start : span.body_end])
            tag_m = re.search(r"\b(?:original_tag|tag)\s*=\s*([A-Z0-9]{3})", selector)
            break
    country_tag = tag_m.group(1) if tag_m else ""

    focuses = _extract_focuses(tree_block)
    tree = FocusTree(
        id=tree_id,
        country_tag=country_tag,
        focuses=focuses,
        path=path.resolve(),
        raw_block=tree_block.strip(),
    )
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
    for span in top_level_assignments(text):
        if (
            span.key != "focus"
            or not span.is_block
            or span.body_start is None
            or span.body_end is None
        ):
            continue
        chunk = text[span.body_start : span.body_end]
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

    spans = top_level_assignments(chunk)
    block_values: dict[str, list[str]] = {}
    scalar_values: dict[str, list[str]] = {}
    for span in spans:
        if span.is_block and span.body_start is not None and span.body_end is not None:
            block_values.setdefault(span.key, []).append(
                dedent_block_body(chunk[span.body_start : span.body_end])
            )
        else:
            scalar_values.setdefault(span.key, []).append(
                chunk[span.value_start : span.value_end].strip().strip('"')
            )

    def block(name: str) -> str:
        return block_values.get(name, [""])[0]

    prereqs = _ref_groups(block_values.get("prerequisite", []))
    mutex = _ref_groups(block_values.get("mutually_exclusive", []))

    return Focus(
        id=fid,
        icon=icon_m.group(1) if icon_m else "GFX_goal_generic_construct_civilian",
        x=int(x_m.group(1)) if x_m else 0,
        y=int(y_m.group(1)) if y_m else 0,
        cost=int(cost_m.group(1)) if cost_m else 10,
        prerequisites=prereqs,
        mutually_exclusive=mutex,
        relative_position_id=rel_m.group(1) if rel_m else "",
        search_filters=strip_comments(block("search_filters")).split(),
        completion_reward=block("completion_reward"),
        available=block("available"),
        bypass=block("bypass"),
        select_effect=block("select_effect"),
        complete_tooltip=block("complete_tooltip"),
        allow_branch=block("allow_branch"),
        ai_will_do=block("ai_will_do"),
        cancel_if_invalid=_bool_value(scalar_values.get("cancel_if_invalid", [])),
        continue_if_invalid=_bool_value(scalar_values.get("continue_if_invalid", [])),
        available_if_capitulated=_bool_value(scalar_values.get("available_if_capitulated", [])),
        will_lead_to_war_with=war_m.group(1) if war_m else "",
        raw_block=chunk.strip(),
    )


def _ref_groups(blocks: list[str]) -> list[list[str]]:
    groups: list[list[str]] = []
    for block_text in blocks:
        refs = [rm.group(1) for rm in FOCUS_REF_RE.finditer(strip_comments(block_text))]
        if refs:
            groups.append(refs)
    return groups


def _bool_value(values: list[str]) -> bool | None:
    if not values or values[0] not in {"yes", "no"}:
        return None
    return values[0] == "yes"


def _extract_block_content(chunk: str, block_name: str) -> str:
    match = find_assignment_block(chunk, block_name)
    if not match:
        return ""
    return dedent_block_body(match[0])


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
    if tree.raw_block:
        body = _patch_focus_tree_body(tree, tree.raw_block)
        return "focus_tree = {\n" + _indent(body, 1) + "\n}\n"
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


def _serialize_focus(focus: Focus, indent: int = 1) -> list[str]:
    prefix = "\t" * indent
    if focus.raw_block and not focus.touched:
        lines = [f"{prefix}focus = {{"]
        for line in focus.raw_block.strip().split("\n"):
            lines.append(f"{prefix}\t{line.rstrip()}")
        lines.append(f"{prefix}}}")
        lines.append("")
        return lines

    if focus.raw_block:
        body = _patch_focus_body(focus, focus.raw_block)
        return [f"{prefix}focus = {{", _indent(body, indent + 1), f"{prefix}}}", ""]

    lines = [f"{prefix}focus = {{"]
    lines.append(f"{prefix}\tid = {focus.id}")
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

    lines.append(f"{prefix}}}")
    lines.append("")
    return lines


def write_focus_tree(tree: FocusTree, mod_root: Path) -> Path:
    tag = tree.country_tag or tree.id.replace("_focus", "").upper()
    filename = f"{tag}_focus.txt"
    if tree.path is None:
        tag = require_country_tag(tag)
    out_path = resolve_mod_output_path(
        mod_root, tree.path or Path("common") / "national_focus" / filename
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    content = serialize_focus_tree(tree)
    out_path.write_text(content, encoding="utf-8")
    return out_path


def serialize_focus_file(trees: list[FocusTree], original: str = "") -> str:
    if not original:
        return "\n".join(serialize_focus_tree(tree).rstrip() for tree in trees) + "\n"
    current = {tree.id: tree for tree in trees}
    text = original
    spans: list[tuple[str, AssignmentSpan]] = []
    for span in top_level_assignments(text):
        if (
            span.key != "focus_tree"
            or not span.is_block
            or span.body_start is None
            or span.body_end is None
        ):
            continue
        body = text[span.body_start : span.body_end]
        match = TREE_ID_RE.search(strip_comments(body))
        if match:
            spans.append((match.group(1), span))
    for tree_id, span in sorted(spans, key=lambda item: item[1].start, reverse=True):
        tree = current.pop(tree_id, None)
        if tree is None:
            text = replace_assignment(text, span, None)
        elif tree.raw_block and span.body_start is not None and span.body_end is not None:
            tree_body = text[span.body_start : span.body_end]
            text = replace_assignment_body(text, span, _patch_focus_tree_body(tree, tree_body))
        else:
            text = replace_assignment(text, span, serialize_focus_tree(tree).strip())
    for tree in current.values():
        text = append_assignment(text, serialize_focus_tree(tree).strip())
    return text


def _patch_focus_tree_body(tree: FocusTree, body: str) -> str:
    if tree.touched:
        body = set_scalar(body, "id", tree.id)
        body = set_scalar(
            body, "default", None if tree.default is None else ("yes" if tree.default else "no")
        )
        body = set_block(body, "continuous_focus_position", tree.continuous_focus_position or None)
        current_shared = [
            body[span.value_start : span.value_end].strip().strip('"')
            for span in assignment_spans(body, "shared_focus")
            if not span.is_block
        ]
        if current_shared != tree.shared_focuses:
            for span in reversed(assignment_spans(body, "shared_focus")):
                body = replace_assignment(body, span, None)
            for shared in tree.shared_focuses:
                body = append_assignment(body, f"shared_focus = {shared}")

    current = {focus.id: focus for focus in tree.focuses}
    focus_spans: list[tuple[str, AssignmentSpan]] = []
    for span in top_level_assignments(body):
        if (
            span.key != "focus"
            or not span.is_block
            or span.body_start is None
            or span.body_end is None
        ):
            continue
        focus_body = body[span.body_start : span.body_end]
        match = FOCUS_ID_RE.search(strip_comments(focus_body))
        if match:
            focus_spans.append((match.group(1), span))
    for focus_id, span in sorted(focus_spans, key=lambda item: item[1].start, reverse=True):
        focus = current.pop(focus_id, None)
        if focus is None:
            body = replace_assignment(body, span, None)
        elif span.body_start is not None and span.body_end is not None:
            focus_body = body[span.body_start : span.body_end]
            if focus.raw_block:
                body = replace_assignment_body(body, span, _patch_focus_body(focus, focus_body))
            else:
                body = replace_assignment(
                    body, span, "\n".join(_serialize_focus(focus, indent=0)).strip()
                )
    for focus in current.values():
        body = append_assignment(body, "\n".join(_serialize_focus(focus, indent=0)).strip())
    return body


def _patch_focus_body(focus: Focus, body: str) -> str:
    if not focus.touched:
        return body
    body = set_scalar(body, "id", focus.id)
    body = _set_scalar_unless_implicit_default(
        body, "icon", focus.icon, "GFX_goal_generic_construct_civilian"
    )
    body = _set_scalar_unless_implicit_default(body, "x", str(focus.x), "0")
    body = _set_scalar_unless_implicit_default(body, "y", str(focus.y), "0")
    body = _set_scalar_unless_implicit_default(body, "cost", str(focus.cost), "10")
    body = set_scalar(body, "relative_position_id", focus.relative_position_id or None)
    for key, flag_value in [
        ("cancel_if_invalid", focus.cancel_if_invalid),
        ("continue_if_invalid", focus.continue_if_invalid),
        ("available_if_capitulated", focus.available_if_capitulated),
    ]:
        body = set_scalar(body, key, None if flag_value is None else ("yes" if flag_value else "no"))
    body = set_scalar(body, "will_lead_to_war_with", focus.will_lead_to_war_with or None)
    for key, block_value in [
        ("search_filters", " ".join(focus.search_filters)),
        ("available", focus.available),
        ("bypass", focus.bypass),
        ("select_effect", focus.select_effect),
        ("completion_reward", focus.completion_reward),
        ("complete_tooltip", focus.complete_tooltip),
        ("allow_branch", focus.allow_branch),
        ("ai_will_do", focus.ai_will_do),
    ]:
        body = set_block(body, key, block_value or None)
    for key, groups in [
        ("prerequisite", focus.prerequisites),
        ("mutually_exclusive", focus.mutually_exclusive),
    ]:
        existing = _ref_groups(
            [
                body[span.body_start : span.body_end]
                for span in assignment_spans(body, key)
                if span.is_block and span.body_start is not None and span.body_end is not None
            ]
        )
        if existing == groups:
            continue
        for span in reversed(assignment_spans(body, key)):
            body = replace_assignment(body, span, None)
        for group in groups:
            body = append_assignment(
                body, f"{key} = {{ " + " ".join(f"focus = {ref}" for ref in group) + " }"
            )
    return body


def _set_scalar_unless_implicit_default(
    body: str, key: str, value: str, implicit_default: str
) -> str:
    if assignment_spans(body, key) or value != implicit_default:
        return set_scalar(body, key, value)
    return body


def _indent(text: str, count: int) -> str:
    prefix = "\t" * count
    return "\n".join(prefix + line.rstrip() for line in text.strip().splitlines())
