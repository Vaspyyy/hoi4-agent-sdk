"""Reference checks read from the syntax tree: true alarms fire, valid script stays quiet."""

from __future__ import annotations

from pathlib import Path

import pytest

from hoi4 import Mod
from hoi4.script_references import (
    COUNTRY,
    STATE,
    UNKNOWN,
    file_references,
    fragment_owner,
    resolve_equipment,
)
from hoi4.validation import VALIDATION_CODES


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def references(mod: Mod) -> list:
    """Every reference finding, unfiltered, the way validate() collects them."""

    findings = mod._validate_script_references(
        known_ideas=set(),
        known_events=set(),
        known_technologies=set(),
        known_equipment=set(),
        known_focus_trees=set(),
    )
    return findings + mod._validate_state_effect_assumptions()


def codes(findings: list, code: str) -> list:
    return [finding for finding in findings if finding.code == code]


def focus_file(reward: str) -> str:
    return (
        "focus_tree = {\n"
        "\tid = test_tree\n"
        "\tfocus = {\n"
        "\t\tid = TST_focus\n"
        "\t\tcompletion_reward = {\n"
        f"{reward}\n"
        "\t\t}\n"
        "\t}\n"
        "}\n"
    )


IDEAS = "ideas = { country = { TST_spirit = { } } }\n"


# --- the walk -----------------------------------------------------------------


def test_walk_names_the_scope_of_every_statement():
    owner = fragment_owner(
        "add_core_of = A\n"
        "123 = { add_core_of = B }\n"
        "every_owned_state = { limit = { is_core_of = ROOT } add_core_of = C }\n"
        "random_list = { 50 = { add_core_of = D } }\n"
        "GER = { add_core_of = E }\n"
        "FROM = { add_core_of = F }\n"
        "my_effect = { add_core_of = G }\n",
        "focus", "TST_focus", COUNTRY,
    )
    scopes = {ref.value: (ref.scope, ref.certain) for ref in owner.of("core_effect")}
    assert scopes == {
        "A": (COUNTRY, True),
        "B": (STATE, True),
        "C": (STATE, True),
        "D": (COUNTRY, True),  # random_list keys are weights, not states
        "E": (COUNTRY, True),
        "F": (UNKNOWN, False),
        "G": (UNKNOWN, False),  # the argument block of an effect
    }


def test_references_ride_on_the_scope_walker():
    """One walker decides what a statement is and where it runs, for every check."""

    def core(text: str) -> list[tuple[str, str, bool]]:
        owner = fragment_owner(text, "focus", "f1", COUNTRY)
        return [(ref.value, ref.scope, ref.certain) for ref in owner.of("core_effect")]

    # NOR is Norway, not a keyword.
    assert core("123 = { NOR = { add_core_of = NOR } }") == [("NOR", COUNTRY, True)]
    # HOI4 has no ordered_ iterators (its documentation and script have none):
    # the token check reports the name, and its block is data, as the game reads it.
    owner = fragment_owner("ordered_owned_state = { add_core_of = GER add_ideas = fake_idea }", "focus", "f1")
    assert [(ref.kind, ref.scope, ref.certain, ref.statement) for ref in owner.refs] == [
        ("core_effect", UNKNOWN, False, False),
        ("idea", UNKNOWN, False, False),
    ]
    # PREV, THIS and ROOT come from the scope stack.
    assert core("123 = { PREV = { add_core_of = GER } }") == [("GER", COUNTRY, True)]
    assert core("123 = { THIS = { add_core_of = GER } }") == [("GER", STATE, True)]
    # An iterator's scope comes from the table, not from words in its name.
    assert core("every_country_division = { add_core_of = GER }") == [("GER", UNKNOWN, False)]


def test_a_bare_top_level_focus_is_a_focus():
    found = file_references(
        "common/national_focus/bare.txt", "common/national_focus/bare.txt",
        "focus = { id = my_bare completion_reward = { add_core_of = GER } }", own=True,
    )
    assert [(owner.kind, owner.id, owner.scope) for owner in found.owners] == [
        ("focus", "my_bare", COUNTRY)
    ]
    assert [(ref.scope, ref.certain) for _, ref in found.refs()] == [(COUNTRY, True)]


