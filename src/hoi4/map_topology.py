"""Country-territory topology checks backed by HOI4's effective province map.

NumPy and Pillow are imported only when topology analysis is requested.  This
keeps the SDK's ordinary content APIs dependency-free while allowing callers
with the ``map`` extra to catch accidental enclaves and disconnected states.
"""

from __future__ import annotations

import csv
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from functools import lru_cache
from importlib import import_module
from pathlib import Path
from typing import Any, Iterable

from .map_render import parse_definition_csv
from .types import State


@dataclass(frozen=True)
class TerritoryComponent:
    """One connected component of a country's owned land."""

    state_ids: tuple[int, ...]
    province_ids: tuple[int, ...]
    land_province_count: int
    contains_capital: bool = False


def find_country_territory_components(
    mod_root: Path,
    hoi4_install: Path | None,
    states: Iterable[State],
    *,
    country_tag: str,
    capital_state_id: int,
    minimum_land_provinces: int = 2,
    allowed_state_ids: Iterable[int] = (),
) -> tuple[TerritoryComponent, ...]:
    """Return significant owned components disconnected from the capital.

    Mod map files override their installed-game counterparts.  Pixel borders
    and valid explicit adjacency rows both contribute graph edges.
    """

    if minimum_land_provinces < 1:
        raise ValueError("minimum_land_provinces must be at least 1")
    provinces_path = _effective_map_file(mod_root, hoi4_install, "provinces.bmp")
    definition_path = _effective_map_file(mod_root, hoi4_install, "definition.csv")
    adjacency_path = _optional_effective_map_file(
        mod_root,
        hoi4_install,
        "adjacencies.csv",
    )
    fingerprints = (
        provinces_path.stat().st_mtime_ns,
        definition_path.stat().st_mtime_ns,
        adjacency_path.stat().st_mtime_ns if adjacency_path is not None else 0,
    )
    land_provinces, graph, _coastal_land_provinces = _load_province_topology(
        provinces_path,
        definition_path,
        adjacency_path,
        fingerprints,
    )

    owned_states = {
        state.id: state
        for state in states
        if state.owner == country_tag
    }
    if capital_state_id not in owned_states:
        return ()

    province_to_state: dict[int, int] = {}
    state_land_provinces: dict[int, set[int]] = {}
    for state_id, state in owned_states.items():
        land = {
            province_id
            for province_id in state.provinces
            if province_id in land_provinces
        }
        state_land_provinces[state_id] = land
        for province_id in land:
            province_to_state[province_id] = state_id

    state_graph: dict[int, set[int]] = {
        state_id: set() for state_id in owned_states
    }
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

    components = _connected_components(state_graph)
    allowed = set(allowed_state_ids)
    result: list[TerritoryComponent] = []
    for state_ids in components:
        contains_capital = capital_state_id in state_ids
        province_ids = sorted(
            {
                province_id
                for state_id in state_ids
                for province_id in state_land_provinces.get(state_id, ())
            }
        )
        if contains_capital:
            continue
        if len(province_ids) < minimum_land_provinces:
            continue
        if set(state_ids) <= allowed:
            continue
        result.append(
            TerritoryComponent(
                state_ids=tuple(sorted(state_ids)),
                province_ids=tuple(province_ids),
                land_province_count=len(province_ids),
                contains_capital=False,
            )
        )
    return tuple(
        sorted(
            result,
            key=lambda component: (
                component.state_ids,
                component.province_ids,
            ),
        )
    )


