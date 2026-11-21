"""Lossless, depth-aware patches for top-level Paradox assignments.

The serializers use these helpers on the *original* source text.  Existing
assignments are therefore edited at their value/body spans instead of being
rendered again, which keeps surrounding whitespace, comments, and line endings
byte-for-byte stable.

All positions come from the syntax tree in :mod:`hoi4.syntax`, so a comment, a
quoted string or a nested block is never mistaken for an assignment.
"""

from __future__ import annotations

import re
import textwrap
from bisect import bisect_right
from collections.abc import Collection
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher

from .parser import ParseError, TokenType, tokenize
from .syntax import Assignment, Block, Tagged, parse, require_statement, require_well_formed


@dataclass(frozen=True)
class AssignmentSpan:
    """Where one ``key OP value`` statement sits in its source text.

    ``start``/``end`` cover the whole statement and ``value_start``/``value_end``
    its value. For a block value ``body_start``/``body_end`` cover the text
    between the braces. ``tag`` is set for a typed block such as
    ``color = rgb { 1 2 3 }``; its value span starts at the tag.
    """

    key: str
    start: int
    end: int
    value_start: int
    value_end: int
    is_block: bool
    body_start: int | None = None
    body_end: int | None = None
    operator: str = "="
    tag: str | None = None


def top_level_assignments(text: str, *, comparisons: bool = False) -> list[AssignmentSpan]:
    """Return assignments at brace depth zero, ignoring comments and strings.

    Only ``key = value`` statements are returned unless ``comparisons`` is
    true, which also returns ``key > value`` and the other comparison
    statements (see :attr:`AssignmentSpan.operator`).

    Like the game, a block that is still open at the end of the text ends
    there. Use :func:`hoi4.syntax.require_well_formed` to reject such text.
    """

    spans: list[AssignmentSpan] = []
    for item in parse(text).root.items:
        if type(item) is not Assignment:
            continue
        operator = item.op
        if operator != "=" and not comparisons:
            continue
        spans.append(_span(item, operator))
    return spans


def _span(item: Assignment, operator: str) -> AssignmentSpan:
    value = item.value
    if type(value) is Block:
        return AssignmentSpan(
            item.key.value, item.start, value.end, value.start, value.end, True,
            value.body_start, value.body_end, operator,
        )
    if type(value) is Tagged:
        block = value.block
        return AssignmentSpan(
            item.key.value, item.start, block.end, value.start, block.end, True,
            block.body_start, block.body_end, operator, value.tag.raw,
        )
    return AssignmentSpan(
        item.key.value, item.start, value.end, value.start, value.end, False,
        operator=operator,
    )


def assignment_spans(text: str, key: str) -> list[AssignmentSpan]:
    return [span for span in top_level_assignments(text) if span.key == key]


def set_scalar(text: str, key: str, value: str | None) -> str:
    spans = assignment_spans(text, key)
    if value is None:
        return _remove_assignments(text, spans)
    # A value of several words, or one with a stray brace, would not stay the
    # value of ``key``. (The key itself may be one that needs quotes.)
    require_statement(f"value = {value}", f"the value of '{key}'")
    if not spans:
        return append_assignment(text, f"{key} = {value}")
    first = spans[0]
    if first.is_block:
        return replace_assignment(text, first, f"{key} = {value}")
    current = text[first.value_start : first.value_end]
    if scalar_values_equivalent(current, value):
        return text
    return text[: first.value_start] + value + text[first.value_end :]


