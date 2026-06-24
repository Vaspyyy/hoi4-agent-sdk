from pathlib import Path

from hoi4.ideas import detect_ideas_container, read_ideas_file, serialize_idea, serialize_ideas_file, write_ideas_file
from hoi4.types import Idea

FIXTURES = Path(__file__).parent / "fixtures"


class TestReadIdeasFile:
    def test_reads_all_ideas(self):
        ideas, container = read_ideas_file(FIXTURES / "common" / "national_ideas" / "ger_ideas.txt")
        assert len(ideas) == 2
        assert container == "country_ideas"

    def test_reads_idea_id(self):
        ideas, _ = read_ideas_file(FIXTURES / "common" / "national_ideas" / "ger_ideas.txt")
        assert ideas[0].id == "GER_spirit_1"
        assert ideas[1].id == "GER_spirit_2"

    def test_reads_icon(self):
        ideas, _ = read_ideas_file(FIXTURES / "common" / "national_ideas" / "ger_ideas.txt")
        assert ideas[0].icon == "GFX_idea_generic_army"

    def test_reads_modifier(self):
        ideas, _ = read_ideas_file(FIXTURES / "common" / "national_ideas" / "ger_ideas.txt")
        assert ideas[0].modifier["army_morale_factor"] == 0.1
        assert ideas[0].modifier["popularity_gain_support_factor"] == 0.05

    def test_reads_negative_modifier(self):
        ideas, _ = read_ideas_file(FIXTURES / "common" / "national_ideas" / "ger_ideas.txt")
        assert ideas[1].modifier["infantry_equipment_manpower_factor"] == -0.1

    def test_empty_file(self, tmp_path):
        p = tmp_path / "empty.txt"
        p.write_text("country_ideas = {\n}\n")
        ideas, container = read_ideas_file(p)
        assert ideas == []
        assert container == "country_ideas"

    def test_ideas_container(self, tmp_path):
        p = tmp_path / "ideas.txt"
        p.write_text("ideas = {\n\tA = { icon = GFX_test }\n}\n")
        ideas, container = read_ideas_file(p)
        assert len(ideas) == 1
        assert ideas[0].id == "A"
        assert container == "ideas"

    def test_reads_from_ideas_dir(self):
        ideas, container = read_ideas_file(FIXTURES / "common" / "national_ideas" / "ger_ideas.txt")
        assert container == "country_ideas"

    def test_reads_nested_designer_category(self, tmp_path):
        p = tmp_path / "luxembourg.txt"
        p.write_text(
            """
ideas = {
    industrial_concern = {
        LUX_arbed = {
            picture = generic_industrial_concern_2
            allowed = { original_tag = LUX }
            research_bonus = { industry = 0.05 }
            modifier = { local_resources_factor = 0.15 }
            ai_will_do = { base = 1 }
            traits = { industrial_steel_mills_trait }
        }
    }
}
""",
            encoding="utf-8",
        )
        ideas, container = read_ideas_file(p)
        assert container == "ideas"
        assert [idea.id for idea in ideas] == ["LUX_arbed"]
        assert ideas[0].category == "industrial_concern"
        assert ideas[0].modifier["local_resources_factor"] == 0.15
        assert ideas[0].research_bonus["industry"] == 0.05
        assert ideas[0].traits == ["industrial_steel_mills_trait"]


class TestSerializeIdea:
    def test_serializes_id(self):
        idea = Idea(id="test_idea", icon="GFX_test", modifier={"key": 0.1})
        text = serialize_idea(idea)
        assert "test_idea" in text
        assert "GFX_test" in text

    def test_serializes_modifier_types(self):
        idea = Idea(id="t", modifier={"float_val": 0.1, "int_val": 5, "bool_val": True, "str_val": "hello"})
        text = serialize_idea(idea)
        assert "float_val = 0.1" in text
        assert "int_val = 5" in text
        assert "bool_val = yes" in text
        assert 'str_val = "hello"' in text


class TestSerializeIdeasFile:
    def test_wraps_in_country_ideas(self):
        ideas = [Idea(id="a", modifier={"x": 1})]
        text = serialize_ideas_file(ideas)
        assert text.startswith("country_ideas = {")

    def test_multiple_ideas(self):
        ideas = [Idea(id="a"), Idea(id="b")]
        text = serialize_ideas_file(ideas)
        assert "id_a" in text or "a = {" in text
        assert "b = {" in text


class TestWriteIdeasFile:
    def test_writes_and_reads_back(self, tmp_path):
        ideas = [
            Idea(id="test_1", icon="GFX_1", modifier={"army_morale_factor": 0.15}),
            Idea(id="test_2", icon="GFX_2", modifier={"naval_morale_factor": -0.05}),
        ]
        path = write_ideas_file(tmp_path / "test_ideas.txt", ideas)
        assert path.exists()

        loaded, container = read_ideas_file(path)
        assert len(loaded) == 2
        assert loaded[0].id == "test_1"
        assert loaded[0].modifier["army_morale_factor"] == 0.15
        assert loaded[1].id == "test_2"
        assert container == "country_ideas"

    def test_writes_with_ideas_container(self, tmp_path):
        ideas = [Idea(id="test_1", icon="GFX_1", modifier={"x": 1})]
        path = write_ideas_file(tmp_path / "test_ideas.txt", ideas, container_name="ideas")
        content = path.read_text()
        assert content.startswith("ideas = {")


class TestDetectIdeasContainer:
    def test_detects_country_ideas(self):
        assert detect_ideas_container("country_ideas = {\n}") == "country_ideas"

    def test_detects_ideas(self):
        assert detect_ideas_container("ideas = {\n}") == "ideas"

    def test_falls_back_to_country_ideas(self):
        assert detect_ideas_container("some other text") == "country_ideas"

    def test_prefers_country_ideas_when_both_present(self):
        text = "country_ideas = {\n}\nideas = {\n}"
        assert detect_ideas_container(text) == "country_ideas"
