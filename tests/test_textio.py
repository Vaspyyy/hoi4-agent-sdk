"""Example tests for :mod:`hoi4.textio`: files keep their exact bytes."""

from __future__ import annotations

import codecs
import random
import re
from pathlib import Path

import pytest

from hoi4.textio import TextFormat, decode_text, detect_format, encode_text, read_text, write_text

BOM = codecs.BOM_UTF8


@pytest.mark.parametrize(
    "data",
    [
        b"a = 1\n",
        BOM + "name = \"König\"\n".encode(),
        "name = \"König – café\"\r\n".encode("cp1252"),
        bytes(range(256)),
        b"\x81\x8d\x8f\x90\x9d = 1\n",  # undefined in Windows-1252: read as Latin-1
        BOM + b"\xff\xfe broken utf-8 after a BOM\n",
        BOM + BOM + b"double\n",
        b"",
        BOM,
    ],
)
def test_round_trip_is_byte_exact(data: bytes) -> None:
    text = decode_text(data)
    assert "\r" not in text
    assert not text.startswith("﻿") or data.startswith(BOM + BOM)
    assert encode_text(text, like=data) == data


def test_unchanged_text_returns_the_original_object() -> None:
    data = "a = 1\r\n".encode("cp1252")
    assert encode_text(decode_text(data), like=data) is data


def test_decoding_order_and_format_detection() -> None:
    assert detect_format(b"a\n") == TextFormat("utf-8", False, "\n", False)
    assert detect_format(BOM + b"a\r\n") == TextFormat("utf-8", True, "\r\n", False)
    assert detect_format(b"\xe9\r") == TextFormat("cp1252", False, "\r", False)
    assert detect_format(b"\x81") .encoding == "latin-1"
    assert detect_format(b"a\r\nb\nc\n") == TextFormat("utf-8", False, "\n", True)
    assert detect_format(b"a\r\nb\n").newline == "\n"  # a tie prefers "\n"


def test_bom_is_dropped_in_memory_and_restored() -> None:
    data = BOM + b"l_english:\n key: \"x\"\n"
    text = decode_text(data)
    assert text.startswith("l_english")
    edited = encode_text(text.replace('"x"', '"y"'), like=data)
    assert edited == BOM + b"l_english:\n key: \"y\"\n"


def test_utf8_without_bom_stays_without_bom() -> None:
    data = "a = é\n".encode()
    assert encode_text(decode_text(data) + "b = 1\n", like=data) == data + b"b = 1\n"


def test_windows_1252_stays_windows_1252() -> None:
    data = "a = café\r\n".encode("cp1252")
    edited = encode_text(decode_text(data) + "b = ü\n", like=data)
    assert edited == "a = café\r\nb = ü\r\n".encode("cp1252")


def test_only_the_line_that_does_not_fit_the_code_page_becomes_utf8() -> None:
    # The untouched legacy lines keep their bytes; only the new line is UTF-8.
    # It still reads back as written although the legacy lines outnumber it.
    data = "a = café\nb = déjà vu\nc = ü\n".encode("cp1252")
    text = decode_text(data) + "d = ł\n"
    edited = encode_text(text, like=data)
    assert edited == data + "d = ł\n".encode()
    assert decode_text(edited) == text
    assert detect_format(edited).encoding == "cp1252"


def test_edited_line_in_the_code_page_must_read_back_the_same() -> None:
    # "ß" followed by a curly quote is 0xDF 0x93 in Windows-1252, which is
    # also a valid UTF-8 sequence and would read back as U+07D3.
    data = "a = café\n".encode("cp1252")
    text = decode_text(data) + "b = Fuß“\nc = Fuß\n"
    edited = encode_text(text, like=data)
    assert edited == data + "b = Fuß“\n".encode() + "c = Fuß\n".encode("cp1252")
    assert decode_text(edited) == text


def test_stray_bytes_do_not_turn_utf8_into_windows_1252() -> None:
    # Repro: one stray byte made the whole file read as Windows-1252.
    data = BOM + b'l_english:\n a:0 "M\xc3\xbcnchen \xce\x95\xce\xbb\xce\xbb\xce\xac\xce\xb4\xce\xb1"\n b:0 "caf\xe9"\n'
    assert decode_text(data) == 'l_english:\n a:0 "München Ελλάδα"\n b:0 "café"\n'
    assert detect_format(data) == TextFormat("utf-8", True, "\n", False, stray_bytes=1)
    # Without a byte-order mark, more valid high bytes than stray ones decide.
    data = b'name = "M\xc3\xbcnchen"\n# caf\xe9 \xc3\xa9t\xe9\n'
    assert decode_text(data) == 'name = "München"\n# café été\n'
    assert detect_format(data).stray_bytes == 2


