"""Byte-exact reading and writing of script and localisation files.

Every text file the SDK edits goes through this module, so that a file keeps
its encoding, its byte-order mark and its line endings, and so that a line the
SDK did not change is written back with exactly the bytes it had.

In memory, text always uses ``"\\n"`` line endings and has no byte-order mark.

How bytes are read
------------------

Each line is read on its own, so what a line means never depends on the rest
of the file:

* A line that is valid UTF-8 is read as UTF-8.
* A line with bytes that are not valid UTF-8 is "UTF-8 with stray bytes" when
  the file has a UTF-8 byte-order mark, or when at least as many of the line's
  high bytes (0x80 and up) belong to valid UTF-8 sequences as do not. Its
  valid sequences are read as UTF-8 and every other byte as its Windows-1252
  character.
* Any other line is a legacy code page line and is read as Windows-1252.

Windows-1252 leaves five bytes undefined (0x81, 0x8D, 0x8F, 0x90, 0x9D); they
are read as their Latin-1 characters. So every byte maps to exactly one
character and back: text in memory never holds U+FFFD or a lone surrogate, and
nothing is lost.

Reading line by line matters because writing is line by line too: an edited
line that does not fit a legacy file's code page is written as UTF-8 while the
untouched lines keep their old bytes. The next read must give that line back
as UTF-8 however many legacy lines surround it.
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
_RAW_LINE_RE = re.compile(rb"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+")

# One valid multi-byte UTF-8 sequence: no overlong forms, no surrogates,
# nothing above U+10FFFF. This is exactly what Python's UTF-8 codec accepts.
_UTF8_SEQUENCE = (
    rb"[\xc2-\xdf][\x80-\xbf]"
    rb"|\xe0[\xa0-\xbf][\x80-\xbf]"
    rb"|[\xe1-\xec\xee\xef][\x80-\xbf]{2}"
    rb"|\xed[\x80-\x9f][\x80-\xbf]"
    rb"|\xf0[\x90-\xbf][\x80-\xbf]{2}"
    rb"|[\xf1-\xf3][\x80-\xbf]{3}"
    rb"|\xf4[\x80-\x8f][\x80-\xbf]{2}"
)
_UTF8_SEQUENCE_RE = re.compile(_UTF8_SEQUENCE)
# Splits bytes into runs of valid UTF-8 (group 1) and single invalid bytes
# (group 2). A broken sequence gives one invalid byte per byte.
_UTF8_SCAN_RE = re.compile(rb"((?:[\x00-\x7f]+|" + _UTF8_SEQUENCE + rb")+)|(.)", re.DOTALL)
_HIGH_BYTES = bytes(range(0x80, 0x100))
_ASCII_BYTES = bytes(range(0x80))
_CP1252_UNDEFINED = b"\x81\x8d\x8f\x90\x9d"


def _legacy_table() -> str:
    chars = []
    for byte in range(256):
        try:
            chars.append(bytes([byte]).decode("cp1252"))
        except UnicodeDecodeError:
            chars.append(chr(byte))  # one of the five undefined bytes
    return "".join(chars)


# Windows-1252 with the Latin-1 character for its five undefined bytes: 256
# bytes, 256 distinct characters, so decoding and encoding are exact inverses.
_LEGACY_TABLE = _legacy_table()
_LEGACY_ENCODING_MAP = codecs.charmap_build(_LEGACY_TABLE)


@dataclass(frozen=True)
class TextFormat:
    """How a text file is stored on disk.

    ``encoding`` is ``"utf-8"``, or for a file whose high bytes are mostly not
    valid UTF-8 (and that has no byte-order mark) the legacy code page:
    ``"cp1252"``, or ``"latin-1"`` when the file holds one of the five bytes
    Windows-1252 leaves undefined. ``stray_bytes`` counts, in a UTF-8 file,
    the bytes that are not valid UTF-8 and were read as Windows-1252
    characters instead.
    """

    encoding: str = "utf-8"
    bom: bool = False
    newline: str = "\n"
    mixed_newlines: bool = False
    stray_bytes: int = 0


@dataclass(frozen=True)
class _Decoded:
    payload: bytes  # the file's bytes without the byte-order mark
    raw: str  # the decoded text, line endings as in the file
    encoding: str
    bom: bool
    stray_bytes: int
    # True when encoding ``raw`` as a whole in ``encoding`` gives ``payload``
    # back: the file is plain UTF-8, or plain legacy without a single valid
    # UTF-8 sequence. Only then may an edit re-encode the whole text at once.
    whole: bool


def _high_count(data: bytes) -> int:
    return len(data) - len(data.translate(None, _HIGH_BYTES))


def _legacy_decode(data: bytes) -> str:
    return codecs.charmap_decode(data, "strict", _LEGACY_TABLE)[0]


def _legacy_name(invalid: bytes) -> str:
    return "latin-1" if any(byte in invalid for byte in _CP1252_UNDEFINED) else "cp1252"


def _read_line(line: bytes, bom: bool) -> tuple[str, int, bytes]:
    """Decode one line by the rule in the module docstring.

    Returns the text, the number of high bytes in valid UTF-8 sequences, and
    the bytes that are not valid UTF-8.
    """

    try:
        return line.decode("utf-8"), _high_count(line), b""
    except UnicodeDecodeError:
        pass
    if _UTF8_SEQUENCE_RE.search(line) is None:
        # Every high byte is invalid: both readings give the same text.
        return _legacy_decode(line), 0, line.translate(None, _ASCII_BYTES)
    parts: list[str] = []
    invalid = bytearray()
    for match in _UTF8_SCAN_RE.finditer(line):
        run = match.group(1)
        if run is not None:
            parts.append(run.decode("utf-8"))
        else:
            byte = match.group(2)
            invalid += byte
            parts.append(_LEGACY_TABLE[byte[0]])
    valid = _high_count(line) - len(invalid)
    if bom or valid >= len(invalid):
        return "".join(parts), valid, bytes(invalid)
    return _legacy_decode(line), valid, bytes(invalid)


def _decode(data: bytes) -> _Decoded:
    bom = data.startswith(codecs.BOM_UTF8)
    payload = data[len(codecs.BOM_UTF8) :] if bom else data
    try:
        return _Decoded(payload, payload.decode("utf-8"), "utf-8", bom, 0, True)
    except UnicodeDecodeError:
        pass
    if not bom and _UTF8_SEQUENCE_RE.search(payload) is None:
        # Not one valid UTF-8 sequence: every line is a legacy line.
        return _Decoded(
            payload, _legacy_decode(payload), _legacy_name(payload), False, 0, True
        )
    texts: list[str] = []
    valid = 0
    invalid = bytearray()
    for line in _RAW_LINE_RE.findall(payload):
        text, line_valid, line_invalid = _read_line(line, bom)
        texts.append(text)
        valid += line_valid
        invalid += line_invalid
    raw = "".join(texts)
    if bom or valid >= len(invalid):
        return _Decoded(payload, raw, "utf-8", bom, len(invalid), False)
    return _Decoded(payload, raw, _legacy_name(bytes(invalid)), bom, 0, False)


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

    decoded = _decode(data)
    newline, mixed = _newline_style(decoded.raw)
    return TextFormat(
        encoding=decoded.encoding,
        bom=decoded.bom,
        newline=newline,
        mixed_newlines=mixed,
        stray_bytes=decoded.stray_bytes,
    )


def decode_text(data: bytes) -> str:
    """Decode file bytes to text with ``"\\n"`` line endings and no byte-order mark.

    Nothing is dropped: valid UTF-8 is read as UTF-8 and any other byte as its
    Windows-1252 (or Latin-1) character, line by line as the module docstring
    describes, and :func:`encode_text` writes them back unchanged.
    """

    raw = _decode(data).raw
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


def _encode_whole(text: str, encoding: str) -> bytes | None:
    """Encode the whole text at once, or return ``None`` when that is not exact."""

    if encoding == "utf-8":
        return text.encode("utf-8")
    try:
        payload = codecs.charmap_encode(text, "strict", _LEGACY_ENCODING_MAP)[0]
    except UnicodeEncodeError:
        return None
    # Legacy bytes that happen to form valid UTF-8 (such as "ß" followed by
    # a curly quote) would read back as UTF-8; let the line path handle them.
    return None if _UTF8_SEQUENCE_RE.search(payload) else payload


def _encode_line(line: str, encoding: str, bom: bool) -> bytes:
    """Encode one edited line in ``encoding`` if it fits and reads back the
    same, otherwise as UTF-8 (which is what the game reads)."""

    if encoding != "utf-8":
        try:
            data = codecs.charmap_encode(line, "strict", _LEGACY_ENCODING_MAP)[0]
        except UnicodeEncodeError:
            pass
        else:
            if _read_line(data, bom)[0] == line:
                return data
    return line.encode("utf-8")


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
      ending when the file mixes styles and stray bytes in a UTF-8 file;
    * an edited line is written in the file's encoding when it fits, otherwise
      as UTF-8. Only that line changes encoding, never the rest of the file.

    Without ``like`` the result is UTF-8 with ``"\\n"`` endings. ``bom`` forces
    the byte-order mark on or off (localisation files need one).
    """

    if like is None:
        payload = text.encode("utf-8")
        return (codecs.BOM_UTF8 + payload) if bom else payload

    decoded = _decode(like)
    raw = decoded.raw
    original = _NEWLINE_RE.sub("\n", raw) if "\r" in raw else raw
    use_bom = decoded.bom if bom is None else bom
    if text == original and use_bom == decoded.bom:
        return like

    newline, mixed = _newline_style(raw)
    # Text in a file with a byte-order mark is UTF-8.
    encoding = "utf-8" if use_bom else decoded.encoding
    whole: bytes | None = None
    if decoded.whole and not mixed and encoding == decoded.encoding:
        # One encoding and one line-ending style for the whole file: encoding
        # it at once gives every unchanged line its old bytes back.
        whole = _encode_whole(text if newline == "\n" else text.replace("\n", newline), encoding)
    if whole is None:
        whole = _merge_lines(decoded.payload, original, text, newline, encoding, use_bom)
    return (codecs.BOM_UTF8 + whole) if use_bom else whole


def _merge_lines(
    payload: bytes, original: str, text: str, newline: str, encoding: str, bom: bool
) -> bytes:
    """Rebuild ``text`` as bytes where every unchanged line keeps its original
    bytes, line ending included, and only edited lines are encoded."""

    raw_lines = _RAW_LINE_RE.findall(payload)
    old_lines = _split_lines(original)
    new_lines = _split_lines(text)
    if len(raw_lines) != len(old_lines):  # pragma: no cover - defensive
        raise AssertionError("line breaks are the same bytes in every encoding")
    default_ending = newline.encode("ascii")
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
    out: list[bytes] = raw_lines[:head]
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
                out.append(_encode_line(line, encoding, bom))
                continue
            # An edited line keeps the ending of the line it replaces.
            ending = default_ending
            if old_start + index < old_end:
                old = raw_lines[old_start + index]
                ending = old[len(old.rstrip(b"\r\n")) :] or default_ending
            out.append(_encode_line(line[:-1], encoding, bom) + ending)
    out.extend(raw_lines[len(raw_lines) - tail :])
    return b"".join(out)


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
