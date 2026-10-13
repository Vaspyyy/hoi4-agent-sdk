"""Source-preserving parsers and patchers for editing the existing HOI4 map.

The functions here are pure text transformations used by :class:`hoi4.Mod`'s
map-editing facade.  Line-based files (``supply_nodes.txt``, ``railways.txt``
and ``buildings.txt``) are edited one line at a time so every untouched line
stays byte-for-byte identical.  Script files (strategic regions and province
lists inside state files) are edited at token level within the relevant block.
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .patching import AssignmentSpan, append_assignment, replace_assignment, top_level_assignments
from .types import ValidationError

SUPPLY_NODES_FILE = "map/supply_nodes.txt"
RAILWAYS_FILE = "map/railways.txt"
BUILDINGS_FILE = "map/buildings.txt"
STRATEGIC_REGIONS_DIR = "map/strategicregions"

MAX_RAILWAY_LEVEL = 5
_DATE_KEY_RE = re.compile(r"^\d+\.\d+\.\d+$")
_LIST_TOKEN_RE = re.compile(r"(#[^\n]*)|(?<![\w.\-])(\d+)(?![\w.])")
_STATE_PROVINCES_RE = re.compile(r"\bprovinces\s*=\s*\{([^{}]*)\}")
_COMMENT_RE = re.compile(r"#[^\n]*")
_BUILDING_STATE_RE = re.compile(r"^(﻿?[ \t]*)(\d+)([ \t]*;)")
_RAILWAY_LEVEL_RE = re.compile(r"^(﻿?[ \t]*)(\d+)")


@dataclass(frozen=True)
class StrategicRegion:
    """One ``strategic_region`` definition in the effective map."""

    id: int
    name: str
    provinces: tuple[int, ...]
    path: str
    has_weather: bool = False


@dataclass(frozen=True)
class Railway:
    """One ``railways.txt`` line; ``index`` is its zero-based line number."""

    level: int
    provinces: tuple[int, ...]
    index: int


@dataclass(frozen=True)
class MapBuilding:
    """One ``buildings.txt`` position; ``index`` is its zero-based line number."""

    state_id: int
    building: str
    x: float
    y: float
    z: float
    rotation: float
    adjacent_sea_province: int
    index: int


@dataclass(frozen=True)
class MapEditReport:
    """What a province move changed, plus anything it deliberately skipped."""

    target_state: int
    moved_provinces: tuple[int, ...] = ()
    source_states: tuple[int, ...] = ()
    created_state: bool = False
    deleted_states: tuple[int, ...] = ()
    emptied_states: tuple[int, ...] = ()
    moved_victory_points: tuple[int, ...] = ()
    moved_province_buildings: tuple[int, ...] = ()
    reassigned_map_buildings: int = 0
    warnings: tuple[str, ...] = ()


# ── Generic line helpers ─────────────────────────────────────────────


def split_lines(text: str) -> list[str]:
    """Split ``text`` into lines that keep their terminators (lossless)."""

    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def _line_content(line: str) -> str:
    return line.split("#", 1)[0].replace("﻿", "").strip()


def _newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def append_line(text: str, line: str) -> str:
    """Append one line, terminating a previously unterminated final line."""

    newline = _newline(text)
    if text and not text.endswith("\n"):
        text += newline
    return text + line + newline


def remove_lines(text: str, indices: Iterable[int]) -> str:
    drop = set(indices)
    return "".join(line for index, line in enumerate(split_lines(text)) if index not in drop)


def _replace_line(text: str, index: int, line: str) -> str:
    lines = split_lines(text)
    lines[index] = line
    return "".join(lines)


# ── supply_nodes.txt ─────────────────────────────────────────────────


def parse_supply_nodes(text: str) -> list[tuple[int, int]]:
    """Return ``(line_index, province_id)`` pairs in file order."""

    result: list[tuple[int, int]] = []
    for index, line in enumerate(split_lines(text)):
        tokens = _line_content(line).split()
        if len(tokens) >= 2 and tokens[0].isdigit() and tokens[1].isdigit():
            result.append((index, int(tokens[1])))
    return result


def add_supply_node_text(text: str, province: int) -> str:
    return append_line(text, f"1 {province}")


def remove_supply_node_text(text: str, province: int) -> tuple[str, int]:
    indices = [index for index, value in parse_supply_nodes(text) if value == province]
    return remove_lines(text, indices), len(indices)


# ── railways.txt ─────────────────────────────────────────────────────


def parse_railways(text: str) -> tuple[list[Railway], list[int]]:
    """Return parsed railways and the line indices of malformed rows."""

    railways: list[Railway] = []
    malformed: list[int] = []
    for index, line in enumerate(split_lines(text)):
        content = _line_content(line)
        if not content:
            continue
        tokens = content.split()
        if not all(token.isdigit() for token in tokens) or len(tokens) < 2:
            malformed.append(index)
            continue
        level, count = int(tokens[0]), int(tokens[1])
        provinces = tuple(int(token) for token in tokens[2:])
        if count != len(provinces):
            malformed.append(index)
            continue
        railways.append(Railway(level=level, provinces=provinces, index=index))
    return railways, malformed


def render_railway(level: int, provinces: Sequence[int]) -> str:
    return f"{level} {len(provinces)} " + " ".join(str(province) for province in provinces)


def set_railway_level_text(text: str, index: int, level: int) -> str:
    line = split_lines(text)[index]
    updated = _RAILWAY_LEVEL_RE.sub(lambda match: match.group(1) + str(level), line, count=1)
    return _replace_line(text, index, updated)


# ── buildings.txt ────────────────────────────────────────────────────


def parse_map_buildings(text: str) -> tuple[list[MapBuilding], list[int]]:
    """Return parsed building positions and the line indices of malformed rows."""

    buildings: list[MapBuilding] = []
    malformed: list[int] = []
    for index, line in enumerate(split_lines(text)):
        content = _line_content(line)
        if not content:
            continue
        fields = [field.strip() for field in content.split(";")]
        if len(fields) < 7:
            malformed.append(index)
            continue
        try:
            buildings.append(
                MapBuilding(
                    state_id=int(fields[0]),
                    building=fields[1],
                    x=float(fields[2]),
                    y=float(fields[3]),
                    z=float(fields[4]),
                    rotation=float(fields[5]),
                    adjacent_sea_province=int(fields[6]),
                    index=index,
                )
            )
        except ValueError:
            malformed.append(index)
    return buildings, malformed


def render_map_building(
    state_id: int,
    building: str,
    x: float,
    y: float,
    z: float,
    rotation: float,
    adjacent_sea_province: int,
) -> str:
    return (
        f"{state_id};{building};{x:.2f};{y:.2f};{z:.2f};{rotation:.2f};"
        f"{adjacent_sea_province}"
    )


def set_map_building_state_text(text: str, index: int, state_id: int) -> str:
    line = split_lines(text)[index]
    match = _BUILDING_STATE_RE.match(line)
    if match is None:
        raise ValueError(f"buildings.txt line {index + 1} has no numeric state field")
    updated = line[: match.start(2)] + str(state_id) + line[match.end(2) :]
    return _replace_line(text, index, updated)


# ── Province-list token editing ──────────────────────────────────────


def list_ints(body: str) -> list[int]:
    """Return the integer atoms of a list body, ignoring comments."""

    return [
        int(token)
        for token in _COMMENT_RE.sub(" ", body).split()
        if token.isdigit()
    ]


def remove_list_tokens(body: str, values: Iterable[int]) -> str:
    """Remove integer atoms from a list body, keeping other bytes intact.

    Each run of adjacent removed atoms takes the horizontal whitespace that
    separated it from the following atom (or, at a line end, from the
    preceding one) so the remaining list keeps its original spacing style.
    """

    targets = set(values)
    if not targets:
        return body
    groups: list[list[int]] = []
    previous_end: int | None = None
    for match in _LIST_TOKEN_RE.finditer(body):
        removed = match.group(2) is not None and int(match.group(2)) in targets
        if not removed:
            previous_end = None
            continue
        start, end = match.start(2), match.end(2)
        if previous_end is not None and not body[previous_end:start].strip(" \t"):
            groups[-1][1] = end
        else:
            groups.append([start, end])
        previous_end = end
    cuts: list[tuple[int, int]] = []
    for start, end in groups:
        after = end
        while after < len(body) and body[after] in " \t":
            after += 1
        before = start
        while before > 0 and body[before - 1] in " \t":
            before -= 1
        if after < len(body) and body[after] not in "\r\n":
            cuts.append((start, after))
        elif before > 0 and body[before - 1] not in "\r\n":
            cuts.append((before, end))
        else:
            cuts.append((start, after))
    result = body
    for start, end in reversed(cuts):
        result = result[:start] + result[end:]
    return result


def append_list_tokens(body: str, values: Sequence[int]) -> str:
    """Append integer atoms after the last existing atom of a list body."""

    if not values:
        return body
    addition = " ".join(str(value) for value in values)
    last_end: int | None = None
    for match in _LIST_TOKEN_RE.finditer(body):
        if match.group(2) is not None:
            last_end = match.end(2)
    if last_end is None:
        if not body.strip():
            return f" {addition} " if "\n" not in body else f" {addition}{body}"
        return f" {addition}{body}"
    return body[:last_end] + " " + addition + body[last_end:]


def patch_int_list_body(body: str, original: Sequence[int], updated: Sequence[int]) -> str | None:
    """Return a minimally edited list body, or ``None`` when reordering is needed.

    Supported edits remove atoms and append new atoms at the end while the
    retained atoms keep their relative order.
    """

    if len(set(original)) != len(original) or len(set(updated)) != len(updated):
        return None
    updated_set = set(updated)
    removed = [value for value in original if value not in updated_set]
    retained = [value for value in original if value in updated_set]
    original_set = set(original)
    appended = [value for value in updated if value not in original_set]
    if retained + appended != list(updated):
        return None
    return append_list_tokens(remove_list_tokens(body, removed), appended)


@lru_cache(maxsize=4096)
def _cached_state_file_provinces(path: Path, stamp: int) -> tuple[int, ...]:
    del stamp  # cache key only
    text = _COMMENT_RE.sub("", path.read_text(encoding="utf-8", errors="ignore"))
    match = _STATE_PROVINCES_RE.search(text)
    return tuple(list_ints(match.group(1))) if match else ()


def state_file_provinces(path: Path) -> tuple[int, ...]:
    """Quickly read a state file's province list without a full parse."""

    return _cached_state_file_provinces(path, path.stat().st_mtime_ns)


