from hoi4.parser import parse_pdx, serialize_pdx, tokenize


class TestTokenizer:
    def test_simple_assignment(self):
        tokens = tokenize('key = value')
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
