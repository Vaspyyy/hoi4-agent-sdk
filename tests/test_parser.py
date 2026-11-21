import pytest

from hoi4.parser import (
    ParseError,
    PdxNode,
    find_assignment_block,
    iter_assignment_blocks,
    parse_pdx,
    serialize_pdx,
    strip_comments,
    tokenize,
)


class TestTokenizer:
    def test_simple_assignment(self):
        tokens = tokenize("key = value")
        assert len(tokens) == 4
        assert tokens[0].value == "key"
        assert tokens[1].value == "="
        assert tokens[2].value == "value"

    def test_quoted_string(self):
        tokens = tokenize('key = "hello world"')
        assert tokens[2].value == "hello world"

    def test_number(self):
        tokens = tokenize("x = 42")
        assert tokens[2].value == "42"

    def test_braces(self):
        tokens = tokenize("block = { a = 1 }")
        assert tokens[2].value == "{"
        assert tokens[6].value == "}"

    def test_comment(self):
        tokens = tokenize("# this is a comment\nkey = value")
        assert tokens[0].value.startswith("#")


class TestParser:
    def test_parse_simple(self):
        root = parse_pdx("key = value")
        node = root.find("key")
        assert node is not None
        assert node.value == "value"

    def test_parse_block(self):
        root = parse_pdx("block = { x = 1 y = 2 }")
        block = root.get_block("block")
        assert block is not None
        assert block.get_int("x") == 1
        assert block.get_int("y") == 2

    def test_parse_nested(self):
        root = parse_pdx("outer = { inner = { x = 5 } }")
        outer = root.get_block("outer")
        inner = outer.get_block("inner")
        assert inner.get_int("x") == 5

    def test_roundtrip(self):
        text = "focus_tree = {\n\tid = test\n\tcountry_tag = GER\n}\n"
        root = parse_pdx(text)
        result = serialize_pdx(root)
        assert "id = test" in result
        assert "country_tag = GER" in result


# ── PdxNode on the syntax tree ──────────────────────────────────


def _round_trip(text: str) -> PdxNode:
    parsed = parse_pdx(text)
    rendered = serialize_pdx(parsed)
    assert parse_pdx(rendered) == parsed, rendered
    return parsed


class TestPdxNodeEditing:
    def test_set_value_on_a_block_makes_a_scalar(self):
        root = parse_pdx("a = { x = 1 } b = rgb { 1 2 3 }")
        root.set_value("a", "5")
        root.set_value("b", "red")
        node = root.find("a")
        assert node.is_assignment() and not node.is_block()
        assert node.children == [] and node.tag is None
        assert serialize_pdx(root) == "a = 5\nb = red\n"

    def test_set_value_appends_and_remove(self):
        root = parse_pdx("a = 1")
        root.set_value("b", "2")
        root.remove("a")
        assert serialize_pdx(root) == "b = 2\n"

    def test_set_value_clears_quoting(self):
        root = parse_pdx('a = "x y"')
        root.set_value("a", "z")
        assert serialize_pdx(root) == "a = z\n"

    def test_accessors(self):
        root = parse_pdx("i = 3 f = 1.5 s = text b = { } bad = x")
        assert root.get_int("i") == 3
        assert root.get_float("f") == 1.5
        assert root.get_int("bad", 7) == 7
        assert root.get_float("bad", 2.5) == 2.5
        assert root.get_value("missing", "d") == "d"
        assert root.get_block("b") is not None
        assert root.get_block("s") is None
        assert len(root.find_all("i")) == 1


