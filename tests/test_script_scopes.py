"""The scope-tracking script walker and the vocabulary it checks against."""

from __future__ import annotations

from pathlib import Path

import pytest

import hoi4.script_vocabulary as script_vocabulary
from hoi4 import Mod
from hoi4.script_scopes import walk_script
from hoi4.script_vocabulary import (
    ScriptSource,
    load_game_script_vocabulary,
    validate_script_sources,
)

EFFECTS = {
    "add_political_power": "COUNTRY",
    "add_stability": "COUNTRY",
    "set_country_flag": "COUNTRY",
    "country_event": "COUNTRY",
    "add_building_construction": "STATE",
    "set_state_flag": "STATE",
    "add_core_of": "STATE",
    "add_skill_level": "CHARACTER",
    "every_owned_state": "COUNTRY",
    "random_owned_state": "COUNTRY",
    "every_army_leader": "COUNTRY",
    "create_dynamic_country": "any",
    "random_list": "any",
    "hidden_effect": "any",
    "if": "any",
    "unused_effect": "COUNTRY",
}
TRIGGERS = {
    "has_country_flag": "COUNTRY",
    "has_state_flag": "STATE",
    "is_coastal": "STATE",
    "tag": "COUNTRY",
    "original_tag": "COUNTRY",
    "has_government": "COUNTRY",
    "any_owned_state": "COUNTRY",
    "any_country_with_original_tag": "any",
    "any_province_building_level": "STATE",
    "always": "any",
    "and": "any",
}


def _section(name: str, scopes: str) -> str:
    return f"## {name}\n\n* Supported Scopes: {scopes}\n* Supported Targets: none\n\n"


def make_game(root: Path) -> Path:
    """A small game install: documentation, a building, an ideology, a character."""

    documentation = root / "documentation"
    documentation.mkdir(parents=True)
    (documentation / "effects_documentation.md").write_text(
        "# Effects\n\n" + "".join(_section(n, s) for n, s in EFFECTS.items()), encoding="utf-8"
    )
    (documentation / "triggers_documentation.md").write_text(
        "# Triggers\n\n" + "".join(_section(n, s) for n, s in TRIGGERS.items()), encoding="utf-8"
    )
    (documentation / "modifiers_documentation.md").write_text(
        "# Modifiers\n\n"
        "## stability_factor\n\n* Number with 2 decimal places\n* **Categories**: country\n\n"
        '##  <span id="production_speed__factor"></span>production_speed_<Building>_factor\n\n'
        "* Number with 2 decimal places\n* **Categories**: country, war_production\n"
        "* **Modified types**: arms_factory, dockyard\n\n"
        '##  <span id="_cost_factor"></span><IdeaGroup>_cost_factor\n\n'
        "* Number with 2 decimal places\n* **Categories**: country\n"
        "* **Modified types**: political_advisor\n",
        encoding="utf-8",
    )
    files = {
        "common/buildings/00_buildings.txt": "buildings = { arms_factory = { } rail_way = { } }",
        "common/ideologies/00_ideologies.txt": "ideologies = { fascism = { } neutrality = { } }",
        "common/idea_tags/00_idea.txt": (
            "idea_categories = { government = {"
            " slot = economy character_slot = theorist character_slot = political_advisor } }"
        ),
        "common/characters/GER.txt": "characters = { GER_erwin_rommel = { name = x } }",
        "common/scripted_effects/vanilla.txt": (
            "vanilla_effect = { add_political_power = 1 set_country_flag = x }"
        ),
        "common/modifier_definitions/defs.txt": "my_game_modifier = { color_type = good }",
    }
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def private_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cache = tmp_path / "cache"
    monkeypatch.setenv("HOI4_SDK_CACHE_DIR", str(cache))
    return cache


@pytest.fixture
def game(tmp_path: Path) -> Path:
    return make_game(tmp_path / "game")


def check(
    game: Path,
    text: str,
    kind: str = "effect",
    scope: str | None = "COUNTRY",
    *,
    info: bool = False,
    **extra,
):
    """Warnings for one block; ``info=True`` keeps the advice too.

    The small test game uses almost none of its tokens, so nearly every token
    would also be an ``unseen_*_token`` advice.
    """

    vocabulary = load_game_script_vocabulary(game)
    source = ScriptSource(text, kind, "focus", "probe", scope=scope, **extra)  # type: ignore[arg-type]
    findings = validate_script_sources([source], vocabulary, custom_tokens={})
    return [item for item in findings if info or item.severity != "info"]


# ── true alarms stay ──────────────────────────────────────────────


def test_misspelled_effect_is_reported(game: Path) -> None:
    findings = check(game, "add_politcal_power = 50")
    assert [item.code for item in findings] == ["unknown_effect_token"]
    assert "add_political_power" in findings[0].message  # the suggestion
    assert findings[0].line == 1


