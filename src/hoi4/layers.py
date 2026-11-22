"""Read-only views of the content a mod inherits from the game and base mods.

A view answers from the source files where they are; nothing is copied.
``FolderView`` is one folder as it is on disk (the game without base mods).
``ContentLayers`` stacks the game and ordered base mods under a writable mod.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Callable, Iterable, Iterator, NamedTuple

from .parser import parse_pdx

# Runtime content only: executables, DLC archives, music and saves are never layered.
CONTENT_DIRS = (
    "common",
    "history",
    "events",
    "decisions",
    "map",
    "gfx",
    "interface",
    "localisation",
    "localization",
    "portraits",
    "documentation",
)


def relative_content_path(value: str | Path) -> Path:
    text = str(value).replace("\\", "/")
    path = Path(text)
    if not text or path.is_absolute() or ".." in path.parts or ":" in text or path == Path("."):
        raise ValueError(f"Unsafe content path: {value!r}")
    return path


def replace_paths(root: Path) -> tuple[Path, ...]:
    descriptor = root / "descriptor.mod"
    if not descriptor.is_file():
        return ()
    parsed = parse_pdx(descriptor.read_text(encoding="utf-8-sig"))
    return tuple(
        relative_content_path(node.value or "") for node in parsed.find_all("replace_path")
    )


class _Entry(NamedTuple):
    name: str
    key: object  # what ``children`` takes to list a folder
    is_dir: bool
    path: Path | None  # the file, for files


def _rglob_order(children: Callable[[object], list[_Entry]], start: object) -> Iterator[_Entry]:
    """The files below ``start``, in the order ``Path.rglob`` gives them.

    That is the folder's own files, then the files of each subfolder in
    listing order, then depth first from the last subfolder (Python 3.13).
    Every folder is listed once.
    """

    first = children(start)
    yield from (entry for entry in first if not entry.is_dir)
    stack = [first]
    while stack:
        for entry in stack.pop():
            if entry.is_dir:
                below = children(entry.key)
                yield from (child for child in below if not child.is_dir)
                stack.append(below)


def _scan(path: str) -> list[os.DirEntry[str]]:
    try:
        with os.scandir(path) as entries:
            return list(entries)
    except OSError:
        return []


def _folder_entries(path: object) -> list[_Entry]:
    """A folder's files and real subfolders (not symlinked ones), as ``rglob`` sees them."""

    assert isinstance(path, str)
    result = []
    for entry in _scan(path):
        try:
            if entry.is_dir(follow_symlinks=False):
                result.append(_Entry(entry.name, entry.path, True, None))
            elif entry.is_file():
                result.append(_Entry(entry.name, None, False, Path(entry.path)))
        except OSError:
            continue
    return result


class ContentView:
    """Read-only content under relative paths such as ``history/states/1.txt``.

    Listings return ``{relative path: source file}`` and never ``stat`` a file
    the folder listing already describes.
    """

    def __copy__(self) -> ContentView:
        return self

    def __deepcopy__(self, memo: dict[int, object]) -> ContentView:
        # Read-only: a copied model keeps pointing at the same view, so views
        # used as keys (country tag mappings) still match after a rollback.
        return self

    @property
    def roots(self) -> tuple[Path, ...]:
        """The folders the view reads, lowest layer first."""
        raise NotImplementedError

    def file(self, relative: str | Path) -> Path | None:
        """The source file that ``relative`` reads, or ``None``."""
        raise NotImplementedError

    def glob(self, directory: str | Path, pattern: str = "*") -> dict[Path, Path]:
        """Files directly in ``directory`` whose name matches, sorted by name."""
        raise NotImplementedError

    def rglob(self, directory: str | Path, pattern: str = "*") -> dict[Path, Path]:
        """Files anywhere below ``directory`` whose name matches, in ``rglob`` order."""
        raise NotImplementedError

    def relative(self, path: Path) -> Path | None:
        """``path`` relative to the root of the view it is in, or ``None``."""
        best: Path | None = None
        for root in self.roots:
            if path.is_relative_to(root) and (best is None or root.is_relative_to(best)):
                best = root
        return None if best is None else path.relative_to(best)


@dataclass(frozen=True)
class FolderView(ContentView):
    """One folder as it is on disk: the same calls as direct path access."""

    root: Path

    @property
    def roots(self) -> tuple[Path, ...]:
        return (self.root,)

    def file(self, relative: str | Path) -> Path | None:
        path = self.root / relative
        return path if path.is_file() else None

    def glob(self, directory: str | Path, pattern: str = "*") -> dict[Path, Path]:
        folder = Path(directory)
        found = sorted(
            entry.name
            for entry in _folder_entries(str(self.root / folder))
            if not entry.is_dir and fnmatchcase(entry.name, pattern)
        )
        return {folder / name: self.root / folder / name for name in found}

    def rglob(self, directory: str | Path, pattern: str = "*") -> dict[Path, Path]:
        result: dict[Path, Path] = {}
        for entry in _rglob_order(_folder_entries, str(self.root / directory)):
            if fnmatchcase(entry.name, pattern):
                assert entry.path is not None
                result[entry.path.relative_to(self.root)] = entry.path
        return result