def find_enclosed_foreign_components(
    mod_root: Path,
    hoi4_install: Path | None,
    states: Iterable[State],
    *,
    country_tag: str,
    minimum_land_provinces: int = 1,
    allowed_state_ids: Iterable[int] = (),
) -> tuple[TerritoryComponent, ...]:
    """Return foreign land components completely enclosed by ``country_tag``.

    A component touching sea or unmapped land is not considered enclosed.  This
    avoids reporting coastal countries or incomplete map data as holes while
    still catching one-state and multi-state land enclaves.
    """

    if minimum_land_provinces < 1:
        raise ValueError("minimum_land_provinces must be at least 1")
    provinces_path = _effective_map_file(mod_root, hoi4_install, "provinces.bmp")
    definition_path = _effective_map_file(mod_root, hoi4_install, "definition.csv")
    adjacency_path = _optional_effective_map_file(
        mod_root,
        hoi4_install,
        "adjacencies.csv",
    )
    fingerprints = (
        provinces_path.stat().st_mtime_ns,
        definition_path.stat().st_mtime_ns,
        adjacency_path.stat().st_mtime_ns if adjacency_path is not None else 0,
    )
    land_provinces, graph, coastal_land_provinces = _load_province_topology(
        provinces_path,
        definition_path,
        adjacency_path,
        fingerprints,
    )
    effective_states = tuple(states)
    state_by_id = {state.id: state for state in effective_states}
    province_to_state = {
        province_id: state.id
        for state in effective_states
        for province_id in state.provinces
        if province_id in land_provinces
    }
    foreign_provinces = {
        province_id
        for province_id, state_id in province_to_state.items()
        if state_by_id[state_id].owner
        and state_by_id[state_id].owner != country_tag
    }
    foreign_graph = {
        province_id: graph.get(province_id, set()) & foreign_provinces
        for province_id in foreign_provinces
    }
    allowed = set(allowed_state_ids)
    result: list[TerritoryComponent] = []
    for province_ids in _connected_components(foreign_graph):
        if len(province_ids) < minimum_land_provinces:
            continue
        if province_ids & coastal_land_provinces:
            continue
        state_ids = {
            province_to_state[province_id]
            for province_id in province_ids
            if province_id in province_to_state
        }
        if not state_ids or state_ids <= allowed:
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
    return tuple(
        sorted(
            result,
            key=lambda component: (component.state_ids, component.province_ids),
        )
    )


@lru_cache(maxsize=4)
def _load_province_topology(
    provinces_path: Path,
    definition_path: Path,
    adjacency_path: Path | None,
    fingerprints: tuple[int, int, int],
) -> tuple[frozenset[int], dict[int, set[int]], frozenset[int]]:
    # ``fingerprints`` is intentionally unused in the body: it forms part of
    # the cache key so replacing a map file invalidates the derived graph.
    del fingerprints
    try:
        np = import_module("numpy")
        image_module = import_module("PIL.Image")
    except ImportError as error:  # pragma: no cover - optional environment
        raise RuntimeError(
            'Territory topology requires the "map" extra: '
            "pip install hoi4-agent-sdk[map]"
        ) from error

    rgb_to_province = parse_definition_csv(definition_path)
    province_types = _province_types(definition_path)
    land_provinces = frozenset(
        province_id
        for province_id, province_type in province_types.items()
        if province_type == "land"
    )
    pixels = np.asarray(
        image_module.open(provinces_path).convert("RGB"),
        dtype=np.uint32,
    )
    packed = (
        (pixels[:, :, 0] << 16)
        | (pixels[:, :, 1] << 8)
        | pixels[:, :, 2]
    )
    packed_to_province = {
        (red << 16) | (green << 8) | blue: province_id
        for (red, green, blue), province_id in rgb_to_province.items()
    }
    unique_colors, inverse = np.unique(packed, return_inverse=True)
    color_ids = np.fromiter(
        (
            packed_to_province.get(int(color), 0)
            for color in unique_colors
        ),
        dtype=np.int32,
        count=len(unique_colors),
    )
    province_grid = color_ids[inverse].reshape(packed.shape)
    graph: dict[int, set[int]] = {
        province_id: set() for province_id in land_provinces
    }
    coastal_land_provinces: set[int] = set()
    _add_pixel_edges(graph, province_grid[:, :-1], province_grid[:, 1:], land_provinces)
    _add_pixel_edges(graph, province_grid[:-1, :], province_grid[1:, :], land_provinces)
    _add_coastal_land(
        coastal_land_provinces,
        province_grid[:, :-1],
        province_grid[:, 1:],
        land_provinces,
    )
    _add_coastal_land(
        coastal_land_provinces,
        province_grid[:-1, :],
        province_grid[1:, :],
        land_provinces,
    )
    # HOI4's world map wraps east-to-west.
    _add_pixel_edges(graph, province_grid[:, :1], province_grid[:, -1:], land_provinces)
    _add_coastal_land(
        coastal_land_provinces,
        province_grid[:, :1],
        province_grid[:, -1:],
        land_provinces,
    )
    if adjacency_path is not None:
        _add_explicit_adjacencies(graph, adjacency_path, land_provinces)
    return land_provinces, graph, frozenset(coastal_land_provinces)


