"""
Ideas / National Spirits - read, create, and write HOI4 ideas.

Idea files live in ``common/ideas/*.txt`` (and the SDK's legacy
``common/national_ideas``). The game reads them as::

    ideas = {
        <category> = {          # country, hidden_ideas, political_advisor, ...
            designer = yes      # category settings (scalars)
            <idea_id> = { ... } # every block inside a category is an idea
        }
    }

Everything is read from one :mod:`hoi4.syntax` tree per file, and every write
replaces the exact span of what changed, so keys the model does not know,
comments, repeated categories and formatting survive byte for byte.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

from ._mapping_patch import patch_scalar_entries, scalar_entries
from .idea_icons import DEFAULT_IDEA_ICON, normalize_idea_icon
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
from .script import pdx_string, pdx_value
from .syntax import (
    Assignment,
    Block,
    Document,
    parse,
    quote,
    require_entry,
    require_key,
    require_well_formed,
)
from .textio import read_text, write_text
from .types import Idea

VALID_CONTAINERS = ("country_ideas", "ideas")

# The game reads every child of ``ideas = { ... }`` as a category. Older SDK
# versions also wrote ideas directly into the container (no category), so a
# container child that clearly carries idea properties is read as such an
# uncategorised idea. This is only a container-level compatibility check:
# inside a category, every ``id = { ... }`` block is an idea, whatever it holds.
_DIRECT_IDEA_SCALARS = frozenset({"picture", "icon", "desc"})
_DIRECT_IDEA_BLOCKS = frozenset({
    "modifier",
    "allowed",
    "allowed_civil_war",
    "allowed_to_remove",
    "available",
    "visible",
    "cancel",
    "research_bonus",
    "equipment_bonus",
    "targeted_modifier",
    "rule",
    "traits",
    "ai_will_do",
    "on_add",
    "on_remove",
    "do_effect",
})


# ── tree queries ──────────────────────────────────────────────


def _is_block_assignment(item: object) -> bool:
    return type(item) is Assignment and item.op == "=" and type(item.value) is Block


def _containers(root: Block) -> list[Assignment]:
    """Return the top-level ``ideas = { ... }`` / ``country_ideas = { ... }`` blocks."""

    return [
        item
        for item in root.items
        if _is_block_assignment(item)
        and type(item) is Assignment
        and item.key.value in VALID_CONTAINERS
    ]


def _is_direct_idea(block: Block) -> bool:
    for item in block.items:
        if type(item) is not Assignment or item.op != "=":
            continue
        key = item.key.value
        if type(item.value) is Block:
            if key in _DIRECT_IDEA_BLOCKS:
                return True
        elif key in _DIRECT_IDEA_SCALARS:
            return True
    return False


def _iter_entries(
    containers: Iterable[Assignment],
) -> Iterator[tuple[Assignment, Assignment | None]]:
    """Yield ``(idea, category)`` for every idea; ``category`` is ``None`` for a direct idea."""

    for container in containers:
        assert type(container.value) is Block
        for item in container.value.items:
            if not _is_block_assignment(item):
                continue
            assert type(item) is Assignment and type(item.value) is Block
            if _is_direct_idea(item.value):
                yield item, None
                continue
            for child in item.value.items:
                if _is_block_assignment(child):
                    assert type(child) is Assignment
                    yield child, item


def _direct_block(block: Block, key: str) -> Block | None:
    for item in block.assignments(key):
        if item.op == "=" and type(item.value) is Block:
            return item.value
    return None


def _category_settings(block: Block) -> str:
    """Return the category's own statements (everything except its ideas)."""

    return "\n".join(item.raw for item in block.items if not _is_block_assignment(item))


def detect_ideas_container(text: str) -> str:
    containers = _containers(parse(text).root)
    if containers:
        return containers[0].key.value
    return "country_ideas"


# ── reading ───────────────────────────────────────────────────


def scan_idea_ids_file(path: Path) -> set[str]:
    """Extract idea IDs without parsing every modeled property."""

    return idea_ids_in_text(read_text(path))


def idea_ids_in_text(text: str) -> set[str]:
    """The id of every idea in the text of an ideas file."""

    root = parse(text).root
    return {idea.key.value for idea, _ in _iter_entries(_containers(root))}


def _coerce(value: str) -> Any:
    if value.lower() in ("yes", "no"):
        return value.lower() == "yes"
    if "." in value:
        try:
            return float(value)
        except ValueError:
            return value
    try:
        return int(value)
    except ValueError:
        return value


