"""Example tests for :mod:`hoi4.syntax`, the one reader for Paradox script."""

from __future__ import annotations

import pytest
from _syntax_checks import assert_covers_all_text, assert_spans_nest

from hoi4.syntax import (
    Assignment,
    Block,
    Document,
    ParseError,
    Scalar,
    Tagged,
    parse,
    quote,
    repair,
    require_well_formed,
    unquote,
)


def _checked(text: str) -> Document:
    doc = parse(text)
    assert_spans_nest(doc)
    assert_covers_all_text(doc)
    return doc


def _only(doc: Document) -> Assignment:
    (item,) = doc.root.items
    assert type(item) is Assignment
    return item


# ── values ──────────────────────────────────────────────────────


def test_quoted_bare_list_values() -> None:
    doc = _checked('names = { "Anna Maria" plain "with # hash" "{brace}" }')
    values = doc.root.block("names").values()
    assert [v.value for v in values] == ["Anna Maria", "plain", "with # hash", "{brace}"]
    assert [v.quoted for v in values] == [True, False, True, True]
    assert doc.comments == []


def test_nested_anonymous_lists() -> None:
    doc = _checked("points = { { 1 2 } { 3 4 } }")
    outer = doc.root.block("points")
    assert outer is not None
    inner = [item for item in outer.items if type(item) is Block]
    assert [[v.value for v in block.values()] for block in inner] == [["1", "2"], ["3", "4"]]
    assert inner[0].raw == "{ 1 2 }"


@pytest.mark.parametrize("tag", ["rgb", "hsv", "HSV", "hsv360"])
def test_typed_block_is_one_value(tag: str) -> None:
    doc = _checked(f"color = {tag} {{ 1 2 3 }} other = 1")
    color = doc.root.first("color")
    assert color is not None
    assert type(color.value) is Tagged
    assert color.value.tag.raw == tag
    assert color.value.raw == f"{tag} {{ 1 2 3 }}"
    assert color.raw == f"color = {tag} {{ 1 2 3 }}"
    assert [v.value for v in color.block.values()] == ["1", "2", "3"]
    assert doc.root.block("color") is color.block
    assert doc.root.scalar("other") == "1"
    assert color.scalar is None


def test_typed_block_in_a_list_is_one_value() -> None:
    # value := TAG "{" item* "}" also holds for bare list values.
    doc = _checked("colors = { rgb { 1 2 3 } hsv { 0.1 0.2 0.3 } }")
    items = doc.root.block("colors").items
    assert [type(item) for item in items] == [Tagged, Tagged]
    assert [item.tag.raw for item in items] == ["rgb", "hsv"]


def test_tag_word_without_block_is_a_scalar() -> None:
    doc = _checked("original_tag = RGB\nnext = 1")
    assert doc.root.scalar("original_tag") == "RGB"
    assert doc.root.scalar("next") == "1"


def test_quoted_keys() -> None:
    doc = _checked('"key with space" = 1\n"q" = { "inner key" = "v" }')
    first = doc.root.items[0]
    assert type(first) is Assignment
    assert first.name == "key with space" and first.key.quoted
    assert doc.root.block("q").scalar("inner key") == "v"


def test_variables_as_keys() -> None:
    doc = _checked("@cost = 5\nfocus = { cost = @cost }")
    assert doc.root.scalar("@cost") == "5"
    assert doc.root.block("focus").scalar("cost") == "@cost"


@pytest.mark.parametrize("op", ["=", "<", ">", "<=", ">=", "!=", "==", "?="])
def test_every_operator(op: str) -> None:
    for spacing in (" ", ""):
        doc = _checked(f"a{spacing}{op}{spacing}2")
        item = _only(doc)
        assert item.op == op
        assert item.scalar == "2"
        assert doc.problems == []


def test_scalar_ignores_comparisons() -> None:
    doc = _checked("a > 2 a = 3")
    assert doc.root.scalar("a") == "3"


@pytest.mark.parametrize("word", ["foo?0", "foo!bar", "!bar", "a?", "x!"])
def test_words_with_question_or_exclamation_mark(word: str) -> None:
    doc = _checked(f"v = {word}")
    assert _only(doc).scalar == word


def test_question_and_exclamation_before_equals_are_operators() -> None:
    doc = _checked("foo?=1 bar!=2")
    assert [(item.name, item.op) for item in doc.root.assignments()] == [
        ("foo", "?="),
        ("bar", "!="),
    ]


def test_inline_maths_is_one_word() -> None:
    doc = _checked("cost = @[ 1 + 2 ] next = 3")
    assert doc.root.scalar("cost") == "@[ 1 + 2 ]"
    assert doc.root.scalar("next") == "3"


