"""
Country Tag Management
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .patching import replace_assignment, top_level_assignments
from .paths import COUNTRY_TAG_RE, require_country_tag
from .syntax import Assignment, Scalar, parse
from .textio import encode_text, read_text, write_text


def _tag_registrations(text: str) -> list[Assignment]:
    return [
        item
        for item in parse(text).root.assignments()
        if item.op == "="
        and type(item.value) is Scalar
        and COUNTRY_TAG_RE.fullmatch(item.name)
    ]


def country_tag_entries(text: str) -> list[tuple[str, str]]:
    """Return the ``TAG = "countries/File.txt"`` registrations of a file, in order.

    Comments, quoted ``#`` characters and other statements such as
    ``dynamic_tags = yes`` are never registrations.
    """

    return [(item.name, item.scalar or "") for item in _tag_registrations(text)]


def remove_country_tag_entries(text: str, tags: set[str] | frozenset[str]) -> str:
    """Remove every registration of ``tags`` and leave all other bytes alone."""

    spans = [
        span
        for span in top_level_assignments(text)
        if span.key in tags and not span.is_block
    ]
    for span in reversed(spans):
        text = replace_assignment(text, span, None)
    return text


def _parse_country_tag_line(line: str) -> tuple[str, str] | None:
    """Read one registration line (kept for callers of the old line reader)."""

    entries = country_tag_entries(line)
    return entries[0] if entries else None


def _parse_tag_file_mapping(country_tags_dir: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if not country_tags_dir.is_dir():
        return mapping
    for f in sorted(country_tags_dir.glob("*.txt")):
        for tag, target in country_tag_entries(read_text(f)):
            mapping[tag] = target
    return mapping


def resolve_country_filename(
    base: Path, tag: str, mapping: dict[str, str] | None = None
) -> Optional[Path]:
    mapping = (
        mapping
        if mapping is not None
        else _parse_tag_file_mapping(base / "common" / "country_tags")
    )
    rel = mapping.get(tag)
    if rel:
        filename = Path(rel).name
        p = base / "common" / "countries" / filename
        if p.exists():
            return p
    p = base / "common" / "countries" / f"{tag}.txt"
    if p.exists():
        return p
    return None


def load_vanilla_tags(hoi4_install: Path) -> set[str]:
    return set(_parse_tag_file_mapping(hoi4_install / "common" / "country_tags"))


def load_mod_tags(mod_root: Path) -> list[str]:
    tags: set[str] = set()
    d = mod_root / "common/country_tags"
    if not d.exists():
        return []
    for f in d.glob("*.txt"):
        tags.update(tag for tag, _ in country_tag_entries(read_text(f)))
    return sorted(tags)


def load_all_tags(hoi4_install: Optional[Path], mod_root: Optional[Path]) -> list[str]:
    tags: set[str] = set()
    if hoi4_install:
        tags.update(load_vanilla_tags(hoi4_install))
    if mod_root:
        tags.update(load_mod_tags(mod_root))
    return sorted(tags)


def add_country_tag(mod_root: Path, tag: str) -> Path:
    tag = require_country_tag(tag)
    p = mod_root / "common/country_tags/00_generated_tags.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    line = f'{tag} = "countries/{tag}.txt"\n'
    if not p.exists():
        write_text(p, line)
        return p
    original = p.read_bytes()
    content = read_text(p)
    if any(existing == tag for existing, _ in country_tag_entries(content)):
        return p
    separator = "\n" if content and not content.endswith("\n") else ""
    p.write_bytes(encode_text(content + separator + line, like=original))
    return p


def sort_generated_country_tags(text: str) -> str:
    """Canonicalize an SDK-owned generated country-tag table."""

    # A registration that owns its whole line (no comment, nothing else on
    # it) is canonical and sorted; the last one of a tag wins. Every other
    # line, comments and commented registrations included, stays first in
    # its original order.
    entries: dict[str, str] = {}
    owned: list[tuple[int, int]] = []
    for item in _tag_registrations(text):
        if type(item.value) is not Scalar or not item.value.quoted:
            continue
        line_start = text.rfind("\n", 0, item.start) + 1
        line_end = text.find("\n", item.end)
        line_end = len(text) if line_end < 0 else line_end
        if text[line_start : item.start].strip() or text[item.end : line_end].strip():
            continue
        entries[item.name] = text[item.start : item.end]
        owned.append((line_start, line_end))
    remaining: list[str] = []
    position = 0
    for start, end in owned:
        remaining.append(text[position:start])
        position = end
    remaining.append(text[position:])
    other = [line.rstrip() for line in "".join(remaining).splitlines() if line.strip()]
    lines = [*other, *(entries[tag] for tag in sorted(entries))]
    return "\n".join(lines) + ("\n" if lines else "")


def remove_country_tag(mod_root: Path, tag: str) -> Path | None:
    tag = require_country_tag(tag)
    path = mod_root / "common" / "country_tags" / "00_generated_tags.txt"
    if not path.exists():
        return None
    original = path.read_bytes()
    text = read_text(path)
    updated = remove_country_tag_entries(text, {tag})
    if updated == text:
        return None
    path.write_bytes(encode_text(updated, like=original))
    return path
