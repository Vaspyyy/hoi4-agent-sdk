"""Structural checks on a :class:`hoi4.syntax.Document`, shared by the syntax tests.

Not a test module (pytest only collects ``test_*.py``). The property tests and
the opt-in real-corpus test both use these, so one definition of "the tree
accounts for every character" applies everywhere.
"""

from __future__ import annotations

import re

from hoi4.syntax import _TOKEN_RE, Assignment, Block, Document, Item, Scalar, Tagged

_OPERATOR_RE = re.compile(r"[<>]=?|[!?=]=|=")


def _item_spans(item: Item, out: list[tuple[int, int]]) -> None:
    if type(item) is Scalar:
        out.append((item.start, item.end))
    elif type(item) is Block:
        _block_spans(item, out)
    elif type(item) is Tagged:
        out.append((item.tag.start, item.tag.end))
        _block_spans(item.block, out)
    elif type(item) is Assignment:
        out.append((item.key.start, item.key.end))
        out.append((item.op_start, item.op_end))
        _item_spans(item.value, out)
    else:  # pragma: no cover - the tree has no other node types
        raise AssertionError(f"unexpected node {item!r}")


def _block_spans(block: Block, out: list[tuple[int, int]]) -> None:
    out.append((block.start, block.body_start))  # "{"
    for item in block.items:
        _item_spans(item, out)
    if block.closed:
        out.append((block.body_end, block.end))  # "}"


def token_spans(doc: Document) -> list[tuple[int, int]]:
    """Every token, comment and problem span of ``doc``, sorted."""

    spans: list[tuple[int, int]] = []
    for item in doc.root.items:
        _item_spans(item, spans)
    spans.extend(doc.comments)
    for problem in doc.problems:
        if problem.kind == "stray_close":
            spans.append((problem.offset, problem.offset + 1))
        elif problem.kind == "stray_operator":
            found = _OPERATOR_RE.match(doc.text, problem.offset)
            assert found is not None, problem
            spans.append(found.span())
        elif problem.kind == "missing_value":
            # ``key =`` with nothing after it: the key and the operator are
            # not in the tree. Comments in between are already in the list.
            token = _TOKEN_RE.match(doc.text, problem.offset)
            assert token is not None and token.start(token.lastindex or 0) == problem.offset
            spans.append(token.span(token.lastindex or 0))
            token = _TOKEN_RE.match(doc.text, token.end())
            while token is not None and token.lastindex == 1:  # comments
                token = _TOKEN_RE.match(doc.text, token.end())
            assert token is not None and token.lastindex == 4, problem  # the operator
            spans.append(token.span(4))
    spans = [span for span in spans if span[1] > span[0]]
    spans.sort()
    return spans


# The characters the tokenizer skips between tokens. Everything else must be
# inside a token, a comment, or a reported problem.
_NOT_SKIPPED = re.compile(r"[^ \t\r\n\f\v\ufeff]")


def assert_covers_all_text(doc: Document) -> None:
    """Every non-whitespace character is in exactly one token, comment or problem."""

    spans = token_spans(doc)
    text = doc.text
    gaps: list[str] = []
    position = 0
    for start, end in spans:
        assert start >= position, f"spans overlap at {start}: {text[start:end]!r}"
        gaps.append(text[position:start])
        position = end
    gaps.append(text[position:])
    if _NOT_SKIPPED.search("".join(gaps)) is None:
        return
    position = 0
    for start, end in [*spans, (len(text), len(text))]:
        found = _NOT_SKIPPED.search(text, position, start)
        assert found is None, f"uncovered text {text[found.start():start][:40]!r} at {found.start()}"
        position = end


def _check_block(block: Block, text: str, lower: int, upper: int) -> None:
    assert lower <= block.start <= block.body_start <= block.body_end <= block.end <= upper
    assert block.raw == text[block.start : block.end]
    assert block.body == text[block.body_start : block.body_end]
    if block is not block.doc.root:
        assert text[block.start : block.body_start] == "{"
        if block.closed:
            assert text[block.body_end : block.end] == "}"
        else:
            assert block.body_end == block.end == len(text)
    position = block.body_start
    for item in block.items:
        assert item.start >= position, f"{item!r} starts before its predecessor ends"
        assert item.end <= block.body_end
        assert item.raw == text[item.start : item.end]
        _check_item(item, text)
        position = item.end


def _check_item(item: Item, text: str) -> None:
    if type(item) is Scalar:
        assert item.start < item.end
    elif type(item) is Block:
        _check_block(item, text, item.start, item.end)
    elif type(item) is Tagged:
        assert item.tag.end <= item.block.start
        _check_block(item.block, text, item.start, item.end)
    elif type(item) is Assignment:
        assert item.key.start < item.key.end <= item.op_start < item.op_end <= item.value.start
        assert item.op in {"=", "<", ">", "<=", ">=", "!=", "==", "?="}
        assert item.raw == text[item.start : item.end]
        _check_item(item.value, text)


def assert_spans_nest(doc: Document) -> None:
    """Spans nest inside their parents, siblings are in source order, raw is exact."""

    _check_block(doc.root, doc.text, 0, len(doc.text))
    assert doc.root.start == 0 and doc.root.end == len(doc.text)
    previous = -1
    for start, end in doc.comments:
        assert previous <= start < end
        assert doc.text[start] == "#"
        assert "\n" not in doc.text[start:end]
        previous = end