def test_misspelled_trigger_is_reported(game: Path) -> None:
    findings = check(game, "if = {\n  limit = { has_contry_flag = x }\n  add_stability = 0.1\n}")
    assert [(item.code, item.line) for item in findings] == [("unknown_trigger_token", 2)]


def test_a_trigger_where_an_effect_belongs_is_named_as_such(game: Path) -> None:
    (finding,) = check(game, "has_country_flag = x")
    assert finding.code == "unknown_effect_token"
    assert "'has_country_flag' is a trigger" in finding.message
    assert "Did you mean" not in finding.message

    (finding,) = check(game, "if = {\n  limit = { add_stability = 0.1 }\n  add_stability = 0.1\n}")
    assert (finding.code, finding.line) == ("unknown_trigger_token", 2)
    assert "'add_stability' is an effect" in finding.message


def test_misspelled_modifier_is_reported(game: Path) -> None:
    findings = check(game, "stabilty_factor = 0.1\nstability_factor = 0.1", kind="modifier")
    assert [(item.code, item.line) for item in findings] == [("unknown_modifier_token", 1)]


def test_state_effect_in_certain_country_scope_is_reported(game: Path) -> None:
    findings = check(game, "add_building_construction = { type = arms_factory level = 1 }")
    assert [item.code for item in findings] == ["unsupported_effect_scope"]
    assert "COUNTRY scope" in findings[0].message


def test_country_trigger_in_iterator_state_scope_is_reported(game: Path) -> None:
    findings = check(game, "any_owned_state = { has_country_flag = x }", kind="trigger")
    assert [item.code for item in findings] == ["unsupported_trigger_scope"]


# ── no false alarms ───────────────────────────────────────────────


def test_keywords_fold_case_like_the_game(game: Path) -> None:
    text = """
    IF = {
        LIMIT = { OR = { Has_Country_Flag = a NOT = { tag = GER } } }
        add_stability = 0.1
    }
    Else_If = { limit = { AND = { always = yes } } add_stability = 0.1 }
    ELSE = { limit = { always = yes } ADD_STABILITY = 0.1 }
    ROOT = { add_stability = 0.1 }
    root = { add_stability = 0.1 }
    OVERLORD = { add_stability = 0.1 }
    """
    assert check(game, text) == []


def test_command_arguments_are_data(game: Path) -> None:
    text = """
    country_event = { id = my.1 days = 3 random_days = 2 }
    any_province_building_level = { province = { all_provinces = yes } building = bunker level < 3 }
    """
    assert check(game, text.split("\n", 2)[1]) == []
    assert check(game, text.split("\n", 2)[2], kind="trigger", scope="STATE") == []


def test_random_list_weights_and_their_modifiers(game: Path) -> None:
    text = """
    random_list = {
        seed = 7
        12.5 = { modifier = { factor = 0 has_country_flag = a } add_stability = 0.1 }
        var:my_weight = { add_political_power = 5 }
        30 = { modifier = { add = 2 has_contry_flag = b } }
    }
    """
    assert [(item.code, item.line) for item in check(game, text)] == [("unknown_trigger_token", 6)]


def test_iterators_take_their_own_arguments_and_change_scope(game: Path) -> None:
    text = """
    every_owned_state = {
        limit = { is_coastal = yes }
        random_select_amount = 2
        display_individual_scopes = yes
        tooltip = my_tt
        include_invisible = yes
        add_building_construction = { type = dockyard level = 1 }
        set_state_flag = here
        PREV = { add_stability = 0.1 }
        owner = { add_stability = 0.1 }
    }
    random_owned_state = { prioritize = { 1 2 } add_core_of = ROOT }
    """
    assert check(game, text) == []
    trigger = "any_country_with_original_tag = { original_tag_to_check = GER has_government = x }"
    assert check(game, trigger, kind="trigger") == []
    # A trigger iterator where effects belong is still an unknown effect.
    assert [item.code for item in check(game, trigger)] == ["unknown_effect_token"]
    findings = check(game, "every_owned_state = { add_stability = 0.1 }")
    assert [item.code for item in findings] == ["unsupported_effect_scope"]


def test_prev_walks_the_scope_stack(game: Path) -> None:
    text = """
    every_owned_state = {
        owner = {
            every_owned_state = {
                PREV.PREV = { set_state_flag = outer }
                PREV = { add_stability = 0.1 }
            }
        }
    }
    """
    assert check(game, text) == []
    wrong = "every_owned_state = { owner = { PREV = { add_stability = 1 } } }"
    assert [item.code for item in check(game, wrong)] == ["unsupported_effect_scope"]


