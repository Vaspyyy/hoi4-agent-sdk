"""Example tests for :mod:`hoi4.patching`: one statement changes, nothing else."""

from __future__ import annotations

import pytest

from hoi4.patching import (
    append_assignment,
    assignment_spans,
    block_bodies_equivalent,
    dedent_block_body,
    move_assignment_before,
    replace_assignment,
    replace_assignment_body,
    scalar_values_equivalent,
    set_block,
    set_scalar,
    top_level_assignments,
)
from hoi4.syntax import parse


# ── the cases from the review ───────────────────────────────────


def test_set_block_on_a_shared_line_does_not_duplicate_neighbours() -> None:
    result = set_block("a = 1 b = 2\n", "b", "x = 3")
    assert result.count("a = 1") == 1
    root = parse(result).root
    assert root.scalar("a") == "1"
    assert root.block("b").scalar("x") == "3"


def test_set_scalar_does_not_touch_a_variable_of_the_same_name() -> None:
    assert set_scalar("@foo = 1\nfoo = 2\n", "foo", "3") == "@foo = 1\nfoo = 3\n"
    assert set_scalar("@foo = 1\n", "foo", "3") == "@foo = 1\nfoo = 3\n"


def test_comparisons_are_not_assignments_unless_asked() -> None:
    assert top_level_assignments("a == 2") == []
    (span,) = top_level_assignments("a == 2", comparisons=True)
    assert (span.key, span.operator) == ("a", "==")
    spans = top_level_assignments("a < 1 b > 2 c != 3 d ?= 4 e = 5", comparisons=True)
    assert [s.operator for s in spans] == ["<", ">", "!=", "?=", "="]
    assert [s.key for s in top_level_assignments("a < 1 e = 5")] == ["e"]


def test_comparison_is_not_overwritten_by_set_scalar() -> None:
    assert set_scalar("a > 2\n", "a", "3") == "a > 2\na = 3\n"


@pytest.mark.parametrize("tag", ["rgb", "hsv", "HSV"])
def test_replacing_a_typed_block_leaves_no_dangling_block(tag: str) -> None:
    source = f"foo = {tag} {{ 1 2 3 }}\nbar = 1\n"
    (span, _) = top_level_assignments(source)
    assert span.tag == tag
    assert source[span.value_start : span.value_end] == f"{tag} {{ 1 2 3 }}"

    scalar = set_scalar(source, "foo", "3")
    assert scalar == "foo = 3\nbar = 1\n"
    assert parse(scalar).problems == []

    block = set_block(source, "foo", "x = 1")
    doc = parse(block)
    assert doc.problems == []
    assert [item.name for item in doc.root.assignments()] == ["foo", "bar"]
    assert doc.root.block("foo").scalar("x") == "1"
    assert tag not in block


def test_removing_an_inline_statement() -> None:
    assert set_scalar("a = 1 b = 2 c = 3\n", "b", None) == "a = 1 c = 3\n"
    assert set_scalar("a = 1 b = 2 c = 3\n", "c", None) == "a = 1 b = 2\n"
    assert set_block("a = 1 b = { x = 1 } c = 3\n", "b", None) == "a = 1 c = 3\n"


def test_removing_the_first_statement_of_a_shared_line() -> None:
    # Regression: the rest of the line kept a stray leading space.
    assert set_scalar("a = 1 b = 2\n", "a", None) == "b = 2\n"
    assert set_scalar("\ta = 1 b = 2\nc = 3\n", "a", None) == "\tb = 2\nc = 3\n"


def test_replacing_the_first_statement_of_a_shared_line_keeps_the_line() -> None:
    # Regression: the rest of the line was pushed onto a new line.
    result = set_block("a = 1 b = 2\n", "a", "x = 3")
    assert result.endswith("} b = 2\n")
    assert parse(result).root.scalar("b") == "2"


def test_trailing_comment_stays_on_its_statement_line() -> None:
    result = set_block("a = 1\nb = 2 # note\n", "b", "x = 1")
    assert result == "a = 1\nb = {\n\tx = 1\n} # note\n"
    assert set_scalar("a = 1\nb = 2 # note\n", "b", None) == "a = 1\n# note\n"


