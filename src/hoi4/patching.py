"""Lossless, depth-aware patches for top-level Paradox assignments.

The serializers use these helpers on the *original* source text.  Existing
assignments are therefore edited at their value/body spans instead of being
rendered again, which keeps surrounding whitespace, comments, and line endings
byte-for-byte stable.

All positions come from the syntax tree in :mod:`hoi4.syntax`, so a comment, a
quoted string or a nested block is never mistaken for an assignment.
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .parser import ParseError, TokenType, tokenize
from .syntax import Assignment, Block, Tagged, parse


@dataclass(frozen=True)
class AssignmentSpan:
    """Where one ``key OP value`` statement sits in its source text.

    ``start``/``end`` cover the whole statement and ``value_start``/``value_end``
    its value. For a block value ``body_start``/``body_end`` cover the text
    between the braces. ``tag`` is set for a typed block such as
    ``color = rgb { 1 2 3 }``; its value span starts at the tag.
    """

    key: str
    start: int
    end: int
    value_start: int
    value_end: int
    is_block: bool
    body_start: int | None = None
    body_end: int | None = None
    operator: str = "="
    tag: str | None = None


def top_level_assignments(text: str, *, comparisons: bool = False) -> list[AssignmentSpan]:
    """Return assignments at brace depth zero, ignoring comments and strings.

    Only ``key = value`` statements are returned unless ``comparisons`` is
    true, which also returns ``key > value`` and the other comparison
    statements (see :attr:`AssignmentSpan.operator`).

    Like the game, a block that is still open at the end of the text ends
    there. Use :func:`hoi4.syntax.require_well_formed` to reject such text.
    """

    spans: list[AssignmentSpan] = []
    for item in parse(text).root.items:
        if type(item) is not Assignment:
            continue
        operator = item.op
        if operator != "=" and not comparisons:
            continue
        spans.append(_span(item, operator))
    return spans


def _span(item: Assignment, operator: str) -> AssignmentSpan:
    value = item.value
    if type(value) is Block:
        return AssignmentSpan(
            item.key.value, item.start, value.end, value.start, value.end, True,
            value.body_start, value.body_end, operator,
        )
    if type(value) is Tagged:
        block = value.block
        return AssignmentSpan(
            item.key.value, item.start, block.end, value.start, block.end, True,
            block.body_start, block.body_end, operator, value.tag.raw,
        )
    return AssignmentSpan(
        item.key.value, item.start, value.end, value.start, value.end, False,
        operator=operator,
    )


def assignment_spans(text: str, key: str) -> list[AssignmentSpan]:
    return [span for span in top_level_assignments(text) if span.key == key]


def set_scalar(text: str, key: str, value: str | None) -> str:
    spans = assignment_spans(text, key)
    if value is None:
        return _remove_assignments(text, spans)
    if not spans:
        return append_assignment(text, f"{key} = {value}")
    first = spans[0]
    if first.is_block:
        return replace_assignment(text, first, f"{key} = {value}")
    current = text[first.value_start : first.value_end]
    if scalar_values_equivalent(current, value):
        return text
    return text[: first.value_start] + value + text[first.value_end :]


def set_block(text: str, key: str, body: str | None) -> str:
    spans = assignment_spans(text, key)
    if body is None:
        return _remove_assignments(text, spans)
    cleaned = body.strip()
    if not spans:
        replacement = f"{key} = {{"
        if cleaned:
            replacement += "\n" + _indent(cleaned, 1) + "\n"
        replacement += "}"
        return append_assignment(text, replacement)
    first = spans[0]
    if (
        not first.is_block
        or first.body_start is None
        or first.body_end is None
        # ``key = rgb { ... }``: the caller's body is untyped, so the tag must go.
        or first.tag is not None
    ):
        replacement = f"{key} = {{"
        if cleaned:
            replacement += "\n" + _indent(cleaned, 1) + "\n"
        replacement += "}"
        return replace_assignment(text, first, replacement)
    current = text[first.body_start : first.body_end]
    if block_bodies_equivalent(current, cleaned):
        return text
    line_start = text.rfind("\n", 0, first.start) + 1
    indent_match = re.match(r"[ \t]*", text[line_start:first.start])
    indent = indent_match.group(0) if indent_match else ""
    return replace_assignment_body(
        text, first,
        _render_block_body_like(current, cleaned, _line_ending(text), indent),
    )


def replace_assignment_body(text: str, span: AssignmentSpan, body: str) -> str:
    """Replace only a block assignment's interior, preserving its wrapper bytes."""
    if not span.is_block or span.body_start is None or span.body_end is None:
        raise ValueError("Assignment is not a block")
    current = text[span.body_start : span.body_end]
    if body == current:
        return text
    return text[: span.body_start] + body + text[span.body_end :]


