"""HOI4 idea-picture naming helpers.

Idea definitions store a bare ``picture`` stem. The engine prepends
``GFX_idea_`` when resolving that stem against interface sprite declarations.
"""

from __future__ import annotations

IDEA_SPRITE_PREFIX = "GFX_idea_"
DEFAULT_IDEA_ICON = "generic_political_support"


def normalize_idea_icon(value: str) -> str:
    """Return the bare picture stem accepted by an HOI4 idea definition."""

    normalized = value.strip()
    while normalized.startswith(IDEA_SPRITE_PREFIX):
        normalized = normalized.removeprefix(IDEA_SPRITE_PREFIX)
    return normalized


def resolve_idea_sprite(value: str) -> str:
    """Return the interface sprite key HOI4 resolves for a picture stem."""

    stem = normalize_idea_icon(value)
    return f"{IDEA_SPRITE_PREFIX}{stem}" if stem else ""
