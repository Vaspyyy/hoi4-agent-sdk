import pytest

from hoi4 import Mod
from hoi4.parser import PdxNode, parse_pdx, serialize_pdx
from hoi4.script import pdx_string, pdx_value


@pytest.mark.parametrize("operator", list("=<>!"))
@pytest.mark.parametrize("pattern", ["{}Tank", "Ta{}nk", "Tank{}"])
def test_operator_scalars_round_trip_through_all_emitters(operator, pattern):
    value = pattern.format(operator)
    assert pdx_value(value) == f'"{value}"'
    scalar = parse_pdx(f"name = {pdx_value(value)}").find("name")
    assert scalar.value == value
    assert scalar.quoted

    source = serialize_pdx(PdxNode(key="name", value=value))
    assert source == f'name = "{value}"\n'
    assert parse_pdx(source).find("name").value == value

    source = Mod.effect_add_equipment(
        "infantry_equipment_0", 100, variant_name=value
    )
    equipment = parse_pdx(source).get_block("add_equipment_to_stockpile")
    assert equipment.find("variant_name").value == value
    assert equipment.get_int("amount") == 100
    assert equipment.find("type").value == "infantry_equipment_0"


@pytest.mark.parametrize(
    "value, expected",
    [
        ("infantry_equipment_0", "infantry_equipment_0"),
        ("yes", "yes"),
        ("no", "no"),
        ("", '""'),
        ("Tank name", '"Tank name"'),
        ('"Tank"', r'"\"Tank\""'),
        ('Tank "Ace"!', r'"Tank \"Ace\"!"'),
        (r"gfx\tank", r"gfx\tank"),
        (r"gfx\tank!", r'"gfx\\tank!"'),
        ('Tank\\"Ace"!', r'"Tank\\\"Ace\"!"'),
        ("Tank#1", '"Tank#1"'),
        ("{Tank}", '"{Tank}"'),
    ],
)
def test_scalar_controls_preserve_text_and_values(value, expected):
    assert pdx_value(value) == expected
    assert parse_pdx(f"name = {expected}").find("name").value == value
    source = serialize_pdx(PdxNode(key="name", value=value))
    assert source == f"name = {expected}\n"
    assert parse_pdx(source).find("name").value == value


@pytest.mark.parametrize("value, expected", [(True, "yes"), (False, "no")])
def test_boolean_scalars(value, expected):
    assert pdx_value(value) == expected
    assert parse_pdx(f"name = {pdx_value(value)}").find("name").value == expected


@pytest.mark.parametrize("value", ["Tank", "", "Tank!", 'Tank\\"Ace"!'])
def test_explicitly_quoted_nodes_stay_quoted(value):
    source = serialize_pdx(PdxNode(key="name", value=value, quoted=True))
    assert source == f"name = {pdx_string(value)}\n"
    node = parse_pdx(source).find("name")
    assert node.value == value
    assert node.quoted


@pytest.mark.parametrize("operator", ["=", "<", ">", "!=", "<=", ">="])
def test_assignment_keys_and_operators_are_preserved(operator):
    source = serialize_pdx(PdxNode(key="name", operator=operator, value="Tank!"))
    assert source == f'name {operator} "Tank!"\n'
    node = parse_pdx(source).find("name")
    assert node.key == "name"
    assert node.operator == operator
    assert node.value == "Tank!"


@pytest.mark.parametrize("invalid", ["\n", "\r", "\x00"])
@pytest.mark.parametrize("emit", [pdx_value, pdx_string])
def test_single_line_validation_is_preserved(invalid, emit):
    with pytest.raises(ValueError, match="newlines or NUL"):
        emit(f"Tank!{invalid}name")