def test_edit_next_to_stray_bytes_keeps_untouched_lines() -> None:
    # Repro: a new character outside the code page re-encoded every line, so
    # "M\xc3\xbcnchen" became "M\xc3\x83\xc2\xbcnchen".
    data = b'name = "M\xc3\xbcnchen"\r\n# caf\xe9\r\nicon = GFX_old\r\n'
    text = decode_text(data)
    assert text == 'name = "München"\n# café\nicon = GFX_old\n'
    edited = encode_text(text.replace("GFX_old", "GFX_ł"), like=data)
    assert edited == data.replace(b"GFX_old", "GFX_ł".encode())
    # An edited line with a stray byte is written as clean UTF-8.
    edited = encode_text(text.replace("# café", "# café!"), like=data)
    assert edited == data.replace(b"caf\xe9", "café!".encode())


def test_mostly_invalid_bytes_mean_a_legacy_line() -> None:
    # "Ã©" is valid UTF-8 for "é", but next to more Windows-1252 accents the
    # line is read as Windows-1252, like the rest of the file.
    data = "Ã© über café naïve\n".encode("cp1252")
    assert decode_text(data) == "Ã© über café naïve\n"
    assert detect_format(data) == TextFormat("cp1252", False, "\n", False)
    with_bom = BOM + data
    assert decode_text(with_bom).startswith("é über café")


@pytest.mark.parametrize("newline", ["\r\n", "\r", "\n"])
def test_uniform_line_endings_are_kept(newline: str) -> None:
    data = newline.join(["a = 1", "b = 2", "c = 3", ""]).encode()
    text = decode_text(data)
    assert text == "a = 1\nb = 2\nc = 3\n"
    edited = encode_text(text.replace("b = 2", "b = 9"), like=data)
    assert edited == data.replace(b"b = 2", b"b = 9")


def test_mixed_line_endings_keep_every_unchanged_line() -> None:
    data = b"a = 1\r\nb = 2\nc = 3\rd = 4\r\ne = 5"
    text = decode_text(data)
    assert text == "a = 1\nb = 2\nc = 3\nd = 4\ne = 5"
    for old, new in [(b"a = 1", b"a = 0"), (b"b = 2", b"b = 9"), (b"c = 3", b"c = 7"),
                     (b"e = 5", b"e = 6")]:
        edited = encode_text(text.replace(old.decode(), new.decode()), like=data)
        # The edited line keeps its own ending, and so does every other line.
        assert edited == data.replace(old, new)


def test_mixed_line_endings_insert_and_delete() -> None:
    data = b"a = 1\r\nb = 2\nc = 3\r\n"
    text = decode_text(data)
    inserted = encode_text(text.replace("b = 2\n", "b = 2\nnew = 1\n"), like=data)
    assert inserted.startswith(b"a = 1\r\nb = 2\n")
    assert inserted.endswith(b"c = 3\r\n")
    assert b"new = 1" in inserted
    deleted = encode_text(text.replace("b = 2\n", ""), like=data)
    assert deleted == b"a = 1\r\nc = 3\r\n"


@pytest.mark.parametrize("separator", ["\f", "\v", "\x1c", "\x85", " ", " "])
def test_only_cr_and_lf_break_lines(separator: str) -> None:
    data = f"a = x{separator}y\r\nb = 2\nc = 3\r\n".encode()
    text = decode_text(data)
    assert text.count("\n") == 3
    assert separator in text
    edited = encode_text(text.replace("c = 3", "c = 4"), like=data)
    assert edited == data.replace(b"c = 3", b"c = 4")


def test_forced_bom_for_localisation() -> None:
    assert encode_text("l_english:\n", bom=True) == BOM + b"l_english:\n"
    assert encode_text("l_english:\n") == b"l_english:\n"
    data = b"l_english:\n"
    assert encode_text(decode_text(data), like=data, bom=True) == BOM + data
    assert encode_text(decode_text(BOM + data), like=BOM + data, bom=False) == data


def test_adding_the_bom_keeps_the_text_of_a_mixed_line() -> None:
    # Two high bytes in a valid UTF-8 pair and three stray ones: without a
    # byte-order mark the line reads as Windows-1252, with one as UTF-8 plus
    # stray bytes. Adding the mark must not change what the line says.
    data = b'l_english:\n k1:0 "\xc3\xa9\xe9\xe9\xe9"\n k2:0 "hello"\n'
    text = decode_text(data)
    assert ' k1:0 "Ã©ééé"\n' in text
    edited_text = text.replace("hello", "world")

    edited = encode_text(edited_text, like=data, bom=True)

    assert edited.startswith(BOM)
    assert decode_text(edited) == edited_text
    assert edited.endswith(b' k2:0 "world"\n')


def test_removing_the_bom_keeps_the_text_of_a_mixed_line() -> None:
    data = BOM + b'a = "\xc3\xa9\xe9\xe9\xe9"\nb = 1\n'
    text = decode_text(data)
    assert text.startswith('a = "éééé"\n')

    for wanted in (text, text.replace("b = 1", "b = 2")):
        edited = encode_text(wanted, like=data, bom=False)
        assert not edited.startswith(BOM)
        assert decode_text(edited) == wanted


