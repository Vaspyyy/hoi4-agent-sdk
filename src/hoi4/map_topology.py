"""Country-territory topology checks backed by HOI4's effective province map.

NumPy and Pillow are imported only when topology analysis is requested.  This
keeps the SDK's ordinary content APIs dependency-free while allowing callers
with the ``map`` extra to catch accidental enclaves and disconnected states.
"""

from __future__ import annotations

import csv
import io
import math
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from functools import lru_cache
from importlib import import_module
from pathlib import Path
from typing import Any
from collections.abc import Iterable

from ._disk_cache import DiskAnalysis, int_list
from ._file_cache import cached_files_result
from .layers import ContentView, as_view
from .map_render import parse_definition_csv
from .textio import read_text
from .types import State


@dataclass(frozen=True)
class TerritoryComponent:
    """One connected component of a country's owned land."""

    state_ids: tuple[int, ...]
    province_ids: tuple[int, ...]
    land_province_count: int
    contains_capital: bool = False


@dataclass(frozen=True)
class ProvinceOwnership:
    """The effective map with the state and owner of each land province.

    Built once (:func:`province_ownership`) and shared when the territory of
    many countries is checked against the same states, such as the country
    package check of every country in one ``validate()`` run.
    """

    land: frozenset[int]
    graph: dict[int, set[int]]
    coastal: frozenset[int]
    state_by_id: dict[int, State]
    # Land province -> the state that lists it (the last such state wins).
    province_to_state: dict[int, int]
    # Owner tag -> its states, in the order the states were given.
    states_of: dict[str, list[State]]


def province_ownership(
    mod_root: Path,
    hoi4_install: Path | ContentView | None,
    states: Iterable[State],
) -> ProvinceOwnership:
    """The effective map and the owner of every land province of ``states``.

    Raises like :func:`load_province_grid`.
    """

    land, graph, coastal = _effective_topology(mod_root, hoi4_install)
    effective_states = tuple(states)
    states_of: dict[str, list[State]] = {}
    for state in effective_states:
        if state.owner:
            states_of.setdefault(state.owner, []).append(state)
    return ProvinceOwnership(
        land=land,
        graph=graph,
        coastal=coastal,
        state_by_id={state.id: state for state in effective_states},
        province_to_state={
            province_id: state.id
            for state in effective_states
            for province_id in state.provinces
            if province_id in land
        },
        states_of=states_of,
    )


def find_country_territory_components(
    mod_root: Path,
    hoi4_install: Path | ContentView | None,
    states: Iterable[State],
    *,
    country_tag: str,
    capital_state_id: int,
    minimum_land_provinces: int = 2,
    allowed_state_ids: Iterable[int] = (),
    ownership: ProvinceOwnership | None = None,
) -> tuple[TerritoryComponent, ...]:
    """Return significant owned components disconnected from the capital.

    Mod map files override their installed-game counterparts.  Pixel borders
    and valid explicit adjacency rows both contribute graph edges. Pass
    ``ownership`` (built from the same ``states``) to share the map between
    calls.
    """

    if minimum_land_provinces < 1:
        raise ValueError("minimum_land_provinces must be at least 1")
    if ownership is None:
        ownership = province_ownership(mod_root, hoi4_install, states)
    land_provinces, graph = ownership.land, ownership.graph

    owned_states = {state.id: state for state in ownership.states_of.get(country_tag, ())}
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
    # Only the country's own provinces can start an edge between its states.
    for province_id, left_state in province_to_state.items():
        for neighbour in graph.get(province_id, ()):
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
    hoi4_install: Path | ContentView | None,
    states: Iterable[State],
    *,
    country_tag: str,
    minimum_land_provinces: int = 1,
    allowed_state_ids: Iterable[int] = (),
    ownership: ProvinceOwnership | None = None,
) -> tuple[TerritoryComponent, ...]:
    """Return foreign land components completely enclosed by ``country_tag``.

    A component touching sea or unmapped land is not considered enclosed.  This
    avoids reporting coastal countries or incomplete map data as holes while
    still catching one-state and multi-state land enclaves. Pass
    ``ownership`` (built from the same ``states``) to share the map between
    calls.
    """

    if minimum_land_provinces < 1:
        raise ValueError("minimum_land_provinces must be at least 1")
    if ownership is None:
        ownership = province_ownership(mod_root, hoi4_install, states)
    graph, coastal = ownership.graph, ownership.coastal
    province_to_state, state_by_id = ownership.province_to_state, ownership.state_by_id

    def owner(province_id: int) -> str | None:
        state_id = province_to_state.get(province_id)
        return None if state_id is None else state_by_id[state_id].owner

    own = [
        province_id
        for province_id, state_id in province_to_state.items()
        if state_by_id[state_id].owner == country_tag
    ]
    # An enclosed component is a connected set of foreign provinces whose
    # neighbours all belong to the country, so it touches the country's
    # border: search outward from there. The map's edges go both ways, so a
    # search from any province of a component finds all of it. A search
    # stops at the first sign that its component is not enclosed: the coast,
    # land of no state or of a state without owner, or a province an earlier
    # stopped search reached (connected to it, so the same component). A
    # finished search has walked its whole component.
    seen: set[int] = set()
    allowed = set(allowed_state_ids)
    result: list[TerritoryComponent] = []
    for start in sorted(
        {neighbour for province_id in own for neighbour in graph.get(province_id, ())}
    ):
        if start in seen:
            continue
        start_owner = owner(start)
        if not start_owner or start_owner == country_tag:
            continue
        component = {start}
        seen.add(start)
        stack = [start]
        enclosed = True
        while stack and enclosed:
            current = stack.pop()
            if current in coastal:
                enclosed = False
                break
            for neighbour in graph.get(current, ()):
                if neighbour in component:
                    continue
                neighbour_owner = owner(neighbour)
                if neighbour_owner == country_tag:
                    continue
                if not neighbour_owner or neighbour in seen:
                    enclosed = False
                    break
                component.add(neighbour)
                seen.add(neighbour)
                stack.append(neighbour)
        if not enclosed or len(component) < minimum_land_provinces:
            continue
        state_ids = {province_to_state[province_id] for province_id in component}
        if state_ids <= allowed:
            continue
        result.append(
            TerritoryComponent(
                state_ids=tuple(sorted(state_ids)),
                province_ids=tuple(sorted(component)),
                land_province_count=len(component),
            )
        )
    return tuple(
        sorted(
            result,
            key=lambda component: (component.state_ids, component.province_ids),
        )
    )


