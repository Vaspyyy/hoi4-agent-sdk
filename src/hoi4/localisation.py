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
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in text:
        raise ValueError("HOI4 localization values cannot contain NUL characters")
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _parse_quoted_value(line: str, start: int) -> str | None:
    value: list[str] = []
    i = start
    while i < len(line):
        if line[i] == "\\" and i + 1 < len(line):
            nxt = line[i + 1]
            if nxt == "n":
                value.append("\n")
            else:
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
        if not line or line.strip().startswith("#"):
            continue
        # Headers have no quoted value; l_-prefixed entry keys are valid.
        m = YML_ENTRY_RE.match(line)
        if m:
            value = _parse_quoted_value(line, m.end())
            if value is not None:
                out[normalize_localization_key(m.group(1))] = value

    return out


def serialize_localization_file(entries: dict[str, str], original: str = "") -> str:
    if original:
        return _patch_localization_text(entries, original)
    lines = ["l_english:"]
    for key in sorted(entries):
        normalized = normalize_localization_key(key)
        lines.append(f' {normalized}:0 "{_escape_localization_value(entries[key])}"')
    lines.append("")
    return "\n".join(lines)


def write_localization_file(path: Path, entries: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    original = (
        path.read_text(encoding="utf-8-sig", errors="ignore") if path.exists() else ""
    )
    path.write_text(
        serialize_localization_file(entries, original=original), encoding="utf-8-sig"
    )


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


def _patch_localization_text(entries: dict[str, str], original: str) -> str:
    desired = {normalize_localization_key(key): str(value) for key, value in entries.items()}
    lines = original.splitlines(keepends=True)
    occurrences: dict[str, list[int]] = {}
    matches: dict[int, re.Match[str]] = {}
    for index, line in enumerate(lines):
        match = YML_ENTRY_RE.match(line)
        if match is None or _quoted_value_end(line, match.end()) is None:
            continue
        key = normalize_localization_key(match.group(1))
        occurrences.setdefault(key, []).append(index)
        matches[index] = match

    patched: list[str] = []
    for index, line in enumerate(lines):
        match = matches.get(index)
        if match is None:
            patched.append(line)
            continue
        key = normalize_localization_key(match.group(1))
        if key not in desired:
            continue
        if index != occurrences[key][-1]:
            patched.append(line)
            continue
        current = _parse_quoted_value(line, match.end())
        if current == desired[key]:
            patched.append(line)
            continue
        value_end = _quoted_value_end(line, match.end())
        assert value_end is not None
        patched.append(
            line[: match.end()]
            + _escape_localization_value(desired[key])
            + line[value_end:]
        )

    new_keys = sorted(set(desired) - set(occurrences))
    if not new_keys:
        return "".join(patched)

    newline = "\r\n" if "\r\n" in original else "\n"
    insertion = len(patched)
    while insertion > 0 and not patched[insertion - 1].strip():
        insertion -= 1
    additions = [
        f' {key}:0 "{_escape_localization_value(desired[key])}"{newline}'
        for key in new_keys
    ]
    if insertion > 0 and not patched[insertion - 1].endswith(("\n", "\r")):
        additions.insert(0, newline)
    patched[insertion:insertion] = additions
    return "".join(patched)


def _quoted_value_end(line: str, start: int) -> int | None:
    i = start
    while i < len(line):
        if line[i] == "\\" and i + 1 < len(line):
            i += 2
            continue
        if line[i] == '"':
            return i
        i += 1
    return None