def test_unit_leader_events_start_in_an_unknown_scope_for_every_check():
    from hoi4.script_scopes import entry_scope

    found = file_references(
        "events/x.txt", "events/x.txt",
        "unit_leader_event = { id = x.1 immediate = { add_core_of = GER } }", own=True,
    )
    assert entry_scope("unit_leader_event", "*") is None
    assert [(owner.kind, owner.scope) for owner in found.owners] == [("event", UNKNOWN)]
    assert [(ref.scope, ref.certain) for _, ref in found.refs()] == [(UNKNOWN, False)]


def test_walk_never_reads_comments_or_quoted_text():
    owner = fragment_owner(
        '# add_ideas = commented\nlog = "add_ideas = quoted"\nadd_ideas = real # add_ideas = tail\n',
        "focus", "TST_focus",
    )
    assert [ref.value for ref in owner.of("idea")] == ["real"]
    assert [ref.line for ref in owner.of("idea")] == [3]


def test_on_action_events_and_border_war_events_are_fired():
    found = file_references(
        "x", "common/on_actions/x.txt",
        "on_actions = { on_startup = { events = { a.1 } random_events = { 10 = a.2 5 = 0 } } }\n",
        own=True,
    )
    fired = {ref.value for _, ref in found.refs() if ref.kind == "event"}
    assert fired == {"a.1", "a.2"}
    owner = fragment_owner(
        "start_border_war = { attacker = { state = 1 on_win = b.1 on_lose = b.2 } }",
        "decision", "d",
    )
    assert {ref.value for ref in owner.of("event")} == {"b.1", "b.2"}


def test_duplicated_archetypes_name_their_copies():
    found = file_references(
        "x", "common/units/equipment/x.txt",
        "equipments = { light_tank_chassis = { is_archetype = yes }\n"
        "  light_tank_chassis_1 = { archetype = light_tank_chassis } }\n"
        "duplicate_archetypes = { light_tank_aa_chassis = { archetype = light_tank_chassis }\n"
        "  cas_airframe = { archetype = small_plane variant_name = { cas_airframe_1 = cas_1 } } }\n",
        own=True,
    )
    ids = resolve_equipment(found.defines.equipment, found.defines.equipment_copies)
    assert {"light_tank_aa_chassis_1", "light_tank_aa_chassis", "cas_airframe_1"} <= ids


# --- unknown ideas ----------------------------------------------------------------


def test_unknown_idea_fires_with_file_and_line(tmp_path):
    path = write(tmp_path, "common/national_focus/t.txt", focus_file("\t\t\tadd_ideas = TST_nowhere"))
    write(tmp_path, "common/ideas/t.txt", IDEAS)
    found = codes(references(Mod(tmp_path)), "unknown_idea_reference")
    assert len(found) == 1
    assert found[0].severity == "warning"
    assert found[0].file_path == str(path)
    assert found[0].line == 6
    assert found[0].focus_id == "TST_focus"


@pytest.mark.parametrize(
    "reward",
    [
        "\t\t\tadd_ideas = TST_spirit",
        "\t\t\t# add_ideas = TST_nowhere",
        '\t\t\tlog = "add_ideas = TST_nowhere"',
        "\t\t\tadd_ideas = TST_[ROOT.GetTag]_spirit",
        "\t\t\thas_idea = TST_advisor",
    ],
)
def test_unknown_idea_stays_quiet_on_valid_script(tmp_path, reward):
    write(tmp_path, "common/national_focus/t.txt", focus_file(reward))
    write(tmp_path, "common/ideas/t.txt", IDEAS)
    write(
        tmp_path, "common/characters/t.txt",
        "characters = { TST_person = { advisor = { slot = political_advisor idea_token = TST_advisor } } }\n",
    )
    assert codes(references(Mod(tmp_path)), "unknown_idea_reference") == []


def test_unknown_idea_says_when_only_the_case_differs(tmp_path):
    write(tmp_path, "common/national_focus/t.txt", focus_file("\t\t\tadd_ideas = tst_SPIRIT"))
    write(tmp_path, "common/ideas/t.txt", IDEAS)
    [finding] = codes(references(Mod(tmp_path)), "unknown_idea_reference")
    assert "'TST_spirit' exists" in finding.message


# --- unknown events -----------------------------------------------------------


EVENTS = "add_namespace = tst\ncountry_event = { id = tst.1 is_triggered_only = yes }\n"


