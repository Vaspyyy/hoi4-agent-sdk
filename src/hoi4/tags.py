"""
Country Tag Management
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

TAG_LINE_RE = re.compile(r'^\s*([A-Z0-9]{3})\s*=\s*".*"\s*$')
TAG_FILE_RE = re.compile(r'^\s*([A-Z0-9]{3})\s*=\s*"(.+)"\s*$')


def _parse_tag_file_mapping(country_tags_dir: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if not country_tags_dir.is_dir():
        return mapping
    for f in sorted(country_tags_dir.glob("*.txt")):
        txt = f.read_text(encoding="utf-8", errors="ignore")
        for line in txt.splitlines():
            m = TAG_FILE_RE.match(line)
            if m:
                mapping[m.group(1)] = m.group(2)
    return mapping


def resolve_country_filename(base: Path, tag: str) -> Optional[Path]:
    mapping = _parse_tag_file_mapping(base / "common" / "country_tags")
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
    p = hoi4_install / "common/country_tags/00_countries.txt"
    if not p.exists():
        return set()
    tags = set()
    txt = p.read_text(encoding="utf-8", errors="ignore")
    for line in txt.splitlines():
        m = TAG_LINE_RE.match(line)
        if m:
            tags.add(m.group(1))
    return tags


def load_mod_tags(mod_root: Path) -> list[str]:
    tags = set()
    d = mod_root / "common/country_tags"
    if not d.exists():
        return []
    for f in d.glob("*.txt"):
        txt = f.read_text(encoding="utf-8", errors="ignore")
        for line in txt.splitlines():
            m = TAG_LINE_RE.match(line)
            if m:
                tags.add(m.group(1))
    return sorted(tags)


def load_all_tags(hoi4_install: Optional[Path], mod_root: Optional[Path]) -> list[str]:
    tags: set[str] = set()
    if hoi4_install:
        tags.update(load_vanilla_tags(hoi4_install))
    if mod_root:
        tags.update(load_mod_tags(mod_root))
    return sorted(tags)


def add_country_tag(mod_root: Path, tag: str) -> None:
    p = mod_root / "common/country_tags/00_generated_tags.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    line = f'{tag} = "countries/{tag}.txt"\n'
    if p.exists():
        content = p.read_text(encoding="utf-8", errors="ignore")
        if re.search(rf"^{re.escape(tag)}\s*=", content, re.MULTILINE):
            return
    with p.open("a", encoding="utf-8") as fh:
        fh.write(line)