# ── State history province data ──────────────────────────────────────


def split_victory_points(value: str, provinces: Iterable[int]) -> tuple[str, str]:
    """Split ``"province points ..."`` pairs into (kept, moved) strings."""

    targets = {str(province) for province in provinces}
    tokens = value.split()
    if len(tokens) % 2:
        raise ValueError("Victory points require province/value pairs")
    kept: list[str] = []
    moved: list[str] = []
    for province, points in zip(tokens[::2], tokens[1::2]):
        (moved if province in targets else kept).extend((province, points))
    return " ".join(kept), " ".join(moved)


def join_victory_points(existing: str, extra: str) -> str:
    return " ".join(part for part in (existing.strip(), extra.strip()) if part)


def split_province_buildings(
    buildings: str,
    provinces: Iterable[int],
) -> tuple[str, dict[int, str]]:
    """Remove ``<province> = { ... }`` blocks for ``provinces``.

    Returns the remaining (dedented) buildings body and the removed blocks,
    each as dedented source text.
    """

    targets = set(provinces)
    moved: dict[int, str] = {}
    result = buildings
    spans = top_level_assignments(buildings)
    for span in reversed(spans):
        if not span.is_block or not span.key.isdigit() or int(span.key) not in targets:
            continue
        moved[int(span.key)] = _dedent_assignment(buildings, span)
        result = replace_assignment(result, span, None)
    return textwrap.dedent(result).strip(), dict(sorted(moved.items()))


