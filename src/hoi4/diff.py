"""
Diff generation for previewing changes.
"""

from __future__ import annotations

import difflib


def unified_diff(original: str, modified: str, filename: str = "") -> str:
    original_lines = original.splitlines(keepends=True)
    modified_lines = modified.splitlines(keepends=True)

    from_label = f"a/{filename}" if filename else "original"
    to_label = f"b/{filename}" if filename else "modified"

    diff_lines = difflib.unified_diff(
        original_lines,
        modified_lines,
        fromfile=from_label,
        tofile=to_label,
    )
    return "".join(diff_lines)
