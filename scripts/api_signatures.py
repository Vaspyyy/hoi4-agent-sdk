#!/usr/bin/env python3
"""Keep the method signatures in ``docs/api.md`` the same as the code.

Usage (from the repository root, with ``src`` on ``PYTHONPATH``)::

    python scripts/api_signatures.py            # list what differs or is missing
    python scripts/api_signatures.py --write    # rewrite the signature cells
    python scripts/api_signatures.py --print    # print every signature line

A signature line is the first cell of a table row in ``docs/api.md`` that is a
code span starting with a public ``Mod`` method name and ``(``, for example::

    | `get_event(event_id, *, default=...) -> Event` | ... |

A ``|`` of a union type is written ``\\|`` there, so the table stays one row.

The line is built from ``inspect.signature``: parameter names, ``*``, ``/``,
defaults and the return annotation. Parameter annotations are left out to
keep the tables short; the docstring and ``help(Mod.name)`` have them. A
lookup's ``default=`` shows as ``default=...`` and its return type without the
type of the default.

``--write`` replaces every signature cell with the generated text. It does
not add rows: a method without a row is listed, and you add the row (with its
description, what it returns and what it raises) to the right table by hand.
``tests/test_doc_blocks.py`` fails when a line differs, a method has no row
or a method has two.
"""

from __future__ import annotations

import enum
import inspect
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_DOC = ROOT / "docs" / "api.md"
ROW = re.compile(r"^\|\s*`(?P<name>[A-Za-z_]\w*)\((?P<rest>[^`]*)`\s*\|")


def public_methods() -> list[str]:
    """Return the sorted names of the public callables of ``hoi4.Mod``."""

    from hoi4 import Mod

    return sorted(
        name for name in dir(Mod) if not name.startswith("_") and callable(getattr(Mod, name))
    )


def _default(value: object) -> str:
    if isinstance(value, enum.Enum) and type(value).__name__ == "_Missing":
        return "..."
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(value, enum.Enum):
        return f"{type(value).__name__}.{value.name}"
    return repr(value)


def _return(annotation: object) -> str:
    if annotation is inspect.Signature.empty:
        return ""
    text = annotation if isinstance(annotation, str) else inspect.formatannotation(annotation)
    # A lookup returns the object or its ``default=``: show the object type.
    text = re.sub(r"\s*\|\s*_D\b", "", text)
    return f" -> {text}"


def signature(name: str) -> str:
    """Return the signature line of ``Mod.<name>``, without backticks."""

    from hoi4 import Mod

    sig = inspect.signature(getattr(Mod, name))
    parts: list[str] = []
    seen_keyword_only = False
    params = [p for p in sig.parameters.values() if p.name not in ("self", "cls")]
    for index, param in enumerate(params):
        if param.kind is param.KEYWORD_ONLY and not seen_keyword_only:
            parts.append("*")
        if param.kind in (param.KEYWORD_ONLY, param.VAR_POSITIONAL):
            seen_keyword_only = True
        if param.kind is param.VAR_POSITIONAL:
            parts.append(f"*{param.name}")
        elif param.kind is param.VAR_KEYWORD:
            parts.append(f"**{param.name}")
        elif param.default is param.empty:
            parts.append(param.name)
        else:
            parts.append(f"{param.name}={_default(param.default)}")
        following = params[index + 1] if index + 1 < len(params) else None
        if param.kind is param.POSITIONAL_ONLY and (
            following is None or following.kind is not param.POSITIONAL_ONLY
        ):
            parts.append("/")
    return f"{name}({', '.join(parts)}){_return(sig.return_annotation)}"


def documented(text: str) -> list[tuple[int, str, str]]:
    """Return ``(line, name, cell)`` for every signature cell of the page."""

    names = set(public_methods())
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        match = ROW.match(line)
        if match and match.group("name") in names:
            cell = f"{match.group('name')}({match.group('rest')}".replace("\\|", "|")
            found.append((number, match.group("name"), cell))
    return found


def problems(text: str) -> list[str]:
    """Return one message per stale, missing or repeated signature line."""

    rows = documented(text)
    messages = []
    seen: dict[str, int] = {}
    for line, name, cell in rows:
        expected = signature(name)
        if cell != expected:
            messages.append(f"docs/api.md:{line}: `{cell}` should be `{expected}`")
        if name in seen:
            messages.append(f"docs/api.md:{line}: {name} has a row already at line {seen[name]}")
        seen.setdefault(name, line)
    for name in public_methods():
        if name not in seen:
            messages.append(f"docs/api.md: no row for Mod.{name}: add `{signature(name)}`")
    return messages


def rewrite(text: str) -> str:
    """Return the page with every signature cell replaced by the generated line."""

    names = set(public_methods())
    out = []
    for line in text.splitlines(keepends=True):
        match = ROW.match(line)
        if match and match.group("name") in names:
            cell = signature(match.group("name")).replace("|", "\\|")
            line = f"| `{cell}` |" + line[match.end() :]
        out.append(line)
    return "".join(out)


def main(argv: list[str]) -> int:
    if "--print" in argv:
        for name in public_methods():
            print(signature(name))
        return 0
    text = API_DOC.read_text(encoding="utf-8")
    if "--write" in argv:
        new = rewrite(text)
        if new != text:
            API_DOC.write_text(new, encoding="utf-8")
        text = new
    messages = problems(text)
    for message in messages:
        print(message)
    return 1 if messages else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
