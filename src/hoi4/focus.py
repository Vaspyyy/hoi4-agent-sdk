"""
Focus Tree System

Read, modify, and write HOI4 national focus trees.

A focus file holds ``focus_tree = { ... }`` blocks and top-level
``shared_focus = { ... }`` / ``joint_focus = { ... }`` (and bare ``focus``)
blocks. Each tree becomes a :class:`FocusTree`; the top-level blocks of a file
are collected in one container tree with ``kind == "file"`` whose ID is the file
name without extension.

Everything is read from :mod:`hoi4.syntax` tree queries. Writing patches the
original text: a changed field replaces only its own span, so comments, keys
the model does not know and the spelling of unchanged values stay as they were.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from .patching import (
    AssignmentSpan,
    append_assignment,
    assignment_spans,
    dedent_block_body,
    replace_assignment,
    replace_assignment_body,
    set_block,
    set_scalar,
)
from .paths import require_country_tag, resolve_mod_output_path
from .syntax import Assignment, Block, parse
from .textio import read_text, write_text
from .types import Focus, FocusTree

#: Keys of focus blocks that may stand at the top level of a focus file.
FOCUS_KINDS = ("focus", "shared_focus", "joint_focus")

DEFAULT_ICON = "GFX_goal_generic_construct_civilian"
_DEFAULTS = {"x": 0, "y": 0, "cost": 10}
_FLAG_KEYS = ("cancel_if_invalid", "continue_if_invalid", "available_if_capitulated")
_BLOCK_FIELDS = (
    "available",
    "bypass",
    "select_effect",
    "completion_reward",
    "complete_tooltip",
    "allow_branch",
    "ai_will_do",
)


# ── loading ──────────────────────────────────────────────────────


def load_focus_tree(
    path: Path, text: str | None = None, *, tree_id: str | None = None
) -> Optional[FocusTree]:
    """Return the first tree of a file, or the one with ``tree_id``."""

    trees = load_focus_trees(path, text=text)
    if tree_id is not None:
        return next((tree for tree in trees if tree.id == tree_id), None)
    return trees[0] if trees else None


def load_focus_trees(path: Path, text: str | None = None) -> list[FocusTree]:
    """Return the ``focus_tree`` blocks of a file, then its top-level focus container."""

    txt = text if text is not None else read_text(path)
    root = parse(txt).root
    constants = _file_constants(root)
    trees: list[FocusTree] = []
    loose: list[Focus] = []
    for item in root.assignments():
        block = item.block
        if block is None or item.op != "=":
            continue
        if item.name == "focus_tree":
            trees.append(_parse_tree(block, path, constants))
        elif item.name in FOCUS_KINDS:
            focus = _parse_focus(block, constants, kind=item.name)
            if focus is not None:
                loose.append(focus)
    if loose:
        trees.append(FocusTree(id=path.stem, focuses=loose, path=path, kind="file"))
    return trees


def _file_constants(root: Block) -> dict[str, str]:
    """Scripted constants such as ``@cost_tier_1 = 5`` defined at the top of a file."""

    constants: dict[str, str] = {}
    for item in root.assignments():
        value = item.scalar
        if item.name.startswith("@") and value is not None:
            constants.setdefault(item.name, value)
    return constants


def _parse_tree(block: Block, path: Path, constants: dict[str, str]) -> FocusTree:
    tree = FocusTree(
        id=block.scalar("id") or path.stem,
        country_tag=_country_tag(block),
        focuses=_focuses_in(block, constants),
        path=path.resolve(),
        raw_block=block.body.strip(),
    )
    _read_tree_fields(tree, block)
    return tree


def _read_tree_fields(tree: FocusTree, block: Block) -> None:
    """Fill the tree-level fields that can be edited after loading."""

    tree.default = _bool(block.scalar("default"))
    position = block.block("continuous_focus_position")
    tree.continuous_focus_position = dedent_block_body(position.body) if position else ""
    tree.shared_focuses = _scalars(block, "shared_focus")


def _country_tag(tree_block: Block) -> str:
    """The first ``tag``/``original_tag`` in the tree's ``country`` selector."""

    selector = tree_block.block("country")
    if selector is None:
        return ""
    found = [
        (item.start, item.scalar)
        for item, _ in selector.walk()
        if item.name in ("tag", "original_tag")
        and item.scalar is not None
        and len(item.scalar) == 3
        and item.scalar.isalnum()
        and item.scalar.isupper()
    ]
    return min(found)[1] if found else ""


