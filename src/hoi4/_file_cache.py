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

Results for files the mod inherits (the game and base mods) can also be kept
on disk between processes: pass ``disk`` (see ``hoi4._disk_cache``).
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TypeVar

from . import _disk_cache
from ._disk_cache import DiskAnalysis, FileOrigin
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
    disk: DiskAnalysis[T] | None = None,
    origin: FileOrigin | None = None,
) -> T:
    """``compute(text of path)``, reused while the file is unchanged.

    ``purpose`` names the question, so different questions about one file
    are kept apart. The result is shared between callers: never change it.
    Errors from reading or computing are not cached and reach the caller.

    With ``disk``, the result is also kept on disk for later processes.
    Pass it only for files the mod inherits, never for the mod's own files.
    ``origin`` is given for a private copy of an inherited file: the result
    is stored under the original file, as it was when the copy was made.
    """

    identity = file_identity(path)
    if identity is None and origin is not None:
        identity = _copy_identity(path, origin)
    if disk is not None and origin is None and _disk_cache.in_private_copy(path):
        disk = None
    key = (purpose, os.fspath(path))
    if identity is not None:
        cached = _CACHE.get(key)
        if cached is not None and cached[0] == identity:
            return cached[1]  # type: ignore[return-value]
    stored: tuple[str, int, int] | None = None
    if disk is not None and identity is not None:
        if origin is not None:
            stored = (f"{origin.path}\0{purpose}", origin.size, origin.mtime_ns)
        else:
            stored = (f"{os.fspath(path)}\0{purpose}", identity[1], identity[2])
        found, stored_result = _disk_cache.lookup(disk, *stored)
        if found:
            _CACHE[key] = (identity, stored_result)
            return stored_result  # type: ignore[no-any-return]
    result = compute(read(Path(path)))
    if identity is not None:
        _CACHE[key] = (identity, result)
    if stored is not None and disk is not None:
        _disk_cache.remember(disk, *stored, result)
    return result


def _copy_identity(path: str | os.PathLike[str], origin: FileOrigin) -> _Identity | None:
    """The identity of a private copy that was just made, from its original.

    A fresh copy is not settled yet, but its content is the original's as it
    was when copied, so the original's recorded time stands for it.
    """

    try:
        stat = os.stat(path)
    except OSError:
        return None
    if stat.st_size != origin.size:
        return None
    return (stat.st_ino, stat.st_size, origin.mtime_ns, -1)


def clear_file_cache() -> None:
    """Forget every remembered answer (for tests and long-lived processes)."""

    _CACHE.clear()


def cached_files_result(
    purpose: str,
    paths: Sequence[Path],
    compute: Callable[[], T],
    disk: DiskAnalysis[T],
) -> T:
    """``compute()`` of several inherited files, kept on disk while they are unchanged.

    The entry belongs to the first file; the others' paths, sizes and times
    are part of its key. Nothing is kept while a file is new or missing.
    Pass only files the mod inherits.
    """

    identities = [file_identity(path) for path in paths]
    if (
        not paths
        or any(identity is None for identity in identities)
        or any(_disk_cache.in_private_copy(path) for path in paths)
    ):
        return compute()
    first = identities[0]
    assert first is not None
    others = "\0".join(
        f"{os.fspath(path)}:{identity[1]}:{identity[2]}"
        for path, identity in zip(paths[1:], identities[1:])
        if identity is not None
    )
    key = f"{os.fspath(paths[0])}\0{purpose}\0{others}"
    found, stored_result = _disk_cache.lookup(disk, key, first[1], first[2])
    if found:
        return stored_result  # type: ignore[no-any-return]
    result = compute()
    _disk_cache.remember(disk, key, first[1], first[2], result)
    return result
