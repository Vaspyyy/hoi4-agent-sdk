from hoi4.validation import _script_warnings
from hoi4.script_scopes import walk_script


def test_logical_operators_do_not_become_country_tags():
    script = "if = { limit = {\nNOT = {\nAND = {\nNOR = { has_war = yes } } } }\nZZZ = { add_political_power = 1 } }"
    findings = _script_warnings(script, known_tags={"ROM"})
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
