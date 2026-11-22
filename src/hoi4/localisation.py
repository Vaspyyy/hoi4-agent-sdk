"""
Localization Utilities

Read, search, and modify HOI4 YML localization files.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Iterable

from ._disk_cache import DiskAnalysis, string_map
from ._file_cache import cached_file_result
from .textio import decode_text, read_text, write_text

YML_ENTRY_RE = re.compile(r'^\s*([^:#\s]+)\s*:\s*(?:\d+\s*)?\s*"')
_HEADER_RE = re.compile(r"^\s*l_([A-Za-z_]+)\s*:\s*(?:#.*)?$")

#: Languages HOI4 loads, in the game's own folder/header spelling. Files live
#: at ``localisation/<language>/*_l_<language>.yml`` with a ``l_<language>:``
#: header and are encoded as UTF-8 with a byte-order mark.
LOCALISATION_LANGUAGES: tuple[str, ...] = (
    "english",
    "braz_por",
    "french",
    "german",
    "japanese",
    "polish",
    "russian",
    "spanish",
    "simp_chinese",
    "korean",
)
DEFAULT_LOCALISATION_LANGUAGE = "english"


def normalize_localisation_language(language: str) -> str:
    """Return the canonical HOI4 language name, accepting an ``l_`` prefix."""
    text = str(language).strip().lower()
    if text.startswith("l_"):
        text = text[2:]
    if text not in LOCALISATION_LANGUAGES:
        raise ValueError(
            f"Unsupported HOI4 localisation language {language!r}; "
            f"expected one of {', '.join(LOCALISATION_LANGUAGES)}"
        )
    return text


def localisation_header_language(text: str) -> str | None:
    """Return the language named by a file's ``l_<language>:`` header.

    Only the first non-blank, non-comment line is considered, matching how
    HOI4 reads the header. ``None`` means that line is not a header.
    """
    for line in text.lstrip("﻿").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _HEADER_RE.match(line)
        return match.group(1).lower() if match else None
    return None


def localisation_language_for_path(path: Path) -> str | None:
    """Return the language folder a localisation path belongs to, if any."""
    parts = [part.lower() for part in Path(path).parts]
    for index in range(len(parts) - 2, -1, -1):
        if parts[index] in {"localisation", "localization"}:
            candidate = parts[index + 1]
            if candidate in LOCALISATION_LANGUAGES:
                return candidate
    return None


def localisation_file_name(stem: str, language: str) -> str:
    """Return ``<stem>_l_<language>.yml``, swapping an existing language suffix."""
    language = normalize_localisation_language(language)
    for known in LOCALISATION_LANGUAGES:
        suffix = f"_l_{known}"
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return f"{stem}_l_{language}.yml"


def normalize_localization_key(key: str) -> str:
    return re.sub(r":\d+$", "", str(key).strip())


def _escape_localization_value(value: str) -> str:
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in text:
        raise ValueError("HOI4 localization values cannot contain NUL characters")
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _closes_value(line: str, index: int) -> bool:
    """Whether the quote at ``index`` ends the value.

    The game takes everything up to the last quote of the line, so a value may
    hold quotes of its own: ``KEY:0 "The "Plan""``. A quote ends the value
    when only blanks or a comment follow it.
    """

    trailing = line[index + 1 :].strip()
    return not trailing or trailing.startswith("#")


# An escape (a backslash and the character after it) or a quote.
_VALUE_TOKEN_RE = re.compile(r'\\(.)|"', re.DOTALL)


def _parse_quoted_value(line: str, start: int) -> str | None:
    """The value that starts after the opening quote at ``start - 1``.

    ``\\n`` is a line break, ``\\"`` and ``\\\\`` are a quote and a
    backslash, any other escape is kept as written. ``None`` when no quote
    ends the value.
    """

    parts: list[str] = []
    position = start
    for found in _VALUE_TOKEN_RE.finditer(line, start):
        escaped = found.group(1)
        if escaped is None:
            index = found.start()
            if _closes_value(line, index):
                parts.append(line[position:index])
                return "".join(parts)
            # A quote inside the value is kept as text.
            continue
        parts.append(line[position : found.start()])
        parts.append(
            "\n" if escaped == "n" else escaped if escaped in '"\\' else "\\" + escaped
        )
        position = found.end()
    return None


def parse_localization_dir(
    loc_dir: Path,
    *,
    file_entries: Callable[[Path], dict[str, str]] | None = None,
) -> tuple[dict[str, str], dict[str, Path]]:
    """Every entry of the ``.yml`` files under ``loc_dir`` and the file it is in."""

    if not loc_dir.exists():
        return {}, {}
    return parse_localization_files(loc_dir.rglob("*.yml"), file_entries=file_entries)


def parse_localization_files(
    files: Iterable[Path],
    *,
    file_entries: Callable[[Path], dict[str, str]] | None = None,
) -> tuple[dict[str, str], dict[str, Path]]:
    """Entries of localisation files in order (a later file wins), and their files.

    ``file_entries`` reads one file (as :func:`localization_file_entries`
    does); pass one that keeps the answers on disk for inherited files.
    """

    entries: dict[str, str] = {}
    sources: dict[str, Path] = {}
    read_file = file_entries if file_entries is not None else localization_file_entries
    for f in files:
        for key, value in read_file(f).items():
            entries[key] = value
            sources[key] = f

    return entries, sources


def localization_file_entries(path: Path) -> dict[str, str]:
    """The entries of one ``.yml`` file, kept in memory until it changes.

    The game's folder is read on every load and validation. The result is
    shared: never change it.
    """

    return cached_file_result(
        "localisation_entries", path, _localization_entries, read=_read_localization
    )


#: How :func:`localization_file_entries` results go to disk and back.
LOCALISATION_ON_DISK: DiskAnalysis[dict[str, str]] = DiskAnalysis(
    "localisation_entries", dict, string_map
)


def localisation_keys_in(text: str) -> frozenset[str]:
    """The keys of a localisation file, read the way the game finds them.

    Only ``KEY:0 "`` at the start of a line counts, so a line whose text the
    strict reader rejects (such as inner quotes) still names its key.
    """

    found: set[str] = set()
    for line in text.splitlines():
        match = YML_ENTRY_RE.match(line)
        if match is not None:
            found.add(normalize_localization_key(match.group(1)))
    return frozenset(found)


def parse_localization_file(path: Path) -> dict[str, str]:
    return _localization_entries(_read_localization(path))


def _read_localization(path: Path) -> str:
    return decode_text(path.read_bytes())


def _localization_entries(txt: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in txt.splitlines():
        if not line or line.strip().startswith("#"):
            continue
        # Headers have no quoted value; l_-prefixed entry keys are valid.
        m = YML_ENTRY_RE.match(line)
        if m:
            value = _parse_quoted_value(line, m.end())
            if value is not None:
                # The key has no colon or blank (see YML_ENTRY_RE), so it
                # is already what normalize_localization_key would return.
                out[m.group(1)] = value

    return out


def serialize_localization_file(
    entries: dict[str, str],
    original: str = "",
    *,
    language: str = DEFAULT_LOCALISATION_LANGUAGE,
) -> str:
    if original:
        return _patch_localization_text(entries, original)
    lines = [f"l_{normalize_localisation_language(language)}:"]
    for key in sorted(entries):
        normalized = normalize_localization_key(key)
        lines.append(f' {normalized}:0 "{_escape_localization_value(entries[key])}"')
    lines.append("")
    return "\n".join(lines)


def write_localization_file(
    path: Path, entries: dict[str, str], *, language: str | None = None
) -> None:
    """Write entries; a new file's header follows ``language`` or its folder."""
    path.parent.mkdir(parents=True, exist_ok=True)
    original = (
        read_text(path) if path.exists() else ""
    )
    resolved = (
        language
        or localisation_language_for_path(path)
        or DEFAULT_LOCALISATION_LANGUAGE
    )
    write_text(
        path,
        serialize_localization_file(entries, original=original, language=resolved),
        bom=True,
    )


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
        if line[i] == '"' and _closes_value(line, i):
            return i
        i += 1
    return None
