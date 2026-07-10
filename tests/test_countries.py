from pathlib import Path

from hoi4.countries import read_country, write_all_country_files
from hoi4.types import Country, Leader

FIXTURES = Path(__file__).parent / "fixtures"


class TestReadCountry:
    def test_reads_tag(self):
        country = read_country(FIXTURES, "WST")
        assert country.tag == "WST"

    def test_reads_color(self):
        country = read_country(FIXTURES, "WST")
        assert country.color == (59, 130, 246)

    def test_reads_capital(self):
        country = read_country(FIXTURES, "WST")
        assert country.capital == 123

    def test_reads_popularities(self):
        country = read_country(FIXTURES, "WST")
        assert country.popularities["democratic"] == 60
        assert country.popularities["fascism"] == 20

    def test_reads_ruling_party(self):
        country = read_country(FIXTURES, "WST")
        assert country.ruling_party == "democratic"

    def test_reads_name_from_localisation(self):
        country = read_country(FIXTURES, "WST")
        assert country.name == "Westralia"

    def test_reads_adjective(self):
        country = read_country(FIXTURES, "WST")
        assert country.adjective == "Westralian"

    def test_reads_leader(self):
        country = read_country(FIXTURES, "WST")
        assert country.leader is not None
        assert country.leader.name == "John Westralia"
        assert country.leader.ideology == "conservatism"

    def test_nonexistent_country_returns_defaults(self):
        country = read_country(FIXTURES, "XYZ")
        assert country.tag == "XYZ"
        assert country.color == (128, 128, 128)
        assert country.capital == 1


class TestWriteCountry:
    def test_write_and_read_roundtrip(self, tmp_path):
        original = Country(
            tag="TST",
            name="Testland",
            adjective="Testish",
            color=(100, 200, 50),
            capital=42,
            ruling_party="fascism",
            popularities={"democratic": 10, "fascism": 70, "communism": 10, "neutrality": 10},
            leader=Leader(
                name="Test Leader", character_id="TST_leader_1", ideology="fascism_ideology"
            ),
            ideas=["test_idea"],
        )

        write_all_country_files(tmp_path, original)
        loaded = read_country(tmp_path, "TST")

        assert loaded.tag == "TST"
        assert loaded.color == (100, 200, 50)
        assert loaded.capital == 42
        assert loaded.ruling_party == "fascism"
        assert loaded.popularities["fascism"] == 70
        assert loaded.name == "Testland"
        assert loaded.adjective == "Testish"

    def test_creates_tag_file(self, tmp_path):
        country = Country(tag="NEW", name="Newland")
        write_all_country_files(tmp_path, country)
        tag_file = tmp_path / "common" / "country_tags" / "00_generated_tags.txt"
        assert tag_file.exists()
        assert "NEW" in tag_file.read_text()

    def test_creates_definition_file(self, tmp_path):
        country = Country(tag="NEW", name="Newland", color=(1, 2, 3))
        write_all_country_files(tmp_path, country)
        def_file = tmp_path / "common" / "countries" / "NEW.txt"
        assert def_file.exists()
        content = def_file.read_text()
        assert "color = { 1 2 3 }" in content

    def test_creates_history_file(self, tmp_path):
        country = Country(tag="NEW", name="Newland", capital=99)
        write_all_country_files(tmp_path, country)
        history_files = list((tmp_path / "history" / "countries").glob("NEW*.txt"))
        assert len(history_files) == 1
        assert "capital = 99" in history_files[0].read_text()

    def test_history_uses_recruit_character_not_set_country_leader(self, tmp_path):
        country = Country(
            tag="NEW",
            name="Newland",
            leader=Leader(name="Boss", character_id="NEW_leader_1"),
        )
        write_all_country_files(tmp_path, country)
        history_file = next((tmp_path / "history" / "countries").glob("NEW*.txt"))
        content = history_file.read_text()
        assert "recruit_character = NEW_leader_1" in content
        assert "set_country_leader" not in content

    def test_creates_localisation_file(self, tmp_path):
        country = Country(tag="NEW", name="Newland", adjective="Newlandish")
        write_all_country_files(tmp_path, country)
        loc_file = tmp_path / "localisation" / "english" / "NEW_country_l_english.yml"
        assert loc_file.exists()
        content = loc_file.read_text()
        assert "Newland" in content
        assert "Newlandish" in content

    def test_creates_character_file(self, tmp_path):
        country = Country(
            tag="NEW",
            name="Newland",
            leader=Leader(name="Boss", character_id="NEW_leader_1"),
        )
        write_all_country_files(tmp_path, country)
        char_file = tmp_path / "common" / "characters" / "NEW_characters.txt"
        assert char_file.exists()
        content = char_file.read_text()
        assert "Boss" in content
        assert "NEW_leader_1" in content