def set_block(text: str, key: str, body: str | None, *, comments: bool = False) -> str:
    """Give the first ``key = { ... }`` the meaning of ``body``.

    Only the statements that differ are rewritten (see :func:`sync_body`), so
    the rest of the block keeps its exact bytes. A ``body`` that means the same
    as the current one changes nothing.

    Comments count as soon as ``body`` has any: a changed, added or dropped
    comment is written. A ``body`` without comments says nothing about the ones
    in the file and leaves them alone, unless ``comments`` is true.

    Raises :class:`~hoi4.syntax.ParseError` for a ``body`` that is not well
    formed on its own (a stray or missing brace, an unterminated string).
    """

    spans = assignment_spans(text, key)
    if body is None:
        return _remove_assignments(text, spans)
    cleaned = body.strip()
    # A body with a stray or missing brace would change the script around it.
    require_well_formed(cleaned, f"the new body of '{key}'")
    if not spans:
        replacement = f"{key} = {{"
        if cleaned:
            replacement += "\n" + _indent(cleaned, 1) + "\n"
        replacement += "}"
        return append_assignment(text, replacement)
    first = spans[0]
    if (
        not first.is_block
        or first.body_start is None
        or first.body_end is None
        # ``key = rgb { ... }``: the caller's body is untyped, so the tag must go.
        or first.tag is not None
    ):
        replacement = f"{key} = {{"
        if cleaned:
            replacement += "\n" + _indent(cleaned, 1) + "\n"
        replacement += "}"
        return replace_assignment(text, first, replacement)
    current = text[first.body_start : first.body_end]
    if same_block_body(current, cleaned, comments=comments):
        return text
    if cleaned and not current.strip() and "\n" in current:
        # An empty block that spans lines: the first statements go one level
        # deeper than its closing brace.
        return replace_assignment_body(
            text, first, _fill_empty_body(current, cleaned, _line_ending(text))
        )
    indent = _line_indent(text, first.start)
    synced = _sync_or_spread(current, cleaned, indent, comments, _line_ending(text))
    if synced is None:
        synced = _render_block_body_like(current, cleaned, _line_ending(text), indent)
    return replace_assignment_body(text, first, synced)


def _sync_or_spread(
    current: str, new: str, indent: str, comments: bool, newline: str
) -> str | None:
    """Change ``current`` in place, or after giving every statement its own line.

    The second layout loses nothing either: it only moves statements, with
    their comments, onto lines of their own.
    """

    synced = sync_body(current, new, comments=comments, newline=newline)
    if synced is None:
        spread = one_statement_per_line(current, indent, newline)
        if spread is not None and spread != current:
            synced = sync_body(spread, new, comments=comments, newline=newline)
    return synced


def same_block_body(current: str, new: str, *, comments: bool = False) -> bool:
    """Whether ``new`` asks for nothing that ``current`` does not already say.

    The two bodies have to mean the same (see :func:`block_bodies_equivalent`).
    Comments are compared as soon as ``new`` has any, or when ``comments`` is
    true: a ``new`` without comments says nothing about the ones in
    ``current``. A compared comment has to say the same and sit at the same
    statement, at every depth: one that moved from the end of one line to
    another, also inside a nested block, is a change.
    """

    if not block_bodies_equivalent(current, new):
        return False
    wanted = _comment_texts(new)
    if not comments and not wanted:
        return True
    if wanted != _comment_texts(current):
        return False
    left = _segments(current, True, ())
    right = _segments(new, True, ())
    if left is None or right is None:
        return True
    return [segment.meaning for segment in left] == [segment.meaning for segment in right]


def _comment_texts(text: str) -> tuple[str, ...]:
    return tuple(text[start:end].strip() for start, end in parse(text).comments)


def replace_assignment_body(text: str, span: AssignmentSpan, body: str) -> str:
    """Replace only a block assignment's interior, preserving its wrapper bytes."""
    if not span.is_block or span.body_start is None or span.body_end is None:
        raise ValueError("Assignment is not a block")
    current = text[span.body_start : span.body_end]
    if body == current:
        return text
    return text[: span.body_start] + body + text[span.body_end :]


def scalar_values_equivalent(left: str, right: str) -> bool:
    """Return whether two scalar spellings have the same modeled value."""
    a = _unquote_scalar(left.strip())
    b = _unquote_scalar(right.strip())
    if a == b:
        return True
    try:
        return Decimal(a) == Decimal(b)
    except InvalidOperation:
        return False


def block_bodies_equivalent(left: str, right: str, *, comments: bool = False) -> bool:
    """Compare block bodies while ignoring formatting and comments.

    Numeric spelling (``0.10`` versus ``0.1``) and harmless quoting of a bare
    atom are normalized as well.  Ordering and repeated assignments remain
    significant. With ``comments=True`` the text of the comments has to match
    as well; their layout still does not count.
    """
    try:
        same = _semantic_tokens(left) == _semantic_tokens(right)
    except ParseError:
        return left.strip() == right.strip()
    return same and (not comments or _comment_texts(left) == _comment_texts(right))


def dedent_block_body(body: str) -> str:
    """Normalize a source block interior for use as an editable model value."""
    return textwrap.dedent(body).strip()


