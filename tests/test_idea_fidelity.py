"""Ideas, dynamic modifiers and ideologies: structure-based reads, span-exact writes."""

from __future__ import annotations

import tracemalloc
from pathlib import Path

import pytest

from hoi4 import Mod
from hoi4.dynamic_modifiers import load_dynamic_modifiers_text, serialize_dynamic_modifiers_file
from hoi4.ideas import read_assigned_ideas, read_ideas_text, scan_idea_ids_file, serialize_ideas_file
from hoi4.ideologies import load_ideologies_text, serialize_ideologies_file
from hoi4.syntax import parse
from hoi4.types import Idea
from hoi4.validation import validate_idea


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))


def _write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _render(mod: Mod, path: Path) -> str:
    rendered = mod._render_dirty_files()[path]
    assert rendered is not None
    return rendered


PROBE = """ideas = {
	country = {
		my_equip_spirit = {
			equipment_bonus = { infantry_equipment = { build_cost_ic = -0.1 instant = yes } }
		}
		other_spirit = {
			modifier = { stability_factor = 0.1 }
		}
	}
}
"""


# ── bug 1: an idea is any block inside a category ─────────────


def test_sibling_edit_does_not_duplicate_idea_without_known_keys(tmp_path: Path) -> None:
    path = _write(tmp_path, "common/ideas/probe.txt", PROBE)
    mod = Mod(tmp_path)

    assert mod.update_idea("other_spirit", modifier={"stability_factor": 0.2})

    assert _render(mod, path) == PROBE.replace("stability_factor = 0.1", "stability_factor = 0.2")


@pytest.mark.parametrize(
    "body",
    [
        "targeted_modifier = { tag = GER attack_bonus_against = 0.1 }",
        "rule = { can_send_volunteers = yes }",
        "research_bonus = { industry = 0.05 }",
        "future_key = { something = yes }",
        "",
    ],
)
def test_any_category_block_is_an_idea(tmp_path: Path, body: str) -> None:
    text = f"ideas = {{\n\tcountry = {{\n\t\todd_spirit = {{ {body} }}\n\t\tplain = {{ picture = x }}\n\t}}\n}}\n"
    path = _write(tmp_path, "common/ideas/odd.txt", text)

    assert scan_idea_ids_file(path) == {"odd_spirit", "plain"}
    mod = Mod(tmp_path)
    assert set(mod.list_ideas()) == {"odd_spirit", "plain"}

    assert mod.update_idea("plain", icon="y")
    assert _render(mod, path) == text.replace("picture = x", "picture = y")

    mod.delete_idea("odd_spirit")
    rendered = _render(mod, path)
    assert "odd_spirit" not in rendered
    assert rendered.count("plain = {") == 1


def test_comments_and_nested_modifiers_are_not_read_as_ideas() -> None:
    text = """ideas = {
	country = {
		# fake_spirit = { modifier = { stability_factor = 1 } }
		real_spirit = {
			ai_will_do = { factor = 1 modifier = { factor = 0 } }
		}
	}
}
"""
    ideas, _ = read_ideas_text(text)

    assert [idea.id for idea in ideas] == ["real_spirit"]
    assert ideas[0].modifier == {}


# ── bug 2: shared text is stored once, not per idea ───────────


def test_category_text_is_not_copied_into_every_idea() -> None:
    body = "".join(
        f"\t\tidea_{index} = {{\n\t\t\tpicture = generic\n\t\t\tmodifier = {{ stability_factor = 0.01 }}\n\t\t}}\n"
        for index in range(500)
    )
    text = "ideas = {\n\tcountry = {\n\t\tdesigner = yes\n" + body + "\t}\n}\n"

    tracemalloc.start()
    try:
        ideas, _ = read_ideas_text(text)
        retained, _ = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(ideas) == 500
    assert {id(idea.category_raw_block) for idea in ideas} == {id(ideas[0].category_raw_block)}
    assert ideas[0].category_raw_block == "designer = yes"
    # The old reader kept the whole category (~40 KB) on each idea: ~20 MB.
    assert retained < 3_000_000


