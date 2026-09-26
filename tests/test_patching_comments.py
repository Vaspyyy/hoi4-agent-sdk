"""Comment boundaries must survive edits through the shared block patcher."""

import pytest

from hoi4 import parse_pdx
from hoi4.patching import assignment_spans, set_block, top_level_assignments


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("original", [" always = yes ", "\n    always = yes ", "\n    always = yes\n"])
@pytest.mark.parametrize(
    "body, keys",
    [
        ("# explanation\nalways = no", ["always"]),
        ("always = no # inline\ntag = GER", ["always", "tag"]),
        ("always = no\n# trailing", ["always"]),
        ("always = no # trailing", ["always"]),
        ('# comment only, including an unmatched " and }', []),
        ('name = "quoted # text" # real comment', ["name"]),
    ],
)
def test_comments_preserve_block_boundaries(newline, original, body, keys):
    prefix = "before = yes" + newline + "    trigger = {"
    suffix = "} # wrapper" + newline + "after = no" + newline
    source = prefix + original.replace("\n", newline) + suffix
    result = set_block(source, "trigger", body)
    assert result.startswith(prefix)
    assert result.endswith(suffix)
    assert [span.key for span in top_level_assignments(result)] == ["before", "trigger", "after"]
    trigger = assignment_spans(result, "trigger")[0]
    interior = result[trigger.body_start:trigger.body_end]
    assert [span.key for span in top_level_assignments(interior)] == keys
    assert [line.strip() for line in interior.splitlines() if line.strip()] == body.splitlines()
    assert parse_pdx(result).get_block("trigger") is not None
    if newline == "\r\n":
        assert "\n" not in result.replace("\r\n", "")
    assert set_block(result, "trigger", body) == result


@pytest.mark.parametrize(
    "body",
    [
        "always = no\ntag = GER",
        'name = "literal # text"\nalways = no',
        r'name = "escaped \"# text"' + "\nalways = no",
        r'name = "escaped backslash \\"' + "\nalways = no",
    ],
)
def test_comment_free_replacements_stay_compact(body):
    source = "trigger = { always = yes }\r\nafter = yes\r\n"
    expected = "trigger = { " + " ".join(body.splitlines()) + " }\r\nafter = yes\r\n"
    assert set_block(source, "trigger", body) == expected
    assert len(top_level_assignments(expected)) == 2


def test_semantically_unchanged_source_is_byte_identical():
    source = "trigger = {\r\n  # retained\r\n  always = yes }\r\n"
    assert set_block(source, "trigger", "always = yes") == source
    assert set_block(source, "trigger", "# new comment\nalways = yes") == source