def _focuses_in(block: Block, constants: dict[str, str]) -> list[Focus]:
    focuses: list[Focus] = []
    for item in block.assignments("focus"):
        body = item.block
        if body is None:
            continue
        focus = _parse_focus(body, constants)
        if focus is not None:
            focuses.append(focus)
    return focuses


def _parse_focus(block: Block, constants: dict[str, str], *, kind: str = "focus") -> Focus | None:
    focus_id = block.scalar("id")
    if not focus_id:
        return None
    icon_blocks: list[str] = []
    for item in block.assignments("icon"):
        icon_body = item.block
        if icon_body is not None:
            icon_blocks.append(dedent_block_body(icon_body.body))
    focus = Focus(
        id=focus_id,
        x=int(_number(block, "x", constants)),
        y=int(_number(block, "y", constants)),
        cost=_number(block, "cost", constants),
        prerequisites=_ref_groups(block, "prerequisite"),
        mutually_exclusive=_ref_groups(block, "mutually_exclusive"),
        relative_position_id=block.scalar("relative_position_id") or "",
        search_filters=_list_values(block, "search_filters"),
        cancel_if_invalid=_bool(block.scalar("cancel_if_invalid")),
        continue_if_invalid=_bool(block.scalar("continue_if_invalid")),
        available_if_capitulated=_bool(block.scalar("available_if_capitulated")),
        will_lead_to_war_with=block.scalar("will_lead_to_war_with") or "",
        raw_block=block.body.strip(),
        icon_blocks=icon_blocks,
        kind=kind,
    )
    for name in _BLOCK_FIELDS:
        value = block.block(name)
        setattr(focus, name, dedent_block_body(value.body) if value is not None else "")
    plain_icon = block.scalar("icon")
    if plain_icon:
        focus.icon = plain_icon
    else:
        named = focus.icon_names()[1:]
        focus.icon = named[0] if named else DEFAULT_ICON
    return focus


def _number(block: Block, key: str, constants: dict[str, str]) -> float:
    """Read a numeric field, resolving file constants; fall back to the game default."""

    raw = block.scalar(key)
    if raw is not None and raw.startswith("@"):
        raw = constants.get(raw)
    parsed = _parse_number(raw)
    return _DEFAULTS[key] if parsed is None else parsed


