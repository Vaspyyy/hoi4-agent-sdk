"""
Country Tag Management
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .paths import require_country_tag

# Keep the generated-table writer's sorting and deduplication policy separate.
TAG_FILE_RE = re.compile(r'^\s*([A-Z0-9]{3})\s*=\s*"(.+)"\s*$')

_COUNTRY_TAG_LINE_RE = re.compile(
    r'^\s*([A-Z0-9]{3})\s*=\s*"([^"]+)"(?:\s*#.*)?\s*$'
)


def _parse_country_tag_line(line: str) -> tuple[str, str] | None:
    """Read a registration without treating hashes inside its quoted path as comments."""
    match = _COUNTRY_TAG_LINE_RE.match(line)
    return (match.group(1), match.group(2)) if match else None


def _parse_tag_file_mapping(country_tags_dir: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if not country_tags_dir.is_dir():
        return mapping
    for f in sorted(country_tags_dir.glob("*.txt")):
        txt = f.read_text(encoding="utf-8", errors="ignore")
        for line in txt.splitlines():
            entry = _parse_country_tag_line(line)
            if entry:
                tag, target = entry
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
    tags = set()
    d = mod_root / "common/country_tags"
    if not d.exists():
        return []
    for f in d.glob("*.txt"):
        txt = f.read_text(encoding="utf-8", errors="ignore")
        for line in txt.splitlines():
            entry = _parse_country_tag_line(line)
            if entry:
                tags.add(entry[0])
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
    if p.exists():
        content = p.read_text(encoding="utf-8", errors="ignore")
        if re.search(rf"^{re.escape(tag)}\s*=", content, re.MULTILINE):
            return p
    with p.open("a", encoding="utf-8") as fh:
        fh.write(line)
    return p


def sort_generated_country_tags(text: str) -> str:
    """Canonicalize an SDK-owned generated country-tag table."""

    other: list[str] = []
    entries: dict[str, str] = {}
    for line in text.splitlines():
        match = TAG_FILE_RE.match(line)
        if match is None:
            if line.strip():
                other.append(line.rstrip())
            continue
        entries[match.group(1)] = line.strip()
    lines = [*other, *(entries[tag] for tag in sorted(entries))]
    return "\n".join(lines) + ("\n" if lines else "")


def remove_country_tag(mod_root: Path, tag: str) -> Path | None:
    tag = require_country_tag(tag)
    path = mod_root / "common" / "country_tags" / "00_generated_tags.txt"
    if not path.exists():
        return None
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)
    kept = [line for line in lines if not re.match(rf"^\s*{re.escape(tag)}\s*=", line)]
    if kept == lines:
        return None
    path.write_text("".join(kept), encoding="utf-8")
    return path
