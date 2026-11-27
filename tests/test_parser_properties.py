from __future__ import annotations

import re

from _syntax_checks import assert_covers_all_text, assert_spans_nest
from hypothesis import given, strategies as st

from hoi4 import syntax
from hoi4.parser import PdxNode, parse_pdx, serialize_pdx
from hoi4.patching import (
    assignment_spans,
    scalar_values_equivalent,
    set_scalar,
    top_level_assignments,
)
from hoi4.script import pdx_value
from hoi4.structured_patching import patch_scalar_mapping
from hoi4.textio import decode_text, encode_text

identifier = st.from_regex(r"[a-z][a-z0-9_]{0,12}", fullmatch=True)
numeric_key = st.integers(min_value=0, max_value=9999).map(str)
date_key = st.tuples(
    st.integers(min_value=1, max_value=2099),
    st.integers(min_value=1, max_value=12),
    st.integers(min_value=1, max_value=28),
).map(lambda parts: ".".join(map(str, parts)))
key = st.one_of(identifier, numeric_key, date_key)
scalar = st.one_of(
    st.integers(min_value=-100_000, max_value=100_000).map(str),
    identifier,
    st.text(
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu", "Nd"),
            whitelist_characters=" _-",
        ),
        min_size=1,
        max_size=20,
    ).map(lambda value: '"' + value.replace('"', '\\"') + '"'),
)


@given(st.text(alphabet='abc012 _-{}#"\\=<>!\t', max_size=30))
def test_generated_scalar_values_round_trip(value: str) -> None:
    assert parse_pdx(f"name = {pdx_value(value)}").find("name").value == value
    source = serialize_pdx(PdxNode(key="name", value=value))
    assert parse_pdx(source).find("name").value == value


@st.composite
def paradox_script(draw: st.DrawFn, depth: int = 0) -> str:
    count = draw(st.integers(min_value=1, max_value=4))
    lines: list[str] = []
    for _ in range(count):
        item_key = draw(key)
        make_block = depth < 3 and draw(st.booleans())
        if make_block:
            body = draw(paradox_script(depth + 1))
            indented = "\n".join(f"\t{line}" for line in body.splitlines())
            lines.append(f"{item_key} = {{\n{indented}\n}}")
        else:
            lines.append(f"{item_key} = {draw(scalar)}")
    return "\n".join(lines) + "\n"


@given(paradox_script())
def test_parse_serialize_parse_equivalence(source: str) -> None:
    parsed = parse_pdx(source)
    assert parse_pdx(serialize_pdx(parsed)) == parsed


@given(st.lists(key, min_size=1, max_size=20, unique=True))
def test_numeric_and_date_keys_are_scanned(keys: list[str]) -> None:
    source = "".join(f"{item_key} = value\n" for item_key in keys)
    assert [span.key for span in top_level_assignments(source)] == keys


@given(
    st.lists(
        st.tuples(key, scalar),
        min_size=1,
        max_size=15,
        unique_by=lambda pair: pair[0],
    )
)
def test_noop_scalar_patch_is_source_preserving(values: list[tuple[str, str]]) -> None:
    source = "".join(
        f"# keep {index}\n{item_key}  =  {value}\n"
        for index, (item_key, value) in enumerate(values)
    )
    mapping = {
        item_key: value.strip('"') if value.startswith('"') else value
        for item_key, value in values
    }
    assert patch_scalar_mapping(source, mapping) == source


# ── realistic and hostile Paradox script ────────────────────────
#
# ``paradox_script`` above only makes clean ``key = value`` lines. The
# generators below make what real files contain: comments anywhere (also
# between a key, its operator and its value), quoted keys and strings holding
# braces, ``#`` and ``=``, every operator, typed and nested anonymous blocks,
# empty blocks, tabs, spaces and Windows line endings. ``hostile_script`` then
# breaks that text on purpose.

