"""
Small helpers for constructing and validating HOI4 script snippets.

This is intentionally conservative. It catches syntax mistakes and common
agent-facing footguns without pretending to fully model the HOI4 effect engine.
"""

from __future__ import annotations

from collections.abc import Mapping


def normalize_block_body(body: str) -> str:
    """Return a block body without one redundant outer ``{ ... }`` pair."""
    text = (body or "").strip()
    if not text or not text.startswith("{") or not text.endswith("}"):
        return text
    if _outer_braces_wrap_all(text):
        return text[1:-1].strip()
    return text


def pdx_value(value: object) -> str:
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
    return str(value)


def pdx_string(value: object) -> str:
    """Serialize a value as an always-quoted Paradox string."""
    text = str(value)
    if "\n" in text or "\r" in text or "\x00" in text:
        raise ValueError("Paradox single-line strings cannot contain newlines or NUL characters")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def effect_block(name: str, fields: Mapping[str, object] | None = None, **kwargs: object) -> str:
    """Build ``name = { key = value ... }`` without f-string brace escaping."""
    data: dict[str, object] = {}
    if fields:
        data.update(fields)
    data.update(kwargs)
    body = " ".join(f"{key} = {pdx_value(value)}" for key, value in data.items())
    return f"{name} = {{ {body} }}"


def scope_block(scope: str | int, *effects: str) -> str:
    """Build ``scope = { effect... }`` without f-string brace escaping."""
    body = " ".join(effect.strip() for effect in effects if effect and effect.strip())
    return f"{scope} = {{ {body} }}"


def validate_script_syntax(script: str) -> list[str]:
    """Return human-readable syntax issues for a raw PDX snippet."""
    issues: list[str] = []
    stack: list[tuple[int, int]] = []
    line = 1
    col = 0
    i = 0
    while i < len(script):
        ch = script[i]
        col += 1
        if ch == "\n":
            line += 1
            col = 0
            i += 1
            continue
        if ch == "#":
            while i < len(script) and script[i] != "\n":
                i += 1
            continue
        if ch == '"':
            quote_line = line
            quote_col = col
            i += 1
            col += 1
            closed = False
            while i < len(script):
                if script[i] == "\\":
                    i += 2
                    col += 2
                    continue
                if script[i] == '"':
                    closed = True
                    break
                if script[i] == "\n":
                    line += 1
                    col = 0
                else:
                    col += 1
                i += 1
            if not closed:
                issues.append(f"Unclosed quote from line {quote_line}, column {quote_col}")
                break
        elif ch == "{":
            stack.append((line, col))
        elif ch == "}":
            if not stack:
                issues.append(f"Unexpected closing brace at line {line}, column {col}")
            else:
                stack.pop()
        i += 1
    for open_line, open_col in stack:
        issues.append(f"Unclosed opening brace from line {open_line}, column {open_col}")
    return issues


def _needs_quotes(value: str) -> bool:
    if value == "":
        return True
    return any(ch.isspace() for ch in value) or any(ch in value for ch in '{}#"')


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
