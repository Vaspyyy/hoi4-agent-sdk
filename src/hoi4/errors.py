"""Error classes shared by the whole SDK.

Every error the SDK raises on purpose derives from :class:`Hoi4Error`, so
``except hoi4.Hoi4Error`` catches all of them. The three common cases have
their own classes:

* :class:`NotFoundError`: an id, a tag, a file entry or an occurrence does not
  exist. It is also a ``KeyError``.
* :class:`AlreadyExistsError`: ``create_x`` or ``add_x`` met an id that already
  exists. It is also a ``ValueError``.
* :class:`ReadOnlyError`: the target is game content that this call cannot
  change. It is also a ``ValueError``.

This module imports nothing from the rest of the package, so every module can
use it.
"""

from __future__ import annotations

import difflib
from collections.abc import Iterable
from typing import Any

__all__ = [
    "AlreadyExistsError",
    "Hoi4Error",
    "NotFoundError",
    "ReadOnlyError",
    "already_exists",
    "not_found",
]

#: How many close matches a :class:`NotFoundError` message names at most.
MAX_SUGGESTIONS = 5


class Hoi4Error(Exception):
    """Base class of every error the SDK raises on purpose."""


class NotFoundError(Hoi4Error, KeyError):
    """Something that a call needs does not exist.

    Attributes:
        kind: What was looked up, in words (``"focus tree"``, ``"state"``).
        id: The id, tag or name that was not found.
        suggestions: Up to five known ids that look like ``id``.

    It is a ``KeyError`` too, so ``except KeyError`` keeps working. Unlike a
    plain ``KeyError``, ``str(error)`` is the message without extra quotes.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: str = "",
        id: Any = None,
        suggestions: Iterable[str] = (),
    ) -> None:
        super().__init__(message)
        self.message = message
        self.kind = kind
        self.id = id
        self.suggestions: tuple[str, ...] = tuple(suggestions)

    def __str__(self) -> str:
        return self.message


class AlreadyExistsError(Hoi4Error, ValueError):
    """``create_x`` or ``add_x`` met an id that already exists.

    Attributes:
        kind: What kind of thing exists already (``"event"``).
        id: Its id.
    """

    def __init__(self, message: str, *, kind: str = "", id: Any = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.id = id


class ReadOnlyError(Hoi4Error, ValueError):
    """The target is game content that this call cannot change or delete."""


def _plural(kind: str) -> str:
    if kind.endswith("y") and not kind.endswith(("ay", "ey", "oy", "uy")):
        return kind[:-1] + "ies"
    if kind.endswith(("s", "x", "ch", "sh")):
        return kind + "es"
    return kind + "s"


def _capitalize(text: str) -> str:
    return text[:1].upper() + text[1:]


def not_found(
    kind: str,
    id: Any,
    candidates: Iterable[Any] = (),
    *,
    where: str = "",
    hint: str = "",
    plural: str | None = None,
) -> NotFoundError:
    """Build a :class:`NotFoundError` with close matches.

    Args:
        kind: What was looked up, lower case (``"focus tree"``).
        id: The missing id.
        candidates: Every id of that kind that is known. The message names up
            to five close matches and how many ids are known, never the list.
        where: Optional place, appended as ``" in <where>"`` (``"focus tree
            'GER_focus'"``).
        hint: Optional sentence appended to the message (what to do instead).
        plural: Plural of ``kind`` when adding ``s`` is wrong.

    Returns:
        The error, ready to raise.
    """
    known = sorted({str(candidate) for candidate in candidates})
    suggestions: list[str] = []
    if isinstance(id, str) and known:
        suggestions = difflib.get_close_matches(id, known, n=MAX_SUGGESTIONS, cutoff=0.6)
    shown = repr(id) if isinstance(id, str) else str(id)
    message = f"{_capitalize(kind)} {shown} not found"
    if where:
        message += f" in {where}"
    message += "."
    if suggestions:
        message += f" Did you mean: {', '.join(suggestions)}?"
    count = len(known)
    noun = kind if count == 1 else (plural or _plural(kind))
    message += f" ({count} {noun} known)"
    if hint:
        message += f" {hint}"
    return NotFoundError(message, kind=kind, id=id, suggestions=suggestions)


def already_exists(kind: str, id: Any, *, hint: str = "") -> AlreadyExistsError:
    """Build an :class:`AlreadyExistsError` with one message style.

    Args:
        kind: What kind of thing exists already, lower case.
        id: Its id.
        hint: Optional sentence appended to the message (how to replace it).

    Returns:
        The error, ready to raise.
    """
    shown = repr(id) if isinstance(id, str) else str(id)
    message = f"{_capitalize(kind)} {shown} already exists."
    if hint:
        message += f" {hint}"
    return AlreadyExistsError(message, kind=kind, id=id)