OPERATORS = ("=", "<", ">", "<=", ">=", "!=", "==", "?=")
# Fixed pools keep generation cheap (one draw per token); the free-text
# strategies mixed in below still reach arbitrary characters.
_SPACES = [" ", "  ", "\t", "\n", "\r\n", "\n\t", " \t ", "\n\n", "\r\n\t\t"]
_COMMENTS = ["# note\n", '#{ "=} #\n', "#\n", "# a = { b }\r\n", "#\u00e9\u4e2d \t\n", "##\n"]
_SPACE = st.sampled_from(_SPACES)
_comment = st.one_of(
    st.sampled_from(_COMMENTS),
    st.text(
        alphabet=st.characters(blacklist_characters="\n", blacklist_categories=("Cs",)),
        max_size=10,
    ).map(lambda body: "#" + body + "\n"),
)
# Between two tokens: whitespace, sometimes a comment.
_gap = st.sampled_from(_SPACES * 3 + [" " + c for c in _COMMENTS])
_tight_or_gap = st.sampled_from([""] * 6 + _SPACES * 3 + [" " + c for c in _COMMENTS])
_WORDS = [
    "a", "b_c", "GER", "yes", "no", "0", "-12", "0.5", "-1.25", "100000", "1936.1.1",
    "foo?0", "var:x", "ROOT.FROM", "event_target:t", "@cost", "@[ 1 + 2 ]", "@[x*2]",
    "rgb", "hsv", "RGB", "x.y.z", "gfx/a.dds", "\u00e9t\u00e9", "\u4e2d",
]
_word = st.one_of(st.sampled_from(_WORDS), identifier)
_STRINGS = ['""', '"a b"', '"{"', '"}"', '"#"', '"a = b"', '"x\\"y"', '"\\\\"', '"two\nlines"',
            '"{ #= }"', '"\t"', '"rgb { 1 }"']
_string = st.one_of(
    st.sampled_from(_STRINGS),
    st.text(alphabet=st.sampled_from(list('ab {}#="\\\n')), max_size=8).map(
        lambda value: syntax.quote(value, force=True)
    ),
)
_scalar_value = st.one_of(_word, _string)
_KEYS = ["a", "id", "cost", "1", "512", "1936.1.1", "@var", '"quoted key"', '"k{"', '""',
         "if", "IF", "rgb", "x.y"]
_key = st.one_of(st.sampled_from(_KEYS), identifier)
_TAG = st.sampled_from(["rgb", "hsv", "HSV", "hsv360"])


@st.composite
def _items(draw: st.DrawFn, depth: int) -> str:
    """A run of statements: the body of a block or of a whole file."""

    parts: list[str] = []
    for _ in range(draw(st.integers(min_value=0, max_value=4))):
        choice = draw(st.integers(min_value=0, max_value=9 if depth < 3 else 3))
        if choice == 0:
            parts.append(draw(_comment))
        elif choice in (1, 2):
            op = draw(st.sampled_from(OPERATORS)) if choice == 2 else "="
            parts.append(draw(_key) + draw(_tight_or_gap) + op + draw(_tight_or_gap)
                         + draw(_scalar_value))
        elif choice == 3:
            parts.append(draw(_scalar_value))  # a bare list value
        elif choice in (4, 5, 6):
            parts.append(draw(_key) + draw(_tight_or_gap) + "=" + draw(_tight_or_gap)
                         + _braced(draw, depth))
        elif choice == 7:
            parts.append(draw(_key) + draw(_gap) + "=" + draw(_gap) + draw(_TAG)
                         + draw(_gap) + _braced(draw, depth))
        elif choice == 8:
            parts.append(_braced(draw, depth))  # nested anonymous list
        else:
            parts.append(draw(_key) + " = {}")
    out = draw(_SPACE) if draw(st.booleans()) else ""
    for part in parts:
        out += part + draw(_gap)
    return out


def _braced(draw: st.DrawFn, depth: int) -> str:
    return "{" + draw(_gap) + draw(_items(depth + 1)) + "}"


script_text = _items(0)
# Statements nested at most one level deep: enough for top-level edits.
shallow_script_text = _items(2)
_noise = st.one_of(
    st.sampled_from(["{", "}", '"', "=", "#", "\n", "<", "!", "?", "@[", "\\", "﻿",
                     "\xa0", "\f", "\r", "rgb {", "}}", "{{"]),
    st.characters(blacklist_categories=("Cs",)),
)


@st.composite
def hostile_script(draw: st.DrawFn) -> str:
    """Generated script broken at a few random places."""

    text = draw(script_text)
    for _ in range(draw(st.integers(min_value=1, max_value=4))):
        index = draw(st.integers(min_value=0, max_value=len(text)))
        if text and draw(st.booleans()):
            text = text[:index] + text[index + 1 :]  # drop a character
        else:
            text = text[:index] + draw(_noise) + text[index:]
    return text


any_script = st.one_of(script_text, hostile_script(), st.text(max_size=60))


@given(any_script)
def test_syntax_parse_never_raises_and_accounts_for_every_character(text: str) -> None:
    doc = syntax.parse(text)
    assert_covers_all_text(doc)
    assert_spans_nest(doc)
    for problem in doc.problems:
        assert 0 <= problem.offset <= len(text)
        assert problem.kind in {
            "unclosed_block", "stray_close", "missing_value", "stray_operator",
            "unterminated_string",
        }