def as_view(root: Path | ContentView | None) -> ContentView | None:
    """A view of ``root``; a plain folder becomes a ``FolderView``."""

    if root is None or isinstance(root, ContentView):
        return root
    return FolderView(Path(root))


def _is_under(relative: Path, folders: Iterable[Path]) -> bool:
    return any(relative.is_relative_to(folder) for folder in folders)


class ContentLayers(ContentView):
    """The game, then base mods in order, under a writable top mod.

    Later layers win. Each mod's ``descriptor.mod`` ``replace_path`` hides
    those folders in every lower layer. Only ``CONTENT_DIRS`` are layered.
    As a view, it is what the top mod inherits: a file the top mod has itself
    (or hides with its own ``replace_path``) is not part of it, because the
    mod's own files are read from the mod folder. ``source()`` adds them back.

    Nothing is copied or cached except the descriptors' ``replace_path``
    lines (read once; ``Mod.reload()`` builds new layers) and which folders
    are reached through a symlink. Files are found where they are on disk.
    """

    def __init__(self, game: Path | None, bases: Iterable[Path], top: Path):
        self.top = top
        self.bases = tuple(bases)
        lowers = tuple(p for p in (game, *self.bases) if p is not None)
        for root in lowers:
            if not root.is_dir():
                raise NotADirectoryError(root)
            if root == top or root.is_relative_to(top) or top.is_relative_to(root):
                raise ValueError("Content layers and output root must be separate directories")
        if len(set(lowers)) != len(lowers):
            raise ValueError("Duplicate content layer")
        self._roots = lowers
        self._real = tuple(root.resolve() for root in lowers)
        # ``_hides[i]``: the replace paths of layer ``i`` (the top is the last
        # layer). They hide every layer below ``i``. The game has none.
        self._hides = tuple(
            () if root == game else replace_paths(root) for root in (*lowers, top)
        )
        self._folder_kind: dict[tuple[int, Path], str] = {}

    @property
    def roots(self) -> tuple[Path, ...]:
        return self._roots

    # ── Single files ─────────────────────────────────────────────

    def source(self, relative: str | Path) -> Path | None:
        """The winning file of the game, the base mods or the top mod."""
        relative = relative_content_path(relative)
        top_file = self.top / relative
        if top_file.is_file():
            return top_file
        return self.inherited(relative)

    def file(self, relative: str | Path) -> Path | None:
        relative = Path(relative)
        if (self.top / relative).is_file():
            return None
        return self.inherited(relative)

    def inherited(self, relative: str | Path) -> Path | None:
        """The lower file a top-mod file at ``relative`` hides, or would hide.

        It honours the top mod's ``replace_path``: a file in a folder the top
        replaces is not inherited even when the top has no file there.
        """

        relative = Path(relative)
        if not relative.parts or relative.parts[0] not in CONTENT_DIRS:
            return None
        for index in range(len(self.roots) - 1, -1, -1):
            if _is_under(relative, self._hides[index + 1]):
                return None
            found = self._layer_file(index, relative)
            if found is not None:
                return found
        return None

    def _layer_file(self, index: int, relative: Path) -> Path | None:
        kind = self._folder(index, relative.parent)
        if kind == "unseen":
            return None
        path = self.roots[index] / relative
        try:
            mode = os.lstat(path).st_mode
            if stat.S_ISLNK(mode):
                mode = os.stat(path).st_mode
                kind = "linked"
        except OSError:
            return None
        if not stat.S_ISREG(mode):
            return None
        if kind == "linked":
            self._check_inside(index, path)
        return path

    def _folder(self, index: int, folder: Path) -> str:
        """How a layer's folder is reached: ``plain``, ``linked`` or ``unseen``.

        Like a recursive listing, only a top-level content folder may be a
        symlink (its files must then stay inside the layer); a deeper
        symlinked folder is not entered at all.
        """

        key = (index, folder)
        kind = self._folder_kind.get(key)
        if kind is None:
            if len(folder.parts) <= 1:
                kind = "linked" if (self.roots[index] / folder).is_symlink() else "plain"
            else:
                kind = self._folder(index, folder.parent)
                if kind != "unseen" and (self.roots[index] / folder).is_symlink():
                    kind = "unseen"
            self._folder_kind[key] = kind
        return kind

    def _check_inside(self, index: int, path: Path) -> None:
        if not path.resolve().is_relative_to(self._real[index]):
            raise ValueError(f"Content symlink escapes layer: {path}")

    # ── Listings ─────────────────────────────────────────────────

    def glob(self, directory: str | Path, pattern: str = "*") -> dict[Path, Path]:
        merged = self._merged(Path(directory), pattern, recursive=False)
        return dict(sorted(merged.items(), key=lambda item: item[0].name))

    def rglob(self, directory: str | Path, pattern: str = "*") -> dict[Path, Path]:
        folder = Path(directory)
        merged = self._merged(folder, pattern, recursive=True)
        # Order the files as ``rglob`` would over one merged folder whose
        # entries were created in ``merged`` order.
        children: dict[Path, dict[str, _Entry]] = {folder: {}}
        for relative, source in merged.items():
            parent = folder
            for name in relative.relative_to(folder).parts[:-1]:
                child = parent / name
                if child not in children:
                    children[parent][name] = _Entry(name, child, True, None)
                    children[child] = {}
                parent = child
            children[parent][relative.name] = _Entry(relative.name, relative, False, source)

        def listing(key: object) -> list[_Entry]:
            assert isinstance(key, Path)
            return list(children[key].values())

        return {
            entry.key: entry.path  # type: ignore[misc]
            for entry in _rglob_order(listing, folder)
        }

    def _merged(self, folder: Path, pattern: str, *, recursive: bool) -> dict[Path, Path]:
        """``{relative: source}`` below ``folder`` in layer order.

        Each layer first drops what its ``replace_path`` hides, then adds its
        files (sorted), so a file keeps the place it got in the lowest layer
        that has it. Files the top mod has are left out.
        """

        if not folder.parts or folder.parts[0] not in CONTENT_DIRS:
            return {}
        # A folder replaced as a whole by layer ``start`` hides all below it.
        start = 0
        for index in range(len(self.roots), 0, -1):
            if _is_under(folder, self._hides[index]):
                start = index
                break
        merged: dict[Path, Path] = {}
        for index in range(start, len(self.roots) + 1):
            hides = [path for path in self._hides[index] if path.is_relative_to(folder)]
            if hides and index > start:
                merged = {
                    relative: source
                    for relative, source in merged.items()
                    if not _is_under(relative, hides)
                }
            if index == len(self.roots):
                break
            merged.update(self._layer_files(index, folder, pattern, recursive))
        if merged:
            for relative in self._top_files(folder, recursive):
                merged.pop(relative, None)
        return merged

    def _layer_files(
        self, index: int, folder: Path, pattern: str, recursive: bool
    ) -> list[tuple[Path, Path]]:
        kind = self._folder(index, folder)
        if kind == "unseen":
            return []
        root = self.roots[index]
        found: list[tuple[tuple[str, ...], Path]] = []
        stack = [str(root / folder)]
        while stack:
            for entry in _scan(stack.pop()):
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if recursive:
                            stack.append(entry.path)
                        continue
                    if not entry.is_file() or not fnmatchcase(entry.name, pattern):
                        continue
                except OSError:
                    continue
                path = Path(entry.path)
                if kind == "linked" or entry.is_symlink():
                    self._check_inside(index, path)
                found.append((path.relative_to(root).parts, path))
        found.sort(key=lambda item: item[0])
        return [(Path(*parts), path) for parts, path in found]

    def _top_files(self, folder: Path, recursive: bool) -> Iterator[Path]:
        stack = [str(self.top / folder)]
        while stack:
            for entry in _scan(stack.pop()):
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if recursive:
                            stack.append(entry.path)
                    elif entry.is_file():
                        yield Path(entry.path).relative_to(self.top)
                except OSError:
                    continue


def expand_replace_paths(
    paths: Iterable[str | Path], content_roots: Iterable[str | Path]
) -> tuple[str, ...]:
    """Declare replacement roots and their existing descendants explicitly.

    Useful when packaging a conversion against engine versions that enumerate
    some content registries in subdirectories. This does not change layer
    resolution rules or modify source content. Stable order supports rebuilds.
    """
    roots = tuple(Path(root).expanduser().resolve() for root in content_roots)
    result: dict[str, None] = {}
    for value in paths:
        relative = relative_content_path(value)
        result.setdefault(relative.as_posix(), None)
        descendants: set[str] = set()
        for root in roots:
            directory = root / relative
            if not directory.resolve().is_relative_to(root):
                raise ValueError(f"Content symlink escapes layer: {directory}")
            if directory.is_dir():
                for child in directory.rglob("*"):
                    if not child.resolve().is_relative_to(root):
                        raise ValueError(f"Content symlink escapes layer: {child}")
                    if child.is_dir():
                        descendants.add(child.relative_to(root).as_posix())
        for descendant in sorted(descendants):
            result.setdefault(descendant, None)
    return tuple(result)