def scalar_values_equivalent(left: str, right: str) -> bool:
    """Return whether two scalar spellings have the same modeled value."""
    a = _unquote_scalar(left.strip())
    b = _unquote_scalar(right.strip())
    if a == b:
        return True
    try:
        return Decimal(a) == Decimal(b)
    except InvalidOperation:
        return False


def block_bodies_equivalent(left: str, right: str) -> bool:
    """Compare block bodies while ignoring formatting and comments.

    Numeric spelling (``0.10`` versus ``0.1``) and harmless quoting of a bare
    atom are normalized as well.  Ordering and repeated assignments remain
    significant.
    """
    try:
        return _semantic_tokens(left) == _semantic_tokens(right)
    except ParseError:
        return left.strip() == right.strip()


def dedent_block_body(body: str) -> str:
    """Normalize a source block interior for use as an editable model value."""
    return textwrap.dedent(body).strip()


def replace_assignment(text: str, span: AssignmentSpan, replacement: str | None) -> str:
    """Replace (or with ``None`` remove) one statement, leaving its neighbours alone."""

    line_start = text.rfind("\n", 0, span.start) + 1
    prefix = text[line_start : span.start]
    if prefix.strip():
        # The statement shares its line with earlier script (``a = 1 b = 2``):
        # only its own span changes.
        if replacement is not None:
            return text[: span.start] + replacement.rstrip() + text[span.end :]
        end = span.end
        while end < len(text) and text[end] in " \t":
            end += 1
        return text[: span.start] + text[end:]
    end = span.end
    if end < len(text) and text[end] == "\r":
        end += 1
    if end < len(text) and text[end] == "\n":
        end += 1
    newline = _line_ending(text)
    rendered = "" if replacement is None else prefix + replacement.rstrip() + newline
    return text[:line_start] + rendered + text[end:]


def append_assignment(text: str, replacement: str) -> str:
    rendered = replacement.rstrip(" \t\r\n")
    if not rendered:
        return text
    newline = _line_ending(text)
    content_end = len(text.rstrip(" \t\r\n"))
    content = text[:content_end]
    trailing = text[content_end:]
    indent = _assignment_indent(text)
    if not rendered[0].isspace() and indent:
        rendered = newline.join(indent + line if line else line for line in rendered.splitlines())
    separator = newline if content else ""
    return content + separator + rendered + trailing


def move_assignment_before(
    text: str,
    span: AssignmentSpan,
    target: AssignmentSpan,
) -> str:
    """Move a later assignment before another, retaining its source spelling."""

    if span.start <= target.start:
        return text

    source_line_start = text.rfind("\n", 0, span.start) + 1
    source_start = (
        source_line_start
        if not text[source_line_start : span.start].strip()
        else span.start
    )
    source_line_end = text.find("\n", span.end)
    logical_line_end = len(text) if source_line_end < 0 else source_line_end
    suffix = text[span.end : logical_line_end]
    owns_line = source_start == source_line_start
    if owns_line and (
        not suffix.strip() or suffix.lstrip().startswith("#")
    ):
        source_end = len(text) if source_line_end < 0 else source_line_end + 1
    else:
        source_end = span.end

    rendered = text[source_start:source_end]
    if not rendered.endswith(("\n", "\r")):
        rendered = rendered.rstrip(" \t") + _line_ending(text)
    remaining = text[:source_start] + text[source_end:]

    target_line_start = remaining.rfind("\n", 0, target.start) + 1
    target_prefix = remaining[target_line_start : target.start]
    insert_at = target_line_start if not target_prefix.strip() else target.start
    return remaining[:insert_at] + rendered + remaining[insert_at:]