def replace_assignment(text: str, span: AssignmentSpan, replacement: str | None) -> str:
    """Replace (or with ``None`` remove) one statement, leaving its neighbours alone."""

    return _replace_span(text, span.start, span.end, replacement)


def _replace_span(text: str, span_start: int, span_end: int, replacement: str | None) -> str:
    line_start = text.rfind("\n", 0, span_start) + 1
    prefix = text[line_start:span_start]
    line_end = text.find("\n", span_end)
    suffix = text[span_end : len(text) if line_end < 0 else line_end]
    if prefix.strip() or suffix.strip():
        # The statement shares its line with other script or a comment
        # (``a = 1 b = 2``, ``b = 2 # note``): only its own span changes.
        if replacement is not None:
            return text[:span_start] + replacement.rstrip() + text[span_end:]
        start = span_start
        end = span_end
        while end < len(text) and text[end] in " \t":
            end += 1
        if end == len(text) or text[end] in "\r\n":
            # Last statement of the line: drop the spaces before it too.
            while start > line_start and text[start - 1] in " \t":
                start -= 1
        return text[:start] + text[end:]
    end = span_end + len(suffix)  # spaces after the statement go with its line
    if end < len(text) and text[end] == "\r":
        end += 1
    if end < len(text) and text[end] == "\n":
        end += 1
    newline = _line_ending(text)
    rendered = ""
    if replacement is not None:
        rendered = prefix + _indent_continuation(replacement.rstrip(), prefix) + newline
    return text[:line_start] + rendered + text[end:]


def _indent_continuation(statement: str, prefix: str) -> str:
    """Move the later lines of a statement written at column zero to ``prefix``.

    A statement whose later lines already start with ``prefix`` (text cut out
    of a file) is left as it is.
    """

    if not prefix or "\n" not in statement:
        return statement
    lines = statement.split("\n")
    later = [line for line in lines[1:] if line.strip()]
    if later and all(line.startswith(prefix) for line in later):
        return statement
    return "\n".join([lines[0], *(prefix + line if line.strip() else line for line in lines[1:])])


# ── statement-by-statement body edits ───────────────────────────


@dataclass(frozen=True)
class _Segment:
    """One top-level statement or free-standing comment of a body.

    ``end`` closes the statement itself and ``full_end`` also covers a comment
    that follows it on the same line, which belongs to it.
    """

    start: int
    end: int
    full_end: int
    meaning: tuple[object, ...]
    item: object | None  # the syntax node; ``None`` for a free-standing comment
    trailing: str = ""  # text of the comment on the statement's line