def test_moved_idea_keeps_category_settings_but_not_siblings(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "common/ideas/source.txt",
        "ideas = {\n\tindustrial_concern = {\n\t\tdesigner = yes\n"
        "\t\tmover = { picture = a }\n\t\tstayer = { picture = b }\n\t}\n}\n",
    )
    mod = Mod(tmp_path)

    mod.update_idea("mover", path="common/ideas/target.txt")
    mod.save()

    target = (tmp_path / "common/ideas/target.txt").read_text(encoding="utf-8")
    source = (tmp_path / "common/ideas/source.txt").read_text(encoding="utf-8")
    assert "designer = yes" in target and "mover" in target and "stayer" not in target
    assert "mover" not in source and "stayer = { picture = b }" in source
    reloaded = Mod(tmp_path)
    assert reloaded.get_idea("mover").category == "industrial_concern"


# ── bug 3: inherited files with an unclosed block ──────────────


UNCLOSED = """ideas = {
	country = {
		INH_spirit = {
			modifier = { stability_factor = 0.1 }
		}
		INH_other = {
			equipment_bonus = { infantry_equipment = { build_cost_ic = -0.1 } }
		}
	}
"""


def test_inherited_ideas_with_unclosed_block_load_edit_and_save_repaired(tmp_path: Path) -> None:
    game = tmp_path / "game"
    _write(game, "common/ideas/INH.txt", UNCLOSED)
    mod_root = tmp_path / "mod"
    mod_root.mkdir()
    mod = Mod(mod_root, game)

    assert {"INH_spirit", "INH_other"} <= mod._known_idea_ids()
    mod.load_inherited_content("common/ideas/INH.txt")
    assert {"INH_spirit", "INH_other"} <= set(mod.list_ideas())

    mod.update_idea("INH_spirit", modifier={"stability_factor": 0.3})
    mod.save()

    saved = (mod_root / "common/ideas/INH.txt").read_text(encoding="utf-8")
    assert not parse(saved).problems
    assert saved == UNCLOSED.replace("0.1 }", "0.3 }", 1) + "}\n"
    assert (game / "common/ideas/INH.txt").read_text(encoding="utf-8") == UNCLOSED


# ── repeated categories and repeated keys ─────────────────────


REPEATED = """ideas = {
	country = {
		first_a = { modifier = { stability_factor = 0.1 } }
	}
	hidden_ideas = {
		hidden_a = { picture = h }
	}
	country = {
		second_a = { modifier = { war_support_factor = 0.1 } }
	}
}
"""


def test_repeated_category_blocks_are_patched_in_place() -> None:
    ideas, _ = read_ideas_text(REPEATED)
    by_id = {idea.id: idea for idea in ideas}
    assert set(by_id) == {"first_a", "hidden_a", "second_a"}

    by_id["second_a"].modifier = {"war_support_factor": 0.2}
    by_id["second_a"].touched = True
    by_id["second_a"].touched_fields = {"modifier"}
    assert serialize_ideas_file(ideas, "ideas", REPEATED) == REPEATED.replace(
        "war_support_factor = 0.1", "war_support_factor = 0.2"
    )

    remaining = [idea for idea in ideas if idea.id != "first_a"]
    remaining.append(Idea(id="new_a", category="country", icon="n", touched=True))
    rendered = serialize_ideas_file(remaining, "ideas", REPEATED)
    assert "first_a" not in rendered
    assert rendered.count("country = {") == 1  # the emptied first block is removed
    assert rendered.index("second_a") < rendered.index("new_a")
    assert "hidden_a = { picture = h }" in rendered


def test_touch_without_change_keeps_repeated_keys_and_empty_blocks(tmp_path: Path) -> None:
    text = """ideas = {
	country = {
		tooltip_spirit = {
			modifier = {
				custom_modifier_tooltip = first_tt
				custom_modifier_tooltip = second_tt
				stability_factor = 0.1
			}
			research_bonus = { }
		}
	}
}
"""
    path = _write(tmp_path, "common/ideas/tt.txt", text)
    mod = Mod(tmp_path)
    idea = mod.get_idea("tooltip_spirit")

    mod.update_idea(
        idea.id, modifier=dict(idea.modifier), research_bonus=dict(idea.research_bonus)
    )
    assert _render(mod, path) == text

    mod.update_idea(idea.id, modifier={**idea.modifier, "stability_factor": 0.2})
    assert _render(mod, path) == text.replace("0.1", "0.2")


