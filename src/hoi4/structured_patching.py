"""Higher-level lossless patches for structured Paradox block contents."""

from __future__ import annotations

from collections.abc import Mapping

from .patching import append_assignment, replace_assignment, scalar_values_equivalent
from .patching import top_level_assignments
from .script import pdx_value


def patch_scalar_mapping(
    text: str,
    values: Mapping[str, object],
    *,
    remove_missing: bool = True,
) -> str:
    """Patch direct scalar children while retaining comments and nested blocks.

    The first assignment for each key is retained, duplicate scalar assignments
    are removed, and nested blocks are never touched. New keys are appended in
    caller-provided mapping order.
    """

    spans = [span for span in top_level_assignments(text) if not span.is_block]
    first_by_key = {span.key: span for span in spans[::-1]}
    remaining = dict(values)
    result = text
    for span in reversed(spans):
        if span.key not in values:
            if remove_missing:
                result = replace_assignment(result, span, None)
            continue
        if span != first_by_key[span.key]:
            result = replace_assignment(result, span, None)
            continue
        rendered = pdx_value(values[span.key])
        current = result[span.value_start : span.value_end]
        if not scalar_values_equivalent(current, rendered):
            result = result[: span.value_start] + rendered + result[span.value_end :]
        remaining.pop(span.key, None)
    for key, value in remaining.items():
        result = append_assignment(result, f"{key} = {pdx_value(value)}")
    return result
