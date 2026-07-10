"""
Localization Utilities

Read, search, and modify HOI4 YML localization files.
"""

from __future__ import annotations

import re
from pathlib import Path

YML_ENTRY_RE = re.compile(r'^\s*([^:#\s]+)\s*:\s*(?:\d+\s*)?\s*"')


def normalize_localization_key(key: str) -> str:
    return re.sub(r":\d+$", "", str(key).strip())


def _escape_localization_value(value: str) -> str:
    text = str(value)
    if "\n" in text or "\r" in text or "\x00" in text:
        raise ValueError("HOI4 localization values must be single-line strings")
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _parse_quoted_value(line: str, start: int) -> str | None:
    value: list[str] = []
    i = start
    while i < len(line):
        if line[i] == "\\" and i + 1 < len(line):
            nxt = line[i + 1]
            value.append(nxt if nxt in {'"', "\\"} else "\\" + nxt)
            i += 2
            continue
        if line[i] == '"':
            trailing = line[i + 1 :].strip()
            if trailing and not trailing.startswith("#"):
                return None
            return "".join(value)
        value.append(line[i])
        i += 1
    return None


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
            value = _parse_quoted_value(line, m.end())
            if value is not None:
                out[normalize_localization_key(m.group(1))] = value

    return out


def serialize_localization_file(entries: dict[str, str]) -> str:
    lines = ["l_english:"]
    for key in sorted(entries):
        normalized = normalize_localization_key(key)
        lines.append(f' {normalized}:0 "{_escape_localization_value(entries[key])}"')
    lines.append("")
    return "\n".join(lines)


def write_localization_file(path: Path, entries: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["l_english:"]
    for key in sorted(entries):
        normalized = normalize_localization_key(key)
        lines.append(f' {normalized}:0 "{_escape_localization_value(entries[key])}"')
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
