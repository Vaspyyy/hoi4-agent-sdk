from __future__ import annotations

from hypothesis import given, strategies as st

from hoi4.parser import parse_pdx, serialize_pdx
from hoi4.patching import top_level_assignments
from hoi4.structured_patching import patch_scalar_mapping

identifier = st.from_regex(r"[a-z][a-z0-9_]{0,12}", fullmatch=True)
numeric_key = st.integers(min_value=0, max_value=9999).map(str)
date_key = st.tuples(
    st.integers(min_value=1, max_value=2099),
    st.integers(min_value=1, max_value=12),
    st.integers(min_value=1, max_value=28),
).map(lambda parts: ".".join(map(str, parts)))
key = st.one_of(identifier, numeric_key, date_key)
scalar = st.one_of(
    st.integers(min_value=-100_000, max_value=100_000).map(str),
    identifier,
    st.text(
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu", "Nd"),
            whitelist_characters=" _-",
        ),
        min_size=1,
        max_size=20,
    ).map(lambda value: '"' + value.replace('"', '\\"') + '"'),
)


@st.composite
def paradox_script(draw: st.DrawFn, depth: int = 0) -> str:
    count = draw(st.integers(min_value=1, max_value=4))
    lines: list[str] = []
    for _ in range(count):
        item_key = draw(key)
        make_block = depth < 3 and draw(st.booleans())
        if make_block:
            body = draw(paradox_script(depth + 1))
            indented = "\n".join(f"\t{line}" for line in body.splitlines())
            lines.append(f"{item_key} = {{\n{indented}\n}}")
        else:
            lines.append(f"{item_key} = {draw(scalar)}")
    return "\n".join(lines) + "\n"


@given(paradox_script())
def test_parse_serialize_parse_equivalence(source: str) -> None:
    parsed = parse_pdx(source)
    assert parse_pdx(serialize_pdx(parsed)) == parsed


@given(st.lists(key, min_size=1, max_size=20, unique=True))
def test_numeric_and_date_keys_are_scanned(keys: list[str]) -> None:
    source = "".join(f"{item_key} = value\n" for item_key in keys)
    assert [span.key for span in top_level_assignments(source)] == keys


@given(
    st.lists(
        st.tuples(key, scalar),
        min_size=1,
        max_size=15,
        unique_by=lambda pair: pair[0],
    )
)
def test_noop_scalar_patch_is_source_preserving(values: list[tuple[str, str]]) -> None:
    source = "".join(
        f"# keep {index}\n{item_key}  =  {value}\n"
        for index, (item_key, value) in enumerate(values)
    )
    mapping = {
        item_key: value.strip('"') if value.startswith('"') else value
        for item_key, value in values
    }
    assert patch_scalar_mapping(source, mapping) == source