_Topology = tuple[frozenset[int], dict[int, set[int]], frozenset[int]]


def _encode_topology(topology: _Topology) -> dict[str, list[Any]]:
    land, graph, coastal = topology
    return {
        "land": sorted(land),
        "graph": [[province, sorted(neighbours)] for province, neighbours in graph.items()],
        "coastal": sorted(coastal),
    }


def _decode_topology(data: object) -> _Topology:
    """A stored topology. Anything else is a damaged entry and is computed again."""

    if not isinstance(data, dict) or not isinstance(data["graph"], list):
        raise TypeError("a stored topology must be a map with a list of neighbours")
    graph: dict[int, set[int]] = {}
    for province, neighbours in data["graph"]:
        if type(province) is not int:
            raise TypeError("a stored province id must be a whole number")
        graph[province] = set(int_list(neighbours))
    return frozenset(int_list(data["land"])), graph, frozenset(int_list(data["coastal"]))


_TOPOLOGY_ON_DISK = DiskAnalysis("province_topology", _encode_topology, _decode_topology)


def _effective_topology(mod_root: Path, hoi4_install: Path | ContentView | None) -> _Topology:
    """Land provinces, the land adjacency graph and coastal land of the effective map.

    Kept in memory while the map files are unchanged. When the mod has no
    map files of its own, the result is also kept on disk for later
    processes (``hoi4._disk_cache``).
    """

    provinces_path = _effective_map_file(mod_root, hoi4_install, "provinces.bmp")
    definition_path = _effective_map_file(mod_root, hoi4_install, "definition.csv")
    adjacency_path = _optional_effective_map_file(mod_root, hoi4_install, "adjacencies.csv")
    fingerprints = (
        provinces_path.stat().st_mtime_ns,
        definition_path.stat().st_mtime_ns,
        adjacency_path.stat().st_mtime_ns if adjacency_path is not None else 0,
    )
    paths = [provinces_path, definition_path]
    if adjacency_path is not None:
        paths.append(adjacency_path)
    inherited = not any(path.is_relative_to(mod_root) for path in paths)
    return _load_province_topology(
        provinces_path, definition_path, adjacency_path, fingerprints, inherited
    )