def test_comments_and_crlf_survive() -> None:
    source = "# head\r\na = 1 # one\r\nb = { x = 1 }\r\n# tail\r\n"
    result = set_scalar(source, "a", "5")
    assert result == source.replace("a = 1", "a = 5")
    result = set_block(source, "b", "y = 2\nz = 3")
    assert result == "# head\r\na = 1 # one\r\nb = { y = 2 z = 3 }\r\n# tail\r\n"
    result = set_scalar(source, "c", "7")
    assert result == source.replace("# tail\r\n", "# tail\r\nc = 7\r\n")
    # Removing a statement never removes the comment that shares its line.
    result = set_scalar(source, "a", None)
    assert result == "# head\r\n# one\r\nb = { x = 1 }\r\n# tail\r\n"


def test_multiline_block_body_keeps_its_layout_and_crlf() -> None:
    source = "b = {\r\n\tx = 1\r\n}\r\n"
    result = set_block(source, "b", "y = 2\nz = 3")
    assert result == "b = {\r\n\ty = 2\r\n\tz = 3\r\n}\r\n"


def test_hash_and_braces_in_strings_are_not_structure() -> None:
    source = 'name = "a = { # }"\ncost = 1\n'
    assert [s.key for s in top_level_assignments(source)] == ["name", "cost"]
    assert set_scalar(source, "cost", "2") == 'name = "a = { # }"\ncost = 2\n'


def test_nested_keys_are_not_top_level() -> None:
    source = "outer = { cost = 1 }\ncost = 2\n"
    assert set_scalar(source, "cost", "9") == "outer = { cost = 1 }\ncost = 9\n"


# ── behaviour of each helper ───────────────────────────────────


def test_set_scalar_equivalent_value_is_a_no_op() -> None:
    source = 'a = "5"\nb = 0.10\n'
    assert set_scalar(source, "a", "5") is source
    assert set_scalar(source, "b", "0.1") is source


def test_set_scalar_changes_only_the_first_occurrence() -> None:
    assert set_scalar("a = 1\na = 2\n", "a", "3") == "a = 3\na = 2\n"
    assert set_scalar("a = 1\na = 2\n", "a", None) == ""


def test_set_block_appends_with_indent_of_file() -> None:
    assert set_block("\ta = 1\n", "b", "x = 1") == "\ta = 1\n\tb = {\n\t\tx = 1\n\t}\n"
    assert set_block("", "b", "") == "b = {}"


def test_set_block_equivalent_body_is_a_no_op() -> None:
    source = "b = {\n\tx = 1 # keep\n}\n"
    assert set_block(source, "b", "x = 1") is source


def test_replace_assignment_body_requires_a_block() -> None:
    source = "a = 1 b = { x }"
    a, b = top_level_assignments(source)
    with pytest.raises(ValueError):
        replace_assignment_body(source, a, "y")
    assert replace_assignment_body(source, b, " y ") == "a = 1 b = { y }"
    assert replace_assignment_body(source, b, " x ") is source


def test_replace_assignment_owning_its_line() -> None:
    source = "a = 1\n\tb = 2\nc = 3\n"
    span = assignment_spans(source, "b")[0]
    assert replace_assignment(source, span, "b = 9") == "a = 1\n\tb = 9\nc = 3\n"
    assert replace_assignment(source, span, None) == "a = 1\nc = 3\n"


def test_append_assignment() -> None:
    assert append_assignment("a = 1\n\n", "b = 2") == "a = 1\nb = 2\n\n"
    assert append_assignment("", "b = 2") == "b = 2"
    assert append_assignment("a = 1", "  \n") == "a = 1"
    assert append_assignment("a = 1 # c", "b = 2") == "a = 1 # c\nb = 2"


def test_move_assignment_before() -> None:
    source = "a = 1\nb = 2\nc = 3 # c\n"
    a, _, c = top_level_assignments(source)
    assert move_assignment_before(source, c, a) == "c = 3 # c\na = 1\nb = 2\n"
    assert move_assignment_before(source, a, c) is source


def test_equivalence_helpers() -> None:
    assert scalar_values_equivalent('"5"', "5.0")
    assert scalar_values_equivalent('"a\\"b"', '"a\\"b"')
    assert not scalar_values_equivalent("a", "b")
    assert block_bodies_equivalent("x = 0.10 # c", " x = 0.1 ")
    assert block_bodies_equivalent("x = word", 'x = "word"')
    assert not block_bodies_equivalent("x = 1 y = 2", "y = 2 x = 1")
    assert block_bodies_equivalent('x = "open', 'x = "open')
    assert dedent_block_body("\n\t\tx = 1\n\t\ty = 2\n") == "x = 1\ny = 2"
