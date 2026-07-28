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

    def test_colors_file_overrides_definition_and_mod_overrides_vanilla(self, tmp_path):
        vanilla = tmp_path / "game"
        mod = tmp_path / "mod"
        for root in (vanilla, mod):
            (root / "common/country_tags").mkdir(parents=True)
            (root / "common/countries").mkdir(parents=True)
        (vanilla / "common/country_tags/tags.txt").write_text(
            'ABC = "countries/ABC.txt"\n', encoding="utf-8"
        )
        (vanilla / "common/countries/ABC.txt").write_text(
            "color = { 1 2 3 }\n", encoding="utf-8"
        )
        (vanilla / "common/countries/colors.txt").write_text(
            "ABC = { color = rgb { 4 5 6 } }\n", encoding="utf-8"
        )
        (mod / "common/country_tags/tags.txt").write_text(
            'ABC = "countries/ABC.txt"\n', encoding="utf-8"
        )
        (mod / "common/countries/ABC.txt").write_text(
            "graphical_culture = western_european_gfx\n", encoding="utf-8"
        )
        (mod / "common/countries/colors.txt").write_text(
            "ABC = { color = rgb { 7 8 9 } color_ui = rgb { 7 8 9 } }\n",
            encoding="utf-8",
        )

        assert read_country(mod, "ABC", vanilla).color == (7, 8, 9)

    def test_reads_initial_assigned_ideas(self, tmp_path):
        history = tmp_path / "history/countries/ABC - Test.txt"
        history.parent.mkdir(parents=True)
        history.write_text(
            "add_ideas = { first_idea second.idea }\nremove_ideas = first_idea\n",
            encoding="utf-8",
        )

        assert read_country(tmp_path, "ABC").ideas == ["second.idea"]

    def test_reads_capital(self):
        country = read_country(FIXTURES, "WST")
        assert country.capital == 123

    def test_reads_popularities(self):
        country = read_country(FIXTURES, "WST")
        assert country.popularities["democratic"] == 60
        assert country.popularities["fascism"] == 20

    def test_reads_custom_popularity_and_explicit_election_setting(self, tmp_path):
        history = tmp_path / "history/countries/ABC - Custom.txt"
        history.parent.mkdir(parents=True)
        history.write_text(
            """set_popularities = {
 democratic = 15
 futurism = 85
}
set_politics = {
 ruling_party = futurism
 elections_allowed = yes
}
""",
            encoding="utf-8",
        )

        country = read_country(tmp_path, "ABC")

        assert country.popularities == {"democratic": 15, "futurism": 85}
        assert country.ruling_party == "futurism"
        assert country.elections_allowed is True

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
        assert loaded.ideas == ["test_idea"]

    def test_custom_popularity_roundtrip_and_localisation(self, tmp_path):
        original = Country(
            tag="CUS",
            name="Customland",
            ruling_party="futurism",
            popularities={"futurism": 100},
            elections_allowed=True,
        )

        write_all_country_files(tmp_path, original)
        loaded = read_country(tmp_path, "CUS")

        assert loaded.popularities == {"futurism": 100}
        assert loaded.ruling_party == "futurism"
        assert loaded.elections_allowed is True
        localisation = (
            tmp_path / "localisation/english/CUS_country_l_english.yml"
        ).read_text(encoding="utf-8-sig")
        assert "CUS_futurism:0" in localisation

    def test_history_setup_fields_roundtrip(self, tmp_path):
        original = Country(
            tag="HST",
            name="Historyland",
            elections_allowed=False,
            stability=0.65,
            war_support=0.45,
            technologies={"infantry_weapons": 1, "tech_support": 2},
            oob="HST_1936",
        )

        write_all_country_files(tmp_path, original)
        loaded = read_country(tmp_path, "HST")

        assert loaded.elections_allowed is False
        assert loaded.stability == 0.65
        assert loaded.war_support == 0.45
        assert loaded.technologies == {
            "infantry_weapons": 1,
            "tech_support": 2,
        }
        assert loaded.oob == "HST_1936"

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
        assert "country_leader = {" in content
        assert "roles =" not in content
