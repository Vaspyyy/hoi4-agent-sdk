"""Map editing: provinces, strategic regions, supply, railways and buildings."""

from __future__ import annotations

import math
import re
import textwrap
import warnings
from pathlib import Path
from collections.abc import Callable
from functools import partial
from typing import Any, Sequence, TypeVar

from .._disk_cache import DiskAnalysis
from ..map_editing import (
    BUILDINGS_FILE,
    MAX_RAILWAY_LEVEL,
    RAILWAYS_FILE,
    STRATEGIC_REGIONS_DIR,
    SUPPLY_NODES_FILE,
    MapBuilding,
    MapEditReport,
    Railway,
    StrategicRegion,
    add_supply_node_text,
    append_line,
    decode_map_buildings,
    decode_strategic_regions,
    encode_map_buildings,
    encode_strategic_regions,
    append_province_buildings,
    dated_history_province_references,
    join_victory_points,
    parse_map_buildings,
    parse_railways,
    parse_strategic_regions,
    parse_supply_nodes,
    patch_region_provinces,
    region_weather_assignment,
    remove_lines,
    remove_supply_node_text,
    render_map_building,
    render_railway,
    render_strategic_region,
    set_map_building_state_text,
    set_railway_level_text,
    split_lines,
    split_province_buildings,
    split_victory_points,
    state_file_provinces,
    validate_map_consistency,
)
from ..map_topology import (
    load_land_adjacency,
    load_province_grid,
)
from ..parser import (
    ParseError,
)
from ..paths import (
    require_country_tag,
    resolve_mod_output_path,
    safe_file_stem,
)
from ..types import (
    State,
    ValidationError,
)
from ._catalogs import CatalogsLayer
from ._states import StatesLayer
from ..textio import read_text

_T = TypeVar("_T")


def _parse_regions(text: str, *, path: str) -> list[StrategicRegion]:
    return parse_strategic_regions(text, path)


_REGIONS_ON_DISK = DiskAnalysis(
    "strategic_regions", encode_strategic_regions, decode_strategic_regions
)
_BUILDINGS_ON_DISK = DiskAnalysis("map_buildings", encode_map_buildings, decode_map_buildings)


