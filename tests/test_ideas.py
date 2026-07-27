from pathlib import Path

from hoi4.ideas import (
    detect_ideas_container,
    read_ideas_file,
    serialize_idea,
    serialize_ideas_file,
    write_ideas_file,
)
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

    def test_reads_description_and_removal_cost(self, tmp_path):
        path = tmp_path / "ideas.txt"
        path.write_text(
            """ideas = {
    country = {
        TST_spirit = {
            desc = "TST_spirit_desc"
            removal_cost = -1
            modifier = { stability_factor = 0.1 }
        }
    }
}
""",
            encoding="utf-8",
        )

        ideas, _ = read_ideas_file(path)

        assert ideas[0].desc == "TST_spirit_desc"
        assert ideas[0].removal_cost == -1


class TestSerializeIdea:
    def test_serializes_id(self):
        idea = Idea(id="test_idea", icon="GFX_test", modifier={"key": 0.1})
        text = serialize_idea(idea)
        assert "test_idea" in text
        assert "picture = GFX_test" in text
        assert "icon =" not in text

    def test_serializes_modifier_types(self):
        idea = Idea(
            id="t", modifier={"float_val": 0.1, "int_val": 5, "bool_val": True, "str_val": "hello"}
        )
        text = serialize_idea(idea)
        assert "float_val = 0.1" in text
        assert "int_val = 5" in text
        assert "bool_val = yes" in text
        assert 'str_val = "hello"' in text

    def test_serializes_description_and_removal_cost(self):
        idea = Idea(id="TST_spirit", desc="TST_spirit_desc", removal_cost=-1)

        text = serialize_idea(idea)

        assert "desc = TST_spirit_desc" in text
        assert "removal_cost = -1" in text

    def test_description_edit_preserves_surrounding_source(self, tmp_path):
        original = """ideas = {
    country = {
        TST_spirit = {
            picture = GFX_idea_TST
            desc   = "TST_old_desc" # keep description note
            removal_cost = -1 # keep removal note
            custom_idea_field = keep
            modifier = { stability_factor = 0.10 }
        }
    }
}
"""
        path = tmp_path / "ideas.txt"
        path.write_text(original, encoding="utf-8")
        ideas, container = read_ideas_file(path)
        ideas[0].desc = "TST_new_desc"
        ideas[0].removal_cost = 10
        ideas[0].touched = True

        text = serialize_ideas_file(ideas, container_name=container, original=original)

        expected = original.replace("TST_old_desc", "TST_new_desc").replace(
            "removal_cost = -1", "removal_cost = 10"
        )
        assert text == expected

    def test_unmodeled_description_block_survives_other_edits(self, tmp_path):
        original = """ideas = {
    country = {
        TST_spirit = {
            icon = GFX_old
            desc = { text = TST_conditional_desc trigger = { always = yes } }
        }
    }
}
"""
        path = tmp_path / "ideas.txt"
        path.write_text(original, encoding="utf-8")
        ideas, container = read_ideas_file(path)
        ideas[0].icon = "GFX_new"
        ideas[0].touched = True

        text = serialize_ideas_file(ideas, container_name=container, original=original)

        assert text == original.replace("icon = GFX_old", "picture = GFX_new")

    def test_unrelated_edit_migrates_legacy_icon_key_without_churn(self, tmp_path):
        original = """ideas = {
    country = {
        TST_spirit = {
            icon   = GFX_old # keep icon note
            modifier = { stability_factor = 0.10 }
        }
    }
}
"""
        path = tmp_path / "ideas.txt"
        path.write_text(original, encoding="utf-8")
        ideas, container = read_ideas_file(path)
        ideas[0].modifier = {"stability_factor": 0.20}
        ideas[0].touched = True
        ideas[0].touched_fields.add("modifier")

        text = serialize_ideas_file(ideas, container_name=container, original=original)

        expected = original.replace("icon   =", "picture   =").replace(
            "stability_factor = 0.10", "stability_factor = 0.2"
        )
        assert text == expected


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


def test_mod_description_edit_does_not_rebuild_unrelated_nested_blocks(
    tmp_path: Path,
) -> None:
    path = tmp_path / "common/ideas/custom.txt"
    path.parent.mkdir(parents=True)
    original = """ideas = { country = {
    custom_spirit = {
        desc = OLD_DESC
        modifier = {
            stability_factor = 0.1
            custom_nested = { future_value = yes }
        }
        research_bonus = {
            industry = 0.05
            custom_research = { future_value = yes }
        }
    }
} }
"""
    path.write_text(original, encoding="utf-8")

    from hoi4 import Mod

    mod = Mod(tmp_path)
    assert mod.update_idea("custom_spirit", desc="NEW_DESC")
    mod.save()

    assert path.read_text(encoding="utf-8") == original.replace("OLD_DESC", "NEW_DESC")


def test_modifier_merge_preserves_nested_unmodeled_modifier_content(tmp_path: Path) -> None:
    path = tmp_path / "common/ideas/custom.txt"
    path.parent.mkdir(parents=True)
    path.write_text(
        """ideas = { country = {
    custom_spirit = { modifier = {
        stability_factor = 0.1
        custom_nested = { future_value = yes }
    } }
} }
""",
        encoding="utf-8",
    )

    from hoi4 import Mod

    mod = Mod(tmp_path)
    assert mod.update_idea(
        "custom_spirit",
        modifier={"political_power_gain": 0.2},
        merge_modifier=True,
    )
    mod.save()

    rendered = path.read_text(encoding="utf-8")
    assert "stability_factor = 0.1" in rendered
    assert "custom_nested = { future_value = yes }" in rendered
    assert "political_power_gain = 0.2" in rendered


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