def test_dotted_and_prefixed_scopes(game: Path) -> None:
    text = """
    FROM.owner = { add_stability = 0.1 }
    ROOT.OVERLORD = { add_stability = 0.1 }
    var:GER.my_state = { set_state_flag = x }
    event_target:somewhere = { add_skill_level = 1 }
    global_event_target:thing = { set_state_flag = x }
    123 = { set_state_flag = x owner = { add_stability = 1 } }
    GER = { add_stability = 1 }
    NOR = { add_stability = 1 }
    GER_erwin_rommel = { add_skill_level = 1 }
    create_dynamic_country = { original_tag = GER add_stability = 1 }
    """
    assert check(game, text) == []
    assert [item.code for item in check(game, "123 = { add_stability = 1 }")] == [
        "unsupported_effect_scope"
    ]


def test_from_is_unknown_unless_the_source_says(game: Path) -> None:
    assert check(game, "FROM = { add_stability = 1 }") == []
    findings = check(game, "FROM = { add_stability = 1 }", from_scope="STATE")
    assert [item.code for item in findings] == ["unsupported_effect_scope"]


def test_unknown_scope_says_nothing_about_scopes(game: Path) -> None:
    assert check(game, "add_building_construction = { type = dockyard }", scope=None) == []


def test_scripted_effects_and_triggers_work_anywhere(game: Path) -> None:
    assert check(game, "every_owned_state = { vanilla_effect = yes }") == []


def test_weight_blocks_are_not_modifiers(game: Path) -> None:
    text = """
    factor = 2
    base = 1
    modifier = { factor = 0 has_country_flag = x }
    modifier = { add = 5 tag = GER }
    modifier = { factor = 3 has_contry_flag = typo }
    """
    findings = check(game, text, kind="weight")
    assert [(item.code, item.line) for item in findings] == [("unknown_trigger_token", 6)]


def test_modifier_blocks_skip_their_own_keys(game: Path) -> None:
    text = """
    custom_modifier_tooltip = my_tt
    hidden_modifier = { stability_factor = 0.1 typo_factor = 1 }
    production_speed_arms_factory_factor = 0.1
    production_speed_rail_way_factor = 0.1
    economy_cost_factor = -0.1
    theorist_cost_factor = -0.1
    political_advisor_cost_factor = -0.1
    fascism_drift = 0.1
    my_game_modifier = 1
    """
    findings = check(game, text, kind="modifier")
    assert [(item.code, item.line) for item in findings] == [("unknown_modifier_token", 3)]


# ── the vocabulary ────────────────────────────────────────────────


def test_modifier_families_come_from_documentation_and_definitions(game: Path) -> None:
    vocabulary = load_game_script_vocabulary(game)
    documented = vocabulary.modifiers["production_speed_dockyard_factor"]
    assert not documented.synthesized
    assert documented.source == "production_speed_<Building>_factor (documented)"
    learned = vocabulary.modifiers["production_speed_rail_way_factor"]
    assert learned.synthesized
    assert learned.source == "production_speed_<Building>_factor from common/buildings"
    assert learned.categories == ("country", "war_production")
    assert vocabulary.modifiers["economy_cost_factor"].source.endswith("common/idea_tags")
    assert vocabulary.triggers["rail_way"].synthesized  # a building is a state trigger
    assert vocabulary.triggers["fascism"].synthesized  # an ideology is a popularity trigger
    assert "ger_erwin_rommel" in vocabulary.characters


def test_mod_definitions_extend_the_families(game: Path, tmp_path: Path) -> None:
    mod_root = tmp_path / "mod"
    (mod_root / "common/buildings").mkdir(parents=True)
    (mod_root / "common/buildings/mod.txt").write_text("buildings = { canal = { } }")
    vocabulary = load_game_script_vocabulary(game).extended([mod_root])
    assert vocabulary.modifiers["production_speed_canal_factor"].synthesized
    assert vocabulary.triggers["canal"].synthesized
    mod = Mod(mod_root, hoi4_install=game)
    assert "production_speed_canal_factor" in mod.game_script_vocabulary().modifiers


def test_documentation_without_scopes_does_not_read_the_next_line(game: Path) -> None:
    path = game / "documentation" / "triggers_documentation.md"
    path.write_text(
        path.read_text() + "## odd_trigger\n\n* Supported Scopes: \n* Supported Targets: none\n"
    )
    vocabulary = load_game_script_vocabulary(game, cache=False)
    assert vocabulary.triggers["odd_trigger"].supported_scopes == ()


