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
    # difflib preserves unterminated content records, so joining them directly
    # would merge adjacent removals, additions, or context lines in the preview.
    return "".join(
        line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
        for line in diff_lines
    )