def test_event_typo_fires(tmp_path):
    write(tmp_path, "events/t.txt", EVENTS)
    path = write(tmp_path, "common/national_focus/t.txt", focus_file("\t\t\tcountry_event = tst.11"))
    [finding] = codes(references(Mod(tmp_path)), "unknown_event_reference")
    assert finding.event_id == "tst.11"
    assert (finding.file_path, finding.line) == (str(path), 6)


@pytest.mark.parametrize(
    "reward",
    [
        "\t\t\tcountry_event = { id = tst.1 days = 3 }",
        "\t\t\t# country_event = tst.11",
        "\t\t\tnews_event = tst.1",
    ],
)
def test_event_reference_stays_quiet_on_valid_script(tmp_path, reward):
    write(tmp_path, "events/t.txt", EVENTS)
    write(tmp_path, "common/national_focus/t.txt", focus_file(reward))
    assert codes(references(Mod(tmp_path)), "unknown_event_reference") == []


def test_events_in_sub_folders_are_known(tmp_path):
    write(tmp_path, "events/sub/t.txt", EVENTS)
    write(tmp_path, "common/national_focus/t.txt", focus_file("\t\t\tcountry_event = tst.1"))
    assert codes(references(Mod(tmp_path)), "unknown_event_reference") == []


def test_an_event_namespace_matches_without_case(tmp_path):
    # The game's own news events: add_namespace = nsb_news, id = NSB_news.200.
    write(
        tmp_path, "events/t.txt",
        "add_namespace = nsb_news\ncountry_event = { id = NSB_news.200 is_triggered_only = yes }\n",
    )
    write(tmp_path, "common/national_focus/t.txt", focus_file("\t\t\tcountry_event = nsb_news.200"))
    mod = Mod(tmp_path)
    assert codes(references(mod), "unknown_event_reference") == []
    assert liveness(mod, "unfired_event") == []
    # The number behind the dot is still compared as it is.
    write(tmp_path, "common/national_focus/t.txt", focus_file("\t\t\tcountry_event = nsb_news.201"))
    assert [f.event_id for f in codes(references(Mod(tmp_path)), "unknown_event_reference")] == [
        "nsb_news.201"
    ]


# --- add_core_of at country scope -----------------------------------------------


def test_core_effect_in_country_scope_fires(tmp_path):
    path = write(tmp_path, "common/national_focus/t.txt", focus_file("\t\t\tadd_core_of = ROOT"))
    [finding] = codes(references(Mod(tmp_path)), "country_scope_core_effect")
    assert finding.severity == "warning"
    assert (finding.file_path, finding.line) == (str(path), 6)


def test_inverted_tag_scoped_core_effect_suggests_the_state_form(tmp_path):
    write(tmp_path, "common/national_focus/t.txt", focus_file("\t\t\tGER = { add_core_of = 64 }"))
    [finding] = codes(references(Mod(tmp_path)), "country_scope_core_effect")
    assert "'64 = { add_core_of = GER }'" in finding.message


@pytest.mark.parametrize(
    "reward",
    [
        "\t\t\t64 = {\n\t\t\t\tadd_core_of = ROOT\n\t\t\t}",
        "\t\t\tevery_owned_state = {\n\t\t\t\tlimit = { is_core_of = ROOT }\n\t\t\t\tadd_core_of = ROOT\n\t\t\t}",
        "\t\t\tcapital_scope = { add_core_of = ROOT }",
        "\t\t\t# add_core_of = ROOT",
        "\t\t\tvar:target_state = { add_core_of = ROOT }",
        "\t\t\tTST_scripted_effect = { add_core_of = ROOT }",
    ],
)
def test_core_effect_stays_quiet_in_a_state_or_unknown_scope(tmp_path, reward):
    write(tmp_path, "common/national_focus/t.txt", focus_file(reward))
    assert codes(references(Mod(tmp_path)), "country_scope_core_effect") == []


def test_core_effect_in_scripted_effect_has_no_known_scope(tmp_path):
    write(tmp_path, "common/scripted_effects/t.txt", "tst_effect = { add_core_of = ROOT }\n")
    assert codes(references(Mod(tmp_path)), "country_scope_core_effect") == []


# --- unknown country tags -----------------------------------------------------


