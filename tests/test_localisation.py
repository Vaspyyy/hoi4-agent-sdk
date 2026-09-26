from pathlib import Path

import pytest

from hoi4.localisation import (
    parse_localization_file,
    serialize_localization_file,
)

FIXTURES = Path(__file__).parent / "fixtures"


class TestParseLocalizationFile:
    def test_parses_entries(self):
        entries = parse_localization_file(FIXTURES / "GER_focus_l_english.yml")
        assert entries["GER_anschluss"] == "Anschluss"
        assert entries["GER_anschluss_desc"] == "Unite with Austria"
        assert entries["GER_rhineland"] == "Rhineland"

    def test_ignores_header(self):
        entries = parse_localization_file(FIXTURES / "GER_focus_l_english.yml")
        assert "l_english" not in entries

    @pytest.mark.parametrize("header", ["l_english:", " l_english: # English", "l_german:"])
    def test_reads_prefixed_keys_but_not_headers(self, tmp_path, header):
        path = tmp_path / "test.yml"
        path.write_text(
            header + '\nl_custom_title:0 "Title"\n l_custom_desc: "Description"\n',
            encoding="utf-8-sig",
        )
        assert parse_localization_file(path) == {
            "l_custom_title": "Title",
            "l_custom_desc": "Description",
        }


class TestSerializeLocalizationFile:
    @pytest.mark.parametrize("newline", ["\n", "\r\n"])
    def test_prefixed_keys_preserve_original_bytes_and_duplicates(self, tmp_path, newline):
        original = newline.join(
            [
                "# translator note",
                "l_english: # language",
                ' l_custom_title:0 "Earlier" # shadowed',
                r' l_custom_title:1 "Title \"quoted\"\nC:\\mods" # keep',
                ' OTHER:0 "Original"',
                "",
            ]
        )
        path = tmp_path / "test_l_english.yml"
        raw = original.encode("utf-8-sig")
        path.write_bytes(raw)
        entries = parse_localization_file(path)
        assert entries["l_custom_title"] == 'Title "quoted"\nC:\\mods'
        assert serialize_localization_file(entries, original).encode("utf-8-sig") == raw

        entries["l_custom_title"] = "Updated"
        rendered = serialize_localization_file(entries, original)
        assert rendered == original.replace(r"Title \"quoted\"\nC:\\mods", "Updated")

    def test_produces_valid_yml(self):
        entries = {"KEY_A": "Value A", "KEY_B": "Value B"}
        result = serialize_localization_file(entries)
        assert result.startswith("l_english:")
        assert 'KEY_A:0 "Value A"' in result
        assert 'KEY_B:0 "Value B"' in result

    def test_sorted_keys(self):
        entries = {"Z_KEY": "Z", "A_KEY": "A", "M_KEY": "M"}
        result = serialize_localization_file(entries)
        lines = result.split("\n")
        keys = [line.split(":0")[0].strip() for line in lines if ":0" in line]
        assert keys == ["A_KEY", "M_KEY", "Z_KEY"]

    def test_real_line_break_round_trips_as_hoi4_escape(self, tmp_path):
        source_value = "First paragraph.\nSecond paragraph."
        result = serialize_localization_file({"EVENT_DESC": source_value})
        path = tmp_path / "event_l_english.yml"
        path.write_text(result, encoding="utf-8-sig")

        assert 'EVENT_DESC:0 "First paragraph.\\nSecond paragraph."' in result
        assert parse_localization_file(path)["EVENT_DESC"] == source_value

    def test_literal_backslash_n_remains_literal_text(self, tmp_path):
        source_value = r"Literal \n text"
        result = serialize_localization_file({"LITERAL": source_value})
        path = tmp_path / "literal_l_english.yml"
        path.write_text(result, encoding="utf-8-sig")

        assert r"Literal \\n text" in result
        assert parse_localization_file(path)["LITERAL"] == source_value