@pytest.mark.parametrize(
    "text",
    [
        "a = # between operator and value\n5",
        "a # between key and operator\n= 5",
        "# first\na = 5 # after",
        "a = 5#tight",
    ],
)
def test_comments_anywhere(text: str) -> None:
    doc = _checked(text)
    assert _only(doc).scalar == "5"
    assert doc.problems == []
    assert len(doc.comments) >= 1


def test_comment_between_tag_and_block() -> None:
    doc = _checked("color = rgb # red\n{ 255 0 0 }")
    color = _only(doc)
    assert type(color.value) is Tagged
    assert len(doc.comments) == 1


def test_hash_inside_string_is_not_a_comment() -> None:
    doc = _checked('a = "#not a comment" # real')
    assert _only(doc).scalar == "#not a comment"
    assert len(doc.comments) == 1
    assert doc.text[doc.comments[0][0] :] == "# real"


def test_strings_with_escapes_and_line_breaks() -> None:
    doc = _checked('a = "say \\"hi\\" \\\\ there\nsecond line" b = 2')
    assert doc.root.scalar("a") == 'say "hi" \\ there\nsecond line'
    assert doc.root.scalar("b") == "2"
    assert doc.problems == []


def test_unknown_escape_is_kept() -> None:
    assert _only(_checked(r'a = "gfx\tank"')).scalar == r"gfx\tank"


def test_byte_order_mark_at_start() -> None:
    doc = _checked("﻿a = 1")
    assert _only(doc).scalar == "1"
    assert _only(doc).start == 1


@pytest.mark.parametrize("space", ["\f", "\v"])
def test_form_feed_and_vertical_tab_are_whitespace(space: str) -> None:
    doc = _checked(f"a = 1{space}b = 2\nc = 3")
    assert [item.scalar for item in doc.root.assignments()] == ["1", "2", "3"]
    assert doc.problems == []


@pytest.mark.parametrize("char", ["\xa0", "\u2028", "\x85", "\u3000", "\x1c"])
def test_unicode_whitespace_never_ends_the_parse(char: str) -> None:
    # Regression: these characters matched neither the skipped whitespace nor a
    # word, so the parser silently stopped and dropped the rest of the file.
    # Like the game's byte-level reader, they are part of a word.
    doc = _checked(f"a = x{char}y\nb = {char}\nc = 3")
    assert doc.root.scalar("a") == f"x{char}y"
    assert doc.root.scalar("b") == char
    assert doc.root.scalar("c") == "3"
    assert doc.problems == []


def test_empty_and_whitespace_only_text() -> None:
    for text in ("", " \n\t\r\n", "# only a comment"):
        doc = _checked(text)
        assert doc.root.items == []
        assert doc.problems == []


def test_unquote_and_quote() -> None:
    assert unquote('"a\\"b"') == 'a"b'
    assert unquote("bare") == "bare"
    assert unquote('"open') == "open"
    assert quote("plain") == "plain"
    assert quote("two words") == '"two words"'
    assert quote('a"b') == '"a\\"b"'
    assert quote("x", force=True) == '"x"'
    assert unquote(quote('mix "q" \\ end')) == 'mix "q" \\ end'


# ── problems ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("a = { b = 1", "unclosed_block"),
        ("a = 1 }", "stray_close"),
        ("a =", "missing_value"),
        ("a = }", "missing_value"),
        ("= 1", "stray_operator"),
        ("a = = 1", "stray_operator"),
        ('a = "open', "unterminated_string"),
        ('a = "ends with escape\\"', "unterminated_string"),
    ],
)
def test_every_problem_kind(text: str, kind: str) -> None:
    doc = _checked(text)
    assert kind in [problem.kind for problem in doc.problems]
    with pytest.raises(ParseError, match="line 1, column"):
        require_well_formed(text, "x.txt")


def test_problem_offsets_point_at_the_fault() -> None:
    doc = parse("a = {\n  b = 1\n}\n}\n")
    (problem,) = doc.problems
    assert problem.kind == "stray_close"
    assert doc.line_col(problem.offset) == (4, 1)
    assert "stray_close" in repr(problem)


def test_unclosed_block_is_closed_at_end_of_file() -> None:
    doc = _checked("a = { b = { c = 1\n")
    outer = doc.root.block("a")
    assert outer is not None and not outer.closed
    assert outer.end == len(doc.text)
    assert outer.block("b").scalar("c") == "1"
    assert [p.kind for p in doc.problems] == ["unclosed_block", "unclosed_block"]


def test_stray_close_is_skipped() -> None:
    doc = _checked("a = 1 } b = 2")
    assert doc.root.scalar("b") == "2"


def test_require_well_formed_returns_the_document() -> None:
    doc = require_well_formed("a = { b = 1 }")
    assert doc.root.block("a").scalar("b") == "1"
    with pytest.raises(ParseError, match=r"in x\.txt"):
        require_well_formed("a = {", "x.txt")


