"""Lossless, depth-aware patches for top-level Paradox assignments."""

from __future__ import annotations

from dataclasses import dataclass

from .parser import ParseError, extract_braced_block


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
        if depth == 0 and (ch.isalpha() or ch == "_"):
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
            if text[j] == "{":
                body, end = extract_braced_block(text, j + 1)
                spans.append(AssignmentSpan(key, start, end, j, end, True, j + 1, end - 1))
                i = end
                continue
            value_start = j
            if text[j] == '"':
                value_end = _skip_quote(text, j)
            else:
                value_end = j
                while (
                    value_end < len(text)
                    and not text[value_end].isspace()
                    and text[value_end] not in "{}#"
                ):
                    value_end += 1
            spans.append(AssignmentSpan(key, start, value_end, value_start, value_end, False))
            i = value_end
            continue
        i += 1
    if depth != 0:
        raise ParseError("Unbalanced braces while scanning assignments")
    return spans


def assignment_spans(text: str, key: str) -> list[AssignmentSpan]:
    return [span for span in top_level_assignments(text) if span.key == key]


def set_scalar(text: str, key: str, value: str | None) -> str:
    return _replace_assignments(text, key, None if value is None else f"{key} = {value}")


def set_block(text: str, key: str, body: str | None) -> str:
    if body is None:
        return _replace_assignments(text, key, None)
    cleaned = body.strip()
    replacement = f"{key} = {{"
    if cleaned:
        replacement += "\n" + _indent(cleaned, 1) + "\n"
    replacement += "}"
    return _replace_assignments(text, key, replacement)


def replace_assignment(text: str, span: AssignmentSpan, replacement: str | None) -> str:
    line_start = text.rfind("\n", 0, span.start) + 1
    indent = text[line_start : span.start]
    start = line_start if not text[line_start : span.start].strip() else span.start
    end = span.end
    if end < len(text) and text[end] == "\r":
        end += 1
    if end < len(text) and text[end] == "\n":
        end += 1
    rendered = "" if replacement is None else indent + replacement.rstrip() + "\n"
    return text[:start] + rendered + text[end:]


def append_assignment(text: str, replacement: str) -> str:
    stripped = text.rstrip()
    separator = "\n" if stripped else ""
    return stripped + separator + replacement.rstrip() + "\n"


def _replace_assignments(text: str, key: str, replacement: str | None) -> str:
    spans = assignment_spans(text, key)
    if not spans:
        return text if replacement is None else append_assignment(text, replacement)
    result = text
    for span in reversed(spans[1:]):
        result = replace_assignment(result, span, None)
    first = assignment_spans(result, key)[0]
    return replace_assignment(result, first, replacement)


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


def _indent(text: str, count: int) -> str:
    prefix = "\t" * count
    return "\n".join(prefix + line.rstrip() for line in text.splitlines())
