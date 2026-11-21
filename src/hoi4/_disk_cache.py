"""Keep per-file results for the game's files on disk, between processes.

Validation reads thousands of the game's own files (and the files of base
mods) on its first run in a process. Those files do not change between
runs, so what one process read out of them is stored here and the next
process reads the stored results instead of parsing the files again.

Only files the mod inherits are stored, never the files of the mod being
edited: callers decide that and pass an :class:`DiskAnalysis` only for
inherited files (see ``hoi4._file_cache.cached_file_result``).

Where it lives: ``$HOI4_SDK_CACHE_DIR/file-results/`` when that variable is
set, else ``$XDG_CACHE_HOME/hoi4-sdk/file-results/``, else
``~/.cache/hoi4-sdk/file-results/``. ``HOI4_SDK_DISK_CACHE=off`` (or ``0``,
``false``, ``no``) turns every disk cache of the SDK off.

What is in it: one JSON file per analysis and code version, named
``<analysis>-<code stamp>.json``. Each entry belongs to one file and one
question and records the file's size and modification time; an entry whose
file now has another size or time is not used. The code stamp is a hash of
every Python source of the ``hoi4`` package, so a changed analysis (or any
other change of the SDK) starts a new file and can never read old results.

The files are JSON, so reading one never runs code. They are written to a
temporary file and renamed into place, so a reader sees either the old or
the new file. Two processes that write at once each write a whole file and
the last one wins. A broken or truncated file is ignored and rebuilt.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Generic, TypeVar

T = TypeVar("T")

# Bump when the layout of the store files changes.
_FORMAT = 1
# Store files of other code versions kept per analysis (another checkout or
# an older install of the SDK may still use them).
_KEEP_VERSIONS = 3
# A temporary file older than this was left by a process that died.
_STALE_TEMPORARY_S = 3600
_OFF_VALUES = frozenset({"0", "off", "false", "no"})
_SUBDIRECTORY = "file-results"


def cache_root() -> Path:
    """The folder of every disk cache of the SDK.

    ``$HOI4_SDK_CACHE_DIR`` when set, else ``$XDG_CACHE_HOME/hoi4-sdk``, else
    ``~/.cache/hoi4-sdk``.
    """

    configured = os.environ.get("HOI4_SDK_CACHE_DIR")
    if configured:
        return Path(configured)
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg and Path(xdg).is_absolute() else Path.home() / ".cache"
    return base / "hoi4-sdk"


def disk_cache_enabled() -> bool:
    """False when ``HOI4_SDK_DISK_CACHE`` turns the disk caches off."""

    return os.environ.get("HOI4_SDK_DISK_CACHE", "").strip().lower() not in _OFF_VALUES


@lru_cache(maxsize=1)
def code_stamp() -> str:
    """A hash of the SDK's code: every ``.py`` file of the ``hoi4`` package.

    Any change of an analysis, or of anything it calls, changes the stamp.
    """

    package = Path(__file__).resolve().parent
    digest = hashlib.sha256(f"{_FORMAT}\0{sys.version_info[0]}.{sys.version_info[1]}\0".encode())
    for path in sorted(package.rglob("*.py")):
        digest.update(path.relative_to(package).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:16]


@dataclass(frozen=True)
class DiskAnalysis(Generic[T]):
    """How the results of one analysis go to JSON and back.

    ``name`` names the store file (letters, digits and ``_``). ``decode``
    must return a result equal to the one ``encode`` was given.
    """

    name: str
    encode: Callable[[T], Any]
    decode: Callable[[Any], T]


def _sorted_strings(values: frozenset[str]) -> list[str]:
    return sorted(values)


#: A set of names read from one file (scripted effects, division templates).
NAMES_ON_DISK: DiskAnalysis[frozenset[str]] = DiskAnalysis("names", _sorted_strings, frozenset)


def _pairs_to_lists(pairs: tuple[tuple[str, str], ...]) -> list[list[str]]:
    return [list(pair) for pair in pairs]


def _lists_to_pairs(data: list[list[str]]) -> tuple[tuple[str, str], ...]:
    return tuple((str(first), str(second)) for first, second in data)


#: Ordered pairs of names read from one file (equipment and what unlocks it).
PAIRS_ON_DISK: DiskAnalysis[tuple[tuple[str, str], ...]] = DiskAnalysis(
    "pairs", _pairs_to_lists, _lists_to_pairs
)

_MISSING = object()


class _Store:
    """The entries of one analysis: those read from disk and those added."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.loaded: dict[str, list[Any]] | None = None
        self.added: dict[str, list[Any]] = {}

    def path(self) -> Path:
        return cache_root() / _SUBDIRECTORY / f"{self.name}-{code_stamp()}.json"

    def _read(self) -> dict[str, list[Any]]:
        try:
            with self.path().open(encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError, RecursionError):
            return {}
        if (
            not isinstance(data, dict)
            or data.get("format") != _FORMAT
            or data.get("analysis") != self.name
            or data.get("code") != code_stamp()
            or not isinstance(data.get("entries"), dict)
        ):
            return {}
        entries: dict[str, list[Any]] = data["entries"]
        return entries

    def get(self, key: str, size: int, mtime_ns: int) -> Any:
        if self.loaded is None:
            self.loaded = self._read()
        entry = self.added.get(key)
        if entry is None:
            # A result read once lives on decoded in the caller's memory
            # cache; the raw copy is not kept twice.
            entry = self.loaded.pop(key, None)
        if (
            isinstance(entry, list)
            and len(entry) == 3
            and entry[0] == size
            and entry[1] == mtime_ns
        ):
            return entry[2]
        return _MISSING

    def put(self, key: str, size: int, mtime_ns: int, data: Any) -> None:
        self.added[key] = [size, mtime_ns, data]

    def flush(self) -> None:
        if not self.added:
            return
        # Merge with what another process may have written meanwhile, then
        # drop the entries of files that changed or are gone.
        entries = self._read()
        entries.update(self.added)
        self.added = {}
        entries = {key: entry for key, entry in entries.items() if _entry_current(key, entry)}
        # Read the file again on the next lookup instead of keeping it all.
        self.loaded = None
        target = self.path()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(
                dir=target.parent, prefix=f".{target.stem}-", suffix=".tmp"
            )
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as stream:
                    json.dump(
                        {
                            "format": _FORMAT,
                            "analysis": self.name,
                            "code": code_stamp(),
                            "entries": entries,
                        },
                        stream,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    )
                os.replace(temporary, target)
            except BaseException:
                Path(temporary).unlink(missing_ok=True)
                raise
        except (OSError, ValueError, TypeError):
            return
        _prune(target.parent, self.name, target)


