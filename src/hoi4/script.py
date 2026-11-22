"""
Small helpers for constructing and validating HOI4 script snippets.

This is intentionally conservative. It catches syntax mistakes and common
agent-facing footguns without pretending to fully model the HOI4 effect engine.
"""

from __future__ import annotations

import re

from ._scalar import needs_quotes as _needs_quotes
from .syntax import parse


def normalize_block_body(body: str) -> str:
    """Return a block body without one redundant outer ``{ ... }`` pair."""
    text = (body or "").strip()
    if not text or not text.startswith("{") or not text.endswith("}"):
        return text
    if _outer_braces_wrap_all(text):
        return text[1:-1].strip()
    return text


def pdx_value(value: object) -> str:
    """Write ``value`` as a Paradox script value, quoting text only when it needs it.

    ``True``/``False`` become ``yes``/``no``; numbers are written as they
    are; any other object as its text.

    Raises:
        ValueError: The text has a line break or a NUL character.
    """
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, str):
        if "\n" in value or "\r" in value or "\x00" in value:
            raise ValueError("Paradox scalar values cannot contain newlines or NUL characters")
        if value in {"yes", "no"}:
            return value
        if _needs_quotes(value):
            return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
        return value
    if isinstance(value, (int, float)):
        return str(value)
    # Any other object (a path, a decimal) is written as its text.
    return pdx_value(str(value))


def pdx_string(value: object) -> str:
    """Serialize a value as an always-quoted Paradox string.

    Raises:
        ValueError: The text has a line break or a NUL character.
    """
    text = str(value)
    if "\n" in text or "\r" in text or "\x00" in text:
        raise ValueError("Paradox single-line strings cannot contain newlines or NUL characters")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def braced(body: str) -> str:
    """Return ``{ body }``; the closing brace goes on a new line if ``body`` ends in a comment.

    A comment runs to the end of its line, so a brace behind it on the same
    line would be part of the comment.
    """
    return f"{{ {body}\n}}" if _ends_in_comment(body) else f"{{ {body} }}"


def _ends_in_comment(text: str) -> bool:
    """Whether anything put behind ``text`` on the same line would land in a comment."""
    comments = parse(text).comments
    return bool(comments) and comments[-1][1] == len(text)


# What the syntax scan stops at outside a string, and inside one.
_SYNTAX_MARK_RE = re.compile(r'[#"{}]')
_STRING_MARK_RE = re.compile(r'[\\"]')


def validate_script_syntax(script: str) -> list[str]:
    """Return human-readable syntax issues for a raw PDX snippet.

    Comments run to the end of their line, a backslash in a string skips the
    next character (a line break too, which is then not counted). The scan
    jumps from one brace, quote or comment mark to the next and counts the
    lines and columns of the text in between.
    """
    issues: list[str] = []
    stack: list[tuple[int, int]] = []
    line = 1
    col = 0
    i = 0
    length = len(script)
    next_mark = _SYNTAX_MARK_RE.search
    next_string_mark = _STRING_MARK_RE.search
    while i < length:
        mark = next_mark(script, i)
        j = mark.start() if mark is not None else length
        # Plain text up to the mark: only the position moves.
        newline = script.rfind("\n", i, j)
        if newline >= 0:
            line += script.count("\n", i, j)
            col = j - newline - 1
        else:
            col += j - i
        if mark is None:
            break
        ch = script[j]
        col += 1
        if ch == "#":
            end = script.find("\n", j)
            i = length if end < 0 else end
            continue
        if ch == '"':
            quote_line = line
            quote_col = col
            i = j + 1
            col += 1
            closed = False
            while i < length:
                found = next_string_mark(script, i)
                k = found.start() if found is not None else length
                newline = script.rfind("\n", i, k)
                if newline >= 0:
                    line += script.count("\n", i, k)
                    col = k - newline - 1
                else:
                    col += k - i
                if found is None:
                    i = length
                    break
                if script[k] == "\\":
                    i = k + 2
                    col += 2
                    continue
                closed = True
                i = k
                break
            if not closed:
                issues.append(f"Unclosed quote from line {quote_line}, column {quote_col}")
                break
        elif ch == "{":
            stack.append((line, col))
        elif not stack:
            issues.append(f"Unexpected closing brace at line {line}, column {col}")
        else:
            stack.pop()
        i = (i if ch == '"' else j) + 1
    for open_line, open_col in stack:
        issues.append(f"Unclosed opening brace from line {open_line}, column {open_col}")
    return issues


def _outer_braces_wrap_all(text: str) -> bool:
    depth = 0
    in_quote = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and in_quote:
            i += 2
            continue
        if ch == '"':
            in_quote = not in_quote
        elif ch == "#" and not in_quote:
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        elif ch == "{" and not in_quote:
            depth += 1
        elif ch == "}" and not in_quote:
            depth -= 1
            if depth == 0 and i != len(text) - 1:
                return False
            if depth < 0:
                return False
        i += 1
    return depth == 0
