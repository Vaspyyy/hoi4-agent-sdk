"""Ordered read-only content resolution for dependent directory-backed mods."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Iterable

from .parser import parse_pdx

# Runtime content only: never snapshot executables, DLC archives, music or saves.
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


def _files(root: Path):
    for name in CONTENT_DIRS:
        directory = root / name
        if directory.is_dir():
            for path in sorted(directory.rglob("*")):
                if path.is_file():
                    if not path.resolve().is_relative_to(root):
                        raise ValueError(f"Content symlink escapes layer: {path}")
                    yield path.relative_to(root), path


def _clone(source: Path, target: Path) -> None:
    """Use a private COW inode; never hardlink mutable snapshot data to sources."""
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl

        with source.open("rb") as src, target.open("wb") as dst:
            fcntl.ioctl(dst.fileno(), 0x40049409, src.fileno())  # Linux FICLONE
    except (ImportError, OSError):
        shutil.copyfile(source, target)


class ContentLayers:
    """Snapshot lower layers, keeping originals available through provenance.

    Later paths win. A layer's replace_path hides lower directories before its
    own files are added. Top files are deliberately absent from the fallback,
    because the facade reads those directly from its writable root.
    """

    def __init__(self, game: Path | None, bases: Iterable[Path], top: Path):
        self.top = top
        self.bases = tuple(bases)
        roots = tuple(p for p in (game, *self.bases) if p is not None)
        for root in roots:
            if not root.is_dir():
                raise NotADirectoryError(root)
            if root == top or root.is_relative_to(top) or top.is_relative_to(root):
                raise ValueError("Content layers and output root must be separate directories")
        if len(set(roots)) != len(roots):
            raise ValueError("Duplicate content layer")
        self.sources: dict[Path, Path] = {}
        for root in (*roots, top):
            if root != game:
                for hidden in replace_paths(root):
                    self.sources = {
                        p: s for p, s in self.sources.items() if not p.is_relative_to(hidden)
                    }
            self.sources.update(_files(root))
        cache = Path.home() / ".cache" / "hoi4-sdk"
        cache.mkdir(parents=True, exist_ok=True)
        self._temp = tempfile.TemporaryDirectory(prefix="layers-", dir=cache)
        self.root = Path(self._temp.name)
        try:
            for relative, source in self.sources.items():
                if not source.is_relative_to(top):
                    _clone(source, self.root / relative)
        except BaseException:
            self.close()
            raise

    def source(self, relative: str | Path) -> Path | None:
        relative = relative_content_path(relative)
        top_file = self.top / relative
        if top_file.is_file():
            return top_file
        return self.sources.get(relative)

    def close(self) -> None:
        self._temp.cleanup()


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
