"""Remember what was read out of a file until the file changes.

Validation asks the same questions about the game's files on every run
(which division templates, scripted effects or localisation keys they
define). The answers are small, the files are large, so each answer is kept
by file and question and reused while the file is unchanged.

A file counts as unchanged while its inode, size, modification time and
change time are the same. A file written in the last few seconds is not
cached at all: file times have a coarse tick, so a second write in the same
tick with the same size would otherwise go unnoticed (git calls this a
"racily clean" file).
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from .textio import read_text

T = TypeVar("T")

# How old a file must be before its answers are kept.
_SETTLED_NS = 3_000_000_000

_Identity = tuple[int, int, int, int]
_CACHE: dict[tuple[str, str], tuple[_Identity, object]] = {}


def file_identity(path: str | os.PathLike[str]) -> _Identity | None:
    """The identity of a settled file, or ``None`` if it is new or missing."""

    try:
        stat = os.stat(path)
    except OSError:
        return None
    if max(stat.st_mtime_ns, stat.st_ctime_ns) > time.time_ns() - _SETTLED_NS:
        return None
    return (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def cached_file_result(
    purpose: str,
    path: str | os.PathLike[str],
    compute: Callable[[str], T],
    *,
    read: Callable[[Path], str] = read_text,
) -> T:
    """``compute(text of path)``, reused while the file is unchanged.

    ``purpose`` names the question, so different questions about one file
    are kept apart. The result is shared between callers: never change it.
    Errors from reading or computing are not cached and reach the caller.
    """

    identity = file_identity(path)
    key = (purpose, os.fspath(path))
    if identity is not None:
        cached = _CACHE.get(key)
        if cached is not None and cached[0] == identity:
            return cached[1]  # type: ignore[return-value]
    result = compute(read(Path(path)))
    if identity is not None:
        _CACHE[key] = (identity, result)
    return result


def clear_file_cache() -> None:
    """Forget every remembered answer (for tests and long-lived processes)."""

    _CACHE.clear()