def _parse_kv(block: Block) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for item in block.assignments():
        if item.op != "=":
            continue
        value = item.scalar
        if value is None:
            continue
        props[item.key.value] = _coerce(value.strip())
    return props


def _idea_from_block(idea_id: str, block: Block) -> Idea:
    idea = Idea(id=idea_id.strip())
    picture = block.scalar("picture") or ""
    icon = block.scalar("icon") or ""
    if picture or icon:
        idea.icon = normalize_idea_icon(picture or icon)
    idea.desc = block.scalar("desc") or ""
    removal_cost = (block.scalar("removal_cost") or "").strip()
    if removal_cost:
        try:
            idea.removal_cost = int(removal_cost)
        except ValueError:
            try:
                idea.removal_cost = float(removal_cost)
            except ValueError:
                # Preserve scripted/custom scalar values instead of dropping
                # them when another modeled field is edited.
                idea.removal_cost = removal_cost
    modifier = _direct_block(block, "modifier")
    if modifier is not None:
        idea.modifier = _parse_kv(modifier)
    allowed = _direct_block(block, "allowed")
    if allowed is not None:
        idea.allowed = dedent_block_body(allowed.body)
    research = _direct_block(block, "research_bonus")
    if research is not None:
        idea.research_bonus = _parse_kv(research)
    traits = _direct_block(block, "traits")
    if traits is not None:
        idea.traits = [scalar.value for scalar in traits.values()]
    ai_will_do = _direct_block(block, "ai_will_do")
    if ai_will_do is not None:
        idea.ai_will_do = dedent_block_body(ai_will_do.body)
    idea.raw_block = block.body.strip()
    return idea


def read_ideas_text(
    text: str, path: Path | None = None, *, document: Document | None = None
) -> tuple[list[Idea], str]:
    """Read every idea of an ideas file's text; returns ``(ideas, container_name)``.

    An idea is any ``id = { ... }`` block inside a category block of the
    ``ideas`` container, whatever keys it holds. A block that is still open at
    the end of the text ends there, the way the game reads it. ``document``
    is ``text`` already parsed.
    """

    containers = _containers((document if document is not None else parse(text)).root)
    container_name = containers[0].key.value if containers else "country_ideas"
    ideas: list[Idea] = []
    # One settings string per category occurrence, shared by its ideas.
    settings: dict[int, str] = {}
    for entry, category in _iter_entries(containers):
        assert type(entry.value) is Block
        idea = _idea_from_block(entry.key.value, entry.value)
        idea.path = path
        if category is not None:
            assert type(category.value) is Block
            idea.category = category.key.value
            shared = settings.get(category.start)
            if shared is None:
                shared = settings[category.start] = _category_settings(category.value)
            idea.category_raw_block = shared
        ideas.append(idea)
    return ideas, container_name


def read_ideas_file(path: Path) -> tuple[list[Idea], str]:
    return read_ideas_text(read_text(path), path)


def _picture(icon: str) -> str | None:
    """The ``picture`` value of an idea icon, spelled as one scalar."""

    name = normalize_idea_icon(icon)
    return quote(name) if name else None


def serialize_idea(idea: Idea, indent: int = 1) -> str:
    tab = "\t" * indent
    if idea.raw_block:
        body = _patch_idea_body(idea, idea.raw_block)
        return f"{tab}{idea.id} = {{\n{_indent(body, indent + 1)}\n{tab}}}"
    lines = [f"{tab}{idea.id} = {{"]
    lines.append(f"{tab}\tpicture = {quote(normalize_idea_icon(idea.icon))}")
    if idea.removal_cost is not None:
        lines.append(f"{tab}\tremoval_cost = {pdx_value(idea.removal_cost)}")
    # A body with a stray or missing brace would change the script around it.
    for name in ("allowed", "ai_will_do"):
        require_well_formed(getattr(idea, name), f"'{name}' of idea '{idea.id}'")
    if idea.allowed:
        lines.append(f"{tab}\tallowed = {{")
        for line in idea.allowed.strip().split("\n"):
            lines.append(f"{tab}\t\t{line.strip()}")
        lines.append(f"{tab}\t}}")
    if idea.modifier:
        lines.append(f"{tab}\tmodifier = {{")
        for key, value in idea.modifier.items():
            rendered = (
                ("yes" if value else "no") if isinstance(value, bool)
                else str(value) if isinstance(value, (int, float))
                else pdx_string(value)
            )
            lines.append(f"{tab}\t\t" + require_entry(key, rendered, f"the modifier '{key}'"))
        lines.append(f"{tab}\t}}")
    if idea.research_bonus:
        lines.append(f"{tab}\tresearch_bonus = {{")
        for key, value in idea.research_bonus.items():
            lines.append(
                f"{tab}\t\t" + require_entry(key, pdx_value(value), f"the research bonus '{key}'")
            )
        lines.append(f"{tab}\t}}")
    if idea.ai_will_do:
        lines.append(f"{tab}\tai_will_do = {{")
        for line in idea.ai_will_do.strip().split("\n"):
            lines.append(f"{tab}\t\t{line.strip()}")
        lines.append(f"{tab}\t}}")
    if idea.traits:
        lines.append(f"{tab}\ttraits = {{ {' '.join(quote(trait) for trait in idea.traits)} }}")
    lines.append(f"{tab}}}")
    return "\n".join(lines)


