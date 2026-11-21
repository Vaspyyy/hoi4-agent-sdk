"""
Paradox script tree for building and rewriting whole snippets.

:func:`parse_pdx` gives an editable :class:`PdxNode` tree and
:func:`serialize_pdx` writes one back. Both are thin views over the lossless
syntax tree in :mod:`hoi4.syntax`, which is the only code that tokenizes
script. Use that module (or :mod:`hoi4.patching`) to change a file in place:
serializing a ``PdxNode`` tree re-renders the text and so normalizes its
whitespace.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

from ._scalar import needs_quotes
from .syntax import (
    _TOKEN_RE,
    Assignment,
    Block,
    Document,
    ParseError,
    Scalar,
    Tagged,
    parse,
    require_well_formed,
    unquote,
)

__all__ = [
    "ParseError",
    "PdxNode",
    "Token",
    "TokenType",
    "extract_braced_block",
    "find_assignment_block",
    "iter_assignment_blocks",
    "parse_pdx",
    "serialize_pdx",
    "strip_comments",
    "tokenize",
]


class TokenType(Enum):
    STRING = auto()
    NUMBER = auto()
    IDENT = auto()
    LBRACE = auto()
    RBRACE = auto()
    EQUALS = auto()
    OPERATOR = auto()
    COMMENT = auto()
    EOF = auto()


@dataclass
class Token:
    type: TokenType
    value: str
    line: int = 0
    col: int = 0


_NUMBER_RE = re.compile(r"-?\d+(\.\d+)?")
_COMMENT_OR_STRING_RE = re.compile(r'"(?:[^"\\]|\\.)*"?|#[^\n]*', re.DOTALL)


def tokenize(text: str) -> list[Token]:
    """Return the tokens of ``text``, ending with an ``EOF`` token.

    Raises :class:`ParseError` for an unterminated quoted string.
    """

    tokens: list[Token] = []
    line = 1
    line_start = 0
    position = 0
    length = len(text)
    while position < length:
        found = _TOKEN_RE.match(text, position)
        if found is None:
            break
        kind = found.lastindex or 0
        start, end = found.span(kind)
        skipped = text.count("\n", position, start)
        if skipped:
            line += skipped
            line_start = text.rfind("\n", position, start) + 1
        col = start - line_start + 1
        raw = text[start:end]
        if kind == 1:
            tokens.append(Token(TokenType.COMMENT, raw, line, col))
        elif kind == 2:
            tokens.append(Token(TokenType.LBRACE, "{", line, col))
        elif kind == 3:
            tokens.append(Token(TokenType.RBRACE, "}", line, col))
        elif kind == 4:
            tokens.append(Token(TokenType.EQUALS if raw == "=" else TokenType.OPERATOR, raw, line, col))
        elif kind == 5:
            if len(raw) < 2 or not raw.endswith('"') or _ends_with_escaped_quote(raw):
                raise ParseError(f"Unterminated quoted string at line {line}, column {col}")
            tokens.append(Token(TokenType.STRING, unquote(raw), line, col))
            inner_breaks = raw.count("\n")
            if inner_breaks:
                line += inner_breaks
                line_start = start + raw.rfind("\n") + 1
        elif _NUMBER_RE.fullmatch(raw):
            tokens.append(Token(TokenType.NUMBER, raw, line, col))
        else:
            tokens.append(Token(TokenType.IDENT, raw, line, col))
        position = end
    tail_breaks = text.count("\n", position)
    if tail_breaks:
        line += tail_breaks
        line_start = text.rfind("\n") + 1
    tokens.append(Token(TokenType.EOF, "", line, length - line_start + 1))
    return tokens


def _ends_with_escaped_quote(raw: str) -> bool:
    backslashes = len(raw) - 1 - len(raw[:-1].rstrip("\\"))
    return backslashes % 2 == 1


@dataclass
class PdxNode:
    """One statement of a script tree.

    * ``key = value``: ``key`` and ``value`` are set.
    * ``key = { ... }``: ``key`` is set and ``children`` holds the body.
      ``tag`` is ``"rgb"`` or ``"hsv"`` for a typed block (``color = rgb { ... }``).
    * a bare list value (``1`` in ``{ 1 2 3 }``): only ``value`` is set.
    * a nested list (``{ 1 2 }`` in ``{ { 1 2 } { 3 4 } }``): ``block`` is true
      and ``key`` is ``None``.
    * a comment: ``is_comment`` is true and ``value`` is its text.
    """

    key: Optional[str] = None
    value: Optional[str] = None
    children: list["PdxNode"] = field(default_factory=list)
    operator: str = "="
    is_comment: bool = False
    block: bool = False
    quoted: bool = False
    key_quoted: bool = False
    tag: Optional[str] = None

    def is_block(self) -> bool:
        return self.block or bool(self.children) or (self.key is not None and self.value is None)

    def is_assignment(self) -> bool:
        return self.value is not None and not self.is_block()

    def is_bare(self) -> bool:
        return self.key is None and self.value is not None

    def find(self, key: str) -> Optional["PdxNode"]:
        for child in self.children:
            if child.key == key:
                return child
        return None

    def find_all(self, key: str) -> list["PdxNode"]:
        return [c for c in self.children if c.key == key]

    def get_value(self, key: str, default: str = "") -> str:
        node = self.find(key)
        if node and node.value is not None:
            return node.value
        return default

    def get_int(self, key: str, default: int = 0) -> int:
        v = self.get_value(key)
        try:
            return int(v)
        except ValueError:
            return default

    def get_float(self, key: str, default: float = 0.0) -> float:
        v = self.get_value(key)
        try:
            return float(v)
        except ValueError:
            return default

    def get_block(self, key: str) -> Optional["PdxNode"]:
        node = self.find(key)
        if node and node.is_block():
            return node
        return None

    def set_value(self, key: str, value: str) -> None:
        """Set ``key = value``, replacing a block of the same key by the scalar."""

        for child in self.children:
            if child.key == key:
                child.value = value
                child.children = []
                child.block = False
                child.tag = None
                child.quoted = False
                return
        self.children.append(PdxNode(key=key, value=value))

    def remove(self, key: str) -> None:
        self.children = [c for c in self.children if c.key != key]

    def add_child(self, node: "PdxNode") -> None:
        self.children.append(node)


def parse_pdx(text: str, *, strict: bool = True) -> PdxNode:
    """Parse script into a :class:`PdxNode` tree.

    With ``strict`` (the default) malformed input raises :class:`ParseError`.
    With ``strict=False`` the text is read the way the game reads it: a block
    still open at the end of the file is closed there and a stray closing
    brace is skipped.
    """

    doc = require_well_formed(text) if strict else parse(text)
    return _TreeBuilder(doc).build()


class _TreeBuilder:
    def __init__(self, doc: Document) -> None:
        self.doc = doc
        self.comments = doc.comments
        self.next_comment = 0

    def build(self) -> PdxNode:
        root = PdxNode()
        root.children = self._children(self.doc.root)
        self._take_comments(len(self.doc.text) + 1, root.children)
        return root

    def _take_comments(self, before: int, into: list[PdxNode]) -> None:
        comments = self.comments
        index = self.next_comment
        text = self.doc.text
        while index < len(comments) and comments[index][0] < before:
            start, end = comments[index]
            into.append(PdxNode(value=text[start:end], is_comment=True))
            index += 1
        self.next_comment = index

    def _children(self, block: Block) -> list[PdxNode]:
        nodes: list[PdxNode] = []
        for item in block.items:
            if isinstance(item, Assignment):
                key = item.key
                value = item.value
                if isinstance(value, Scalar):
                    self._take_comments(value.end, nodes)
                    nodes.append(
                        PdxNode(
                            key=key.value,
                            value=value.value,
                            operator=item.op,
                            quoted=value.quoted,
                            key_quoted=key.quoted,
                        )
                    )
                    continue
                tag = value.tag.raw if isinstance(value, Tagged) else None
                body = value.block if isinstance(value, Tagged) else value
                self._take_comments(body.start, nodes)
                node = PdxNode(
                    key=key.value, operator=item.op, block=True, key_quoted=key.quoted, tag=tag
                )
                node.children = self._children(body)
                self._take_comments(body.end, node.children)
                nodes.append(node)
            elif isinstance(item, Scalar):
                self._take_comments(item.start, nodes)
                nodes.append(PdxNode(value=item.value, quoted=item.quoted))
            else:
                nested = item.block if isinstance(item, Tagged) else item
                self._take_comments(nested.start, nodes)
                node = PdxNode(block=True)
                node.children = self._children(nested)
                self._take_comments(nested.end, node.children)
                nodes.append(node)
        return nodes


def strip_comments(text: str) -> str:
    """Return text with line comments removed, preserving quoted strings."""

    if "#" not in text:
        return text
    return _COMMENT_OR_STRING_RE.sub(
        lambda found: found.group() if found.group().startswith('"') else "", text
    )


def find_assignment_block(text: str, key: str, start: int = 0) -> tuple[str, int, int] | None:
    """Find the first ``key = { ... }`` at or after ``start``, at any depth.

    Comments and quoted strings are never mistaken for script. Returns
    ``(block_content, assignment_start, assignment_end)`` where
    ``assignment_end`` is the character after the closing brace.
    """

    best: Assignment | None = None
    for item, _ in parse(text).root.walk():
        if (
            item.start >= start
            and type(item.value) is Block
            and item.key.value == key
            and item.op == "="
            and (best is None or item.start < best.start)
        ):
            best = item
    if best is None:
        return None
    block = best.value
    assert type(block) is Block
    return block.body, best.start, block.end


def iter_assignment_blocks(text: str, key: str) -> list[tuple[str, int, int]]:
    """Return all ``key = { ... }`` blocks outside comments and strings.

    Blocks are found at any depth, in source order. A block nested inside
    another match is part of that match and is not returned again.
    """

    out: list[tuple[str, int, int]] = []
    stack: list[Block] = [parse(text).root]
    while stack:
        block = stack.pop()
        nested: list[Block] = []
        for item in block.items:
            if type(item) is Assignment:
                value = item.value
                if type(value) is Block:
                    if item.key.value == key and item.op == "=":
                        out.append((value.body, item.start, value.end))
                    else:
                        nested.append(value)
                elif type(value) is Tagged:
                    nested.append(value.block)
            elif type(item) is Block:
                nested.append(item)
            elif type(item) is Tagged:
                nested.append(item.block)
        stack.extend(reversed(nested))
    out.sort(key=lambda found: found[1])
    return out


def _scalar_text(value: str, quoted: bool) -> str:
    if quoted or needs_quotes(value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def serialize_pdx(node: PdxNode, indent: int = 0) -> str:
    """Render a :class:`PdxNode` tree as script, one statement per line."""

    parts: list[str] = []
    _serialize(node, indent, parts)
    return "".join(parts)


def _serialize(node: PdxNode, indent: int, parts: list[str]) -> None:
    tab = "\t" * indent

    if node.is_comment:
        parts.append(f"{tab}{node.value}\n")
        return

    if node.is_block():
        if node.key is not None:
            key = _scalar_text(node.key, node.key_quoted)
            tag = f"{node.tag} " if node.tag else ""
            parts.append(f"{tab}{key} {node.operator} {tag}{{\n")
            for child in node.children:
                _serialize(child, indent + 1, parts)
            parts.append(f"{tab}}}\n")
        elif node.block:
            parts.append(f"{tab}{{\n")
            for child in node.children:
                _serialize(child, indent + 1, parts)
            parts.append(f"{tab}}}\n")
        else:
            for child in node.children:
                _serialize(child, indent, parts)
    elif node.value is not None:
        value = _scalar_text(node.value, node.quoted)
        if node.key is not None:
            key = _scalar_text(node.key, node.key_quoted)
            parts.append(f"{tab}{key} {node.operator} {value}\n")
        else:
            parts.append(f"{tab}{value}\n")


def extract_braced_block(text: str, start_index: int) -> tuple[str, int]:
    """Return the body of the block whose ``{`` sits just before ``start_index``.

    Returns ``(body, end)`` where ``end`` is the index after the closing brace.
    Like the game, a block that is still open at the end of the text ends there.
    """

    depth = 1
    i = start_index
    length = len(text)
    while i < length and depth > 0:
        c = text[i]
        if c == '"':
            i += 1
            while i < length and text[i] != '"':
                if text[i] == "\\":
                    i += 1
                i += 1
        elif c == "#":
            while i < length and text[i] != "\n":
                i += 1
            continue
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    if depth != 0:
        return text[start_index:], length
    return text[start_index : i - 1], i