def test_unknown_country_scope_fires_and_aliases_are_known(tmp_path):
    write(tmp_path, "common/country_tags/t.txt", 'TST = "countries/Test.txt"\n')
    write(tmp_path, "common/country_tag_aliases/t.txt", "ALI = { original_tag = TST }\n")
    path = write(
        tmp_path, "common/national_focus/t.txt",
        focus_file(
            "\t\t\tZZZ = { add_stability = 0.1 }\n"
            "\t\t\tALI = { add_stability = 0.1 }\n"
            "\t\t\tD05 = { add_stability = 0.1 }\n"
            "\t\t\tNOT = { has_war = yes }\n"
            "\t\t\t# QQQ = { add_stability = 0.1 }\n"
            "\t\t\tstart_civil_war = { RRR = { } }"
        ),
    )
    found = codes(references(Mod(tmp_path)), "unknown_country_scope")
    assert [(finding.message.rsplit(" ", 1)[1], finding.line) for finding in found] == [("'ZZZ'", 6)]
    assert found[0].file_path == str(path)


# --- references to other content ------------------------------------------------


def test_technology_equipment_and_focus_tree_references(tmp_path):
    write(tmp_path, "common/technologies/t.txt", "technologies = { tst_tech = { research_cost = 1 } }\n")
    write(tmp_path, "common/units/equipment/t.txt", "equipments = { tst_gun = { } }\n")
    write(
        tmp_path, "common/national_focus/t.txt",
        focus_file(
            "\t\t\tset_technology = { tst_tech = 1 popup = no }\n"
            "\t\t\tset_technology = { tst_missing = 1 }\n"
            "\t\t\tadd_equipment_to_stockpile = { type = tst_gun amount = 10 }\n"
            "\t\t\tadd_equipment_to_stockpile = { type = tst_cannon amount = 10 }\n"
            "\t\t\tload_focus_tree = { tree = test_tree }\n"
            "\t\t\tload_focus_tree = missing_tree"
        ),
    )
    findings = references(Mod(tmp_path))
    assert [f.message.rsplit(" ", 1)[1] for f in codes(findings, "unknown_technology_reference")] == ["'tst_missing'"]
    assert [f.message.rsplit(" ", 1)[1] for f in codes(findings, "unknown_equipment_reference")] == ["'tst_cannon'"]
    assert [f.message.rsplit(" ", 1)[1] for f in codes(findings, "unknown_focus_tree_reference")] == ["'missing_tree'"]


def test_runtime_recruit_character_fires_in_effects_not_in_history(tmp_path):
    write(tmp_path, "history/countries/TST - Test.txt", "recruit_character = TST_person\n")
    write(tmp_path, "common/national_focus/t.txt", focus_file("\t\t\trecruit_character = TST_person"))
    found = codes(references(Mod(tmp_path)), "runtime_recruit_character")
    assert [(finding.severity, finding.line) for finding in found] == [("error", 6)]


# --- advice is info ------------------------------------------------------------


def test_faction_advice_is_info_and_only_inside_a_country_iterator(tmp_path):
    write(
        tmp_path, "common/national_focus/t.txt",
        focus_file(
            "\t\t\tadd_to_faction = ITA\n"
            "\t\t\tGER = { add_to_faction = ITA }\n"
            "\t\t\tevery_country = { limit = { is_neighbor_of = ROOT } add_to_faction = ROOT }"
        ),
    )
    found = codes(references(Mod(tmp_path)), "faction_scope_footgun")
    assert [(finding.severity, finding.line) for finding in found] == [("info", 8)]


def test_civil_war_advice_is_info_and_quiet_when_set_up(tmp_path):
    write(
        tmp_path, "common/national_focus/t.txt",
        focus_file(
            "\t\t\tstart_civil_war = { ideology = fascism size = 0.5 }\n"
            "\t\t\tstart_civil_war = { ideology = fascism capital = 64 states = { 64 } }\n"
            "\t\t\tload_focus_tree = { tree = test_tree }"
        ),
    )
    findings = references(Mod(tmp_path))
    assert [(f.severity, f.line) for f in codes(findings, "civil_war_scope_footgun")] == [("info", 6)]
    assert codes(findings, "civil_war_focus_tree_missing") == []