def serialize_ideas_file(
    ideas: list[Idea],
    container_name: str = "country_ideas",
    original: str = "",
) -> str:
    """Render the ideas of one file.

    With ``original`` (the file's current text) only what changed is replaced:
    a patched field, a removed or moved idea, an appended new idea. Everything
    else, including blocks the model does not know, keeps its exact bytes.
    """

    if original:
        root = parse(original).root
        containers = _containers(root)
        if containers:
            return _patch_ideas_text(original, containers, ideas)

    uncategorized: list[Idea] = []
    categories: dict[str, list[Idea]] = {}
    for idea in ideas:
        if not idea.category:
            uncategorized.append(idea)
            continue
        categories.setdefault(idea.category, []).append(idea)
    parts = [f"{container_name} = {{"]
    for idea in uncategorized:
        parts.append(serialize_idea(idea))
        parts.append("")
    for category, category_ideas in categories.items():
        parts.append(_serialize_category(category, category_ideas, indent=1))
        parts.append("")
    parts.append("}")
    parts.append("")
    rendered = "\n".join(parts)
    if original.strip():
        # A file without an ideas container keeps its other content.
        return append_assignment(original, rendered.rstrip("\n"))
    return rendered


def _serialize_category(category: str, ideas: list[Idea], indent: int) -> str:
    prefix = "\t" * indent
    settings = next((idea.category_raw_block for idea in ideas if idea.category_raw_block), "")
    lines = [f"{prefix}{require_key(category, 'an idea category')} = {{"]
    if settings.strip():
        lines.append(_indent(settings, indent + 1))
    for idea in ideas:
        lines.append(serialize_idea(idea, indent=indent + 1))
        lines.append("")
    lines.append(f"{prefix}}}")
    return "\n".join(lines)


def _migrate_legacy_icon_key(body: str) -> str:
    """Rename a legacy top-level idea ``icon`` assignment without rebuilding it."""

    if assignment_spans(body, "picture"):
        return body
    icon_spans = [span for span in assignment_spans(body, "icon") if not span.is_block]
    if len(icon_spans) != 1:
        return body
    span = icon_spans[0]
    return body[: span.start] + "picture" + body[span.start + len(span.key) :]


def _remove_legacy_desc_key(body: str) -> str:
    """Remove unsupported top-level scalar ``desc`` from older SDK output."""

    for span in sorted(assignment_spans(body, "desc"), key=lambda item: item.start, reverse=True):
        if span.is_block:
            continue
        line_start = body.rfind("\n", 0, span.start) + 1
        line_end = body.find("\n", span.end)
        if line_end < 0:
            line_end = len(body)
        prefix = body[line_start : span.start]
        suffix = body[span.end : line_end]
        if not prefix.strip() and (
            not suffix.strip() or suffix.lstrip().startswith("#")
        ):
            if line_end < len(body):
                line_end += 1
            body = body[:line_start] + body[line_end:]
        else:
            body = replace_assignment(body, span, None)
    return body