def test_install_data_is_cached_and_refreshed(game: Path, private_cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    load_game_script_vocabulary(game)
    assert len(list((private_cache / "script-vocabulary").glob("*.json"))) == 1

    def fail(root: Path) -> dict:
        raise AssertionError("the cache should have been used")

    monkeypatch.setattr(script_vocabulary, "_install_data", fail)
    assert "add_stability" in load_game_script_vocabulary(game).effects
    monkeypatch.undo()
    monkeypatch.setenv("HOI4_SDK_CACHE_DIR", str(private_cache))
    building = game / "common/buildings/00_buildings.txt"
    building.write_text("buildings = { arms_factory = { } rail_way = { } canal = { } }")
    assert "production_speed_canal_factor" in load_game_script_vocabulary(game).modifiers


def test_a_broken_cache_file_is_read_again(game: Path, private_cache: Path) -> None:
    load_game_script_vocabulary(game)
    for path in (private_cache / "script-vocabulary").glob("*.json"):
        path.write_text("{ not json")
    assert "add_stability" in load_game_script_vocabulary(game).effects


def test_unseen_tokens_are_info(game: Path) -> None:
    findings = check(game, "unused_effect = yes", info=True)
    assert [(item.code, item.severity) for item in findings] == [("unseen_effect_token", "info")]
    assert check(game, "vanilla_effect = yes", info=True) == []  # scripted: never unseen


# ── where findings point ──────────────────────────────────────────


def test_walker_lines_come_from_the_tree() -> None:
    statements = walk_script("a = 1\n\nif = {\n  limit = { b = yes }\n  c = 2\n}", "effect")
    assert [(item.name, item.kind, item.line) for item in statements] == [
        ("a", "effect", 1),
        ("b", "trigger", 4),
        ("c", "effect", 5),
    ]


def test_option_findings_carry_file_lines(game: Path, tmp_path: Path) -> None:
    mod_root = tmp_path / "mod"
    events = mod_root / "events/test.txt"
    events.parent.mkdir(parents=True)
    events.write_text(
        "add_namespace = my\n"
        "country_event = {\n"
        "\tid = my.1\n"
        "\toption = {\n"
        "\t\tname = my.1.a #note\n"
        "\t\tadd_stability = 1\n"
        "\t\tai_chance = { factor = 2 modifier = { factor = 0 has_contry_flag = x } }\n"
        "\t\tadd_politcal_power = 5\n"
        "\t}\n"
        "}\n"
    )
    mod = Mod(mod_root, hoi4_install=game)
    findings = [item for item in mod.validate_script_vocabulary() if item.severity != "info"]
    assert sorted((item.code, item.line) for item in findings) == [
        ("unknown_effect_token", 8),
        ("unknown_trigger_token", 7),
    ]


def test_mod_findings_carry_file_lines(game: Path, tmp_path: Path) -> None:
    mod_root = tmp_path / "mod"
    focus_file = mod_root / "common/national_focus/test.txt"
    focus_file.parent.mkdir(parents=True)
    focus_file.write_text(
        "focus_tree = {\n"
        "\tid = test_tree\n"
        "\tfocus = {\n"
        "\t\tid = TST_one\n"
        "\t\tcompletion_reward = {\n"
        "\t\t\tadd_stability = 0.1\n"
        "\t\t\tadd_building_construction = { type = arms_factory level = 1 }\n"
        "\t\t\tadd_politcal_power = 5\n"
        "\t\t}\n"
        "\t\tai_will_do = { factor = 1 modifier = { factor = 0 has_contry_flag = x } }\n"
        "\t}\n"
        "}\n"
    )
    mod = Mod(mod_root, hoi4_install=game)
    findings = [item for item in mod.validate_script_vocabulary() if item.severity != "info"]
    assert sorted((item.code, item.line) for item in findings) == [
        ("unknown_effect_token", 8),
        ("unknown_trigger_token", 10),
        ("unsupported_effect_scope", 7),
    ]
    assert all(item.file_path == str(focus_file) for item in findings)
    assert all(item.focus_id == "TST_one" for item in findings)


def test_a_template_learns_only_from_its_own_folders(game: Path) -> None:
    modifiers = game / "documentation" / "modifiers_documentation.md"
    modifiers.write_text(
        modifiers.read_text()
        + '\n##  <span id="_track"></span><Doctrine>_track_mastery_gain_factor\n\n'
        "* **Categories**: country\n* **Modified types**: armor\n"
    )
    for folder, name in (("tracks", "armor"), ("tracks", "screens"), ("grand_doctrines", "mass_assault")):
        path = game / "common/doctrines" / folder / f"{name}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{name} = {{ }}")
    vocabulary = load_game_script_vocabulary(game, cache=False)
    assert "screens_track_mastery_gain_factor" in vocabulary.modifiers
    assert "mass_assault_track_mastery_gain_factor" not in vocabulary.modifiers
