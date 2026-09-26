"""
Paradox Script Parser

Tokenizer and parser for Paradox script files (.txt, .gfx, .mod, .yml).
Handles nested braces, quoted strings, comments, and bare identifiers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

from ._scalar import needs_quotes


class ParseError(ValueError):
    """Raised when Paradox script cannot be parsed without losing structure."""


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


def tokenize(text: str) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    line = 1
    col = 1

    while i < len(text):
        c = text[i]

        if c == "\n":
            line += 1
            col = 1
            i += 1
            continue

        if c in " \t\r":
            col += 1
            i += 1
            continue

        if c == "#":
            start = i
            start_col = col
            while i < len(text) and text[i] != "\n":
                i += 1
                col += 1
            tokens.append(Token(TokenType.COMMENT, text[start:i], line, start_col))
            continue

        if c == "{":
            tokens.append(Token(TokenType.LBRACE, "{", line, col))
            i += 1
            col += 1
            continue

        if c == "}":
            tokens.append(Token(TokenType.RBRACE, "}", line, col))
            i += 1
            col += 1
            continue

        if c in "=<>!":
            start_col = col
            op = c
            i += 1
            col += 1
            if i < len(text) and text[i] == "=":
                op += "="
                i += 1
                col += 1
            if op == "=":
                tokens.append(Token(TokenType.EQUALS, op, line, start_col))
            else:
                tokens.append(Token(TokenType.OPERATOR, op, line, start_col))
            continue

        if c == '"':
            start_line = line
            start_col = col
            i += 1
            col += 1
            value: list[str] = []
            while i < len(text):
                if text[i] == '"':
                    break
                if text[i] == "\\" and i + 1 < len(text):
                    nxt = text[i + 1]
                    if nxt in {'"', "\\"}:
                        value.append(nxt)
                    else:
                        value.extend(("\\", nxt))
                    i += 2
                    col += 2
                    continue
                if text[i] == "\n":
                    line += 1
                    col = 0
                value.append(text[i])
                i += 1
                col += 1
            if i >= len(text):
                raise ParseError(
                    f"Unterminated quoted string at line {start_line}, column {start_col}"
                )
            tokens.append(Token(TokenType.STRING, "".join(value), start_line, start_col))
            i += 1
            col += 1
            continue

        start = i
        start_col = col
        while i < len(text) and text[i] not in ' \t\r\n{}=#"<>!':
            i += 1
            col += 1
        word = text[start:i]

        if re.match(r"^-?\d+(\.\d+)?$", word):
            tokens.append(Token(TokenType.NUMBER, word, line, start_col))
        else:
            tokens.append(Token(TokenType.IDENT, word, line, start_col))

    tokens.append(Token(TokenType.EOF, "", line, col))
    return tokens


@dataclass
class PdxNode:
    key: Optional[str] = None
    value: Optional[str] = None
    children: list["PdxNode"] = field(default_factory=list)
    operator: str = "="
    is_comment: bool = False
    block: bool = False
    quoted: bool = False

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
        for child in self.children:
            if child.key == key:
                child.value = value
                child.children = []
                return
        self.children.append(PdxNode(key=key, value=value))

    def remove(self, key: str) -> None:
        self.children = [c for c in self.children if c.key != key]

    def add_child(self, node: "PdxNode") -> None:
        self.children.append(node)


class PdxParser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0

    def current(self) -> Token:
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return Token(TokenType.EOF, "")

    def advance(self) -> Token:
        t = self.current()
        self.pos += 1
        return t

    def parse(self) -> PdxNode:
        root = PdxNode()
        root.children = self._parse_statements()
        return root

    def _parse_statements(self, end_token: TokenType = TokenType.EOF) -> list[PdxNode]:
        stmts: list[PdxNode] = []
        while self.current().type not in (end_token, TokenType.EOF):
            stmt = self._parse_statement()
            if stmt:
                stmts.append(stmt)
        if end_token != TokenType.EOF and self.current().type == TokenType.EOF:
            current = self.current()
            raise ParseError(f"Unclosed block before line {current.line}, column {current.col}")
        return stmts

    def _parse_statement(self) -> Optional[PdxNode]:
        tok = self.current()

        if tok.type == TokenType.COMMENT:
            self.advance()
            return PdxNode(value=tok.value, is_comment=True)

        if tok.type == TokenType.RBRACE:
            raise ParseError(f"Unexpected closing brace at line {tok.line}, column {tok.col}")

        if tok.type in (TokenType.IDENT, TokenType.STRING, TokenType.NUMBER):
            value = self.advance().value
            if self.current().type in (TokenType.EQUALS, TokenType.OPERATOR):
                op = self.advance().value
                node = self._parse_rhs(value)
                node.operator = op
                return node
            return PdxNode(key=None, value=value, quoted=tok.type == TokenType.STRING)

        if tok.type == TokenType.LBRACE:
            self.advance()
            children = self._parse_statements(TokenType.RBRACE)
            if self.current().type == TokenType.RBRACE:
                self.advance()
            node = PdxNode(block=True)
            node.children = children
            return node

        self.advance()
        return None

    def _parse_rhs(self, key: str) -> PdxNode:
        while self.current().type == TokenType.COMMENT:
            self.advance()
        tok = self.current()
        if tok.type == TokenType.LBRACE:
            self.advance()
            children = self._parse_statements(TokenType.RBRACE)
            if self.current().type == TokenType.RBRACE:
                self.advance()
            node = PdxNode(key=key, block=True)
            node.children = children
            return node
        if tok.type in (TokenType.IDENT, TokenType.STRING, TokenType.NUMBER):
            val = self.advance().value
            return PdxNode(key=key, value=val, quoted=tok.type == TokenType.STRING)
        raise ParseError(f"Expected a value for {key!r} at line {tok.line}, column {tok.col}")


def parse_pdx(text: str) -> PdxNode:
    tokens = tokenize(text)
    parser = PdxParser(tokens)
    return parser.parse()


def strip_comments(text: str) -> str:
    """Return text with line comments removed, preserving quoted strings."""
    out: list[str] = []
    in_quote = False
    i = 0
    while i < len(text):
        c = text[i]
        if c == "\\" and in_quote:
            out.append(c)
            i += 1
            if i < len(text):
                out.append(text[i])
        elif c == '"':
            in_quote = not in_quote
            out.append(c)
        elif c == "#" and not in_quote:
            while i < len(text) and text[i] != "\n":
                i += 1
            if i < len(text):
                out.append("\n")
            continue
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _match_key(text: str, pos: int, key: str) -> bool:
    end = pos + len(key)
    if text[pos:end] != key:
        return False
    before = text[pos - 1] if pos > 0 else " "
    after = text[end] if end < len(text) else " "
    ident = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_"
    return before not in ident and after not in ident


def find_assignment_block(text: str, key: str, start: int = 0) -> tuple[str, int, int] | None:
    """Find ``key = { ... }`` outside comments and strings.

    Returns ``(block_content, assignment_start, assignment_end)`` where
    ``assignment_end`` is the character after the closing brace.
    """
    i = start
    in_quote = False
    while i < len(text):
        c = text[i]
        if c == "\\" and in_quote:
            i += 2
            continue
        if c == '"':
            in_quote = not in_quote
            i += 1
            continue
        if c == "#" and not in_quote:
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        if not in_quote and _match_key(text, i, key):
            j = i + len(key)
            while j < len(text) and text[j].isspace():
                j += 1
            if j >= len(text) or text[j] != "=":
                i += 1
                continue
            j += 1
            while j < len(text) and text[j].isspace():
                j += 1
            if j >= len(text) or text[j] != "{":
                i += 1
                continue
            try:
                content, end = extract_braced_block(text, j + 1)
            except ValueError:
                return None
            return content, i, end
        i += 1
    return None


def iter_assignment_blocks(text: str, key: str) -> list[tuple[str, int, int]]:
    """Return all ``key = { ... }`` blocks outside comments and strings."""
    out: list[tuple[str, int, int]] = []
    pos = 0
    while True:
        match = find_assignment_block(text, key, pos)
        if match is None:
            break
        out.append(match)
        pos = match[2]
    return out


def serialize_pdx(node: PdxNode, indent: int = 0) -> str:
    parts: list[str] = []
    tab = "\t" * indent

    if node.is_comment:
        parts.append(f"{tab}{node.value}\n")
        return "".join(parts)

    if node.is_block():
        if node.key is not None:
            parts.append(f"{tab}{node.key} {node.operator} {{\n")
            for child in node.children:
                parts.append(serialize_pdx(child, indent + 1))
            parts.append(f"{tab}}}\n")
        else:
            for child in node.children:
                parts.append(serialize_pdx(child, indent))
    elif node.value is not None:
        if node.key is not None:
            v = node.value
            if node.quoted or needs_quotes(v):
                v = '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
            parts.append(f"{tab}{node.key} {node.operator} {v}\n")
        else:
            parts.append(f"{tab}{node.value}\n")

    return "".join(parts)


def extract_braced_block(text: str, start_index: int) -> tuple[str, int]:
    depth = 1
    i = start_index
    while i < len(text) and depth > 0:
        c = text[i]
        if c == '"':
            i += 1
            while i < len(text) and text[i] != '"':
                if text[i] == "\\":
                    i += 1
                i += 1
        elif c == "#":
            while i < len(text) and text[i] != "\n":
                i += 1
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    if depth != 0:
        raise ValueError("Unbalanced braces")
    return text[start_index : i - 1], i
