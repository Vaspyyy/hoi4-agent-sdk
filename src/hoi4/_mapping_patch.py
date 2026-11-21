"""Set the ``key = scalar`` entries of a block body with the smallest possible edit."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping

from .patching import (
    AssignmentSpan,
    append_assignment,
    replace_assignment,
    scalar_values_equivalent,
    top_level_assignments,
)
from .script import pdx_value
from .syntax import as_scalar, require_entry


def patch_scalar_entries(
    text: str,
    values: Mapping[str, object],
    *,
    remove_missing: bool = True,
    render: Callable[[object], str] = pdx_value,
    skip: Collection[str] = (),
) -> str:
    """Make the direct scalar entries of ``text`` read back as ``values``.

    The readers model a repeated key by its last occurrence, so:

    * a key whose last value already equals the model is left alone, with
      every repetition (``custom_modifier_tooltip`` is often repeated);
    * a changed key gets only its last value replaced;
    * with ``remove_missing``, every entry of a key the model lacks is removed;
    * new keys are appended in mapping order.

    Nested blocks, comments, comparisons and keys in ``skip`` are never touched.
    """

    occurrences: dict[str, list[AssignmentSpan]] = {}
    for span in top_level_assignments(text):
        if not span.is_block and span.key not in skip:
            occurrences.setdefault(span.key, []).append(span)
    edits: list[tuple[AssignmentSpan, str | None]] = []
    for key, spans in occurrences.items():
        if key not in values:
            if remove_missing:
                edits.extend((span, None) for span in spans)
            continue
        last = spans[-1]
        rendered = as_scalar(render(values[key]))
        if not scalar_values_equivalent(text[last.value_start : last.value_end], rendered):
            edits.append((last, rendered))
    result = text
    for span, replacement in sorted(edits, key=lambda edit: edit[0].start, reverse=True):
        if replacement is None:
            result = replace_assignment(result, span, None)
        else:
            result = result[: span.value_start] + replacement + result[span.value_end :]
    for key, value in values.items():
        if key not in occurrences and key not in skip:
            result = append_assignment(
                result, require_entry(key, render(value), f"the entry '{key}'")
            )
    return result


def scalar_entries(text: str, *, skip: Collection[str] = ()) -> dict[str, str]:
    """Return the direct ``key = scalar`` entries of ``text`` (last occurrence wins)."""

    return {
        span.key: text[span.value_start : span.value_end]
        for span in top_level_assignments(text)
        if not span.is_block and span.key not in skip
    }