class TestSerializeRoundTrip:
    @pytest.mark.parametrize(
        "text",
        [
            '"quoted key" = "quoted value"\n',
            'a = { "x y" plain "z" }\n',
            "points = { { 1 2 } { 3 4 } }\n",
            "color = rgb { 1 2 3 }\ncolor = HSV { 0.1 0.2 0.3 }\n",
            "colors = { rgb { 1 2 3 } hsv { 4 5 6 } }\n",
            "# head\na = 1 # tail\nb = { # inside\n\tc = 2\n}\n",
            "a = # between\n5\n",
            "a < 1 b > 2 c <= 3 d >= 4 e != 5 f == 6 g ?= 7\n",
            'a = "say \\"hi\\" \\\\"\n',
            'a = "two\nlines"\n',
            "@var = 5\nb = @var\nc = foo?0\n",
            "empty = { }\n",
        ],
    )
    def test_round_trip(self, text):
        _round_trip(text)

    def test_quoted_values_and_keys_stay_quoted(self):
        rendered = serialize_pdx(parse_pdx('"k" = "v" plain = word'))
        assert rendered == '"k" = "v"\nplain = word\n'

    def test_typed_block_keeps_its_tag(self):
        rendered = serialize_pdx(parse_pdx("color = rgb { 1 2 3 }"))
        assert rendered == "color = rgb {\n\t1\n\t2\n\t3\n}\n"

    def test_typed_block_in_a_list_keeps_its_tag(self):
        root = parse_pdx("colors = { rgb { 1 2 3 } }")
        (color,) = root.get_block("colors").children
        assert color.tag == "rgb" and color.key is None
        assert "rgb {" in serialize_pdx(root)

    def test_inline_maths_is_not_quoted(self):
        # Regression: ``@[ 1 + 2 ]`` was rendered as the string "@[ 1 + 2 ]".
        root = parse_pdx("cost = @[ 1 + 2 ]")
        assert root.get_value("cost") == "@[ 1 + 2 ]"
        assert serialize_pdx(root) == "cost = @[ 1 + 2 ]\n"

    def test_comments_are_kept_in_order(self):
        rendered = serialize_pdx(parse_pdx("# a\nx = 1 # b\n# c\n"))
        assert rendered == "# a\nx = 1\n# b\n# c\n"

    def test_nested_list_nodes(self):
        root = parse_pdx("p = { { 1 2 } }")
        (inner,) = root.get_block("p").children
        assert inner.block and inner.key is None
        assert [child.value for child in inner.children] == ["1", "2"]
        assert all(child.is_bare() for child in inner.children)


class TestStrictness:
    @pytest.mark.parametrize("text", ["a = {", "a = 1 }", "a =", "= 1", 'a = "open'])
    def test_strict_rejects_malformed(self, text):
        with pytest.raises(ParseError):
            parse_pdx(text)

    def test_lenient_reads_like_the_game(self):
        root = parse_pdx("a = { b = 1\n", strict=False)
        assert root.get_block("a").get_int("b") == 1
        root = parse_pdx("a = 1 } b = 2", strict=False)
        assert root.get_int("b") == 2

    def test_tokenize_rejects_unterminated_string(self):
        with pytest.raises(ParseError, match="line 2"):
            tokenize('a = 1\nb = "open')

    def test_tokenize_positions_and_types(self):
        tokens = tokenize('a = "x\ny" b >= 2.5\n# c')
        kinds = [(t.type.name, t.value, t.line, t.col) for t in tokens]
        assert kinds == [
            ("IDENT", "a", 1, 1),
            ("EQUALS", "=", 1, 3),
            ("STRING", "x\ny", 1, 5),
            ("IDENT", "b", 2, 4),
            ("OPERATOR", ">=", 2, 6),
            ("NUMBER", "2.5", 2, 9),
            ("COMMENT", "# c", 3, 1),
            ("EOF", "", 3, 4),
        ]


class TestSpanHelpers:
    def test_strip_comments_keeps_strings(self):
        assert strip_comments('a = "#x" # y\nb = 1') == 'a = "#x" \nb = 1'
        assert strip_comments("a = 1") == "a = 1"

    def test_find_assignment_block_skips_comments_and_strings(self):
        text = '# target = { no }\nname = "target = { no }"\nouter = { target = { yes = 1 } }'
        body, start, end = find_assignment_block(text, "target")
        assert body.strip() == "yes = 1"
        assert text[start:end] == "target = { yes = 1 }"
        assert find_assignment_block(text, "target", start + 1) is None
        assert find_assignment_block(text, "missing") is None

    def test_find_assignment_block_returns_the_first_in_source_order(self):
        text = "a = { t = { n = 1 } } t = { n = 2 }"
        body, _, _ = find_assignment_block(text, "t")
        assert body.strip() == "n = 1"

    def test_iter_assignment_blocks_in_source_order(self):
        text = "t = { t = { inner } } x = { t = { 2 } } t = rgb { 3 }"
        found = iter_assignment_blocks(text, "t")
        assert [text[s:e] for _, s, e in found] == ["t = { t = { inner } }", "t = { 2 }"]


def test_set_value_turns_a_comparison_into_an_assignment():
    node = parse_pdx("a > 1\n")
    node.set_value("a", "2")
    assert serialize_pdx(node) == "a = 2\n"


def test_require_statement_value_and_entry():
    from hoi4.syntax import ParseError, require_entry, require_statement, require_value

    assert require_statement("owner = GER") == "owner = GER"
    assert require_value('"two words"') == '"two words"'
    assert require_value("rgb { 1 2 3 }") == "rgb { 1 2 3 }"
    assert require_entry("stability_factor", 0.1) == "stability_factor = 0.1"
    for bad in ("a b", "} x = 1 {", "ok } x = 1 { y = 2", "", "a\nb = 2"):
        with pytest.raises(ParseError):
            require_value(bad)
    with pytest.raises(ParseError):
        require_entry("a = 1 } x = 1 { b", 1)
    with pytest.raises(ParseError):
        require_statement("a = 1 b = 2")
