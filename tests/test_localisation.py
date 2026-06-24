from pathlib import Path

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


class TestSerializeLocalizationFile:
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
