"""The one reader for Paradox script: a lossless syntax tree with exact positions.

Every other module that reads script builds on this one. The tree never copies
or rewrites source text. Each node only remembers where it sits in the
original string, so:

* reading is a tree query (no regular expressions over raw text, which cannot
  tell a comment or a nested block from the real thing);
* writing is a replacement of one exact span, which leaves every other byte of
  the file alone.

The grammar is the one the game accepts::

    file   := item*
    item   := key OP value | value
    value  := scalar | "{" item* "}" | TAG "{" item* "}"     (rgb { 1 2 3 })
    OP     := "=" | "<" | ">" | "<=" | ">=" | "!=" | "==" | "?="
    scalar := bare word | "quoted string"

Like the game, the parser is forgiving: a block that is still open at the end
of the file is closed there, and a stray closing brace is skipped. Each such
repair is recorded in :attr:`Document.problems` so validation can report it.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Iterator
from functools import lru_cache

from ._scalar import needs_quotes

__all__ = [
    "Assignment",
    "Block",
    "Document",
    "Item",
    "ParseError",
    "Problem",
    "Scalar",
    "Tagged",
    "Value",
    "as_scalar",
    "parse",
    "quote",
    "repair",
    "require_entry",
    "require_key",
    "require_statement",
    "require_well_formed",
    "unquote",
]

# One token per match, with any leading whitespace skipped in the same step.
# Whitespace is what the game's byte-level reader treats as such: ASCII space,
# tab, line breaks, form feed and vertical tab, plus a stray byte-order mark.
# Every other character (a no-break space, a Unicode line separator) is part
# of a word, so every character of any text belongs to some token and the
# parser can never stop early.
# A lone "!" or "?" belongs to a word ("foo?0" is a variable with a default);
# only "!=" and "?=" are operators.
_TOKEN_RE = re.compile(
    r"""[ \t\r\n\f\v\ufeff]*(?:
        (\#[^\n]*)                                   # 1 comment
      | (\{)                                         # 2 open
      | (\})                                         # 3 close
      | ([<>]=?|[!?=]=|=)                            # 4 operator
      | ("(?:[^"\\]|\\.)*"?)                         # 5 string (maybe unterminated)
      | (@\[[^\]\n]*\]                               # 6 word: inline maths @[ 1 + 2 ]
        |(?:[^ \t\r\n\f\v\ufeff{}=<>\#"!?]|[!?](?!=))+)   #   or a plain word
    )""",
    re.VERBOSE | re.DOTALL,
)
_COMMENT, _OPEN, _CLOSE, _OP, _STRING, _WORD = 1, 2, 3, 4, 5, 6
_SPACES = frozenset(" \t\r\n\f\v\ufeff")

# A scalar followed by a block is one value for these typed constructors.
_TAGS = frozenset({"rgb", "hsv", "hsv360"})


class ParseError(ValueError):
    """Raised when Paradox script cannot be parsed without losing structure."""



def unquote(raw: str) -> str:
    """Return the value of a scalar as written in source (quotes and escapes removed)."""

    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        inner = raw[1:-1]
    elif raw[:1] == '"':  # unterminated string
        inner = raw[1:]
    else:
        return raw
    if "\\" not in inner:
        return inner
    return re.sub(r'\\(["\\])', r"\1", inner)


def quote(value: str, *, force: bool = False) -> str:
    """Spell ``value`` as a scalar, adding quotes and escapes only when needed."""

    if not force and not needs_quotes(value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Problem:
    """A place where the source is malformed and the parser repaired it.

    ``kind`` is one of ``"unclosed_block"``, ``"stray_close"``,
    ``"missing_value"``, ``"stray_operator"`` or ``"unterminated_string"``.
    """

    __slots__ = ("offset", "kind", "message")

    def __init__(self, offset: int, kind: str, message: str) -> None:
        self.offset = offset
        self.kind = kind
        self.message = message

    def __repr__(self) -> str:
        return f"Problem({self.offset}, {self.kind!r}, {self.message!r})"


class Scalar:
    """A bare word or a quoted string."""

    __slots__ = ("doc", "start", "end", "quoted")

    def __init__(self, doc: Document, start: int, end: int, quoted: bool) -> None:
        self.doc = doc
        self.start = start
        self.end = end
        self.quoted = quoted

    @property
    def raw(self) -> str:
        """The exact source spelling, including quotes."""

        return self.doc.text[self.start : self.end]

    @property
    def value(self) -> str:
        """The value with quotes and escapes removed."""

        raw = self.doc.text[self.start : self.end]
        return unquote(raw) if self.quoted else raw

    def __repr__(self) -> str:
        return f"Scalar({self.raw!r})"


class Block:
    """``{ ... }``, or the whole file for :attr:`Document.root`."""

    __slots__ = ("doc", "start", "end", "body_start", "body_end", "items", "closed")

    def __init__(self, doc: Document, start: int, body_start: int) -> None:
        self.doc = doc
        self.start = start
        self.end = body_start
        self.body_start = body_start
        self.body_end = body_start
        self.items: list[Item] = []
        self.closed = True

    @property
    def raw(self) -> str:
        return self.doc.text[self.start : self.end]

    @property
    def body(self) -> str:
        """The source between the braces."""

        return self.doc.text[self.body_start : self.body_end]

    # ── queries ────────────────────────────────────────────────

    def assignments(self, key: str | None = None, *, fold: bool = False) -> Iterator[Assignment]:
        """Yield ``key OP value`` items, optionally only those with ``key``.

        ``fold`` compares keys without regard to case, the way the game reads
        effect and trigger names.
        """

        if key is None:
            for item in self.items:
                if type(item) is Assignment:
                    yield item
            return
        wanted = key.lower() if fold else key
        for item in self.items:
            if type(item) is Assignment:
                name = item.key.value
                if (name.lower() if fold else name) == wanted:
                    yield item

    def first(self, key: str, *, fold: bool = False) -> Assignment | None:
        """Return the first ``key = ...`` item, or ``None``."""

        return next(self.assignments(key, fold=fold), None)

    def scalar(self, key: str, default: str | None = None, *, fold: bool = False) -> str | None:
        """Return the value of the first ``key = scalar`` item."""

        for item in self.assignments(key, fold=fold):
            if type(item.value) is Scalar and item.op == "=":
                return item.value.value
        return default

    def block(self, key: str, *, fold: bool = False) -> Block | None:
        """Return the block of the first ``key = { ... }`` item."""

        for item in self.assignments(key, fold=fold):
            value = item.value
            if type(value) is Block:
                return value
            if type(value) is Tagged:
                return value.block
        return None

    def blocks(self, key: str, *, fold: bool = False) -> list[Block]:
        """Return the block of every ``key = { ... }`` item."""

        found: list[Block] = []
        for item in self.assignments(key, fold=fold):
            value = item.value
            if type(value) is Block:
                found.append(value)
            elif type(value) is Tagged:
                found.append(value.block)
        return found

    def values(self) -> list[Scalar]:
        """Return the bare scalars of a list block such as ``{ 1 2 3 }``."""

        return [item for item in self.items if type(item) is Scalar]

    def walk(self) -> Iterator[tuple[Assignment, tuple[Assignment, ...]]]:
        """Yield every assignment at any depth with the chain of assignments around it.

        Assignments come in source order: an assignment, then everything
        inside its block, then the next one.
        """

        stack: list[tuple[Iterator[Item], tuple[Assignment, ...]]] = [(iter(self.items), ())]
        while stack:
            pending, parents = stack[-1]
            item = next(pending, None)
            if item is None:
                stack.pop()
                continue
            if type(item) is Assignment:
                yield item, parents
                value = item.value
                if type(value) is Block:
                    stack.append((iter(value.items), (*parents, item)))
                elif type(value) is Tagged:
                    stack.append((iter(value.block.items), (*parents, item)))
            elif type(item) is Block:
                stack.append((iter(item.items), parents))
            elif type(item) is Tagged:
                stack.append((iter(item.block.items), parents))

    def __repr__(self) -> str:
        return f"Block({len(self.items)} items)"


class Tagged:
    """A typed block such as ``rgb { 255 0 0 }``."""

    __slots__ = ("doc", "tag", "block")

    def __init__(self, doc: Document, tag: Scalar, block: Block) -> None:
        self.doc = doc
        self.tag = tag
        self.block = block

    @property
    def start(self) -> int:
        return self.tag.start

    @property
    def end(self) -> int:
        return self.block.end

    @property
    def raw(self) -> str:
        return self.doc.text[self.tag.start : self.block.end]

    def __repr__(self) -> str:
        return f"Tagged({self.tag.raw!r})"


Value = Scalar | Block | Tagged


class Assignment:
    """``key OP value``."""

    __slots__ = ("doc", "key", "op_start", "op_end", "value")

    def __init__(
        self, doc: Document, key: Scalar, op_start: int, op_end: int, value: Value
    ) -> None:
        self.doc = doc
        self.key = key
        self.op_start = op_start
        self.op_end = op_end
        self.value = value

    @property
    def start(self) -> int:
        return self.key.start

    @property
    def end(self) -> int:
        return self.value.end

    @property
    def op(self) -> str:
        return self.doc.text[self.op_start : self.op_end]

    @property
    def raw(self) -> str:
        return self.doc.text[self.key.start : self.value.end]

    @property
    def name(self) -> str:
        """The key with quotes removed."""

        return self.key.value

    @property
    def scalar(self) -> str | None:
        """The value if it is a scalar, else ``None``."""

        value = self.value
        return value.value if type(value) is Scalar else None

    @property
    def block(self) -> Block | None:
        """The block if the value is ``{ ... }`` or ``tag { ... }``, else ``None``."""

        value = self.value
        if type(value) is Block:
            return value
        if type(value) is Tagged:
            return value.block
        return None

    def __repr__(self) -> str:
        return f"Assignment({self.key.raw} {self.op} ...)"


Item = Assignment | Scalar | Block | Tagged


class Document:
    """A parsed script file: the source text plus its syntax tree."""

    __slots__ = ("text", "root", "comments", "problems", "_line_starts")

    def __init__(self, text: str) -> None:
        self.text = text
        self.comments: list[tuple[int, int]] = []
        self.problems: list[Problem] = []
        self._line_starts: list[int] | None = None
        self.root = Block(self, 0, 0)
        _parse_into(self)

    def line_col(self, offset: int) -> tuple[int, int]:
        """Return the 1-based line and column of a source offset."""

        starts = self._line_starts
        if starts is None:
            starts = [0]
            find = self.text.find
            position = find("\n")
            while position >= 0:
                starts.append(position + 1)
                position = find("\n", position + 1)
            self._line_starts = starts
        line = bisect_right(starts, offset)
        return line, offset - starts[line - 1] + 1

    def in_comment(self, offset: int) -> bool:
        """Return whether a source offset lies inside a comment."""

        comments = self.comments
        index = bisect_right(comments, (offset, len(self.text) + 1)) - 1
        return index >= 0 and comments[index][0] <= offset < comments[index][1]

    def without_comments(self) -> str:
        """Return the text with every comment replaced by nothing.

        Line breaks are kept, so line numbers still match the source.
        """

        if not self.comments:
            return self.text
        parts: list[str] = []
        position = 0
        for start, end in self.comments:
            parts.append(self.text[position:start])
            position = end
        parts.append(self.text[position:])
        return "".join(parts)


def _parse_into(doc: Document) -> None:
    text = doc.text
    length = len(text)
    comments = doc.comments
    problems = doc.problems
    match = _TOKEN_RE.match

    root = doc.root
    stack: list[Block] = [root]
    block = root
    items = root.items
    # An assignment that has its key and operator but not yet its value, per
    # open block: (key, op_start, op_end) or None.
    pending: tuple[Scalar, int, int] | None = None
    position = 0

    def peek(after: int, wanted: int) -> re.Match[str] | None:
        """Return the next token after ``after`` if it is of kind ``wanted``.

        Comments in between are skipped, the way the game's reader drops them
        (``key # note`` then ``= value`` on the next line is one statement).
        They are recorded only if the token is taken.
        """

        skipped: list[tuple[int, int]] = []
        following = match(text, after)
        while following is not None and following.lastindex == _COMMENT:
            skipped.append(following.span(_COMMENT))
            following = match(text, following.end(_COMMENT))
        if following is None or following.lastindex != wanted:
            return None
        comments.extend(skipped)
        return following

    def open_tagged(scalar: Scalar, after: int) -> Tagged | None:
        """Start ``rgb { ... }`` if ``scalar`` is a type tag followed by a block."""

        nonlocal position, block, items
        if text[scalar.start : scalar.end].lower() not in _TAGS:
            return None
        following = peek(after, _OPEN)
        if following is None:
            return None
        tagged_block = Block(doc, following.start(_OPEN), following.end(_OPEN))
        position = following.end(_OPEN)
        stack.append(tagged_block)
        block = tagged_block
        items = tagged_block.items
        return Tagged(doc, scalar, tagged_block)

    while position < length:
        found = match(text, position)
        if found is None:
            # Only trailing whitespace is left.
            break
        kind = found.lastindex or 0
        start, end = found.span(kind)
        position = end

        if kind == _COMMENT:
            comments.append((start, end))
            continue

        if kind == _WORD or kind == _STRING:
            quoted = kind == _STRING
            if quoted and (end - start < 2 or text[end - 1] != '"' or _escaped(text, end - 1)):
                problems.append(Problem(start, "unterminated_string", "Unterminated quoted string"))
            scalar = Scalar(doc, start, end, quoted)
            if pending is not None:
                key, op_start, op_end = pending
                pending = None
                # ``key = rgb {``: the block that follows is the value.
                owner = items
                tagged = None if quoted else open_tagged(scalar, end)
                owner.append(Assignment(doc, key, op_start, op_end, tagged or scalar))
                continue
            following = peek(end, _OP)
            if following is not None:
                pending = (scalar, following.start(_OP), following.end(_OP))
                position = following.end(_OP)
                continue
            # A bare ``rgb { ... }`` in a list is one value too.
            owner = items
            tagged = None if quoted else open_tagged(scalar, end)
            owner.append(tagged or scalar)
            continue

        if kind == _OPEN:
            child = Block(doc, start, end)
            if pending is not None:
                key, op_start, op_end = pending
                pending = None
                items.append(Assignment(doc, key, op_start, op_end, child))
            else:
                items.append(child)
            stack.append(child)
            block = child
            items = child.items
            continue

        if kind == _CLOSE:
            if pending is not None:
                problems.append(Problem(pending[0].start, "missing_value", f"Missing value for {pending[0].raw}"))
                pending = None
            if len(stack) == 1:
                problems.append(Problem(start, "stray_close", "Unexpected closing brace"))
                continue
            block.body_end = start
            block.end = end
            stack.pop()
            block = stack[-1]
            items = block.items
            continue

        # A stray operator: ``= value`` with no key, or ``key = = value``.
        if pending is not None:
            problems.append(Problem(pending[0].start, "missing_value", f"Missing value for {pending[0].raw}"))
            pending = None
        problems.append(Problem(start, "stray_operator", f"Unexpected operator {text[start:end]}"))

    if pending is not None:
        problems.append(Problem(pending[0].start, "missing_value", f"Missing value for {pending[0].raw}"))
    # Like the game, close anything still open at the end of the file.
    while len(stack) > 1:
        open_block = stack.pop()
        open_block.body_end = length
        open_block.end = length
        open_block.closed = False
        problems.append(Problem(open_block.start, "unclosed_block", "Unclosed block"))
    root.body_end = length
    root.end = length


def _escaped(text: str, index: int) -> bool:
    """Return whether the character at ``index`` is preceded by an odd run of backslashes."""

    count = 0
    index -= 1
    while index >= 0 and text[index] == "\\":
        count += 1
        index -= 1
    return count % 2 == 1


@lru_cache(maxsize=8)
def _cached_parse(text: str) -> Document:
    return Document(text)


def parse(text: str) -> Document:
    """Parse script text. Never raises: see :attr:`Document.problems`.

    One edit usually asks several questions about the same file, so the most
    recent large texts are cached. A :class:`Document` is never modified.
    """

    if len(text) < 4096:
        # Cheap to parse again, and must not push whole files out of the cache.
        return Document(text)
    return _cached_parse(text)


def require_well_formed(text: str, source: str | None = None) -> Document:
    """Parse ``text`` and raise :class:`ParseError` if anything had to be repaired."""

    doc = parse(text)
    if doc.problems:
        problem = doc.problems[0]
        line, col = doc.line_col(problem.offset)
        where = f" in {source}" if source else ""
        raise ParseError(f"{problem.message} at line {line}, column {col}{where}")
    return doc


def require_statement(statement: str, source: str | None = None) -> str:
    """Return ``statement`` if it is exactly one well-formed statement.

    A writer that puts a value after ``key =`` uses this for values it does
    not quote itself: ``owner = GER`` is one statement, ``owner = a b`` is two
    and ``owner = } x = 1 {`` is not well formed. Both would land in the
    script around the statement. Raises :class:`ParseError` otherwise.
    """

    doc = require_well_formed(statement, source)
    if len(doc.root.items) != 1:
        where = f" in {source}" if source else ""
        raise ParseError(
            f"Expected one statement, found {len(doc.root.items)}{where}: {statement.strip()[:80]!r}"
        )
    return statement


def _is_one_scalar(text: str) -> bool:
    """Whether ``text`` is exactly one bare word or one quoted string."""

    if not text:
        return False
    doc = parse(text)
    items = doc.root.items
    return (
        not doc.problems
        and not doc.comments
        and len(items) == 1
        and type(items[0]) is Scalar
        and items[0].start == 0
        and items[0].end == len(text)
    )


def as_scalar(value: object) -> str:
    """Return script text that is exactly one scalar.

    ``value`` is script text a writer has already spelled (``GER``, ``0.5``,
    ``"two words"``). Text that is one bare word or one quoted string comes
    back as it is. Any other text (several words, a brace, a ``#``) comes
    back as one quoted string, so a value can never end early and spill into
    the script around it. This is a safety net, not a way to spell values: a
    writer that holds plain text from a model spells it with :func:`quote`,
    which also keeps quote characters and spaces at the ends of the text.
    """

    text = str(value)
    return text if _is_one_scalar(text) else quote(text, force=True)


def require_key(key: object, source: str | None = None) -> str:
    """Return ``key`` if it is exactly one bare word or quoted string.

    A key is the name of a statement or of a block (a modifier, an event
    type, an idea category). Raises :class:`ParseError` for anything else:
    ``a = 1 } x = 1 { b`` or ``x = 1 #`` as a key would become a statement of
    its own.
    """

    text = str(key)
    if not _is_one_scalar(text):
        where = f" in {source}" if source else ""
        raise ParseError(f"Expected one word as a key{where}, found {text[:80]!r}")
    return text


def require_entry(key: object, value: object, source: str | None = None) -> str:
    """Return ``key = value`` as exactly one statement.

    The key has to be one word (:func:`require_key`); the value is written as
    one scalar (:func:`as_scalar`).
    """

    return f"{require_key(key, source)} = {as_scalar(value)}"


def repair(text: str) -> str:
    """Return ``text`` spelled out the way the game reads it.

    Blocks still open at the end of the file get their closing braces and stray
    closing braces are removed. Both changes keep the meaning the game gives
    the file. Other problems (a missing value, an unterminated string) cannot
    be repaired and are left as they are.
    """

    doc = parse(text)
    stray = sorted(problem.offset for problem in doc.problems if problem.kind == "stray_close")
    unclosed = sum(1 for problem in doc.problems if problem.kind == "unclosed_block")
    if not stray and not unclosed:
        return text
    parts: list[str] = []
    position = 0
    for offset in stray:
        parts.append(text[position:offset])
        position = offset + 1
        # ``1}b`` must not become the single word ``1b``.
        if 0 < offset and position < len(text):
            if text[offset - 1] not in _SPACES and text[position] not in _SPACES:
                parts.append(" ")
    parts.append(text[position:])
    repaired = "".join(parts)
    if unclosed:
        repaired = repaired.rstrip("\n") + "\n" + "}\n" * unclosed
    return repaired
