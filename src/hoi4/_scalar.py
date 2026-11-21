"""Shared scalar quoting rules, independent of parsing and script builders."""

import re

# What the reader in ``hoi4.syntax`` takes for one bare word (its ``_TOKEN_RE``):
# no whitespace as the reader sees it (a byte-order mark counts), no brace,
# operator, ``#`` or quote. ``tests/test_scalar_values.py`` tries every
# character against the reader, so the two cannot drift apart unnoticed.
_BARE_WORD = re.compile(r'(?:[^ \t\r\n\f\v﻿{}=<>#"!?]|[!?](?!=))+')
# ``@[ ... ]`` is inline maths: the reader ends the word at the ``]``.
_INLINE_MATHS = re.compile(r"@\[[^\]\n]*\]")


def needs_quotes(value: str) -> bool:
    """Whether ``value`` must be quoted to be read back as one scalar."""

    if not value or any(ch.isspace() or ch in '{}#"=<>!' for ch in value):
        return True
    if _BARE_WORD.fullmatch(value) is None:
        return True
    maths = _INLINE_MATHS.match(value)
    return maths is not None and maths.end() != len(value)