def _effective_map_file(
    mod_root: Path,
    hoi4_install: Path | None,
    filename: str,
) -> Path:
    path = _optional_effective_map_file(mod_root, hoi4_install, filename)
    if path is None:
        raise FileNotFoundError(
            f"Effective HOI4 map file is missing: map/{filename}"
        )
    return path


def _optional_effective_map_file(
    mod_root: Path,
    hoi4_install: Path | None,
    filename: str,
) -> Path | None:
    for root in (mod_root, hoi4_install):
        if root is None:
            continue
        candidate = root / "map" / filename
        if candidate.is_file():
            return candidate
    return None


def _province_types(path: Path) -> dict[int, str]:
    result: dict[int, str] = {}
    with path.open(encoding="utf-8", errors="ignore", newline="") as handle:
        for row in csv.reader(handle, delimiter=";"):
            if len(row) < 5:
                continue
            try:
                province_id = int(row[0].strip())
            except ValueError:
                continue
            result[province_id] = row[4].strip().lower()
    return result


def _add_pixel_edges(
    graph: dict[int, set[int]],
    left: Any,
    right: Any,
    land_provinces: AbstractSet[int],
) -> None:
    np = import_module("numpy")

    mask = (left != right) & (left != 0) & (right != 0)
    if not bool(np.any(mask)):
        return
    pairs = np.stack((left[mask], right[mask]), axis=1)
    for raw_left, raw_right in np.unique(pairs, axis=0):
        first = int(raw_left)
        second = int(raw_right)
        if first not in land_provinces or second not in land_provinces:
            continue
        graph.setdefault(first, set()).add(second)
        graph.setdefault(second, set()).add(first)


def _add_coastal_land(
    coastal: set[int],
    left: Any,
    right: Any,
    land_provinces: AbstractSet[int],
) -> None:
    np = import_module("numpy")

    mask = (left != right) & (left != 0) & (right != 0)
    if not bool(np.any(mask)):
        return
    pairs = np.stack((left[mask], right[mask]), axis=1)
    for raw_left, raw_right in np.unique(pairs, axis=0):
        first = int(raw_left)
        second = int(raw_right)
        if first in land_provinces and second not in land_provinces:
            coastal.add(first)
        if second in land_provinces and first not in land_provinces:
            coastal.add(second)


def _add_explicit_adjacencies(
    graph: dict[int, set[int]],
    path: Path,
    land_provinces: AbstractSet[int],
) -> None:
    with path.open(encoding="utf-8", errors="ignore", newline="") as handle:
        for row in csv.reader(handle, delimiter=";"):
            if len(row) < 3:
                continue
            try:
                first = int(row[0].strip())
                second = int(row[1].strip())
            except ValueError:
                continue
            adjacency_type = row[2].strip().lower()
            if first < 0 or second < 0:
                continue
            if adjacency_type in {"impassable", "disabled"}:
                continue
            if first not in land_provinces or second not in land_provinces:
                continue
            graph.setdefault(first, set()).add(second)
            graph.setdefault(second, set()).add(first)


def _connected_components(graph: dict[int, set[int]]) -> list[set[int]]:
    remaining = set(graph)
    result: list[set[int]] = []
    while remaining:
        root = min(remaining)
        component: set[int] = set()
        stack = [root]
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            remaining.discard(current)
            stack.extend(graph.get(current, set()) - component)
        result.append(component)
    return result