def test_read_and_write(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    original = b"a = 1\r\n"
    path.write_bytes(original)
    text = read_text(path)
    assert text == "a = 1\n"
    written = write_text(path, text + "b = 2\n", like=original)
    assert path.read_bytes() == written == b"a = 1\r\nb = 2\r\n"


def test_edit_next_to_identical_lines_keeps_their_endings() -> None:
    # Regression: the line diff paired the edited blank line's neighbour with
    # the wrong blank line, so "\r\n\n" became "x\n\r\n".
    data = b"\r\n\n"
    assert encode_text("x\n\n", like=data) == b"x\r\n\n"
    data = b"}\r\n}\n}\r\n"
    assert encode_text("}\n} # c\n}\n", like=data) == b"}\r\n} # c\n}\r\n"


# ── random round trips ───────────────────────────────────────────────

_CHARS = "aZ0 ={}\"#\t\f\x85éüßÄ€“”–…łŁΕλάд中́﻿😀\U0010fffd"
_TRUNCATED = [b"\xc3", b"\xe2\x82", b"\xf0\x9f\x98", b"\xed\xa0\x80", b"\xc0\xaf", b"\xf8"]


def _random_chunk(rng: random.Random) -> bytes:
    kind = rng.randrange(7)
    if kind == 0:
        return bytes(rng.choice(b"abc xyz={}#\"") for _ in range(rng.randrange(1, 6)))
    if kind == 1:
        return "".join(rng.choice(_CHARS) for _ in range(rng.randrange(1, 4))).encode()
    if kind == 2:
        return bytes([rng.randrange(0x80, 0x100)])
    if kind == 3:
        return rng.choice(_TRUNCATED)
    if kind == 4:
        return BOM
    if kind == 5:
        return rng.choice([b"\r", b"\n", b"\r\n"])
    return "".join(rng.choice("éü€“ßÄ") for _ in range(rng.randrange(1, 3))).encode("cp1252")


def _random_line_text(rng: random.Random) -> str:
    pool = rng.choice(["abc =", "abc = éü€“ßÄ", _CHARS.replace("﻿", ""), "Fuß“"])
    return "".join(rng.choice(pool) for _ in range(rng.randrange(0, 8)))


def _raw_lines(data: bytes) -> list[bytes]:
    payload = data[len(BOM) :] if data.startswith(BOM) else data
    return re.findall(rb"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+", payload)


def test_random_bytes_round_trip_and_survive_line_edits() -> None:
    rng = random.Random(20261121)
    for _ in range(3000):
        data = b"".join(_random_chunk(rng) for _ in range(rng.randrange(0, 25)))
        text = decode_text(data)
        assert "\r" not in text
        assert re.search("[\ud800-\udfff]", text) is None  # no lone surrogates
        assert text.count("�") <= data.count("�".encode())
        text.encode("utf-8")  # always printable and encodable
        assert encode_text(text, like=data) == data

        lines = re.findall(r"[^\n]*\n|[^\n]+", text)
        if not lines:
            continue
        index = rng.randrange(len(lines))
        old = lines[index]
        terminator = "\n" if old.endswith("\n") else ""
        # Never empty: an empty line after a line that ends in a lone CR
        # would join it into one CR LF, which is a different file.
        new = _random_line_text(rng) or "x"
        if new + terminator == old:
            continue
        edited_text = "".join(lines[:index] + [new + terminator] + lines[index + 1 :])
        edited = encode_text(edited_text, like=data)
        assert edited.startswith(BOM) == data.startswith(BOM)
        before, after = _raw_lines(data), _raw_lines(edited)
        assert len(after) == len(before)
        assert after[:index] == before[:index]
        assert after[index + 1 :] == before[index + 1 :]
        # Reading is per line, so the saved file reads back as the edited text.
        assert decode_text(edited) == edited_text


def test_random_bytes_read_back_when_the_bom_is_added_or_removed() -> None:
    rng = random.Random(20261122)
    for _ in range(3000):
        data = b"".join(_random_chunk(rng) for _ in range(rng.randrange(0, 25)))
        text = decode_text(data)
        flipped = not data.startswith(BOM)
        if not flipped and text.startswith("﻿"):
            # Text that starts with U+FEFF cannot be stored without a mark:
            # the reader would take the character for one.
            continue
        result = encode_text(text, like=data, bom=flipped)
        assert result.startswith(BOM) == flipped
        assert decode_text(result) == text
        # A line that reads the same either way keeps its bytes.
        before, after = _raw_lines(data), _raw_lines(result)
        assert len(after) == len(before)
        for old_line, new_line in zip(before, after):
            if new_line != old_line:
                assert decode_text(old_line) != decode_text(BOM + old_line)
