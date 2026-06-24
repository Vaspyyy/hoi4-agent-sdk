"""
Localization Utilities

Read, search, and modify HOI4 YML localization files.
"""

from __future__ import annotations

import re
from pathlib import Path

YML_ENTRY_RE = re.compile(r'^\s*([^:#\s]+)\s*:\s*(?:\d+\s*)?\s*"([^"]*)"\s*$')


def parse_localization_dir(loc_dir: Path) -> tuple[dict[str, str], dict[str, Path]]:
    entries: dict[str, str] = {}
    sources: dict[str, Path] = {}

    if not loc_dir.exists():
        return entries, sources

    for f in loc_dir.rglob("*.yml"):
        file_entries = parse_localization_file(f)
        for key, value in file_entries.items():
            entries[key] = value
            sources[key] = f

    return entries, sources


def parse_localization_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    raw = path.read_bytes()
    try:
        txt = raw.decode("utf-8-sig")
    except Exception:
        txt = raw.decode("utf-8", errors="ignore")

    for line in txt.splitlines():
        if not line or line.strip().startswith("#") or line.strip().startswith("l_"):
            continue
        m = YML_ENTRY_RE.match(line)
        if m:
            out[m.group(1)] = m.group(2)

    return out


def serialize_localization_file(entries: dict[str, str]) -> str:
    lines = ["l_english:"]
    for key in sorted(entries):
        sep = " " if ":" in key else ":0 "
        lines.append(f' {key}{sep}"{entries[key]}"')
    lines.append("")
    return "\n".join(lines)


def write_localization_file(path: Path, entries: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_existing(path)

    merged = {**existing, **entries}

    lines = ["l_english:"]
    for key in sorted(merged):
        sep = " " if ":" in key else ":0 "
        lines.append(f' {key}{sep}"{merged[key]}"')
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8-sig")


def delete_localization_keys(path: Path, keys_to_delete: set[str]) -> None:
    if not path.exists():
        return
    existing = _read_existing(path)
    for key in keys_to_delete:
        existing.pop(key, None)
    write_localization_file(path, existing)


def _read_existing(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return parse_localization_file(path)