def _parse_number(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    if not value.is_finite():
        return None
    if value == value.to_integral_value() and "." not in raw:
        return int(value)
    return float(value)


def _format_number(value: float) -> str:
    if isinstance(value, int) or float(value).is_integer():
        return str(int(value))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _ref_groups(block: Block, key: str) -> list[list[str]]:
    return [group for _, group in _ref_group_items(block, key)]


def _ref_group_items(block: Block, key: str) -> list[tuple[Assignment, list[str]]]:
    """``prerequisite = { focus = A focus = B }`` items with their focus IDs.

    Groups without any ``focus = ...`` are not modeled and stay untouched.
    """

    groups: list[tuple[Assignment, list[str]]] = []
    for item in block.assignments(key):
        body = item.block
        if body is None:
            continue
        refs = [ref.scalar for ref in body.assignments("focus") if ref.scalar]
        if refs:
            groups.append((item, refs))
    return groups


def _list_values(block: Block, key: str) -> list[str]:
    body = block.block(key)
    return [value.value for value in body.values()] if body is not None else []


def _scalars(block: Block, key: str) -> list[str]:
    return [item.scalar for item in block.assignments(key) if item.scalar is not None]


def _bool(value: str | None) -> bool | None:
    if value not in ("yes", "no"):
        return None
    return value == "yes"


def _yes_no(value: bool | None) -> str | None:
    return None if value is None else ("yes" if value else "no")


# ── rendering new script ─────────────────────────────────────────


def serialize_focus_tree(tree: FocusTree) -> str:
    """Render one tree (or a file container) as a complete snippet."""

    if tree.kind == "file":
        return "\n".join(_render_focus(focus, focus.kind) for focus in tree.focuses) + "\n"
    if tree.raw_block:
        body = _patch_focus_tree_body(tree, "\n\t" + _reindent(tree.raw_block, "\t") + "\n", {})
        return "focus_tree = {" + body + "}\n"
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
        parts.append(_reindent(_render_focus(focus), "\t"))
        parts.append("")

    parts.append("}")
    parts.append("")
    return "\n".join(parts)


def _render_focus(focus: Focus, key: str = "focus") -> str:
    """Render one focus block at indent zero."""

    if focus.raw_block:
        body = "\n\t" + _reindent(focus.raw_block, "\t") + "\n"
        return f"{key} = {{" + _patch_focus_body(focus, body, {}) + "}"
    lines = [f"{key} = {{", f"\tid = {focus.id}"]
    for icon_body in focus.icon_blocks:
        lines.append(_reindent(_render_block("icon", icon_body), "\t"))
    lines.append(f"\ticon = {focus.icon}")
    lines.append(f"\tx = {focus.x}")
    lines.append(f"\ty = {focus.y}")
    if focus.relative_position_id:
        lines.append(f"\trelative_position_id = {focus.relative_position_id}")
    lines.append(f"\tcost = {_format_number(focus.cost)}")
    for key_name, groups in (
        ("prerequisite", focus.prerequisites),
        ("mutually_exclusive", focus.mutually_exclusive),
    ):
        for group in groups:
            lines.append(f"\t{key_name} = {{ " + " ".join(f"focus = {ref}" for ref in group) + " }")
    if focus.search_filters:
        lines.append(f"\tsearch_filters = {{ {' '.join(focus.search_filters)} }}")
    for flag in _FLAG_KEYS:
        flag_value = _yes_no(getattr(focus, flag))
        if flag_value is not None:
            lines.append(f"\t{flag} = {flag_value}")
    if focus.will_lead_to_war_with:
        lines.append(f"\twill_lead_to_war_with = {focus.will_lead_to_war_with}")
    for name in _BLOCK_FIELDS:
        value = getattr(focus, name)
        if value:
            lines.append(_reindent(_render_block(name, value, compact=False), "\t"))
    lines.append("}")
    return "\n".join(lines)


def _render_block(key: str, body: str, *, compact: bool = True) -> str:
    cleaned = _reindent(body.strip(), "")
    if not cleaned:
        return f"{key} = {{ }}"
    if compact and "\n" not in cleaned and "#" not in cleaned and len(cleaned) <= 60:
        return f"{key} = {{ {cleaned} }}"
    return f"{key} = {{\n" + _reindent(cleaned, "\t") + "\n}"


def _reindent(text: str, indent: str) -> str:
    """Put ``indent`` before every line of a stripped body, keeping relative depth.

    A stored body (``raw_block``) starts at its first token, so its first line
    has no indentation while the following lines keep their source indent.
    """

    lines = text.strip("\n").splitlines()
    if not lines:
        return ""
    first, rest = lines[0], lines[1:]
    first_indent = len(first) - len(first.lstrip(" \t"))
    # Lines after the first are its siblings or children; siblings sit at the
    # smallest indent found.
    widths = [len(line) - len(line.lstrip(" \t")) for line in rest if line.strip()]
    if first_indent:
        widths.append(first_indent)
    common = min(widths) if widths else 0
    out = [indent + first[min(first_indent, common) :].rstrip()]
    for line in rest:
        out.append(indent + line[common:].rstrip() if line.strip() else "")
    return "\n".join(out)


def write_focus_tree(tree: FocusTree, mod_root: Path) -> Path:
    tag = tree.country_tag or tree.id.replace("_focus", "").upper()
    filename = f"{tag}_focus.txt"
    if tree.path is None:
        tag = require_country_tag(tag)
    out_path = resolve_mod_output_path(
        mod_root, tree.path or Path("common") / "national_focus" / filename
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    original = out_path.read_bytes() if out_path.exists() else None
    write_text(out_path, serialize_focus_tree(tree), like=original)
    return out_path


# ── patching an existing file ────────────────────────────────────


def serialize_focus_file(trees: list[FocusTree], original: str = "") -> str:
    """Write ``trees`` (all models that live in one file) over ``original``.

    Each block the models own is patched in place; blocks the models no longer
    contain are removed, new ones are appended. Everything else in the file
    (``style``, constants, blocks without an ``id``) stays byte-identical.
    """

    if not original:
        return "\n".join(serialize_focus_tree(tree).rstrip() for tree in trees) + "\n"
    constants = _file_constants(parse(original).root)
    current = {tree.id: tree for tree in trees if tree.kind != "file"}
    loose: dict[str, Focus] = {}
    for tree in trees:
        if tree.kind == "file":
            for focus in tree.focuses:
                loose.setdefault(focus.id, focus)

    owned: list[tuple[str, str, AssignmentSpan]] = []
    for item in parse(original).root.assignments():
        block = item.block
        if block is None or item.op != "=":
            continue
        if item.name == "focus_tree" or item.name in FOCUS_KINDS:
            block_id = block.scalar("id")
            if block_id:
                owned.append((item.name, block_id, _assignment_span(item)))

    text = original
    for key, block_id, span in reversed(owned):
        if key == "focus_tree":
            owner = current.pop(block_id, None)
            if owner is None:
                text = replace_assignment(text, span, None)
            elif owner.raw_block:
                assert span.body_start is not None and span.body_end is not None
                tree_body = text[span.body_start : span.body_end]
                text = replace_assignment_body(
                    text, span, _patch_focus_tree_body(owner, tree_body, constants)
                )
            else:
                text = replace_assignment(text, span, serialize_focus_tree(owner).strip())
            continue
        model = loose.pop(block_id, None)
        if model is None:
            text = replace_assignment(text, span, None)
            continue
        text = _patch_focus_span(text, span, model, model.kind, constants)
    for new_tree in current.values():
        text = append_assignment(text, serialize_focus_tree(new_tree).strip())
    for new_focus in loose.values():
        text = append_assignment(text, _render_focus(new_focus, new_focus.kind))
    return text


def _assignment_span(item: Assignment) -> AssignmentSpan:
    block = item.block
    assert block is not None
    return AssignmentSpan(
        item.name, item.start, item.end, item.value.start, item.value.end, True,
        block.body_start, block.body_end,
    )


def _patch_focus_span(
    text: str, span: AssignmentSpan, focus: Focus, key: str, constants: dict[str, str]
) -> str:
    """Patch one ``<key> = { ... }`` focus block of ``text`` in place."""

    if span.body_start is None or span.body_end is None:
        return text
    if focus.raw_block:
        body = text[span.body_start : span.body_end]
        text = replace_assignment_body(text, span, _patch_focus_body(focus, body, constants))
    else:
        text = replace_assignment(text, span, _render_focus(focus, key))
        return text
    if span.key != key:
        # ``shared_focus = {`` became ``joint_focus = {``: only the key changes.
        text = text[: span.start] + key + text[span.start + len(span.key) :]
    return text


def _patch_focus_tree_body(tree: FocusTree, body: str, constants: dict[str, str]) -> str:
    if tree.touched:
        root = parse(body).root
        baseline = FocusTree(id=root.scalar("id") or tree.id)
        _read_tree_fields(baseline, root)
        if baseline.id != tree.id:
            body = set_scalar(body, "id", tree.id)
        if baseline.default != tree.default:
            body = set_scalar(body, "default", _yes_no(tree.default))
        if baseline.continuous_focus_position != tree.continuous_focus_position:
            body = set_block(
                body, "continuous_focus_position", tree.continuous_focus_position or None
            )
        if baseline.shared_focuses != tree.shared_focuses:
            body = _sync_scalar_list(body, "shared_focus", tree.shared_focuses)

    current = {focus.id: focus for focus in tree.focuses}
    focus_spans: list[tuple[str, AssignmentSpan]] = []
    for item in parse(body).root.assignments("focus"):
        block = item.block
        if block is None or item.op != "=":
            continue
        focus_id = block.scalar("id")
        if focus_id:
            focus_spans.append((focus_id, _assignment_span(item)))
    for focus_id, span in reversed(focus_spans):
        focus = current.pop(focus_id, None)
        if focus is None:
            body = replace_assignment(body, span, None)
        else:
            body = _patch_focus_span(body, span, focus, "focus", constants)
    for focus in current.values():
        body = append_assignment(body, _render_focus(focus))
    return body


def _patch_focus_body(focus: Focus, body: str, constants: dict[str, str]) -> str:
    """Write the fields of ``focus`` that differ from what ``body`` says.

    The baseline is the focus read back from ``body`` itself, so a field the
    caller did not change is never rewritten and keeps its exact spelling.
    """

    if not focus.touched:
        return body
    baseline = _baseline(body, constants)

    if baseline.id != focus.id:
        body = set_scalar(body, "id", focus.id)
    for key in ("x", "y", "cost"):
        value = getattr(focus, key)
        if getattr(baseline, key) != value:
            body = set_scalar(body, key, _format_number(value))
    if baseline.relative_position_id != focus.relative_position_id:
        body = set_scalar(body, "relative_position_id", focus.relative_position_id or None)
    for flag in _FLAG_KEYS:
        if getattr(baseline, flag) != getattr(focus, flag):
            body = set_scalar(body, flag, _yes_no(getattr(focus, flag)))
    if baseline.will_lead_to_war_with != focus.will_lead_to_war_with:
        body = set_scalar(body, "will_lead_to_war_with", focus.will_lead_to_war_with or None)
    if baseline.search_filters != focus.search_filters:
        body = set_block(body, "search_filters", " ".join(focus.search_filters) or None)
    for name in _BLOCK_FIELDS:
        if getattr(baseline, name) != getattr(focus, name):
            body = set_block(body, name, getattr(focus, name) or None)
    if baseline.prerequisites != focus.prerequisites:
        body = _sync_ref_groups(body, "prerequisite", focus.prerequisites)
    if baseline.mutually_exclusive != focus.mutually_exclusive:
        body = _sync_ref_groups(body, "mutually_exclusive", focus.mutually_exclusive)
    if baseline.icon_blocks != focus.icon_blocks:
        body = _sync_icon_blocks(body, focus.icon_blocks)
    icon_now = _baseline(body, constants).icon
    if icon_now != focus.icon:
        body = _set_plain_icon(body, focus.icon)
    return body


def _baseline(body: str, constants: dict[str, str]) -> Focus:
    return _parse_focus(parse(body).root, constants) or Focus(id="")


# ── span helpers for repeated keys ───────────────────────────────


def _set_block_at(text: str, span: AssignmentSpan, body: str) -> str:
    """Give one block assignment a new body, rendered like its current one."""

    line_start = text.rfind("\n", 0, span.start) + 1
    if text[line_start : span.start].strip():
        line_start = span.start
    piece = set_block(text[line_start : span.end], span.key, body)
    return text[:line_start] + piece + text[span.end :]


def _insert_after(text: str, span: AssignmentSpan, statement: str) -> str:
    line_start = text.rfind("\n", 0, span.start) + 1
    prefix = text[line_start : span.start]
    if prefix.strip():
        # A one-line block: stay on the line; a multi-line statement keeps
        # its own line breaks so a comment never swallows the rest.
        return text[: span.end] + " " + statement + text[span.end :]
    lines = statement.splitlines()
    rendered = "\n".join(prefix + line if line else line for line in lines)
    return text[: span.end] + "\n" + rendered + text[span.end :]


def _insert_before(text: str, span: AssignmentSpan, statement: str) -> str:
    line_start = text.rfind("\n", 0, span.start) + 1
    prefix = text[line_start : span.start]
    if prefix.strip():
        separator = "\n" if "\n" in statement else " "
        return text[: span.start] + statement + separator + text[span.start :]
    lines = statement.splitlines()
    rendered = "\n".join(prefix + line if line else line for line in lines)
    return text[:line_start] + rendered + "\n" + text[line_start:]


def _sync_scalar_list(body: str, key: str, values: list[str]) -> str:
    """Make the ``key = value`` statements of ``body`` match ``values``, in place."""

    spans = [span for span in assignment_spans(body, key) if not span.is_block]
    for index in reversed(range(len(spans))):
        span = spans[index]
        if index < len(values):
            if body[span.value_start : span.value_end].strip('"') != values[index]:
                body = body[: span.value_start] + values[index] + body[span.value_end :]
        else:
            body = replace_assignment(body, span, None)
    extra = values[len(spans) :]
    if extra:
        spans = [span for span in assignment_spans(body, key) if not span.is_block]
        for value in reversed(extra):
            statement = f"{key} = {value}"
            body = _insert_after(body, spans[-1], statement) if spans else append_assignment(
                body, statement
            )
    return body


def _sync_ref_groups(body: str, key: str, groups: list[list[str]]) -> str:
    """Make the modeled ``key = { focus = ... }`` groups of ``body`` match ``groups``."""

    def spans() -> list[AssignmentSpan]:
        return [_assignment_span(item) for item, _ in _ref_group_items(parse(body).root, key)]

    existing = spans()
    current = _ref_groups(parse(body).root, key)
    for index in reversed(range(len(existing))):
        if index < len(groups):
            if current[index] != groups[index]:
                body = _set_block_at(
                    body, existing[index], "\n".join(f"focus = {ref}" for ref in groups[index])
                )
        else:
            body = replace_assignment(body, existing[index], None)
    extra = groups[len(existing) :]
    if extra:
        existing = spans()
        for group in reversed(extra):
            statement = f"{key} = {{ " + " ".join(f"focus = {ref}" for ref in group) + " }"
            body = (
                _insert_after(body, existing[-1], statement)
                if existing
                else append_assignment(body, statement)
            )
    return body


def _sync_icon_blocks(body: str, icon_blocks: list[str]) -> str:
    """Make the ``icon = { ... }`` blocks of ``body`` match ``icon_blocks``, in place."""

    def spans() -> list[AssignmentSpan]:
        return [span for span in assignment_spans(body, "icon") if span.is_block]

    existing = spans()
    for index in reversed(range(len(existing))):
        span = existing[index]
        if index < len(icon_blocks):
            assert span.body_start is not None and span.body_end is not None
            if dedent_block_body(body[span.body_start : span.body_end]) != icon_blocks[index]:
                body = _set_block_at(body, span, icon_blocks[index])
        else:
            body = replace_assignment(body, span, None)
    extra = icon_blocks[len(existing) :]
    if extra:
        existing = spans()
        plain = [span for span in assignment_spans(body, "icon") if not span.is_block]
        for icon_body in reversed(extra):
            statement = _render_block("icon", icon_body)
            if existing:
                body = _insert_after(body, existing[-1], statement)
            elif plain:
                body = _insert_before(body, plain[0], statement)
            else:
                body = append_assignment(body, statement)
    return body


def _set_plain_icon(body: str, icon: str) -> str:
    """Set the plain ``icon = GFX_...`` entry without touching icon blocks."""

    plain = [span for span in assignment_spans(body, "icon") if not span.is_block]
    if plain:
        span = plain[0]
        return body[: span.value_start] + icon + body[span.value_end :]
    blocks = [span for span in assignment_spans(body, "icon") if span.is_block]
    if blocks:
        return _insert_after(body, blocks[-1], f"icon = {icon}")
    return append_assignment(body, f"icon = {icon}")