@given(script_text)
def test_realistic_script_is_well_formed_and_round_trips(text: str) -> None:
    doc = syntax.parse(text)
    assert doc.problems == [], (text, doc.problems)
    assert syntax.repair(text) is text
    # A bare word with "!" is rendered quoted by serialize_pdx (the shared
    # quoting rule), which changes the node's ``quoted`` flag. The game reads
    # both spellings the same, so only the strict comparison skips those.
    if "!" in text:
        return
    parsed = parse_pdx(text)
    rendered = serialize_pdx(parsed)
    assert parse_pdx(rendered) == parsed, rendered


_brace_damage = st.lists(
    st.tuples(st.integers(min_value=0, max_value=10_000), st.sampled_from(["{", "}", ""])),
    min_size=1,
    max_size=4,
)


@given(st.one_of(shallow_script_text, st.text(alphabet="{} a=1\n#\"", max_size=40)), _brace_damage)
def test_repair_makes_brace_damage_well_formed(text: str, damage: list[tuple[int, str]]) -> None:
    for position, brace in damage:
        index = position % (len(text) + 1)
        if brace:
            text = text[:index] + brace + text[index:]
        elif index < len(text) and text[index] in "{}":
            text = text[:index] + text[index + 1 :]
    kinds = {problem.kind for problem in syntax.parse(text).problems}
    if not kinds:
        assert syntax.repair(text) is text
    elif kinds <= {"unclosed_block", "stray_close"}:
        repaired = syntax.repair(text)
        assert syntax.parse(repaired).problems == [], repaired
        assert syntax.repair(repaired) is repaired


@given(shallow_script_text, st.data())
def test_set_scalar_changes_only_the_target_value(text: str, data: st.DataObject) -> None:
    candidates = [
        item.name for item in syntax.parse(text).root.assignments()
        if item.op == "=" and item.scalar is not None
    ]
    if not candidates:
        return
    target = data.draw(st.sampled_from(candidates))
    first = assignment_spans(text, target)[0]
    if first.is_block:
        return
    new = data.draw(st.one_of(_word, _string))
    result = set_scalar(text, target, new)
    if scalar_values_equivalent(text[first.value_start : first.value_end], new):
        assert result is text
    else:
        assert result == text[: first.value_start] + new + text[first.value_end :]
        assert syntax.parse(result).problems == []


@given(st.binary(max_size=200))
def test_textio_round_trips_arbitrary_bytes(data: bytes) -> None:
    assert encode_text(decode_text(data), like=data) == data


_line_content = st.text(
    alphabet=st.sampled_from([*"ab =#{}\t", "\f", "\v", "\x1c", "\x85", "é", "€",
                                                   " ", "﻿"]),
    max_size=8,
)
_RAW_LINE = re.compile(rb"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+")


@given(
    st.lists(st.tuples(_line_content, st.sampled_from(["\n", "\r\n", "\r"])), min_size=1,
             max_size=12),
    st.booleans(),
    st.sampled_from(["utf-8", "cp1252"]),
    st.data(),
)
def test_editing_one_line_keeps_every_other_lines_bytes(
    lines: list[tuple[str, str]], last_unterminated: bool, encoding: str, data: st.DataObject
) -> None:
    if encoding == "cp1252":
        lines = [(content.encode("cp1252", "replace").decode("cp1252"), end)
                 for content, end in lines]
    if last_unterminated:
        lines[-1] = (lines[-1][0] or "x", "")
    raw = b"".join((content + end).encode(encoding) for content, end in lines)
    text = decode_text(raw)
    assert encode_text(text, like=raw) == raw
    # Count lines from the bytes: a generated "\r" before an empty "\n" line
    # is one Windows line ending.
    old_raw_lines = _RAW_LINE.findall(raw)
    index = data.draw(st.integers(min_value=0, max_value=len(old_raw_lines) - 1))
    new_content = "§edit§"  # unique, so the edit cannot look like a move
    text_lines = text.split("\n")
    text_lines[index] = new_content
    edited = encode_text("\n".join(text_lines), like=raw)
    new_raw_lines = _RAW_LINE.findall(edited)
    assert len(new_raw_lines) == len(old_raw_lines)
    for number, (old, new) in enumerate(zip(old_raw_lines, new_raw_lines, strict=True)):
        if number != index:
            assert new == old
        else:
            # The edited line keeps its own line ending too.
            assert new.rstrip(b"\r\n") != old.rstrip(b"\r\n") or new == old
            assert new[len(new.rstrip(b"\r\n")):] == old[len(old.rstrip(b"\r\n")):]