def _patch_idea_body(idea: Idea, body: str) -> str:
    if not idea.touched:
        return body
    body = _migrate_legacy_icon_key(body)
    body = _remove_legacy_desc_key(body)
    picture_spans = [
        span for span in assignment_spans(body, "picture") if not span.is_block
    ]
    if picture_spans:
        # Any touched idea is a write boundary, so repair prefixed values even
        # when the caller changed a different modeled field.
        body = set_scalar(body, "picture", _picture(idea.icon))
    fields = idea.touched_fields or {
        "icon",
        "removal_cost",
        "allowed",
        "modifier",
        "research_bonus",
        "traits",
        "ai_will_do",
    }
    if "icon" in fields:
        picture_spans = assignment_spans(body, "picture")
        if picture_spans or (idea.icon and normalize_idea_icon(idea.icon) != DEFAULT_IDEA_ICON):
            body = set_scalar(body, "picture", _picture(idea.icon))
    if "removal_cost" in fields:
        body = set_scalar(
            body,
            "removal_cost",
            pdx_value(idea.removal_cost) if idea.removal_cost is not None else None,
        )
    if "allowed" in fields:
        body = set_block(body, "allowed", idea.allowed or None)
    if "modifier" in fields:
        body = _patch_mapping_block(
            body,
            "modifier",
            idea.modifier,
            merge=idea.modifier_merge,
            render=lambda value: pdx_string(value) if isinstance(value, str) else pdx_value(value),
        )
    if "research_bonus" in fields:
        body = _patch_mapping_block(
            body, "research_bonus", idea.research_bonus, merge=False, render=pdx_value
        )
    if "traits" in fields:
        body = set_block(body, "traits", " ".join(quote(trait) for trait in idea.traits) or None)
    if "ai_will_do" in fields:
        body = set_block(body, "ai_will_do", idea.ai_will_do or None)
    return body


def _patch_mapping_block(
    body: str,
    key: str,
    values: dict[str, Any],
    *,
    merge: bool,
    render: Callable[[Any], str],
) -> str:
    """Set the scalar entries of ``key = { ... }``, changing only the values that differ.

    Nested blocks and comments inside the block are kept. Without ``merge``,
    scalar entries missing from ``values`` are removed, and an empty mapping
    removes a block that still has entries.
    """

    spans = [span for span in assignment_spans(body, key) if span.is_block and span.tag is None]
    if not spans:
        if not values:
            return body
        rendered = "\n".join(
            require_entry(name, render(value), f"the entry '{name}' of '{key}'")
            for name, value in values.items()
        )
        return set_block(body, key, rendered)
    span = spans[0]
    assert span.body_start is not None and span.body_end is not None
    current = body[span.body_start : span.body_end]
    if not values and not merge:
        return body if not scalar_entries(current) else set_block(body, key, None)
    patched = patch_scalar_entries(current, values, remove_missing=not merge, render=render)
    return replace_assignment_body(body, span, patched)


# An edit of one statement inside a block: remove it, replace the whole
# statement, or replace only its body.
_Edit = tuple[Assignment, str, str]


def _local_span(item: Assignment, base: int) -> AssignmentSpan:
    value = item.value
    assert type(value) is Block
    return AssignmentSpan(
        item.key.value,
        item.start - base,
        value.end - base,
        value.start - base,
        value.end - base,
        True,
        value.body_start - base,
        value.body_end - base,
        item.op,
    )


def _line_indent(text: str, offset: int) -> str:
    line_start = text.rfind("\n", 0, offset) + 1
    line = text[line_start:offset]
    return line[: len(line) - len(line.lstrip(" \t"))]


def _apply_edits(
    text: str, block: Block, edits: list[_Edit], appended: list[str], outer_indent: str
) -> str:
    """Return ``block``'s body with ``edits`` applied and ``appended`` statements added."""

    body = text[block.body_start : block.body_end]
    base = block.body_start
    for item, action, payload in sorted(edits, key=lambda edit: edit[0].start, reverse=True):
        span = _local_span(item, base)
        if action == "remove":
            body = replace_assignment(body, span, None)
        elif action == "replace":
            body = replace_assignment(body, span, payload)
        else:
            assert span.body_start is not None and span.body_end is not None
            body = body[: span.body_start] + payload + body[span.body_end :]
    for statement in appended:
        if body.strip():
            body = append_assignment(body, statement)
        else:
            inner = outer_indent + "\t"
            lines = "\n".join(inner + line if line else line for line in statement.splitlines())
            body = "\n" + lines + "\n" + outer_indent
    return body