class MapLayer(StatesLayer, CatalogsLayer):
    """Map editing: provinces, strategic regions, supply, railways and buildings."""

    def create_state(
        self,
        provinces: Sequence[int],
        *,
        name: str,
        owner: str | None = None,
        cores: Sequence[str] | None = None,
        state_category: str = "rural",
        manpower: int | str = 0,
        state_id: int | None = None,
        resources: dict[str, str | int | float] | None = None,
        buildings: str = "",
        impassable: bool = False,
        localisation_file: str | Path | None = None,
        allow_empty_source: bool = False,
        delete_empty_states: bool = False,
        reassign_map_buildings: bool = True,
    ) -> int:
        """Queue a new state made of ``provinces`` and return its ID.

        Provinces leave their current states together with their victory
        points and province-level history buildings. ``owner``/``cores`` left
        as ``None`` inherit from the source state contributing most provinces.
        ``name`` is written as ``STATE_<id>`` localisation. Map building
        positions inside moved provinces are reassigned; anything skipped is
        reported through :class:`RuntimeWarning`. Use :meth:`move_provinces`
        when the full :class:`MapEditReport` is needed.
        """

        if not isinstance(name, str) or not name.strip():
            raise ValueError("create_state() requires a non-empty name")
        if not re.fullmatch(r"[A-Za-z0-9_]+", state_category or ""):
            raise ValueError(f"Invalid state_category {state_category!r}")
        manpower_text = str(manpower).strip()
        if not manpower_text.isdigit():
            raise ValueError(f"manpower must be a non-negative integer, got {manpower!r}")
        province_list = self._normalize_province_ids(provinces)
        effective_ids = set(self._effective_state_provinces()[0])
        if state_id is None:
            new_id = max(effective_ids, default=0) + 1
        else:
            if isinstance(state_id, bool) or not isinstance(state_id, int) or state_id <= 0:
                raise ValueError(f"state_id must be a positive integer, got {state_id!r}")
            if state_id in effective_ids:
                raise ValueError(f"State {state_id} already exists")
            new_id = state_id
        target_path = resolve_mod_output_path(
            self.mod_root,
            Path("history") / "states" / f"{new_id}-{safe_file_stem(name.strip(), fallback='STATE')}.txt",
        )
        if target_path.exists() and target_path not in self._deleted_state_files:
            raise FileExistsError(f"State file already exists: {target_path}")
        plan = self._plan_province_move(province_list, new_id)
        primary_state: State | None = None
        if plan and (owner is None or cores is None):
            primary_id = max(sorted(plan), key=lambda sid: len(plan[sid]))
            primary_state = self.get_state(primary_id)
        owner_tag = (
            require_country_tag(owner) if owner
            else (primary_state.owner if primary_state is not None and owner is None else "")
        )
        if cores is None:
            core_tags = list(primary_state.cores) if primary_state is not None else []
        else:
            core_tags = []
            for core in cores:
                tag = require_country_tag(core)
                if tag not in core_tags:
                    core_tags.append(tag)
        state = State(
            id=new_id,
            name=f"STATE_{new_id}",
            owner=owner_tag,
            cores=core_tags,
            resources=dict(resources or {}),
            buildings=textwrap.dedent(buildings).strip(),
            manpower=manpower_text,
            state_category=state_category,
            impassable=impassable,
            path=target_path,
        )
        snapshot = self._snapshot()
        try:
            self._states[new_id] = state
            self._state_source_paths[new_id] = target_path
            self._deleted_state_ids.discard(new_id)
            self._state_index_cache.clear()
            report = self._apply_province_move(
                plan,
                province_list,
                state,
                created=True,
                allow_empty_source=allow_empty_source,
                delete_empty_states=delete_empty_states,
                reassign_map_buildings=reassign_map_buildings,
            )
            self.set_loc(f"STATE_{new_id}", name.strip(), file_path=localisation_file)
        except Exception:
            self._restore(snapshot)
            raise
        for message in report.warnings:
            warnings.warn(message, RuntimeWarning, stacklevel=2)
        return new_id

    def move_provinces(
        self,
        provinces: Sequence[int],
        to_state: int,
        *,
        allow_empty_source: bool = False,
        delete_empty_states: bool = False,
        reassign_map_buildings: bool = True,
    ) -> MapEditReport:
        """Queue moving ``provinces`` (with their province data) into ``to_state``.

        A source state that would lose every province raises ``ValueError``
        unless ``delete_empty_states=True`` (delete it; reported) or
        ``allow_empty_source=True`` (keep it empty; reported as a warning).
        """

        province_list = self._normalize_province_ids(provinces)
        target = self.get_state(to_state)
        plan = self._plan_province_move(province_list, target.id)
        snapshot = self._snapshot()
        try:
            self._materialize_state_override(target)
            return self._apply_province_move(
                plan,
                province_list,
                target,
                created=False,
                allow_empty_source=allow_empty_source,
                delete_empty_states=delete_empty_states,
                reassign_map_buildings=reassign_map_buildings,
            )
        except Exception:
            self._restore(snapshot)
            raise

    def split_state(
        self,
        state_id: int,
        provinces: Sequence[int],
        /,
        **create_state_kwargs: Any,
    ) -> int:
        """Create a new state from some of ``state_id``'s provinces."""

        province_list = self._normalize_province_ids(provinces)
        state = self.get_state(state_id)
        outside = [province for province in province_list if province not in state.provinces]
        if outside:
            raise ValueError(
                f"Province(s) {', '.join(map(str, outside))} do not belong to state {state_id}"
            )
        return self.create_state(province_list, **create_state_kwargs)

    def list_strategic_regions(self) -> list[StrategicRegion]:
        """Return effective strategic regions (mod files override base files)."""

        regions: list[StrategicRegion] = []
        for relative in self._strategic_region_files():
            text = self._map_text(relative)
            if text is None:
                continue
            try:
                regions.extend(parse_strategic_regions(text, relative))
            except ParseError:
                continue
        return sorted(regions, key=lambda region: (region.id, region.path))

    def get_strategic_region(self, region_id: int) -> StrategicRegion:
        for region in self.list_strategic_regions():
            if region.id == region_id:
                return region
        raise KeyError(f"Strategic region {region_id} not found")

    def move_provinces_to_strategic_region(
        self,
        provinces: Sequence[int],
        region_id: int,
    ) -> StrategicRegion:
        """Queue moving land or sea provinces into strategic region ``region_id``."""

        province_list = self._normalize_province_ids(provinces)
        self._require_known_provinces(province_list)
        regions = self.list_strategic_regions()
        target = next((region for region in regions if region.id == region_id), None)
        if target is None:
            raise KeyError(f"Strategic region {region_id} not found")
        texts = self._strategic_region_removals(regions, province_list, skip=target)
        additions = [province for province in province_list if province not in target.provinces]
        if additions:
            base = texts.get(target.path, self._map_text(target.path) or "")
            texts[target.path] = patch_region_provinces(base, region_id, add=additions)
        for relative, text in texts.items():
            self._queue_map_text(relative, text)
        return self.get_strategic_region(region_id)

    def create_strategic_region(
        self,
        name: str,
        provinces: Sequence[int],
        *,
        region_id: int | None = None,
        weather: str | None = None,
        localisation_file: str | Path | None = None,
    ) -> int:
        """Queue a new strategic region and return its ID.

        ``weather`` may be a ``weather = { ... }`` assignment or its body. When
        omitted, the weather block of the region currently holding most of the
        provinces is copied verbatim.
        """

        if not isinstance(name, str) or not name.strip():
            raise ValueError("create_strategic_region() requires a non-empty name")
        province_list = self._normalize_province_ids(provinces)
        self._require_known_provinces(province_list)
        regions = self.list_strategic_regions()
        existing = {region.id for region in regions}
        if region_id is None:
            new_id = max(existing, default=0) + 1
        else:
            if isinstance(region_id, bool) or not isinstance(region_id, int) or region_id <= 0:
                raise ValueError(f"region_id must be a positive integer, got {region_id!r}")
            if region_id in existing:
                raise ValueError(f"Strategic region {region_id} already exists")
            new_id = region_id
        relative = (
            f"{STRATEGIC_REGIONS_DIR}/{new_id}-"
            f"{safe_file_stem(name.strip(), fallback='region')}.txt"
        )
        if self._map_text(relative) is not None:
            raise FileExistsError(f"Strategic region file already exists: {relative}")
        weather_text = weather
        if weather_text is None:
            wanted = set(province_list)
            donors = sorted(
                (
                    (-len(wanted.intersection(region.provinces)), region.id, region)
                    for region in regions
                    if region.has_weather and wanted.intersection(region.provinces)
                ),
                key=lambda item: (item[0], item[1]),
            )
            if donors:
                donor = donors[0][2]
                weather_text = region_weather_assignment(
                    self._map_text(donor.path) or "", donor.id
                )
            else:
                warnings.warn(
                    f"Strategic region {new_id} has no weather block: none of its "
                    "provinces belonged to a region with weather",
                    RuntimeWarning,
                    stacklevel=2,
                )
        texts = self._strategic_region_removals(regions, province_list, skip=None)
        texts[relative] = render_strategic_region(
            new_id, f"STRATEGICREGION_{new_id}", province_list, weather_text
        )
        self.set_loc(f"STRATEGICREGION_{new_id}", name.strip(), file_path=localisation_file)
        for path, text in texts.items():
            self._queue_map_text(path, text)
        return new_id

    def list_supply_nodes(self) -> list[int]:
        text = self._map_text(SUPPLY_NODES_FILE)
        return [] if text is None else [province for _, province in parse_supply_nodes(text)]

    def add_supply_node(self, province: int) -> None:
        """Queue a ``map/supply_nodes.txt`` entry for a land province."""

        province = self._require_land_province(province)
        text = self._map_text(SUPPLY_NODES_FILE) or ""
        if any(value == province for _, value in parse_supply_nodes(text)):
            raise ValueError(f"Province {province} already has a supply node")
        self._queue_map_text(SUPPLY_NODES_FILE, add_supply_node_text(text, province))

    def remove_supply_node(self, province: int) -> None:
        text = self._map_text(SUPPLY_NODES_FILE)
        updated, removed = remove_supply_node_text(text or "", province)
        if not removed:
            raise KeyError(f"Province {province} has no supply node")
        self._queue_map_text(SUPPLY_NODES_FILE, updated)

    def list_railways(self) -> list[Railway]:
        text = self._map_text(RAILWAYS_FILE)
        return [] if text is None else parse_railways(text)[0]

    def add_railway(
        self,
        level: int,
        provinces: Sequence[int],
        *,
        check_adjacency: bool = True,
    ) -> Railway:
        """Queue a railway line through consecutive land provinces.

        Consecutive provinces must be adjacent when the effective province map
        (and the optional ``map`` extra) is available; otherwise that check is
        skipped.
        """

        if isinstance(level, bool) or not isinstance(level, int) or not (
            1 <= level <= MAX_RAILWAY_LEVEL
        ):
            raise ValueError(f"Railway level must be 1-{MAX_RAILWAY_LEVEL}, got {level!r}")
        route = [self._require_land_province(province) for province in provinces]
        if len(route) < 2:
            raise ValueError("A railway needs at least two provinces")
        if len(set(route)) != len(route):
            raise ValueError("A railway cannot visit the same province twice")
        if check_adjacency:
            graph = self._optional_land_adjacency()
            if graph is not None:
                for left, right in zip(route, route[1:]):
                    if right not in graph.get(left, set()):
                        raise ValueError(f"Provinces {left} and {right} are not adjacent")
        text = append_line(self._map_text(RAILWAYS_FILE) or "", render_railway(level, route))
        self._queue_map_text(RAILWAYS_FILE, text)
        return Railway(level=level, provinces=tuple(route), index=len(split_lines(text)) - 1)

    def remove_railway(self, railway: Railway | Sequence[int]) -> int:
        """Remove railway lines matching a listed ``Railway`` or a province route.

        A route matches in either direction. Returns the number of removed lines.
        """

        text, matches = self._match_railways(railway)
        self._queue_map_text(RAILWAYS_FILE, remove_lines(text, (item.index for item in matches)))
        return len(matches)

    def set_railway_level(self, railway: Railway | Sequence[int], level: int) -> int:
        if isinstance(level, bool) or not isinstance(level, int) or not (
            1 <= level <= MAX_RAILWAY_LEVEL
        ):
            raise ValueError(f"Railway level must be 1-{MAX_RAILWAY_LEVEL}, got {level!r}")
        text, matches = self._match_railways(railway)
        for item in matches:
            text = set_railway_level_text(text, item.index, level)
        self._queue_map_text(RAILWAYS_FILE, text)
        return len(matches)

    def list_map_buildings(self, state_id: int | None = None) -> list[MapBuilding]:
        text = self._map_text(BUILDINGS_FILE)
        if text is None:
            return []
        entries = parse_map_buildings(text)[0]
        return [entry for entry in entries if state_id is None or entry.state_id == state_id]

    def add_map_building(
        self,
        state_id: int,
        building: str,
        x: float,
        y: float,
        z: float,
        rotation: float,
        adjacent_sea_province: int = 0,
    ) -> MapBuilding:
        """Queue a ``map/buildings.txt`` position (``z`` is the north-south axis)."""

        self.get_state(state_id)
        if not isinstance(building, str) or not re.fullmatch(r"[A-Za-z0-9_]+", building):
            raise ValueError(f"Invalid building type {building!r}")
        coordinates = [float(value) for value in (x, y, z, rotation)]
        if not all(math.isfinite(value) for value in coordinates):
            raise ValueError("Building coordinates and rotation must be finite numbers")
        if isinstance(adjacent_sea_province, bool) or not isinstance(adjacent_sea_province, int) or (
            adjacent_sea_province < 0
        ):
            raise ValueError("adjacent_sea_province must be 0 or a sea province ID")
        if adjacent_sea_province:
            kind = self._effective_province_types().get(adjacent_sea_province)
            if self._effective_province_types() and kind is None:
                raise KeyError(f"Province {adjacent_sea_province} is not in definition.csv")
            # Floating harbors reference their coastal land province instead.
            expected = "land" if building == "floating_harbor" else "sea"
            if kind is not None and kind != expected:
                raise ValueError(f"Province {adjacent_sea_province} is not a {expected} province")
        px, py, pz, protation = coordinates
        line = render_map_building(state_id, building, px, py, pz, protation, adjacent_sea_province)
        text = append_line(self._map_text(BUILDINGS_FILE) or "", line)
        self._queue_map_text(BUILDINGS_FILE, text)
        return MapBuilding(
            state_id=state_id,
            building=building,
            x=round(coordinates[0], 2),
            y=round(coordinates[1], 2),
            z=round(coordinates[2], 2),
            rotation=round(coordinates[3], 2),
            adjacent_sea_province=adjacent_sea_province,
            index=len(split_lines(text)) - 1,
        )

    def remove_map_buildings(
        self,
        state_id: int | None = None,
        building: str | None = None,
        *,
        entries: Sequence[MapBuilding] = (),
    ) -> int:
        """Remove building positions by state/type filters or listed entries."""

        if state_id is None and building is None and not entries:
            raise ValueError("remove_map_buildings() requires state_id, building, or entries")
        text = self._map_text(BUILDINGS_FILE)
        parsed = parse_map_buildings(text or "")[0]
        if entries:
            current = set(parsed)
            stale = [entry for entry in entries if entry not in current]
            if stale:
                raise KeyError(
                    "Map building entries are stale or missing: "
                    + ", ".join(f"line {entry.index + 1}" for entry in stale)
                )
            indices = {entry.index for entry in entries}
        else:
            indices = {
                entry.index
                for entry in parsed
                if (state_id is None or entry.state_id == state_id)
                and (building is None or entry.building == building)
            }
        if indices and text is not None:
            self._queue_map_text(BUILDINGS_FILE, remove_lines(text, indices))
        return len(indices)

    def _map_result(
        self,
        relative: str,
        purpose: str,
        compute: Callable[[str], _T],
        disk: DiskAnalysis[_T],
    ) -> _T | None:
        """``compute`` of a map file's pending or effective text; ``None`` when absent.

        The result for an inherited file is kept on disk for later processes.
        """

        if relative in self._map_file_updates:
            return compute(self._map_file_updates[relative])
        source = self.content_source(relative)
        if source is None or (
            not source.is_relative_to(self.mod_root)
            and self._mod_hides_inherited(Path(relative))
        ):
            return None
        return self._cached_result(purpose, source, compute, disk)

    def _map_text(self, relative: str) -> str | None:
        """Pending or effective text of a map file; ``None`` when absent."""

        if relative in self._map_file_updates:
            return self._map_file_updates[relative]
        source = self.content_source(relative)
        if source is None or (
            not source.is_relative_to(self.mod_root)
            and self._mod_hides_inherited(Path(relative))
        ):
            return None
        # What is read here is what an edit of the map file builds on.
        return self._keep_source(source, read_text(source))

    def _queue_map_text(self, relative: str, text: str) -> None:
        resolve_mod_output_path(self.mod_root, relative)
        self._map_file_updates[relative] = text
        self._dirty.add("map")

    def _strategic_region_files(self) -> list[str]:
        files = {
            Path(relative).as_posix()
            for relative, source in self.content_files(STRATEGIC_REGIONS_DIR).items()
            if relative.endswith(".txt")
            and (
                source.is_relative_to(self.mod_root)
                or not self._mod_hides_inherited(Path(relative))
            )
        }
        files.update(
            relative for relative in self._map_file_updates
            if relative.startswith(STRATEGIC_REGIONS_DIR + "/")
        )
        return sorted(files)

    def _strategic_region_removals(
        self,
        regions: Sequence[StrategicRegion],
        provinces: Sequence[int],
        *,
        skip: StrategicRegion | None,
    ) -> dict[str, str]:
        wanted = set(provinces)
        texts: dict[str, str] = {}
        for region in regions:
            if region == skip:
                continue
            moving = [province for province in region.provinces if province in wanted]
            if not moving:
                continue
            if set(region.provinces) <= wanted:
                raise ValueError(
                    f"Moving these provinces would leave strategic region {region.id} empty"
                )
            base = texts.get(region.path, self._map_text(region.path) or "")
            texts[region.path] = patch_region_provinces(base, region.id, remove=moving)
        return texts

    @staticmethod
    def _normalize_province_ids(provinces: Sequence[int] | int) -> list[int]:
        values = [provinces] if isinstance(provinces, int) else list(provinces)
        result: list[int] = []
        for province in values:
            if isinstance(province, bool) or not isinstance(province, int) or province <= 0:
                raise ValueError(f"Province IDs must be positive integers, got {province!r}")
            if province not in result:
                result.append(province)
        if not result:
            raise ValueError("At least one province is required")
        return result

    def _require_known_provinces(self, provinces: Sequence[int]) -> None:
        types = self._effective_province_types()
        if not types:
            return
        unknown = [province for province in provinces if province not in types]
        if unknown:
            raise KeyError(
                f"Province(s) {', '.join(map(str, unknown))} are not in definition.csv"
            )

    def _require_land_province(self, province: int) -> int:
        (province,) = self._normalize_province_ids([province])
        types = self._effective_province_types()
        if types:
            kind = types.get(province)
            if kind is None:
                raise KeyError(f"Province {province} is not in definition.csv")
            if kind != "land":
                raise ValueError(f"Province {province} is a {kind} province, not land")
        return province

    def _optional_land_adjacency(self) -> dict[int, set[int]] | None:
        try:
            return load_land_adjacency(self.mod_root, self._base_content)
        except (FileNotFoundError, RuntimeError, OSError, ValueError):
            return None

    def _match_railways(self, railway: Railway | Sequence[int]) -> tuple[str, list[Railway]]:
        text = self._map_text(RAILWAYS_FILE)
        parsed = parse_railways(text or "")[0]
        if isinstance(railway, Railway):
            matches = [item for item in parsed if item == railway]
        else:
            route = tuple(int(province) for province in railway)
            matches = [
                item for item in parsed
                if item.provinces == route or item.provinces == route[::-1]
            ]
        if text is None or not matches:
            raise KeyError(f"No railway matches {railway!r}")
        return text, matches

    def _effective_state_provinces(self) -> tuple[dict[int, tuple[int, ...]], dict[int, str]]:
        """Province lists and source paths of every effective, non-deleted state."""

        paths = {entry["id"]: str(entry["path"]) for entry in self.state_index(include_vanilla=True)}
        provinces: dict[int, tuple[int, ...]] = {}
        for state_id in sorted(set(paths) | set(self._states)):
            if state_id in self._deleted_state_ids:
                continue
            state = self._states.get(state_id)
            if state is not None:
                provinces[state_id] = tuple(state.provinces)
                if state.path is not None:
                    paths[state_id] = str(state.path)
                continue
            try:
                provinces[state_id] = state_file_provinces(Path(paths[state_id]))
            except OSError:
                continue
        return provinces, {state_id: paths[state_id] for state_id in provinces if state_id in paths}

    def _plan_province_move(self, provinces: Sequence[int], target_id: int) -> dict[int, list[int]]:
        """Validate a province move and group provinces by their source state."""

        types = self._effective_province_types()
        state_provinces = self._effective_state_provinces()[0]
        owners: dict[int, list[int]] = {}
        for state_id, members in state_provinces.items():
            for province in members:
                owners.setdefault(province, []).append(state_id)
        plan: dict[int, list[int]] = {}
        for province in provinces:
            kind = types.get(province)
            if types and kind is None:
                raise KeyError(f"Province {province} is not in definition.csv")
            if kind is not None and kind != "land":
                raise ValueError(
                    f"Province {province} is a {kind} province; only land provinces belong to states"
                )
            current = owners.get(province, [])
            sources = [state_id for state_id in current if state_id != target_id]
            if not sources:
                if target_id in current:
                    raise ValueError(f"Province {province} already belongs to state {target_id}")
                if not types:
                    raise KeyError(
                        f"Province {province} belongs to no state and no definition.csv "
                        "is available to confirm it exists"
                    )
            for state_id in sources:
                plan.setdefault(state_id, []).append(province)
        return plan

    def _apply_province_move(
        self,
        plan: dict[int, list[int]],
        provinces: Sequence[int],
        target: State,
        *,
        created: bool,
        allow_empty_source: bool,
        delete_empty_states: bool,
        reassign_map_buildings: bool,
    ) -> MapEditReport:
        emptied = sorted(
            state_id for state_id, moving in plan.items()
            if not set(self.get_state(state_id).provinces) - set(moving)
        )
        if emptied and not (allow_empty_source or delete_empty_states):
            raise ValueError(
                f"Moving these provinces would leave state(s) {', '.join(map(str, emptied))} "
                "empty; pass delete_empty_states=True to delete them or "
                "allow_empty_source=True to keep empty states"
            )
        report_warnings: list[str] = []
        moved_vp_provinces: list[int] = []
        moved_building_provinces: list[int] = []
        moved_points: list[str] = []
        moved_blocks: list[str] = []
        for state_id in sorted(plan):
            moving = set(plan[state_id])
            source = self.get_state(state_id)
            self._materialize_state_override(source)
            source.provinces = [province for province in source.provinces if province not in moving]
            kept_points, points = split_victory_points(source.victory_points, moving)
            if points:
                source.victory_points = kept_points
                moved_points.append(points)
                moved_vp_provinces.extend(int(value) for value in points.split()[::2])
            kept_buildings, blocks = split_province_buildings(source.buildings, moving)
            if blocks:
                source.buildings = kept_buildings
                moved_blocks.extend(blocks.values())
                moved_building_provinces.extend(blocks)
            dated = dated_history_province_references(source.history, moving)
            if dated:
                report_warnings.append(
                    f"State {state_id} has dated history buildings or victory points for "
                    f"province(s) {', '.join(map(str, sorted(dated)))}; they were left in "
                    "place for review"
                )
            self._dirty_states.add(state_id)
        target.provinces = target.provinces + [
            province for province in provinces if province not in target.provinces
        ]
        if moved_points:
            target.victory_points = join_victory_points(
                target.victory_points, " ".join(moved_points)
            )
        if moved_blocks:
            target.buildings = append_province_buildings(target.buildings, moved_blocks)
        self._dirty_states.add(target.id)
        self._dirty.add("states")

        reassigned = 0
        if reassign_map_buildings:
            reassigned, building_warnings = self._reassign_map_buildings(
                {state_id: set(moving) for state_id, moving in plan.items()},
                target.id,
                fully_moved=set(emptied),
            )
            report_warnings.extend(building_warnings)
        deleted: list[int] = []
        kept_empty: list[int] = []
        for state_id in emptied:
            if delete_empty_states:
                report_warnings.extend(self._delete_state(state_id))
                deleted.append(state_id)
            else:
                kept_empty.append(state_id)
                report_warnings.append(f"State {state_id} now has no provinces")
        if deleted and not reassign_map_buildings:
            remaining = {
                entry.state_id for entry in self.list_map_buildings()
                if entry.state_id in deleted
            }
            if remaining:
                report_warnings.append(
                    "map/buildings.txt still references deleted state(s) "
                    + ", ".join(map(str, sorted(remaining)))
                )
        return MapEditReport(
            target_state=target.id,
            moved_provinces=tuple(provinces),
            source_states=tuple(sorted(plan)),
            created_state=created,
            deleted_states=tuple(deleted),
            emptied_states=tuple(kept_empty),
            moved_victory_points=tuple(moved_vp_provinces),
            moved_province_buildings=tuple(moved_building_provinces),
            reassigned_map_buildings=reassigned,
            warnings=tuple(report_warnings),
        )

    def _reassign_map_buildings(
        self,
        moved: dict[int, set[int]],
        target_id: int,
        *,
        fully_moved: set[int],
    ) -> tuple[int, list[str]]:
        text = self._map_text(BUILDINGS_FILE)
        if text is None:
            return 0, []
        candidates = [
            entry for entry in parse_map_buildings(text)[0] if entry.state_id in moved
        ]
        if not candidates:
            return 0, []
        problems: list[str] = []
        grid = None
        partial = sorted({entry.state_id for entry in candidates} - fully_moved)
        if partial:
            try:
                grid = load_province_grid(self.mod_root, self._base_content)
            except (FileNotFoundError, RuntimeError, OSError, ValueError) as error:
                problems.append(
                    "map/buildings.txt positions of state(s) "
                    f"{', '.join(map(str, partial))} were not reassigned because the "
                    f"province map is unavailable ({error})"
                )
        count = 0
        for entry in candidates:
            if entry.state_id in fully_moved:
                hit = True
            elif grid is None:
                continue
            else:
                hit = grid.province_at(entry.x, entry.z) in moved[entry.state_id]
            if hit:
                text = set_map_building_state_text(text, entry.index, target_id)
                count += 1
        if count:
            self._queue_map_text(BUILDINGS_FILE, text)
        return count, problems

    def _delete_state(self, state_id: int) -> list[str]:
        """Queue deletion of a state's file(s); returns report warnings."""

        state = self.get_state(state_id)
        relatives: list[Path] = []
        for candidate in (state.path, state.source_path, self._state_source_paths.get(state_id)):
            if candidate is None:
                continue
            resolved = candidate.resolve(strict=False)
            if resolved.is_relative_to(self.mod_root):
                relatives.append(resolved.relative_to(self.mod_root))
            elif self._base_content is not None:
                relative = self._base_content.relative(resolved)
                if relative is not None:
                    relatives.append(relative)
        if self._base_content is not None:
            inherited = self._inherited_state_paths().get(state_id)
            if inherited is not None:
                relative = self._base_content.relative(inherited)
                assert relative is not None
                relatives.append(relative)
        shadow = (
            f"# State {state_id} was removed by hoi4-agent-sdk map editing.\n"
            "# This empty file shadows the inherited definition.\n"
        )
        for relative in dict.fromkeys(relatives):
            if self._content_layers is not None:
                inherited_file = self._content_layers.inherited(relative) is not None
            else:
                inherited_file = (
                    self.hoi4_install is not None and (self.hoi4_install / relative).is_file()
                )
            target = resolve_mod_output_path(self.mod_root, relative)
            self._deleted_state_files[target] = shadow if inherited_file else None
        self._states.pop(state_id, None)
        if state_id in self._state_ids:
            self._state_ids.remove(state_id)
        self._state_source_paths.pop(state_id, None)
        self._dirty_states.discard(state_id)
        self._deleted_state_ids.add(state_id)
        self._state_index_cache.clear()
        self._dirty.add("states")
        remaining = self._effective_state_provinces()[0]
        if remaining and state_id < max(remaining):
            return [
                f"Deleted state {state_id} leaves a gap in state IDs; HOI4 expects "
                "contiguous state IDs, so renumber the highest state into this ID"
            ]
        return []

    def _validate_map_editing(self) -> list[ValidationError]:
        """Map-geometry reference checks (states, regions, supply, rails, buildings)."""

        province_types = self._effective_province_types()
        state_provinces, state_paths = self._effective_state_provinces()
        errors: list[ValidationError] = []
        region_files = self._strategic_region_files()
        regions: list[StrategicRegion] | None = None
        if region_files:
            regions = []
            for relative in region_files:
                try:
                    found = self._map_result(
                        relative,
                        f"strategic_regions:{relative}",
                        partial(_parse_regions, path=relative),
                        _REGIONS_ON_DISK,
                    )
                    if found is None:
                        continue
                    regions.extend(found)
                except ParseError as error:
                    errors.append(
                        ValidationError(
                            message=f"Could not parse strategic region file {relative}: {error}",
                            severity="error",
                            code="strategic_region_parse_error",
                            file_path=relative,
                        )
                    )
        supply_text = self._map_text(SUPPLY_NODES_FILE)
        railway_text = self._map_text(RAILWAYS_FILE)
        buildings = self._map_result(
            BUILDINGS_FILE, "map_buildings", parse_map_buildings, _BUILDINGS_ON_DISK
        )
        adjacency = None
        if railway_text is not None and (
            RAILWAYS_FILE in self._map_file_updates
            or (self.mod_root / RAILWAYS_FILE).is_file()
        ):
            adjacency = self._optional_land_adjacency()
        errors.extend(
            validate_map_consistency(
                province_types=province_types,
                state_provinces=state_provinces,
                state_paths=state_paths,
                regions=regions,
                supply_nodes=(
                    None if supply_text is None
                    else [province for _, province in parse_supply_nodes(supply_text)]
                ),
                railways=None if railway_text is None else parse_railways(railway_text),
                buildings=buildings,
                land_adjacency=adjacency,
                railway_path=RAILWAYS_FILE,
                supply_path=SUPPLY_NODES_FILE,
                buildings_path=BUILDINGS_FILE,
            )
        )
        # The game ships strategic regions without provinces (15, 189, 253).
        # An empty region is the mod's business only when its own file holds it.
        return [
            error for error in errors
            if error.code != "empty_strategic_region"
            or self._mod_owns_map_file(error.file_path or "")
        ]

    def _mod_owns_map_file(self, relative: str) -> bool:
        return relative in self._map_file_updates or (self.mod_root / relative).is_file()