# ── repair ──────────────────────────────────────────────────────


def test_repair_of_well_formed_text_is_identical() -> None:
    text = "a = { b = 1 } # c\n"
    assert repair(text) is text


def test_repair_closes_unclosed_blocks() -> None:
    repaired = repair("a = {\n\tb = {\n\t\tc = 1\n")
    assert repaired == "a = {\n\tb = {\n\t\tc = 1\n}\n}\n"
    assert parse(repaired).problems == []


def test_repair_after_trailing_comment() -> None:
    repaired = repair("a = { # open")
    assert parse(repaired).problems == []
    assert parse(repaired).root.block("a") is not None


def test_repair_removes_stray_closes() -> None:
    repaired = repair("a = 1\n}\nb = 2\n")
    assert repaired == "a = 1\n\nb = 2\n"
    assert parse(repaired).problems == []


def test_repair_keeps_tokens_apart_when_removing_a_stray_close() -> None:
    # Regression: removing the "}" glued "1" and "b" into one word "1b".
    repaired = repair("a = 1}b = 2")
    doc = parse(repaired)
    assert doc.problems == []
    assert doc.root.scalar("a") == "1"
    assert doc.root.scalar("b") == "2"


def test_repair_leaves_unrepairable_problems() -> None:
    text = "a = = 1"
    assert repair(text) == text


# ── document helpers ────────────────────────────────────────────


def test_line_col() -> None:
    doc = parse("ab\ncd\n\nx")
    assert doc.line_col(0) == (1, 1)
    assert doc.line_col(1) == (1, 2)
    assert doc.line_col(3) == (2, 1)
    assert doc.line_col(7) == (4, 1)
    assert doc.line_col(len(doc.text)) == (4, 2)


def test_in_comment() -> None:
    text = 'a = "#x" # comment\nb = 1'
    doc = parse(text)
    start = text.index("# comment")
    assert not doc.in_comment(text.index("#x"))
    assert not doc.in_comment(start - 1)
    assert doc.in_comment(start)
    assert doc.in_comment(text.index("\n") - 1)
    assert not doc.in_comment(text.index("\n"))
    assert not doc.in_comment(len(text) - 1)


def test_without_comments_keeps_lines() -> None:
    text = 'a = 1 # one\n# two\nb = "#keep"\n'
    doc = parse(text)
    assert doc.without_comments() == 'a = 1 \n\nb = "#keep"\n'
    plain = parse("a = 1\n")
    assert plain.without_comments() is plain.text


def test_walk_yields_every_assignment_with_parents() -> None:
    doc = parse("a = { b = { c = 1 } list = { { d = 2 } } col = rgb { e = 3 } } f = 4")
    walked = [(item.name, [p.name for p in parents]) for item, parents in doc.root.walk()]
    assert walked == [
        ("a", []),
        ("b", ["a"]),
        ("c", ["a", "b"]),
        ("list", ["a"]),
        ("d", ["a", "list"]),
        ("col", ["a"]),
        ("e", ["a", "col"]),
        ("f", []),
    ]


def test_fold_compares_keys_without_case() -> None:
    doc = parse("IF = { limit = { Tag = GER } } If = { x = 1 }")
    assert doc.root.first("if") is None
    assert doc.root.first("if", fold=True).key.raw == "IF"
    assert len(list(doc.root.assignments("if", fold=True))) == 2
    assert len(doc.root.blocks("IF", fold=True)) == 2
    limit = doc.root.block("if", fold=True).block("LIMIT", fold=True)
    assert limit.scalar("tag", fold=True) == "GER"
    assert limit.scalar("tag") is None


def test_queries_and_defaults() -> None:
    doc = parse("a = 1 a = 2 b = { } c = rgb { 1 }")
    assert doc.root.scalar("a") == "1"
    assert doc.root.scalar("missing", "dflt") == "dflt"
    assert doc.root.scalar("b") is None
    assert doc.root.block("a") is None
    assert len(doc.root.blocks("c")) == 1
    assert [item.name for item in doc.root.assignments()] == ["a", "a", "b", "c"]
    assert doc.root.block("b").items == []
    assert doc.root.block("b").body == " "


def test_reprs_do_not_raise() -> None:
    doc = parse("a = { 1 } b = rgb { 1 }")
    for item in doc.root.items:
        repr(item)
        assert type(item) is Assignment
        repr(item.value)
    repr(doc.root)
    assert isinstance(doc.root.items[0].block.items[0], Scalar)


def test_large_texts_are_cached_and_small_are_not() -> None:
    small = "a = 1\n"
    assert parse(small) is not parse(small)
    large = "a = 1\n" * 1000
    assert parse(large) is parse(large)