def _patch_ideas_text(text: str, containers: list[Assignment], ideas: list[Idea]) -> str:
    wanted: dict[str, Idea] = {}
    for idea in ideas:
        wanted.setdefault(idea.id, idea)
    placed: set[str] = set()

    def entry_edit(entry: Assignment, category: str) -> tuple[str, str] | None:
        idea_id = entry.key.value
        idea = wanted.get(idea_id)
        if idea is None:
            return ("remove", "")
        if idea_id in placed:
            # A second definition in the same file. Loading reports it and
            # Mod refuses the rewrite; a direct caller keeps it as it is.
            return None
        if (idea.category or "") != category:
            # Moved to another category: it is appended there below.
            return ("remove", "")
        placed.add(idea_id)
        if not idea.touched:
            return None
        assert type(entry.value) is Block
        body = entry.value.body
        if not idea.raw_block and (body.strip() or not idea.touched_fields):
            # Replaced by a definition built from the model (overwrite=True).
            # A loaded idea with an empty body is patched like any other.
            return ("replace", serialize_idea(idea, indent=0).strip())
        patched = _patch_idea_body(idea, body)
        return None if patched == body else ("body", patched)

    container_edits: list[list[_Edit]] = [[] for _ in containers]
    # (container index, category, its edits, how many ideas it had, how many stay)
    categories: list[tuple[int, Assignment, list[_Edit], int, int]] = []
    last_category: dict[str, int] = {}
    for index, container in enumerate(containers):
        assert type(container.value) is Block
        for item in container.value.items:
            if not _is_block_assignment(item):
                continue
            assert type(item) is Assignment and type(item.value) is Block
            if _is_direct_idea(item.value):
                edit = entry_edit(item, "")
                if edit is not None:
                    container_edits[index].append((item, *edit))
                continue
            edits: list[_Edit] = []
            had = kept = 0
            for child in item.value.items:
                if not _is_block_assignment(child):
                    continue
                assert type(child) is Assignment
                had += 1
                edit = entry_edit(child, item.key.value)
                if edit is None or edit[0] != "remove":
                    kept += 1
                if edit is not None:
                    edits.append((child, *edit))
            last_category[item.key.value] = len(categories)
            categories.append((index, item, edits, had, kept))

    # New and moved ideas go to the last occurrence of their category, or
    # into a new category at the end of the last container.
    category_appends: dict[int, list[str]] = {}
    container_appends: list[list[str]] = [[] for _ in containers]
    new_categories: dict[str, list[Idea]] = {}
    for idea in wanted.values():
        if idea.id in placed:
            continue
        category = idea.category or ""
        if not category:
            container_appends[-1].append(serialize_idea(idea, indent=0).strip())
        elif category in last_category:
            category_appends.setdefault(last_category[category], []).append(
                serialize_idea(idea, indent=0).strip()
            )
        else:
            new_categories.setdefault(category, []).append(idea)
    for category, category_ideas in new_categories.items():
        container_appends[-1].append(_serialize_category(category, category_ideas, indent=0))

    for position, (index, item, edits, had, kept) in enumerate(categories):
        appended = category_appends.get(position, [])
        if not edits and not appended:
            continue
        if had and not kept and not appended:
            container_edits[index].append((item, "remove", ""))
            continue
        assert type(item.value) is Block
        body = _apply_edits(text, item.value, edits, appended, _line_indent(text, item.start))
        container_edits[index].append((item, "body", body))

    result = text
    for index in reversed(range(len(containers))):
        edits = container_edits[index]
        appended = container_appends[index]
        if not edits and not appended:
            continue
        container = containers[index]
        assert type(container.value) is Block
        body = _apply_edits(
            text, container.value, edits, appended, _line_indent(text, container.start)
        )
        result = result[: container.value.body_start] + body + result[container.value.body_end :]
    return result


def _indent(text: str, count: int) -> str:
    prefix = "\t" * count
    return "\n".join(prefix + line.rstrip() for line in text.strip().splitlines())


def write_ideas_file(path: Path, ideas: list[Idea], container_name: str = "country_ideas") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = serialize_ideas_file(ideas, container_name=container_name)
    write_text(path, content)
    return path


def read_assigned_ideas(history_file: Path) -> list[str]:
    """Return the ideas a country history file adds at the start date.

    Only top-level ``add_ideas`` / ``remove_ideas`` count; dated blocks apply
    later in the game.
    """

    if not history_file.exists():
        return []
    root = parse(read_text(history_file)).root
    assigned: list[str] = []
    removed: set[str] = set()
    for item in root.assignments():
        key = item.key.value
        if key not in ("add_ideas", "remove_ideas") or item.op != "=":
            continue
        value = item.value
        if type(value) is Block:
            names = [scalar.value for scalar in value.values()]
        elif item.scalar is not None:
            names = [item.scalar]
        else:
            continue
        if key == "add_ideas":
            assigned.extend(names)
        else:
            removed.update(names)
    return [idea for idea in dict.fromkeys(assigned) if idea not in removed]