def _entry_current(key: str, entry: object) -> bool:
    if not isinstance(entry, list) or len(entry) != 3:
        return False
    path = key.partition("\0")[0]
    try:
        stat = os.stat(path)
    except OSError:
        return False
    return bool(stat.st_size == entry[0] and stat.st_mtime_ns == entry[1])


def _prune(directory: Path, name: str, current: Path) -> None:
    """Keep the newest store files of an analysis and drop dead temporaries."""

    try:
        versions = sorted(
            (path for path in directory.glob(f"{name}-*.json") if path != current),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for old in versions[_KEEP_VERSIONS - 1 :]:
            old.unlink(missing_ok=True)
        limit = time.time() - _STALE_TEMPORARY_S
        for temporary in directory.glob(f".{name}-*.tmp"):
            if temporary.stat().st_mtime < limit:
                temporary.unlink(missing_ok=True)
    except OSError:
        return


_LOCK = threading.RLock()
_STORES: dict[str, _Store] = {}


def _store(name: str) -> _Store:
    store = _STORES.get(name)
    if store is None:
        store = _STORES[name] = _Store(name)
    return store


def lookup(analysis: DiskAnalysis[Any], key: str, size: int, mtime_ns: int) -> tuple[bool, Any]:
    """``(True, result)`` when the store holds a result for this file state.

    ``key`` starts with the file's path and a NUL character.
    """

    if not disk_cache_enabled():
        return False, None
    with _LOCK:
        raw = _store(analysis.name).get(key, size, mtime_ns)
    if raw is _MISSING:
        return False, None
    try:
        return True, analysis.decode(raw)
    except (TypeError, ValueError, KeyError, IndexError, AttributeError):
        # An entry another version of the format wrote: compute it again.
        return False, None


def remember(analysis: DiskAnalysis[T], key: str, size: int, mtime_ns: int, result: T) -> None:
    """Add a result; :func:`flush` writes it."""

    if not disk_cache_enabled():
        return
    data = analysis.encode(result)
    with _LOCK:
        _store(analysis.name).put(key, size, mtime_ns, data)


def flush() -> None:
    """Write every store that has new results."""

    if not disk_cache_enabled():
        return
    with _LOCK:
        for store in _STORES.values():
            store.flush()


def forget() -> None:
    """Drop what was read from disk and what was not yet written (for tests)."""

    with _LOCK:
        _STORES.clear()


atexit.register(flush)