@lru_cache(maxsize=4)
def _load_province_topology(
    provinces_path: Path,
    definition_path: Path,
    adjacency_path: Path | None,
    fingerprints: tuple[int, int, int],
    on_disk: bool = False,
) -> _Topology:
    # ``fingerprints`` is intentionally unused in the body: it forms part of
    # the cache key so replacing a map file invalidates the derived graph.
    del fingerprints
    if on_disk:
        paths = [provinces_path, definition_path]
        if adjacency_path is not None:
            paths.append(adjacency_path)
        return cached_files_result(
            "province_topology",
            paths,
            lambda: _compute_province_topology(provinces_path, definition_path, adjacency_path),
            _TOPOLOGY_ON_DISK,
        )
    return _compute_province_topology(provinces_path, definition_path, adjacency_path)


def _compute_province_topology(
    provinces_path: Path,
    definition_path: Path,
    adjacency_path: Path | None,
) -> _Topology:
    try:
        np = import_module("numpy")
        image_module = import_module("PIL.Image")
    except ImportError as error:  # pragma: no cover - optional environment
        raise RuntimeError(
            'Territory topology requires the "map" extra: '
            "pip install hoi4-agent-sdk[map]"
        ) from error

    province_types = _province_types(definition_path)
    land_provinces = frozenset(
        province_id
        for province_id, province_type in province_types.items()
        if province_type == "land"
    )
    province_grid = _province_id_grid(np, image_module, provinces_path, definition_path)
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


def _province_id_grid(
    np: Any,
    image_module: Any,
    provinces_path: Path,
    definition_path: Path,
) -> Any:
    """Return a top-down array of province IDs (0 for unknown colors)."""

    rgb_to_province = parse_definition_csv(definition_path)
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
    return color_ids[inverse].reshape(packed.shape)


@dataclass(frozen=True)
class ProvinceGrid:
    """Province lookup for HOI4 map coordinates.

    HOI4 positions use ``x`` from the western edge and ``z`` from the
    *southern* edge; image rows count from the top, so ``z`` is flipped.
    """

    width: int
    height: int
    grid: Any

    def province_at(self, x: float, z: float) -> int | None:
        column = math.floor(x)
        row = self.height - 1 - math.floor(z)
        if not (0 <= column < self.width and 0 <= row < self.height):
            return None
        province_id = int(self.grid[row, column])
        return province_id or None


def load_province_grid(mod_root: Path, hoi4_install: Path | ContentView | None) -> ProvinceGrid:
    """Load the effective ``provinces.bmp`` as a province-ID lookup grid.

    Raises ``FileNotFoundError`` when a map file is missing and
    ``RuntimeError`` when the optional ``map`` extra is not installed.
    """

    provinces_path = _effective_map_file(mod_root, hoi4_install, "provinces.bmp")
    definition_path = _effective_map_file(mod_root, hoi4_install, "definition.csv")
    return _load_province_grid(
        provinces_path,
        definition_path,
        (provinces_path.stat().st_mtime_ns, definition_path.stat().st_mtime_ns),
    )


@lru_cache(maxsize=2)
def _load_province_grid(
    provinces_path: Path,
    definition_path: Path,
    fingerprints: tuple[int, int],
) -> ProvinceGrid:
    del fingerprints  # cache key only
    try:
        np = import_module("numpy")
        image_module = import_module("PIL.Image")
    except ImportError as error:  # pragma: no cover - optional environment
        raise RuntimeError(
            'Province lookup requires the "map" extra: '
            "pip install hoi4-agent-sdk[map]"
        ) from error
    grid = _province_id_grid(np, image_module, provinces_path, definition_path)
    height, width = grid.shape
    return ProvinceGrid(width=int(width), height=int(height), grid=grid)


def load_land_adjacency(
    mod_root: Path,
    hoi4_install: Path | ContentView | None,
) -> dict[int, set[int]]:
    """Return the effective land-province adjacency graph.

    Pixel borders (with east-west wrap) and passable ``adjacencies.csv`` rows
    contribute edges. Raises like :func:`load_province_grid`.
    """

    _land, graph, _coastal = _effective_topology(mod_root, hoi4_install)
    return graph


def _effective_map_file(
    mod_root: Path,
    hoi4_install: Path | ContentView | None,
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
    hoi4_install: Path | ContentView | None,
    filename: str,
) -> Path | None:
    candidate = mod_root / "map" / filename
    if candidate.is_file():
        return candidate
    inherited = as_view(hoi4_install)
    return inherited.file(Path("map") / filename) if inherited is not None else None


def _province_types(path: Path) -> dict[int, str]:
    result: dict[int, str] = {}
    for row in csv.reader(io.StringIO(read_text(path)), delimiter=";"):
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
    for row in csv.reader(io.StringIO(read_text(path)), delimiter=";"):
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