def _dedent_assignment(text: str, span: AssignmentSpan) -> str:
    line_start = text.rfind("\n", 0, span.start) + 1
    prefix = text[line_start : span.start]
    raw = text[span.start : span.end]
    if prefix.strip():
        return raw
    return textwrap.dedent(prefix + raw).strip()


def append_province_buildings(buildings: str, blocks: Iterable[str]) -> str:
    parts = [buildings.strip()] if buildings.strip() else []
    parts.extend(block.strip() for block in blocks if block.strip())
    return "\n".join(parts)


def dated_history_province_references(history: str, provinces: Iterable[int]) -> set[int]:
    """Return provinces referenced by dated history buildings or victory points."""

    targets = set(provinces)
    found: set[int] = set()
    try:
        spans = top_level_assignments(history)
    except ValueError:
        return found
    for span in spans:
        if not span.is_block or not _DATE_KEY_RE.match(span.key):
            continue
        body = history[span.body_start : span.body_end]
        for inner in top_level_assignments(body):
            if not inner.is_block:
                continue
            inner_body = body[inner.body_start : inner.body_end]
            if inner.key == "buildings":
                for item in top_level_assignments(inner_body):
                    if item.key.isdigit() and int(item.key) in targets:
                        found.add(int(item.key))
            elif inner.key == "victory_points":
                values = list_ints(inner_body)
                found.update(value for value in values[::2] if value in targets)
    return found