def _remove_assignments(text: str, spans: list[AssignmentSpan]) -> str:
    result = text
    for span in reversed(spans):
        result = replace_assignment(result, span, None)
    return result


def _block_comments(body: str) -> tuple[bool, bool]:
    """Return whether real comments exist and whether the body ends in one."""
    comments = parse(body).comments
    if not comments:
        return False, False
    return True, "\n" not in body[comments[-1][0] :]


def _render_block_body_like(
    current: str, body: str, newline: str, outer_indent: str,
) -> str:
    cleaned = _dedent_block_body(body)
    leading_match = re.match(r"\s*", current)
    trailing_match = re.search(r"\s*$", current)
    leading = leading_match.group(0) if leading_match else ""
    trailing = trailing_match.group(0) if trailing_match else ""

    has_comments, ends_in_comment = _block_comments(cleaned)
    if "\n" not in current and "\r" not in current and not has_comments:
        compact = " ".join(line.strip() for line in cleaned.splitlines() if line.strip())
        return leading + compact + trailing

    if "\n" not in current and "\r" not in current:
        leading = newline + outer_indent + "\t"
        trailing = newline + outer_indent
    else:
        newline = _line_ending(current)
        if ends_in_comment and "\n" not in trailing:
            # A multiline source can still close on its final content line.
            trailing = newline + outer_indent

    indent = re.split(r"\r?\n", leading)[-1]
    lines = cleaned.splitlines()
    if not lines:
        return leading + trailing
    rendered = lines[0].rstrip()
    for line in lines[1:]:
        rendered += newline + indent + line.rstrip()
    return leading + rendered + trailing


def _dedent_block_body(body: str) -> str:
    cleaned = textwrap.dedent(body.strip(" \t\r\n"))
    lines = cleaned.splitlines()
    if len(lines) < 2 or lines[0][:1].isspace():
        return cleaned
    continuation = [line for line in lines[1:] if line.strip()]
    if not continuation:
        return cleaned
    widths = [len(line) - len(line.lstrip(" \t")) for line in continuation]
    common = min(widths)
    if common <= 0:
        return cleaned
    return "\n".join([lines[0], *(line[common:] if line.strip() else line for line in lines[1:])])


def _semantic_tokens(text: str) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    for token in tokenize(text):
        if token.type in {TokenType.COMMENT, TokenType.EOF}:
            continue
        if token.type == TokenType.NUMBER:
            try:
                value = format(Decimal(token.value).normalize(), "f")
            except InvalidOperation:
                value = token.value
            result.append(("number", value))
        elif token.type in {TokenType.IDENT, TokenType.STRING}:
            result.append(("atom", token.value))
        else:
            result.append((token.type.name, token.value))
    return tuple(result)


def _unquote_scalar(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return _unescape_quoted(value[1:-1])
    return value


def _unescape_quoted(value: str) -> str:
    result: list[str] = []
    i = 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value) and value[i + 1] in {'"', "\\"}:
            result.append(value[i + 1])
            i += 2
            continue
        result.append(value[i])
        i += 1
    return "".join(result)


def _assignment_indent(text: str) -> str:
    spans = top_level_assignments(text)
    if not spans:
        return ""
    line_start = text.rfind("\n", 0, spans[0].start) + 1
    prefix = text[line_start : spans[0].start]
    return prefix if not prefix.strip() else ""


def _line_ending(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _indent(text: str, count: int) -> str:
    prefix = "\t" * count
    return "\n".join(prefix + line.rstrip() for line in text.splitlines())