def sync_body(
    current: str,
    new: str,
    *,
    comments: bool = False,
    keep: Collection[str] = (),
    newline: str | None = None,
) -> str | None:
    """Rewrite ``current`` so that it means ``new``, statement by statement.

    Statements that both bodies share keep their exact bytes, whatever their
    spacing. A statement only in ``current`` is cut out (with the comment on
    its line), one only in ``new`` is put in, and a block whose inside changed
    is edited the same way one level down. This is how a list block gets one
    more entry without losing the comments next to the others.

    Comments are compared as soon as ``new`` has any, or when ``comments`` is
    true; otherwise the comments of ``current`` are left where they are.
    Statements of ``current`` whose key is in ``keep`` are not touched and are
    not expected in ``new``. ``newline`` is the line ending for new lines; by
    default the one ``current`` uses.

    Returns ``None`` when the edit cannot be done this way (malformed text, or
    a layout that would not survive, such as a comment put into a one-line
    block). The caller then replaces the body as a whole.
    """

    count = comments or bool(parse(new).comments)
    old = _segments(current, count, keep)
    wanted = _segments(new, count, ())
    if old is None or wanted is None:
        return None
    inline = "\n" not in current
    newline = newline or _line_ending(current)
    text = current
    matcher = SequenceMatcher(
        None, [segment.meaning for segment in old], [segment.meaning for segment in wanted],
        autojunk=False,
    )

    # The plan: stretches to swap, and same-key pairs to edit in place. Inside
    # a changed stretch, a statement that keeps its key is the same statement
    # with a new value.
    swaps: list[tuple[int, int, int, int]] = []
    pairs: list[tuple[int, int]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        same_key = SequenceMatcher(
            None,
            [_statement_key(segment, ("old", index)) for index, segment in enumerate(old[i1:i2])],
            [_statement_key(segment, ("new", index)) for index, segment in enumerate(wanted[j1:j2])],
            autojunk=False,
        )
        for kind, a1, a2, b1, b2 in same_key.get_opcodes():
            if kind == "equal":
                pairs.extend((i1 + a1 + offset, j1 + b1 + offset) for offset in range(a2 - a1))
            else:
                swaps.append((i1 + a1, i1 + a2, j1 + b1, j1 + b2))

    # A statement that is cut in one place and put in another has moved: it
    # takes its own bytes along, with the comment on its line.
    cut: dict[tuple[object, ...], list[_Segment]] = {}
    for i1, i2, _, _ in swaps:
        for segment in old[i1:i2]:
            cut.setdefault(segment.meaning, []).append(segment)
    pieces: dict[int, tuple[str, _Segment]] = {}
    for _, _, j1, j2 in swaps:
        for index in range(j1, j2):
            moved = cut.get(wanted[index].meaning)
            pieces[index] = (current, moved.pop(0)) if moved else (new, wanted[index])

    # Offsets come from ``current``, so the edits run from its end to its start.
    # At the same statement, the in-place edit comes before the insert in
    # front of it, which would move the statement's offsets.
    edits: list[tuple[int, ...]] = [*swaps, *pairs]
    for edit in sorted(edits, key=lambda entry: (entry[0], len(entry) == 2), reverse=True):
        if len(edit) == 2:
            text = _replace_segment(text, old[edit[0]], new, wanted[edit[1]], count, newline)
            continue
        i1, i2, j1, j2 = edit
        if j1 < j2:
            placed = _insert_segments(
                text, [pieces[index] for index in range(j1, j2)], old, i2, inline, newline
            )
            if placed is None:
                return None
            text = placed
        for segment in reversed(old[i1:i2]):
            text = _replace_span(text, segment.start, segment.full_end, None)
    # Whatever the layout did, the result has to say exactly what was asked,
    # and no comment may reach the closing brace that follows the body.
    result = _segments(text, count, keep)
    if result is None or [segment.meaning for segment in result] != [
        segment.meaning for segment in wanted
    ]:
        return None
    result_comments = parse(text).comments
    if result_comments and "\n" not in text[result_comments[-1][0] :]:
        return None
    return text


def _statement_key(segment: _Segment, fallback: object) -> object:
    """What makes a statement "the same one": its key and operator."""

    item = segment.item
    if type(item) is Assignment:
        return ("statement", item.name, item.op)
    return fallback


def _comment_shape(text: str, item: object) -> tuple[object, ...]:
    """Where the comments of one statement sit.

    For a block they are told per inner statement, at every depth, so a
    comment that moves between two statements of a nested block is a change.
    For any other statement it is the text of its comments.
    """

    start, end = item.start, item.end  # type: ignore[attr-defined]
    block = item.block if type(item) is Assignment else item if type(item) is Block else None
    inner = None
    if block is not None and block.closed:
        inner = _segments(text[block.body_start : block.body_end], True, ())
    if block is None or inner is None:
        return _comment_texts(text[start:end])
    outside = _comment_texts(text[start : block.body_start]) + _comment_texts(
        text[block.body_end : end]
    )
    return (outside, tuple(segment.meaning for segment in inner))


def _segments(text: str, count_comments: bool, keep: Collection[str]) -> list[_Segment] | None:
    doc = parse(text)
    if doc.problems:
        return None
    items = doc.root.items
    starts = [item.start for item in items]
    # Comments between statements; one inside a statement is part of it. A
    # comment span ends before the line break, also before a ``\r``.
    free = [
        (start, start + len(text[start:end].rstrip()))
        for start, end in doc.comments
        if not (index := bisect_right(starts, start) - 1) >= 0 or start >= items[index].end
    ]
    free_starts = [start for start, _ in free]
    segments: list[_Segment] = []
    owned: set[int] = set()
    try:
        for position, item in enumerate(items):
            full_end = item.end
            trailing = ""
            following = bisect_right(free_starts, item.end - 1)
            if following < len(free):
                start, end = free[following]
                next_start = starts[position + 1] if position + 1 < len(items) else len(text)
                if start < next_start and "\n" not in text[item.end : start]:
                    owned.add(following)
                    full_end = end
                    trailing = text[start:end].strip()
            if type(item) is Assignment and item.name in keep:
                continue
            raw = text[item.start : item.end]
            meaning: tuple[object, ...] = ("statement", _semantic_tokens(raw))
            if count_comments:
                meaning += (_comment_shape(text, item), trailing)
            segments.append(_Segment(item.start, item.end, full_end, meaning, item, trailing))
    except ParseError:
        return None
    if count_comments:
        for index, (start, end) in enumerate(free):
            if index not in owned:
                segments.append(
                    _Segment(start, end, end, ("comment", text[start:end].strip()), None)
                )
        segments.sort(key=lambda segment: segment.start)
    return segments


def _replace_segment(
    text: str, old: _Segment, new_text: str, new: _Segment, count_comments: bool, newline: str
) -> str:
    """Put ``new`` where ``old`` is; a block with the same key is edited inside."""

    before, after = old.item, new.item
    if (
        type(before) is Assignment
        and type(after) is Assignment
        and before.name == after.name
        and before.op == after.op
        and type(before.value) is Block
        and type(after.value) is Block
        and (not count_comments or old.trailing == new.trailing)
    ):
        inner = _sync_or_spread(
            text[before.value.body_start : before.value.body_end],
            new_text[after.value.body_start : after.value.body_end],
            _line_indent(text, old.start),
            count_comments,
            newline,
        )
        if inner is not None:
            return text[: before.value.body_start] + inner + text[before.value.body_end :]
    indent = _line_indent(text, old.start)
    # Without comment comparison the comment on the statement's line stays.
    old_end = old.full_end if count_comments else old.end
    new_end = new.full_end if count_comments else new.end
    return (
        text[: old.start]
        + _reindented(new_text, new.start, new_end, indent, newline)
        + text[old_end:]
    )


def _insert_segments(
    text: str,
    added: list[tuple[str, _Segment]],
    old: list[_Segment],
    position: int,
    inline: bool,
    newline: str,
) -> str | None:
    """Put ``added`` before ``old[position]``, or after everything else.

    Each entry of ``added`` is a statement with the text it is cut from.
    """

    anchor = old[position] if position < len(old) else None
    one_line = all(
        segment.item is not None
        and segment.full_end == segment.end
        and "\n" not in source[segment.start : segment.end]
        for source, segment in added
    )
    joined = " ".join(source[segment.start : segment.end] for source, segment in added)

    def lines(indent: str) -> list[str]:
        return [
            _reindented(source, segment.start, segment.full_end, indent, newline)
            for source, segment in added
        ]

    if anchor is not None:
        line_start = text.rfind("\n", 0, anchor.start) + 1
        prefix = text[line_start : anchor.start]
        if inline or prefix.strip() or line_start == 0:
            # The anchor shares its line with something before it, if only
            # with the opening brace: stay on that line.
            return text[: anchor.start] + joined + " " + text[anchor.start :] if one_line else None
        return (
            text[:line_start]
            + "".join(prefix + line + newline for line in lines(prefix))
            + text[line_start:]
        )

    end = len(text.rstrip(" \t\r\n"))
    if end == 0:
        return None
    if inline:
        return text[:end] + " " + joined + text[end:] if one_line else None
    last = old[-1] if old else None
    if (
        one_line
        and last is not None
        and last.item is not None
        and last.full_end == end
        and last.end == end
        and len(old) > 1
        and "\n" not in text[old[-2].full_end : last.start]
    ):
        # Several entries share the last line (a province list): join them.
        return text[:end] + " " + joined + text[end:]
    if text.rfind("\n", 0, end) >= 0:
        indent = _line_indent(text, end)
    else:
        # The only content sits on the line of the opening brace.
        indent = text[end:].rpartition("\n")[2] + "\t"
    # Spaces that end the last line stay on that line: it keeps its bytes.
    line_end = end
    while line_end < len(text) and text[line_end] in " \t":
        line_end += 1
    if line_end < len(text) and text[line_end] in "\r\n":
        end = line_end
    return text[:end] + "".join(newline + indent + line for line in lines(indent)) + text[end:]


def one_statement_per_line(body: str, indent: str, newline: str | None = None) -> str | None:
    """Lay a block body out with one statement per line.

    ``indent`` is the indentation of the line that holds the block's opening
    brace. Every statement keeps its bytes and the comment on its line, and a
    comment between statements gets a line of its own. This layout can hold
    any edit, so it is what a caller falls back to when :func:`sync_body`
    cannot change a body in place. ``newline`` is the line ending to use; by
    default the one ``body`` uses. Returns ``None`` for malformed text.
    """

    spans = statement_spans(body)
    if spans is None:
        return None
    newline = newline or _line_ending(body)
    inner = indent + "\t"
    return newline + "".join(inner + body[start:end] + newline for start, end in spans) + indent


def statement_spans(text: str, *, keep: Collection[str] = ()) -> list[tuple[int, int]] | None:
    """Where the statements and free-standing comments of ``text`` are.

    A statement's span includes the comment on its line. Statements whose key
    is in ``keep`` are left out, together with their comments. Returns
    ``None`` for malformed text.
    """

    segments = _segments(text, True, keep)
    if segments is None:
        return None
    return [(segment.start, segment.full_end) for segment in segments]


def _reindented(text: str, start: int, end: int, indent: str, newline: str = "\n") -> str:
    """``text[start:end]`` with its later lines moved to ``indent``."""

    raw = text[start:end]
    if "\n" not in raw:
        return raw
    line_start = text.rfind("\n", 0, start) + 1
    base = text[line_start:start]
    if base.strip():
        base = ""
    lines = [line.removesuffix("\r") for line in raw.split("\n")]
    result = [lines[0]]
    for line in lines[1:]:
        if base and line.startswith(base):
            line = line[len(base) :]
        result.append(indent + line if line.strip() else "")
    return newline.join(result)


def _line_indent(text: str, offset: int) -> str:
    """The leading whitespace of the line that holds ``offset``."""

    line_start = text.rfind("\n", 0, offset) + 1
    match = re.match(r"[ \t]*", text[line_start:])
    return match.group(0) if match else ""


def append_assignment(text: str, replacement: str) -> str:
    rendered = replacement.rstrip(" \t\r\n")
    if not rendered:
        return text
    newline = _line_ending(text)
    content_end = len(text.rstrip(" \t\r\n"))
    if content_end:
        # Spaces that end the last line stay on that line: it keeps its bytes.
        line_end = content_end
        while line_end < len(text) and text[line_end] in " \t":
            line_end += 1
        if line_end < len(text) and text[line_end] in "\r\n":
            content_end = line_end
    content = text[:content_end]
    trailing = text[content_end:]
    if not content and text:
        return _fill_empty_body(text, rendered, newline)
    indent = _assignment_indent(text)
    if not rendered[0].isspace() and indent:
        rendered = newline.join(indent + line if line else line for line in rendered.splitlines())
    separator = newline if content else ""
    if "\n" not in trailing and _ends_in_comment(rendered):
        # Whatever follows on the line (a closing brace) must stay out of the comment.
        trailing = newline + trailing
    return content + separator + rendered + trailing


def _ends_in_comment(text: str) -> bool:
    """Whether the last line of ``text`` ends in a comment."""

    if "#" not in text:
        return False
    comments = parse(text).comments
    return bool(comments) and "\n" not in text[comments[-1][0] :]


def _fill_empty_body(text: str, rendered: str, newline: str) -> str:
    """Put the first statement into a body that holds only whitespace."""

    if "\n" not in text:
        # ``key = { }``: stay on the line when the statement fits on one.
        if "\n" not in rendered and not _ends_in_comment(rendered):
            return " " + rendered.strip() + " "
        return newline + _indent(textwrap.dedent(rendered), 1).replace("\n", newline) + newline
    # ``key = {`` and ``}`` on separate lines: one level deeper than the brace.
    head, _, closing = text.rpartition("\n")
    head = head.rstrip("\r")
    if not rendered[0].isspace():
        indent = closing + "\t"
        rendered = newline.join(
            indent + line if line else line for line in rendered.splitlines()
        )
    return head + newline + rendered + newline + closing


def move_assignment_before(
    text: str,
    span: AssignmentSpan,
    target: AssignmentSpan,
) -> str:
    """Move a later assignment before another, retaining its source spelling."""

    if span.start <= target.start:
        return text

    source_line_start = text.rfind("\n", 0, span.start) + 1
    source_start = (
        source_line_start
        if not text[source_line_start : span.start].strip()
        else span.start
    )
    source_line_end = text.find("\n", span.end)
    logical_line_end = len(text) if source_line_end < 0 else source_line_end
    suffix = text[span.end : logical_line_end]
    owns_line = source_start == source_line_start
    if owns_line and (
        not suffix.strip() or suffix.lstrip().startswith("#")
    ):
        source_end = len(text) if source_line_end < 0 else source_line_end + 1
    else:
        source_end = span.end

    rendered = text[source_start:source_end]
    if not rendered.endswith(("\n", "\r")):
        rendered = rendered.rstrip(" \t") + _line_ending(text)
    remaining = text[:source_start] + text[source_end:]

    target_line_start = remaining.rfind("\n", 0, target.start) + 1
    target_prefix = remaining[target_line_start : target.start]
    insert_at = target_line_start if not target_prefix.strip() else target.start
    return remaining[:insert_at] + rendered + remaining[insert_at:]


def _remove_assignments(text: str, spans: list[AssignmentSpan]) -> str:
    result = text
    for span in reversed(spans):
        result = replace_assignment(result, span, None)
    return result


def _block_comments(body: str) -> tuple[bool, bool]:
    """Return whether real comments exist and whether the body ends in one."""
    comments = parse(body).comments
    if not comments:
        return False, False
    return True, "\n" not in body[comments[-1][0] :]


def _render_block_body_like(
    current: str, body: str, newline: str, outer_indent: str,
) -> str:
    cleaned = _dedent_block_body(body)
    leading_match = re.match(r"\s*", current)
    trailing_match = re.search(r"\s*$", current)
    leading = leading_match.group(0) if leading_match else ""
    trailing = trailing_match.group(0) if trailing_match else ""

    has_comments, ends_in_comment = _block_comments(cleaned)
    if "\n" not in current and "\r" not in current and not has_comments:
        compact = " ".join(line.strip() for line in cleaned.splitlines() if line.strip())
        return leading + compact + trailing

    if "\n" not in current and "\r" not in current:
        leading = newline + outer_indent + "\t"
        trailing = newline + outer_indent
    else:
        newline = _line_ending(current)
        if ends_in_comment and "\n" not in trailing:
            # A multiline source can still close on its final content line.
            trailing = newline + outer_indent

    indent = re.split(r"\r?\n", leading)[-1]
    lines = cleaned.splitlines()
    if not lines:
        return leading + trailing
    rendered = lines[0].rstrip()
    for line in lines[1:]:
        rendered += newline + indent + line.rstrip()
    return leading + rendered + trailing


def _dedent_block_body(body: str) -> str:
    cleaned = textwrap.dedent(body.strip(" \t\r\n"))
    lines = cleaned.splitlines()
    if len(lines) < 2 or lines[0][:1].isspace():
        return cleaned
    continuation = [line for line in lines[1:] if line.strip()]
    if not continuation:
        return cleaned
    widths = [len(line) - len(line.lstrip(" \t")) for line in continuation]
    common = min(widths)
    if common <= 0:
        return cleaned
    return "\n".join([lines[0], *(line[common:] if line.strip() else line for line in lines[1:])])


def _semantic_tokens(text: str) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    for token in tokenize(text):
        if token.type in {TokenType.COMMENT, TokenType.EOF}:
            continue
        if token.type == TokenType.NUMBER:
            try:
                value = format(Decimal(token.value).normalize(), "f")
            except InvalidOperation:
                value = token.value
            result.append(("number", value))
        elif token.type in {TokenType.IDENT, TokenType.STRING}:
            result.append(("atom", token.value))
        else:
            result.append((token.type.name, token.value))
    return tuple(result)


def _unquote_scalar(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return _unescape_quoted(value[1:-1])
    return value


def _unescape_quoted(value: str) -> str:
    result: list[str] = []
    i = 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value) and value[i + 1] in {'"', "\\"}:
            result.append(value[i + 1])
            i += 2
            continue
        result.append(value[i])
        i += 1
    return "".join(result)


def _assignment_indent(text: str) -> str:
    spans = top_level_assignments(text)
    if not spans:
        return ""
    line_start = text.rfind("\n", 0, spans[0].start) + 1
    prefix = text[line_start : spans[0].start]
    return prefix if not prefix.strip() else ""


def _line_ending(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _indent(text: str, count: int) -> str:
    prefix = "\t" * count
    return "\n".join(prefix + line.rstrip() for line in text.splitlines())