# ── Strategic regions ────────────────────────────────────────────────


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def _region_spans(text: str) -> list[tuple[AssignmentSpan, int | None]]:
    result: list[tuple[AssignmentSpan, int | None]] = []
    for span in top_level_assignments(text):
        if span.key != "strategic_region" or not span.is_block:
            continue
        body = text[span.body_start : span.body_end]
        region_id: int | None = None
        for inner in top_level_assignments(body):
            if inner.key == "id" and not inner.is_block:
                raw = _unquote(body[inner.value_start : inner.value_end])
                if raw.isdigit():
                    region_id = int(raw)
                break
        result.append((span, region_id))
    return result


def parse_strategic_regions(text: str, path: str) -> list[StrategicRegion]:
    regions: list[StrategicRegion] = []
    for span, region_id in _region_spans(text):
        if region_id is None:
            continue
        body = text[span.body_start : span.body_end]
        name = ""
        provinces: tuple[int, ...] = ()
        has_weather = False
        for inner in top_level_assignments(body):
            if inner.key == "name" and not inner.is_block:
                name = _unquote(body[inner.value_start : inner.value_end])
            elif inner.key == "provinces" and inner.is_block:
                provinces = provinces + tuple(list_ints(body[inner.body_start : inner.body_end]))
            elif inner.key == "weather" and inner.is_block:
                has_weather = True
        regions.append(
            StrategicRegion(
                id=region_id,
                name=name,
                provinces=provinces,
                path=path,
                has_weather=has_weather,
            )
        )
    return regions


def patch_region_provinces(
    text: str,
    region_id: int,
    *,
    remove: Iterable[int] = (),
    add: Sequence[int] = (),
) -> str:
    """Edit one region's ``provinces`` block in place."""

    removed = set(remove)
    for span, current_id in _region_spans(text):
        if current_id != region_id:
            continue
        assert span.body_start is not None and span.body_end is not None
        body = text[span.body_start : span.body_end]
        province_spans = [
            inner
            for inner in top_level_assignments(body)
            if inner.key == "provinces" and inner.is_block
        ]
        if not province_spans:
            if add:
                body = append_assignment(
                    body,
                    "provinces = { " + " ".join(str(value) for value in add) + " }",
                )
        else:
            for index, inner in enumerate(reversed(province_spans)):
                assert inner.body_start is not None and inner.body_end is not None
                inner_body = body[inner.body_start : inner.body_end]
                inner_body = remove_list_tokens(inner_body, removed)
                if index == 0:
                    inner_body = append_list_tokens(inner_body, add)
                body = body[: inner.body_start] + inner_body + body[inner.body_end :]
        return text[: span.body_start] + body + text[span.body_end :]
    raise KeyError(f"Strategic region {region_id} not found")


def region_weather_assignment(text: str, region_id: int) -> str | None:
    """Return the region's ``weather = { ... }`` source, dedented to depth zero."""

    for span, current_id in _region_spans(text):
        if current_id != region_id:
            continue
        body = text[span.body_start : span.body_end]
        for inner in top_level_assignments(body):
            if inner.key == "weather" and inner.is_block:
                return _dedent_assignment(body, inner)
    return None


def render_strategic_region(
    region_id: int,
    name_key: str,
    provinces: Sequence[int],
    weather: str | None,
) -> str:
    lines = [
        "strategic_region = {",
        f"\tid = {region_id}",
        f'\tname = "{name_key}"',
        "\tprovinces = {",
        "\t\t" + " ".join(str(province) for province in provinces),
        "\t}",
    ]
    if weather and weather.strip():
        assignment = weather.strip()
        if not re.match(r"^weather\s*=", assignment):
            assignment = "weather = {\n" + textwrap.indent(
                textwrap.dedent(weather).strip(), "\t"
            ) + "\n}"
        lines.extend("\t" + line if line.strip() else line for line in assignment.splitlines())
    lines.append("}")
    return "\n".join(lines) + "\n"


