"""Lossless, depth-aware patches for top-level Paradox assignments.

The serializers use these helpers on the *original* source text.  Existing
assignments are therefore edited at their value/body spans instead of being
rendered again, which keeps surrounding whitespace, comments, and line endings
byte-for-byte stable.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
import textwrap

from .parser import ParseError, TokenType, extract_braced_block, tokenize


@dataclass(frozen=True)
class AssignmentSpan:
    key: str
    start: int
    end: int
    value_start: int
    value_end: int
    is_block: bool
    body_start: int | None = None
    body_end: int | None = None


def top_level_assignments(text: str) -> list[AssignmentSpan]:
    """Return assignments at brace depth zero, ignoring comments and strings."""
    spans: list[AssignmentSpan] = []
    i = 0
    depth = 0
    while i < len(text):
        ch = text[i]
        if depth == 0 and ch == '"':
            start = i
            key_end = _skip_quote(text, i)
            j = _skip_space_and_comments(text, key_end)
            if j < len(text) and text[j] == "=":
                key = _unescape_quoted(text[start + 1 : key_end - 1])
                value_at = _skip_space_and_comments(text, j + 1)
                if value_at >= len(text):
                    raise ParseError(f"Missing value for {key!r}")
                span, i = _assignment_span(text, key, start, value_at)
                spans.append(span)
                continue
            i = key_end
            continue
        if ch == '"':
            i = _skip_quote(text, i)
            continue
        if ch == "#":
            i = _skip_comment(text, i)
            continue
        if ch == "{":
            depth += 1
            i += 1
            continue
        if ch == "}":
            depth -= 1
            if depth < 0:
                raise ParseError(f"Unexpected closing brace at offset {i}")
            i += 1
            continue
        if depth == 0 and (ch.isalnum() or ch == "_"):
            start = i
            i += 1
            while i < len(text) and (text[i].isalnum() or text[i] in "_.:-"):
                i += 1
            key = text[start:i]
            j = _skip_space_and_comments(text, i)
            if j >= len(text) or text[j] != "=":
                continue
            j = _skip_space_and_comments(text, j + 1)
            if j >= len(text):
                raise ParseError(f"Missing value for {key!r}")
            span, i = _assignment_span(text, key, start, j)
            spans.append(span)
            continue
        i += 1
    if depth != 0:
        raise ParseError("Unbalanced braces while scanning assignments")
    return spans


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
    if not first.is_block or first.body_start is None or first.body_end is None:
        replacement = f"{key} = {{"
        if cleaned:
            replacement += "\n" + _indent(cleaned, 1) + "\n"
        replacement += "}"
        return replace_assignment(text, first, replacement)
    current = text[first.body_start : first.body_end]
    if block_bodies_equivalent(current, cleaned):
        return text
    return replace_assignment_body(text, first, _render_block_body_like(current, cleaned))


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
    line_start = text.rfind("\n", 0, span.start) + 1
    indent = text[line_start : span.start]
    start = line_start if not text[line_start : span.start].strip() else span.start
    end = span.end
    if end < len(text) and text[end] == "\r":
        end += 1
    if end < len(text) and text[end] == "\n":
        end += 1
    newline = _line_ending(text)
    rendered = "" if replacement is None else indent + replacement.rstrip() + newline
    return text[:start] + rendered + text[end:]


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


def _remove_assignments(text: str, spans: list[AssignmentSpan]) -> str:
    result = text
    for span in reversed(spans):
        result = replace_assignment(result, span, None)
    return result


def _assignment_span(
    text: str, key: str, start: int, value_start: int
) -> tuple[AssignmentSpan, int]:
    if text[value_start] == "{":
        _, end = extract_braced_block(text, value_start + 1)
        return (
            AssignmentSpan(
                key,
                start,
                end,
                value_start,
                end,
                True,
                value_start + 1,
                end - 1,
            ),
            end,
        )
    if text[value_start] == '"':
        value_end = _skip_quote(text, value_start)
    else:
        value_end = value_start
        while (
            value_end < len(text)
            and not text[value_end].isspace()
            and text[value_end] not in "{}#"
        ):
            value_end += 1
    return (
        AssignmentSpan(key, start, value_end, value_start, value_end, False),
        value_end,
    )


def _skip_quote(text: str, start: int) -> int:
    i = start + 1
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            i += 2
            continue
        if text[i] == '"':
            return i + 1
        i += 1
    raise ParseError(f"Unterminated quote at offset {start}")


def _skip_comment(text: str, start: int) -> int:
    end = text.find("\n", start)
    return len(text) if end < 0 else end + 1


def _skip_space_and_comments(text: str, start: int) -> int:
    i = start
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        if text[i] == "#":
            i = _skip_comment(text, i)
            continue
        break
    return i


def _render_block_body_like(current: str, body: str) -> str:
    cleaned = _dedent_block_body(body)
    leading_match = re.match(r"\s*", current)
    trailing_match = re.search(r"\s*$", current)
    leading = leading_match.group(0) if leading_match else ""
    trailing = trailing_match.group(0) if trailing_match else ""

    if "\n" not in current and "\r" not in current:
        compact = " ".join(line.strip() for line in cleaned.splitlines() if line.strip())
        return leading + compact + trailing

    newline = _line_ending(current)
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
