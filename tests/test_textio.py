"""Example tests for :mod:`hoi4.textio`: files keep their exact bytes."""

from __future__ import annotations

import codecs
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


def test_text_that_does_not_fit_the_code_page_upgrades_to_utf8() -> None:
    data = "a = café\n".encode("cp1252")
    edited = encode_text(decode_text(data) + "b = 中\n", like=data)
    assert edited == "a = café\nb = 中\n".encode()
    assert decode_text(edited) == "a = café\nb = 中\n"


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