def test_idea_tooltip_advice_is_info_and_quiet_when_hidden_or_swapped(tmp_path):
    write(tmp_path, "common/ideas/t.txt", "ideas = { country = { a = { } b = { } c = { } } }\n")
    write(
        tmp_path, "events/t.txt",
        "add_namespace = tst\n"
        "country_event = { id = tst.1 is_triggered_only = yes\n"
        "  option = { remove_ideas = a remove_ideas = b add_ideas = c }\n"
        "  option = { hidden_effect = { remove_ideas = a remove_ideas = b } add_ideas = c }\n"
        "  option = { remove_ideas = a remove_ideas = b swap_ideas = { remove_idea = a add_idea = c } }\n"
        "}\n",
    )
    found = codes(references(Mod(tmp_path)), "bad_idea_tooltip_pattern")
    assert [(finding.severity, finding.line) for finding in found] == [("info", 3)]


def test_state_advice_is_info(tmp_path):
    write(
        tmp_path, "history/states/64-Test.txt",
        "state = { id = 64 name = STATE_64 history = { owner = TST add_core_of = TST } }\n",
    )
    write(
        tmp_path, "common/national_focus/t.txt",
        focus_file(
            "\t\t\t64 = { add_resistance = 10 }\n"
            "\t\t\tTST = { transfer_state = 64 }\n"
            "\t\t\tif = { limit = { 64 = { is_owned_by = ITA } } TST = { transfer_state = 64 } }"
        ),
    )
    findings = references(Mod(tmp_path))
    assert [(f.severity, f.line) for f in codes(findings, "resistance_on_core_state")] == [("info", 6)]
    assert [(f.severity, f.line) for f in codes(findings, "revolt_state_already_owned")] == [("info", 7)]


# --- liveness --------------------------------------------------------------------


def liveness(mod: Mod, code: str) -> list:
    return [finding for finding in mod.analyze_content_liveness().findings if finding.code == code]


def test_flag_set_and_never_read_fires_where_it_is_set(tmp_path):
    path = write(
        tmp_path, "common/national_focus/t.txt",
        focus_file(
            "\t\t\tset_country_flag = TST_lonely\n"
            "\t\t\tset_country_flag = { flag = TST_paired days = 30 }\n"
            "\t\t\t# has_country_flag = TST_lonely"
        ),
    )
    write(tmp_path, "common/decisions/t.txt", "cat = { d = { available = { has_country_flag = TST_paired } } }\n")
    found = liveness(Mod(tmp_path), "flag_set_never_read")
    assert [(f.message, f.file_path, f.line) for f in found] == [
        ("Flag 'TST_lonely' is written but never read.", str(path), 6)
    ]


def test_flag_read_never_set_fires_and_built_names_count(tmp_path):
    write(
        tmp_path, "common/national_focus/t.txt",
        focus_file(
            "\t\t\tif = { limit = { has_country_flag = TST_never } }\n"
            "\t\t\tif = { limit = { has_country_flag = TST_built_GER } }\n"
            "\t\t\tset_country_flag = TST_built_[ROOT.GetTag]"
        ),
    )
    found = liveness(Mod(tmp_path), "flag_read_never_set")
    assert [(f.message.split("'")[1], f.line) for f in found] == [("TST_never", 6)]


def test_flags_used_by_the_game_count_but_its_own_flags_are_never_reported(tmp_path):
    game = tmp_path / "game"
    write(game, "common/scripted_guis/t.txt", "gui = { visible = { has_country_flag = TST_mod_flag } }\n")
    write(game, "events/v.txt", "country_event = { id = v.1 immediate = { set_country_flag = V_lonely } }\n")
    mod_root = tmp_path / "mod"
    write(mod_root, "common/national_focus/t.txt", focus_file("\t\t\tset_country_flag = TST_mod_flag"))
    mod = Mod(mod_root, hoi4_install=game)
    assert liveness(mod, "flag_set_never_read") == []
    assert liveness(mod, "flag_read_never_set") == []


def test_project_flags_are_tracked(tmp_path):
    path = write(
        tmp_path, "common/national_focus/t.txt",
        focus_file("\t\t\tset_project_flag = TST_alone\n\t\t\tset_project_flag = TST_read"),
    )
    write(tmp_path, "events/t.txt", "country_event = { id = t.1 trigger = { has_project_flag = TST_read } }\n")
    mod = Mod(tmp_path)
    assert [(f.message, f.file_path, f.line) for f in liveness(mod, "flag_set_never_read")] == [
        ("Flag 'TST_alone' is written but never read.", str(path), 6)
    ]


