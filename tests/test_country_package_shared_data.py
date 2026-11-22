"""The country package check builds its map and script evidence once per run.

Both used to be computed again for every country. The shared versions must
give exactly what the per-country versions gave; the per-country code is
kept here as the reference.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import hoi4.mod._country_package as country_package
from hoi4 import Mod
from hoi4.map_topology import (
    ProvinceOwnership,
    TerritoryComponent,
    _connected_components,
    find_country_territory_components,
    find_enclosed_foreign_components,
)
from hoi4.mod._country_package import _runtime_activation_of
from hoi4.parser import PdxNode, parse_pdx
from hoi4.types import State

# ── the per-country reference versions ─────────────────────────────


def reference_evidence(
    roots: list[tuple[str, PdxNode]], tag: str
) -> tuple[set[int], set[int], set[str], set[str]]:
    """The walk ``_country_runtime_activation_evidence`` did for one tag."""

    transferred_states: set[int] = set()
    cored_states: set[int] = set()
    activators: set[str] = set()
    sources: set[str] = set()

    def scalar_int(node: PdxNode) -> int | None:
        if node.value is None:
            return None
        try:
            return int(node.value)
        except ValueError:
            return None

    def walk(node: PdxNode, *, in_country_scope: bool = False, state_scope: int | None = None) -> None:
        country_scope = in_country_scope
        nested_state_scope = state_scope
        if node.is_block():
            if node.key == tag:
                country_scope = True
                nested_state_scope = None
            elif node.key and node.key.isdigit():
                nested_state_scope = int(node.key)
                country_scope = False
        if country_scope and node.key == "transfer_state":
            state_id = scalar_int(node)
            if state_id is not None:
                transferred_states.add(state_id)
        if nested_state_scope is not None and node.key == "add_core_of" and node.value == tag:
            cored_states.add(nested_state_scope)
        if node.key in {"release", "release_puppet", "puppet"} and node.value == tag:
            activators.add(node.key)
        if (
            node.key == "create_subject"
            and node.is_block()
            and any(
                child.key in {"subject", "target"} and child.value == tag
                for child in node.children
            )
        ):
            activators.add("create_subject")
        for child in node.children:
            walk(child, in_country_scope=country_scope, state_scope=nested_state_scope)

    for source, root in roots:
        before = (len(transferred_states), len(cored_states), len(activators))
        walk(root)
        if (len(transferred_states), len(cored_states), len(activators)) != before:
            sources.add(source)
    return transferred_states, cored_states, activators, sources


def shared_evidence(
    roots: list[tuple[str, PdxNode]], tag: str
) -> tuple[set[int], set[int], set[str], set[str]]:
    """The shared index, merged the way ``_build_runtime_activation_index`` does."""

    transferred: set[int] = set()
    cored: set[int] = set()
    activated: set[str] = set()
    sources: set[str] = set()
    for source, root in roots:
        found = _runtime_activation_of(root).get(tag)
        if found is None:
            continue
        states, cores, activators = found
        if not (states <= transferred and cores <= cored and activators <= activated):
            sources.add(source)
        transferred |= states
        cored |= cores
        activated |= activators
    return transferred, cored, activated, sources


def reference_enclosed(
    land: frozenset[int],
    graph: dict[int, set[int]],
    coastal: frozenset[int],
    states: tuple[State, ...],
    country_tag: str,
    minimum_land_provinces: int,
    allowed_state_ids: set[int],
) -> tuple[TerritoryComponent, ...]:
    """``find_enclosed_foreign_components`` as it was, after reading the map."""

    state_by_id = {state.id: state for state in states}
    province_to_state = {
        province_id: state.id
        for state in states
        for province_id in state.provinces
        if province_id in land
    }
    foreign_provinces = {
        province_id
        for province_id, state_id in province_to_state.items()
        if state_by_id[state_id].owner and state_by_id[state_id].owner != country_tag
    }
    foreign_graph = {
        province_id: graph.get(province_id, set()) & foreign_provinces
        for province_id in foreign_provinces
    }
    result: list[TerritoryComponent] = []
    for province_ids in _connected_components(foreign_graph):
        if len(province_ids) < minimum_land_provinces:
            continue
        if province_ids & coastal:
            continue
        state_ids = {
            province_to_state[province_id]
            for province_id in province_ids
            if province_id in province_to_state
        }
        if not state_ids or state_ids <= allowed_state_ids:
            continue
        boundary = {
            neighbour
            for province_id in province_ids
            for neighbour in graph.get(province_id, ())
            if neighbour not in province_ids
        }
        if not boundary or any(
            neighbour not in province_to_state
            or state_by_id[province_to_state[neighbour]].owner != country_tag
            for neighbour in boundary
        ):
            continue
        result.append(
            TerritoryComponent(
                state_ids=tuple(sorted(state_ids)),
                province_ids=tuple(sorted(province_ids)),
                land_province_count=len(province_ids),
            )
        )
    return tuple(sorted(result, key=lambda c: (c.state_ids, c.province_ids)))


def reference_disconnected(
    land: frozenset[int],
    graph: dict[int, set[int]],
    states: tuple[State, ...],
    country_tag: str,
    capital_state_id: int,
    minimum_land_provinces: int,
    allowed_state_ids: set[int],
) -> tuple[TerritoryComponent, ...]:
    """``find_country_territory_components`` as it was, after reading the map."""

    owned_states = {state.id: state for state in states if state.owner == country_tag}
    if capital_state_id not in owned_states:
        return ()
    province_to_state: dict[int, int] = {}
    state_land_provinces: dict[int, set[int]] = {}
    for state_id, state in owned_states.items():
        owned_land = {province_id for province_id in state.provinces if province_id in land}
        state_land_provinces[state_id] = owned_land
        for province_id in owned_land:
            province_to_state[province_id] = state_id
    state_graph: dict[int, set[int]] = {state_id: set() for state_id in owned_states}
    for province_id, neighbours in graph.items():
        left_state = province_to_state.get(province_id)
        if left_state is None:
            continue
        for neighbour in neighbours:
            right_state = province_to_state.get(neighbour)
            if right_state is None or right_state == left_state:
                continue
            state_graph[left_state].add(right_state)
            state_graph[right_state].add(left_state)
    result: list[TerritoryComponent] = []
    for state_ids in _connected_components(state_graph):
        province_ids = sorted(
            {p for state_id in state_ids for p in state_land_provinces.get(state_id, ())}
        )
        if capital_state_id in state_ids or len(province_ids) < minimum_land_provinces:
            continue
        if set(state_ids) <= allowed_state_ids:
            continue
        result.append(
            TerritoryComponent(
                state_ids=tuple(sorted(state_ids)),
                province_ids=tuple(province_ids),
                land_province_count=len(province_ids),
            )
        )
    return tuple(sorted(result, key=lambda c: (c.state_ids, c.province_ids)))


# ── random scripts ─────────────────────────────────────────────────

TAGS = ["GER", "ITA", "FRA", "123"]
BLOCK_KEYS = [*TAGS, "5", "7", "if", "limit", "every_country", "hidden_effect"]


def statements() -> st.SearchStrategy[str]:
    leaf = st.one_of(
        st.builds(lambda n: f"transfer_state = {n}", st.sampled_from(["1", "5", "9", "x"])),
        st.builds(lambda tag: f"add_core_of = {tag}", st.sampled_from(TAGS)),
        st.builds(
            lambda key, tag: f"{key} = {tag}",
            st.sampled_from(["release", "release_puppet", "puppet", "annex_country"]),
            st.sampled_from(TAGS),
        ),
        st.builds(
            lambda key, tag: f"create_subject = {{ {key} = {tag} }}",
            st.sampled_from(["subject", "target", "other"]),
            st.sampled_from(TAGS),
        ),
    )
    return st.recursive(
        leaf,
        lambda inner: st.builds(
            lambda key, body: f"{key} = {{ {' '.join(body)} }}",
            st.sampled_from(BLOCK_KEYS),
            st.lists(inner, max_size=4),
        ),
        max_leaves=12,
    )


@settings(max_examples=300, deadline=None)
@given(st.lists(st.lists(statements(), min_size=1, max_size=4), min_size=1, max_size=5))
def test_the_shared_index_gives_what_the_walk_per_country_gave(bodies: list[list[str]]) -> None:
    roots = [(f"script {index}", parse_pdx(" ".join(body))) for index, body in enumerate(bodies)]
    for tag in [*TAGS, "5", "if", "XXX"]:
        assert shared_evidence(roots, tag) == reference_evidence(roots, tag), tag


def test_the_mod_answers_every_country_from_one_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = Mod(tmp_path)
    mod.create_event("test.1", immediate="GER = { transfer_state = 1 } 2 = { add_core_of = ITA }")
    mod.create_event("test.2", immediate="release = FRA ITA = { transfer_state = 3 }")
    walked: list[PdxNode] = []

    def counted(root: PdxNode) -> dict[str, tuple[set[int], set[int], set[str]]]:
        walked.append(root)
        return _runtime_activation_of(root)

    monkeypatch.setattr(country_package, "_runtime_activation_of", counted)
    mod._validation_run = {}
    try:
        assert mod._country_runtime_activation_evidence("GER")[:3] == ({1}, set(), set())
        assert mod._country_runtime_activation_evidence("ITA")[:3] == ({3}, {2}, set())
        assert mod._country_runtime_activation_evidence("FRA")[:3] == (set(), set(), {"release"})
        assert mod._country_runtime_activation_evidence("SWE") == (set(), set(), set(), set())
        assert len(walked) == 2  # once per script, not once per country
    finally:
        mod._validation_run = None


# ── random maps ────────────────────────────────────────────────────


@st.composite
def maps(draw: st.DrawFn) -> tuple[frozenset[int], dict[int, set[int]], frozenset[int], tuple[State, ...]]:
    count = draw(st.integers(min_value=1, max_value=24))
    provinces = list(range(1, count + 1))
    graph: dict[int, set[int]] = {province: set() for province in provinces}
    for left, right in draw(
        st.lists(st.tuples(st.sampled_from(provinces), st.sampled_from(provinces)), max_size=50)
    ):
        if left != right:
            graph[left].add(right)
            graph[right].add(left)
    coastal = frozenset(draw(st.sets(st.sampled_from(provinces), max_size=4)))
    # Some provinces belong to no state; a state may list a province twice
    # or list one that is not land.
    states: list[State] = []
    for state_id in range(1, draw(st.integers(min_value=1, max_value=8)) + 1):
        listed = draw(st.lists(st.sampled_from([*provinces, 99]), max_size=5))
        owner = draw(st.sampled_from(["GER", "ITA", "FRA", ""]))
        states.append(State(id=state_id, owner=owner, provinces=listed))
    return frozenset(provinces), graph, coastal, tuple(states)


def ownership_of(
    land: frozenset[int], graph: dict[int, set[int]], coastal: frozenset[int], states: tuple[State, ...]
) -> ProvinceOwnership:
    states_of: dict[str, list[State]] = {}
    for state in states:
        if state.owner:
            states_of.setdefault(state.owner, []).append(state)
    return ProvinceOwnership(
        land=land,
        graph=graph,
        coastal=coastal,
        state_by_id={state.id: state for state in states},
        province_to_state={
            province: state.id for state in states for province in state.provinces if province in land
        },
        states_of=states_of,
    )


@settings(max_examples=500, deadline=None)
@given(
    maps(),
    st.sampled_from(["GER", "ITA", "SWE"]),
    st.integers(min_value=1, max_value=3),
    st.sets(st.integers(min_value=1, max_value=8), max_size=2),
)
def test_the_shared_map_gives_what_the_search_per_country_gave(
    world: tuple[frozenset[int], dict[int, set[int]], frozenset[int], tuple[State, ...]],
    tag: str,
    minimum: int,
    allowed: set[int],
) -> None:
    land, graph, coastal, states = world
    ownership = ownership_of(land, graph, coastal, states)
    root = Path("/nowhere")
    assert find_enclosed_foreign_components(
        root, None, states, country_tag=tag, minimum_land_provinces=minimum,
        allowed_state_ids=allowed, ownership=ownership,
    ) == reference_enclosed(land, graph, coastal, states, tag, minimum, allowed)
    for capital in {state.id for state in states}:
        assert find_country_territory_components(
            root, None, states, country_tag=tag, capital_state_id=capital,
            minimum_land_provinces=minimum, allowed_state_ids=allowed, ownership=ownership,
        ) == reference_disconnected(land, graph, states, tag, capital, minimum, allowed)