# ── read_assigned_ideas and validation ────────────────────────


def test_read_assigned_ideas_uses_the_tree(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "history/countries/TST - Test.txt",
        """# add_ideas = { commented_out }
add_ideas = { first_idea second_idea }
add_ideas = third_idea
remove_ideas = second_idea
1939.1.1 = { add_ideas = later_idea }
""",
    )

    assert read_assigned_ideas(path) == ["first_idea", "third_idea"]


def test_equipment_bonus_idea_is_not_reported_as_empty() -> None:
    ideas, _ = read_ideas_text(PROBE)
    equip = next(idea for idea in ideas if idea.id == "my_equip_spirit")

    assert not [error for error in validate_idea(equip) if "no modifiers" in error.message]
    assert [error for error in validate_idea(Idea(id="empty")) if "no modifiers" in error.message]


# ── ideologies ────────────────────────────────────────────────


def test_ideology_reader_is_top_level_and_comment_aware() -> None:
    text = """# ideologies = { commented = { } }
wrapper = { ideologies = { nested = { } } }
ideologies = # comment with a { brace
{
	real = {
		color = { 1 2 3 }
		rules = { can_force_government = yes }
	}
}
"""
    ideologies = load_ideologies_text(text)
    assert [ideology.id for ideology in ideologies] == ["real"]

    ideologies[0].rules = {"can_force_government": "no"}
    ideologies[0].touched_fields = {"rules"}
    assert serialize_ideologies_file(ideologies, original=text) == text.replace(
        "can_force_government = yes", "can_force_government = no"
    )


def test_ideology_edit_in_unclosed_container_keeps_the_last_character() -> None:
    text = "ideologies = {\n\treal = {\n\t\tcolor = { 1 2 3 }\n\t}\n"
    ideologies = load_ideologies_text(text)
    ideologies[0].color = (4, 5, 6)
    ideologies[0].touched_fields = {"color"}

    assert serialize_ideologies_file(ideologies, original=text) == text.replace("1 2 3", "4 5 6")


def test_inherited_ideologies_with_unclosed_block_are_read(tmp_path: Path) -> None:
    game = tmp_path / "game"
    _write(game, "common/ideologies/00_ideologies.txt", "ideologies = {\n\tgame_ideology = { color = { 1 2 3 } }\n")
    mod_root = tmp_path / "mod"
    mod_root.mkdir()

    mod = Mod(mod_root, game)

    assert "game_ideology" in mod.list_ideologies()


# ── dynamic modifiers ─────────────────────────────────────────


def test_dynamic_modifier_edit_keeps_repeated_tooltips_and_comments() -> None:
    text = """# commented = { stability_factor = 1 }
my_modifier = {
	icon = GFX_x
	custom_modifier_tooltip = a_tt
	custom_modifier_tooltip = b_tt
	stability_factor = 0.1 # keep me
}
"""
    modifiers = load_dynamic_modifiers_text(text)
    assert [modifier.id for modifier in modifiers] == ["my_modifier"]

    modifiers[0].modifier = {**modifiers[0].modifier, "stability_factor": 0.2}
    modifiers[0].touched_fields = {"modifier"}
    assert serialize_dynamic_modifiers_file(modifiers, original=text) == text.replace(
        "0.1 #", "0.2 #"
    )


def test_overwrite_then_update_replaces_the_old_definition(tmp_path: Path) -> None:
    path = _write(tmp_path, "common/ideas/probe.txt", PROBE)
    mod = Mod(tmp_path)

    mod.create_idea("other_spirit", icon="fresh", modifier={"war_support_factor": 0.1}, overwrite=True)
    mod.update_idea("other_spirit", removal_cost=5)
    rendered = _render(mod, path)

    assert "stability_factor" not in rendered
    assert "war_support_factor = 0.1" in rendered and "removal_cost = 5" in rendered
    assert rendered.count("my_equip_spirit = {") == 1
