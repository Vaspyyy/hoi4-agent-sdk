from hoi4 import Mod
from hoi4.script_scopes import walk_script


def test_logical_operators_do_not_become_country_tags(tmp_path):
    script = "if = { limit = {\nNOT = {\nAND = {\nNOR = { has_war = yes } } } }\nZZZ = { add_political_power = 1 } }"
    findings = Mod(tmp_path).validate_effect(script)
    unknown = [f.message for f in findings if f.code == "unknown_country_scope"]
    assert len(unknown) == 1 and "'ZZZ'" in unknown[0]


def test_state_scope_survives_nested_uppercase_logic():
    commands = walk_script(
        "876 = { NOT = { AND = { OR = { is_core_of = ROM } } } }", "trigger", scope="COUNTRY"
    )
    assert [(c.name, c.scope) for c in commands] == [("is_core_of", "STATE")]


def test_nor_is_norway_for_the_scope_walker():
    # HOI4 documents AND, OR and NOT but no NOR; the game's own script uses
    # NOR = { ... } for Norway, also among effects.
    commands = walk_script("NOR = { country_event = { id = x.1 } }", "effect", scope="COUNTRY")
    assert [(c.name, c.kind, c.scope) for c in commands] == [("country_event", "effect", "COUNTRY")]