def test_unfired_event_fires_for_the_mods_event_only(tmp_path):
    game = tmp_path / "game"
    write(game, "events/v.txt", "country_event = { id = v.1 is_triggered_only = yes }\n")
    mod_root = tmp_path / "mod"
    path = write(
        mod_root, "events/t.txt",
        "add_namespace = tst\n"
        "country_event = { id = tst.1 is_triggered_only = yes }\n"
        "country_event = { id = tst.2 is_triggered_only = yes }\n"
        "country_event = { id = tst.3 is_triggered_only = yes }\n",
    )
    write(
        mod_root, "common/decisions/t.txt",
        "cat = { d = { complete_effect = {\n"
        "  start_border_war = { attacker = { state = 1 on_win = tst.2 } }\n"
        "  # country_event = tst.3\n"
        "} } }\n",
    )
    write(game, "common/scripted_guis/t.txt", "gui = { effects = { x_click = { country_event = tst.1 } } }\n")
    found = liveness(Mod(mod_root, hoi4_install=game), "unfired_event")
    assert [(f.event_id, f.file_path, f.line) for f in found] == [("tst.3", str(path), 4)]


def test_unused_localization_spares_overrides_and_named_definitions(tmp_path):
    game = tmp_path / "game"
    write(
        game, "localisation/english/game_l_english.yml",
        'l_english:\n compliance_15:0 "Compliance"\n brilliant_strategist:0 "Brilliant"\n'
        ' infantry_equipment_1:0 "Rifle"\n',
    )
    mod_root = tmp_path / "mod"
    write(mod_root, "common/resistance_compliance_modifiers/my.txt", "my_compliance = { }\n")
    write(
        mod_root, "localisation/english/replace/my_l_english.yml",
        'l_english:\n my_compliance:0 "Mine"\n my_compliance_desc:0 "About mine"\n'
        ' compliance_15:0 "Ours"\n brilliant_strategist:0 "Ours"\n infantry_equipment_1:0 "Ours"\n'
        ' my_leftover:0 "Nobody shows this"\n'
        # Another text pulls this one in as $my_part$.
        ' my_part:0 "a part"\n my_bold_part:0 "a bold part"\n'
        ' my_orphan_tooltip:0 "With $my_part$, $my_bold_part|H$ and $NOT_A_KEY$"\n',
    )
    found = liveness(Mod(mod_root, hoi4_install=game), "unused_localization")
    assert [finding.message.split("'")[1] for finding in found] == [
        "my_leftover",
        "my_orphan_tooltip",
    ]


def test_ungranted_idea_fires_unless_granted_or_shown(tmp_path):
    path = write(
        tmp_path, "common/ideas/t.txt",
        "ideas = { country = {\n  TST_dead = { }\n  TST_shown = { }\n  TST_given = { }\n} }\n",
    )
    write(
        tmp_path, "common/national_focus/t.txt",
        focus_file("\t\t\tshow_ideas_tooltip = TST_shown\n\t\t\tadd_timed_idea = { idea = TST_given days = 10 }"),
    )
    found = liveness(Mod(tmp_path), "ungranted_idea")
    assert [(f.idea_id, f.file_path, f.line) for f in found] == [("TST_dead", str(path), 2)]


# --- every finding is registered and located -------------------------------------


def test_every_reference_finding_has_a_registered_code_a_file_and_a_line(tmp_path):
    write(tmp_path, "history/states/64-Test.txt", "state = { id = 64 history = { owner = TST add_core_of = TST } }\n")
    write(
        tmp_path, "common/national_focus/t.txt",
        focus_file(
            "\t\t\tadd_ideas = TST_nowhere\n\t\t\tcountry_event = nope.1\n\t\t\tadd_core_of = ROOT\n"
            "\t\t\tZZZ = { }\n\t\t\trecruit_character = X\n\t\t\tstart_civil_war = { size = 1 }\n"
            "\t\t\t64 = { add_resistance = 1 }\n\t\t\tTST = { transfer_state = 64 }\n"
            "\t\t\tset_country_flag = TST_lonely"
        ),
    )
    mod = Mod(tmp_path)
    findings = references(mod) + list(mod.analyze_content_liveness().findings)
    assert len({finding.code for finding in findings}) >= 8
    for finding in findings:
        if finding.code == "unused_localization":
            continue
        assert finding.code in VALIDATION_CODES, finding.code
        assert finding.file_path and finding.line, finding