# ── Validation ───────────────────────────────────────────────────────


def _preview(values: Sequence[int], limit: int = 20) -> str:
    shown = ", ".join(str(value) for value in values[:limit])
    if len(values) > limit:
        shown += f", ... ({len(values)} total)"
    return shown


def validate_map_consistency(
    *,
    province_types: Mapping[int, str],
    state_provinces: Mapping[int, Sequence[int]],
    state_paths: Mapping[int, str],
    regions: Sequence[StrategicRegion] | None,
    supply_nodes: Sequence[int] | None,
    railways: tuple[Sequence[Railway], Sequence[int]] | None,
    buildings: tuple[Sequence[MapBuilding], Sequence[int]] | None,
    land_adjacency: Mapping[int, set[int]] | None = None,
    railway_path: str | None = None,
    supply_path: str | None = None,
    buildings_path: str | None = None,
) -> list[ValidationError]:
    """Check state, strategic-region, supply, railway, and building references.

    ``None`` for an input means that file is absent from the effective map and
    its checks are skipped.  An empty ``province_types`` mapping (no
    ``definition.csv``) skips province-existence and coverage checks.
    """

    errors: list[ValidationError] = []
    known = {province for province in province_types if province > 0}
    land = {
        province for province, kind in province_types.items()
        if province > 0 and kind == "land"
    }
    sea = {
        province for province, kind in province_types.items()
        if province > 0 and kind == "sea"
    }

    province_states: dict[int, list[int]] = {}
    for state_id, provinces in sorted(state_provinces.items()):
        for province in provinces:
            province_states.setdefault(province, []).append(state_id)
    for province, owners in sorted(province_states.items()):
        if len(owners) > 1:
            errors.append(
                ValidationError(
                    message=(
                        f"Province {province} belongs to multiple states: "
                        + ", ".join(str(owner) for owner in owners)
                    ),
                    severity="error",
                    code="province_in_multiple_states",
                    state_id=owners[0],
                    file_path=state_paths.get(owners[0]),
                )
            )
    if known:
        for state_id, provinces in sorted(state_provinces.items()):
            unknown = sorted({p for p in provinces if p not in known})
            non_land = sorted({p for p in provinces if p in known and p not in land})
            if unknown:
                errors.append(
                    ValidationError(
                        message=(
                            f"State {state_id} references provinces missing from "
                            f"definition.csv: {_preview(unknown)}"
                        ),
                        severity="error",
                        code="unknown_state_province",
                        state_id=state_id,
                        file_path=state_paths.get(state_id),
                    )
                )
            if non_land:
                errors.append(
                    ValidationError(
                        message=(
                            f"State {state_id} contains non-land provinces: "
                            f"{_preview(non_land)}"
                        ),
                        severity="error",
                        code="non_land_state_province",
                        state_id=state_id,
                        file_path=state_paths.get(state_id),
                    )
                )
        missing = sorted(land - set(province_states))
        if missing:
            errors.append(
                ValidationError(
                    message=(
                        f"{len(missing)} land province(s) belong to no state: "
                        f"{_preview(missing)}"
                    ),
                    severity="warning",
                    code="province_missing_state",
                )
            )

    if regions is not None:
        seen_regions: dict[int, StrategicRegion] = {}
        province_regions: dict[int, list[int]] = {}
        for region in regions:
            first = seen_regions.get(region.id)
            if first is not None:
                errors.append(
                    ValidationError(
                        message=f"Strategic region ID {region.id} is defined more than once",
                        severity="error",
                        code="duplicate_strategic_region_id",
                        file_path=region.path,
                        related_file_path=first.path,
                    )
                )
                continue
            seen_regions[region.id] = region
            if not region.provinces:
                errors.append(
                    ValidationError(
                        message=f"Strategic region {region.id} has no provinces",
                        severity="error",
                        code="empty_strategic_region",
                        file_path=region.path,
                    )
                )
            for province in region.provinces:
                province_regions.setdefault(province, []).append(region.id)
        for province, region_ids in sorted(province_regions.items()):
            if len(region_ids) > 1:
                errors.append(
                    ValidationError(
                        message=(
                            f"Province {province} belongs to multiple strategic regions: "
                            + ", ".join(str(region_id) for region_id in region_ids)
                        ),
                        severity="error",
                        code="province_in_multiple_strategic_regions",
                        file_path=seen_regions[region_ids[0]].path,
                    )
                )
        if known:
            unknown_region_provinces = sorted(set(province_regions) - known)
            if unknown_region_provinces:
                errors.append(
                    ValidationError(
                        message=(
                            "Strategic regions reference provinces missing from "
                            f"definition.csv: {_preview(unknown_region_provinces)}"
                        ),
                        severity="error",
                        code="unknown_strategic_region_province",
                    )
                )
            missing_regions = sorted((land | sea) - set(province_regions))
            if missing_regions:
                errors.append(
                    ValidationError(
                        message=(
                            f"{len(missing_regions)} province(s) belong to no strategic "
                            f"region: {_preview(missing_regions)}"
                        ),
                        severity="warning",
                        code="province_missing_strategic_region",
                    )
                )

    if supply_nodes is not None and known:
        invalid = sorted({province for province in supply_nodes if province not in land})
        if invalid:
            errors.append(
                ValidationError(
                    message=(
                        "Supply nodes reference unknown or non-land provinces: "
                        f"{_preview(invalid)}"
                    ),
                    severity="error",
                    code="invalid_supply_node",
                    file_path=supply_path,
                )
            )

    if railways is not None:
        parsed_railways, malformed_railways = railways
        for index in malformed_railways:
            errors.append(
                ValidationError(
                    message=f"railways.txt line {index + 1} is malformed",
                    severity="error",
                    code="malformed_railway",
                    file_path=railway_path,
                    line=index + 1,
                )
            )
        for railway in parsed_railways:
            problems: list[str] = []
            if not 1 <= railway.level <= MAX_RAILWAY_LEVEL:
                problems.append(f"level {railway.level} is outside 1-{MAX_RAILWAY_LEVEL}")
            if len(railway.provinces) < 2:
                problems.append("it needs at least two provinces")
            if known:
                bad = [province for province in railway.provinces if province not in land]
                if bad:
                    problems.append(f"unknown or non-land provinces {_preview(bad)}")
            if land_adjacency is not None:
                for left, right in zip(railway.provinces, railway.provinces[1:]):
                    if right not in land_adjacency.get(left, set()):
                        problems.append(f"provinces {left} and {right} are not adjacent")
            if problems:
                errors.append(
                    ValidationError(
                        message=f"Railway on line {railway.index + 1}: " + "; ".join(problems),
                        severity="error",
                        code="invalid_railway",
                        file_path=railway_path,
                        line=railway.index + 1,
                    )
                )

    if buildings is not None:
        parsed_buildings, malformed_buildings = buildings
        for index in malformed_buildings:
            errors.append(
                ValidationError(
                    message=f"buildings.txt line {index + 1} is malformed",
                    severity="error",
                    code="malformed_map_building",
                    file_path=buildings_path,
                    line=index + 1,
                )
            )
        unknown_states: dict[int, int] = {}
        bad_sea: dict[int, int] = {}
        for entry in parsed_buildings:
            if entry.state_id not in state_provinces:
                unknown_states.setdefault(entry.state_id, entry.index)
            if (
                known
                and entry.adjacent_sea_province
                and entry.adjacent_sea_province not in sea
            ):
                bad_sea.setdefault(entry.adjacent_sea_province, entry.index)
        for state_id, index in sorted(unknown_states.items()):
            errors.append(
                ValidationError(
                    message=f"buildings.txt references missing state {state_id}",
                    severity="error",
                    code="map_building_unknown_state",
                    state_id=state_id,
                    file_path=buildings_path,
                    line=index + 1,
                )
            )
        for province, index in sorted(bad_sea.items()):
            errors.append(
                ValidationError(
                    message=f"buildings.txt references unknown or non-sea province {province} as adjacent sea",
                    severity="error",
                    code="map_building_unknown_province",
                    file_path=buildings_path,
                    line=index + 1,
                )
            )
    return errors
