from hoi4.validation import _script_warnings
from hoi4.script_vocabulary import _script_commands


def test_logical_operators_do_not_become_country_tags():
    script = "if = { limit = {\nNOT = {\nAND = {\nNOR = { has_war = yes } } } }\nZZZ = { add_political_power = 1 } }"
    findings = _script_warnings(script, known_tags={"ROM"})
    unknown = [f.message for f in findings if f.code == "unknown_country_scope"]
    assert len(unknown) == 1 and "'ZZZ'" in unknown[0]


def test_state_scope_survives_nested_uppercase_logic():
    commands = _script_commands(
        "876 = { NOT = { AND = { NOR = { is_core_of = ROM } } } }", "trigger", scope="COUNTRY"
    )
    assert [(c.name, c.scope) for c in commands] == [("is_core_of", "STATE")]
