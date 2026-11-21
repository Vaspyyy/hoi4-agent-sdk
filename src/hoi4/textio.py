"""Byte-exact reading and writing of script and localisation files.

Every text file the SDK edits goes through this module, so that a file keeps
its encoding, its byte-order mark and its line endings, and so that a line the
SDK did not change is written back with exactly the bytes it had.

In memory, text always uses ``"\\n"`` line endings and has no byte-order mark.
"""

from __future__ import annotations

import codecs
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

__all__ = [
    "TextFormat",
    "decode_text",
    "detect_format",
    "encode_text",
    "read_text",
    "write_text",
]

_NEWLINE_RE = re.compile(r"\r\n|\r|\n")
_LF_LINE_RE = re.compile(r"[^\n]*\n|[^\n]+")
_RAW_LINE_RE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+")
# Tried in order. UTF-8 is what the game expects; Windows-1252 is what older
# files and many editors on Windows produce; Latin-1 maps every byte, so the
# last step cannot fail and no byte is ever dropped.
_ENCODINGS = ("utf-8", "cp1252", "latin-1")


@dataclass(frozen=True)
class TextFormat:
    """How a text file is stored on disk."""

    encoding: str = "utf-8"
    bom: bool = False
    newline: str = "\n"
    mixed_newlines: bool = False


def _decode(data: bytes) -> tuple[str, str, bool]:
    bom = data.startswith(codecs.BOM_UTF8)
    payload = data[len(codecs.BOM_UTF8) :] if bom else data
    for encoding in _ENCODINGS:
        try:
            return payload.decode(encoding), encoding, bom
        except UnicodeDecodeError:
            continue
    raise AssertionError("latin-1 decodes every byte string")  # pragma: no cover


def _newline_style(raw: str) -> tuple[str, bool]:
    counts: dict[str, int] = {}
    for match in _NEWLINE_RE.finditer(raw):
        ending = match.group()
        counts[ending] = counts.get(ending, 0) + 1
    if not counts:
        return "\n", False
    dominant = max(counts, key=lambda ending: (counts[ending], ending == "\n"))
    return dominant, len(counts) > 1


def detect_format(data: bytes) -> TextFormat:
    """Return the encoding, byte-order mark and line-ending style of ``data``."""

    raw, encoding, bom = _decode(data)
    newline, mixed = _newline_style(raw)
    return TextFormat(encoding=encoding, bom=bom, newline=newline, mixed_newlines=mixed)


def decode_text(data: bytes) -> str:
    """Decode file bytes to text with ``"\\n"`` line endings and no byte-order mark.

    Nothing is dropped: bytes that are not valid UTF-8 are read as Windows-1252
    (or Latin-1), and :func:`encode_text` writes them back unchanged.
    """

    raw = _decode(data)[0]
    if "\r" in raw:
        raw = _NEWLINE_RE.sub("\n", raw)
    return raw


def read_text(path: str | Path) -> str:
    """Read a script or localisation file. See :func:`decode_text`."""

    return decode_text(Path(path).read_bytes())


def _split_lines(text: str) -> list[str]:
    """Split on ``"\\n"`` only, keeping the terminators.

    ``str.splitlines()`` is not used: it also breaks on form feeds and Unicode
    separators, which must stay inside a line.
    """

    return _LF_LINE_RE.findall(text)


def _encode(text: str, encoding: str) -> bytes:
    try:
        return text.encode(encoding)
    except UnicodeEncodeError:
        # New content does not fit the legacy code page: UTF-8 is what the
        # game reads, so upgrade the file instead of losing characters.
        return text.encode("utf-8")


def encode_text(
    text: str,
    *,
    like: bytes | None = None,
    bom: bool | None = None,
) -> bytes:
    """Encode ``text`` the way the file ``like`` is stored.

    ``like`` is the current content of the file being replaced, or of the file
    a new override was copied from. With ``like``:

    * unchanged text returns ``like`` itself, byte for byte;
    * the encoding, byte-order mark and line-ending style are kept;
    * a line that did not change keeps its exact bytes, including its own line
      ending when the file mixes styles.

    Without ``like`` the result is UTF-8 with ``"\\n"`` endings. ``bom`` forces
    the byte-order mark on or off (localisation files need one).
    """

    if like is None:
        payload = text.encode("utf-8")
        return (codecs.BOM_UTF8 + payload) if bom else payload

    raw, encoding, had_bom = _decode(like)
    original = _NEWLINE_RE.sub("\n", raw) if "\r" in raw else raw
    use_bom = had_bom if bom is None else bom
    if text == original and use_bom == had_bom:
        return like

    newline, mixed = _newline_style(raw)
    if not mixed:
        rendered = text if newline == "\n" else text.replace("\n", newline)
    else:
        rendered = _merge_line_endings(raw, original, text, newline)
    payload = _encode(rendered, encoding)
    return (codecs.BOM_UTF8 + payload) if use_bom else payload


def _merge_line_endings(raw: str, original: str, text: str, newline: str) -> str:
    """Rebuild ``text`` so unchanged lines keep their own original line ending."""

    raw_lines = _RAW_LINE_RE.findall(raw)
    old_lines = _split_lines(original)
    new_lines = _split_lines(text)
    if len(raw_lines) != len(old_lines):  # pragma: no cover - defensive
        return text.replace("\n", newline)
    # Unchanged lines at the start and the end are matched first. The diff
    # below would otherwise be free to pair an edited line's neighbours with
    # identical lines elsewhere (blank lines, repeated braces), which moves
    # their line endings around.
    shortest = min(len(old_lines), len(new_lines))
    head = 0
    while head < shortest and old_lines[head] == new_lines[head]:
        head += 1
    tail = 0
    while tail < shortest - head and old_lines[-1 - tail] == new_lines[-1 - tail]:
        tail += 1
    out: list[str] = raw_lines[:head]
    matcher = SequenceMatcher(
        None,
        old_lines[head : len(old_lines) - tail],
        new_lines[head : len(new_lines) - tail],
        autojunk=False,
    )
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        old_start += head
        old_end += head
        new_start += head
        new_end += head
        if tag == "equal":
            out.extend(raw_lines[old_start:old_end])
            continue
        for index, line in enumerate(new_lines[new_start:new_end]):
            if not line.endswith("\n"):
                out.append(line)
                continue
            # An edited line keeps the ending of the line it replaces.
            ending = newline
            if old_start + index < old_end:
                old = raw_lines[old_start + index]
                ending = old[len(old.rstrip("\r\n")) :] or newline
            out.append(line[:-1] + ending)
    out.extend(raw_lines[len(raw_lines) - tail :])
    return "".join(out)


def write_text(
    path: str | Path,
    text: str,
    *,
    like: bytes | None = None,
    bom: bool | None = None,
) -> bytes:
    """Write ``text`` with :func:`encode_text` and return the bytes written.

    Without ``like``, a file that already exists at ``path`` is its own model:
    it keeps its encoding, byte-order mark and line endings.
    """

    target = Path(path)
    if like is None and target.is_file():
        like = target.read_bytes()
    data = encode_text(text, like=like, bom=bom)
    target.write_bytes(data)
    return data
