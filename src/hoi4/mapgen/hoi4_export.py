"""Pure-Python export of generated territory and province data to HOI4 files."""

from __future__ import annotations

import csv
import io
import json
import os
import struct
import tempfile
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np  # type: ignore[import-not-found]
from PIL import Image  # type: ignore[import-not-found]
from scipy.ndimage import binary_dilation  # type: ignore[import-not-found,import-untyped]

from .progress import CancelCallback, Progress, ProgressCallback, check_cancelled

DEFAULT_MAP = """\
definitions = "definition.csv"
provinces = "provinces.bmp"
positions = "positions.txt"
terrain = "terrain.bmp"
rivers = "rivers.bmp"
heightmap = "heightmap.bmp"
tree_definition = "trees.bmp"
continent = "continent.txt"
adjacency_rules = "adjacency_rules.txt"
adjacencies = "adjacencies.csv"
ambient_object = "ambient_object.txt"
seasons = "seasons.txt"
tree = { 3 4 7 10 }
"""

SEASONS = """\
winter = { start_date=00.12.01 end_date=00.02.10 }
spring = { start_date=00.03.10 end_date=00.04.22 }
summer = { start_date=00.05.20 end_date=00.09.10 }
autumn = { start_date=00.10.10 end_date=00.10.31 }
"""

_WEATHER_PERIODS = (
    ("0.0", "30.0", -6, 12),
    ("0.1", "27.1", -7, 12),
    ("0.2", "30.2", -2, 15),
    ("0.3", "29.3", 1, 16),
    ("0.4", "30.4", 4, 19),
    ("0.5", "29.5", 8, 22),
    ("0.6", "30.6", 9, 24),
    ("0.7", "30.7", 9, 25),
    ("0.8", "29.8", 6, 22),
    ("0.9", "30.9", 3, 18),
    ("0.10", "29.10", -1, 15),
    ("0.11", "30.11", -5, 13),
)

_GENERATED_MANIFEST = Path("map/.hoi4-agent-sdk-generated.json")


@dataclass(frozen=True, slots=True)
class TotalConversionProfile:
    """Explicit resources needed when generated maps replace vanilla content."""

    scaffold_root: str | Path | None = None
    blank_vanilla_country_tags: bool = True


def _stage_total_conversion_profile(
    staged_root: Path,
    profile: TotalConversionProfile,
    hoi4_install: str | Path | None,
) -> int:
    copied = 0
    if profile.scaffold_root is not None:
        scaffold = Path(profile.scaffold_root).expanduser().resolve(strict=False)
        if not scaffold.is_dir():
            raise NotADirectoryError(f"Total-conversion scaffold is not a directory: {scaffold}")
        for source in sorted(scaffold.rglob("*")):
            if source.is_symlink():
                raise ValueError(f"Total-conversion scaffold contains a symlink: {source}")
            if not source.is_file():
                continue
            if (
                source.name == ".gitkeep"
                or source.suffix.lower() == ".md"
                or source.name.startswith("descriptor") and source.suffix == ".mod"
                or source.name.startswith("colourMappings")
            ):
                continue
            relative = source.relative_to(scaffold)
            target = staged_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
            copied += 1

    if profile.blank_vanilla_country_tags:
        tag_names: list[str] = []
        if hoi4_install is not None:
            tag_dir = Path(hoi4_install) / "common" / "country_tags"
            if tag_dir.is_dir():
                tag_names = sorted(path.name for path in tag_dir.glob("*.txt") if path.is_file())
        if not tag_names:
            tag_names = ["00_countries.txt", "zz_dynamic_countries.txt"]
        destination = staged_root / "common" / "country_tags"
        destination.mkdir(parents=True, exist_ok=True)
        for name in tag_names:
            _write(destination / name, "# HOI4 Agent SDK total-conversion override\n")
    return copied


def _write(path: str | Path, content: str, *, bom: bool = False) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(("\ufeff" if bom else "") + content, encoding="utf-8")


def _integer_id(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    digits = "".join(character for character in str(value) if character.isdigit())
    if not digits:
        raise ValueError(f"ID has no numeric component: {value!r}")
    return int(digits)


def _normalise_records(
    province_data: list[dict[str, Any]], territory_data: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    provinces = [dict(item) for item in province_data]
    territories = [dict(item) for item in territory_data]
    for item in provinces:
        item["province_id"] = _integer_id(item["province_id"])
        if item.get("territory_id") not in (None, ""):
            item["territory_id"] = _integer_id(item["territory_id"])
    for item in territories:
        item["territory_id"] = _integer_id(item["territory_id"])
        item["province_ids"] = [_integer_id(value) for value in item.get("province_ids", [])]
    return provinces, territories


def _packed_pixels(image: Image.Image) -> np.ndarray:
    array = np.asarray(image.convert("RGB"), dtype=np.uint32)
    return (array[..., 0] << 16) | (array[..., 1] << 8) | array[..., 2]


def _packed_color(item: dict[str, Any]) -> int:
    return int(item["R"]) << 16 | int(item["G"]) << 8 | int(item["B"])


def _province_id_array(
    province_data: list[dict[str, Any]], province_image: Image.Image
) -> tuple[np.ndarray, np.ndarray]:
    pixels = _packed_pixels(province_image)
    color_to_id = {_packed_color(item): int(item["province_id"]) for item in province_data}
    water_ids = {
        int(item["province_id"])
        for item in province_data
        if item.get("province_type") in {"ocean", "sea", "lake"}
    }
    unique, inverse = np.unique(pixels.ravel(), return_inverse=True)
    id_lookup = np.array([color_to_id.get(int(value), -1) for value in unique], dtype=np.int64)
    ids = id_lookup[inverse].reshape(pixels.shape)
    return ids, np.isin(ids, list(water_ids))


def compute_adjacencies(
    province_data: list[dict[str, Any]], province_image: Image.Image
) -> set[tuple[int, int]]:
    """Find orthogonal province neighbors, including HOI4's east–west wrap."""
    ids, _ = _province_id_array(province_data, province_image)
    result: set[tuple[int, int]] = set()
    horizontal = (ids[:, :-1] != ids[:, 1:]) & (ids[:, :-1] >= 0) & (ids[:, 1:] >= 0)
    for y, x in zip(*np.where(horizontal)):
        a, b = int(ids[y, x]), int(ids[y, x + 1])
        result.add((min(a, b), max(a, b)))
    vertical = (ids[:-1] != ids[1:]) & (ids[:-1] >= 0) & (ids[1:] >= 0)
    for y, x in zip(*np.where(vertical)):
        a, b = int(ids[y, x]), int(ids[y + 1, x])
        result.add((min(a, b), max(a, b)))
    for a, b in zip(ids[:, :1].ravel(), ids[:, -1:].ravel()):
        if a >= 0 and b >= 0 and a != b:
            result.add((int(min(a, b)), int(max(a, b))))
    return result


def compute_coastal_provinces(
    province_data: list[dict[str, Any]], province_image: Image.Image
) -> set[int]:
    """Find land touching ocean orthogonally, with east–west wrapping only."""
    ids, water = _province_id_array(province_data, province_image)
    lake_ids = {
        int(item["province_id"]) for item in province_data if item.get("province_type") == "lake"
    }
    ocean_ids = {
        int(item["province_id"])
        for item in province_data
        if item.get("province_type") in {"ocean", "sea"}
    }
    ocean = np.isin(ids, list(ocean_ids))
    ocean_contact = binary_dilation(ocean)
    ocean_contact[:, :1] |= ocean[:, -1:]
    ocean_contact[:, -1:] |= ocean[:, :1]
    candidates = ocean_contact & ~water & (ids >= 0)
    return {int(value) for value in np.unique(ids[candidates]) if value not in lake_ids}


def export_definition_csv(
    province_data: list[dict[str, Any]],
    path: str | Path,
    coastal: set[int] | None = None,
) -> None:
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\n")
    writer.writerow([0, 0, 0, 0, "land", "false", "unknown", 0])
    coastal = coastal or set()
    for item in province_data:
        province_type = str(item.get("province_type", "land"))
        hoi4_type = "sea" if province_type == "ocean" else province_type
        default_terrain = {"land": "plains", "ocean": "ocean", "sea": "ocean", "lake": "lakes"}
        terrain = item.get("province_terrain", default_terrain.get(province_type, "plains"))
        province_id = int(item["province_id"])
        writer.writerow(
            [
                province_id,
                item["R"],
                item["G"],
                item["B"],
                hoi4_type,
                "true" if province_id in coastal else "false",
                terrain,
                0 if province_type in {"ocean", "sea", "lake"} else 1,
            ]
        )
    _write(path, output.getvalue())


def export_provinces_bmp(province_image: Image.Image, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    province_image.convert("RGB").save(target, format="BMP")


def export_provinces_png(province_image: Image.Image, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    province_image.convert("RGB").save(target, format="PNG")


def _default_palette() -> list[int]:
    return [component for value in range(256) for component in (value, value, value)]


def _palette_from_install(hoi4_install: str | Path | None, filename: str) -> list[int]:
    if hoi4_install is None:
        return _default_palette()
    source = Path(hoi4_install) / "map" / filename
    if not source.is_file():
        return _default_palette()
    with Image.open(source) as image:
        return list(image.getpalette() or _default_palette())[:768]


def _save_indexed_bmp(
    path: Path, size: tuple[int, int], fill_index: int, palette: list[int]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("P", size, fill_index)
    image.putpalette(palette)
    image.save(path, format="BMP")
    with path.open("r+b") as handle:
        handle.seek(46)
        handle.write(struct.pack("<I", 0))
        handle.seek(50)
        handle.write(struct.pack("<I", 0))


def export_flat_map_bitmaps(
    size: tuple[int, int], map_dir: str | Path, hoi4_install: str | Path | None = None
) -> None:
    target = Path(map_dir)
    terrain_palette = _palette_from_install(hoi4_install, "terrain.bmp")
    _save_indexed_bmp(target / "terrain.bmp", size, 0, terrain_palette)
    _save_indexed_bmp(
        target / "rivers.bmp", size, 255, _palette_from_install(hoi4_install, "rivers.bmp")
    )
    _save_indexed_bmp(
        target / "heightmap.bmp",
        size,
        128,
        _palette_from_install(hoi4_install, "heightmap.bmp"),
    )
    tree_size = (max(1, round(size[0] * 0.3)), max(1, round(size[1] * 0.3)))
    _save_indexed_bmp(
        target / "trees.bmp", tree_size, 0, _palette_from_install(hoi4_install, "trees.bmp")
    )
    _save_indexed_bmp(target / "cities.bmp", size, 0, terrain_palette)
    Image.new("RGB", (max(1, size[0] // 2), max(1, size[1] // 2)), (128, 128, 255)).save(
        target / "world_normal.bmp", format="BMP"
    )


def _write_flat_dds(
    path: Path,
    width: int,
    height: int,
    color: tuple[int, int, int, int],
) -> None:
    header = struct.pack(
        "<4s I I I I I I I 44x I I I I I I I I I I I I I",
        b"DDS ",
        124,
        0x81007,
        height,
        width,
        width * 4,
        0,
        0,
        32,
        0x41,
        0,
        32,
        0x000000FF,
        0x0000FF00,
        0x00FF0000,
        0xFF000000,
        0x1000,
        0,
        0,
        0,
        0,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + bytes(color) * width * height)


def export_strategic_regions(
    territory_data: list[dict[str, Any]],
    province_data: list[dict[str, Any]],
    out_dir: str | Path,
) -> list[tuple[int, str]]:
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    by_territory: dict[int, list[int]] = {}
    for province in province_data:
        if province.get("territory_id") not in (None, ""):
            by_territory.setdefault(int(province["territory_id"]), []).append(
                int(province["province_id"])
            )
    periods = "\n".join(
        "\t\tperiod={\n"
        f"\t\t\tbetween={{ {start} {end} }}\n"
        f"\t\t\ttemperature={{ {low}.0 {high}.0 }}\n"
        "\t\t\tno_phenomenon=0.500\n"
        "\t\t\train_light=0.300\n"
        "\t\t\train_heavy=0.100\n"
        "\t\t}\n"
        for start, end, low, high in _WEATHER_PERIODS
    )
    result: list[tuple[int, str]] = []
    for territory in territory_data:
        territory_id = int(territory["territory_id"])
        provinces = by_territory.get(territory_id) or [
            int(value) for value in territory.get("province_ids", [])
        ]
        if not provinces:
            continue
        province_list = " ".join(str(value) for value in sorted(provinces))
        content = (
            "strategic_region={\n"
            f"\tid={territory_id}\n"
            f'\tname="STRATEGICREGION_{territory_id}"\n'
            f"\tprovinces={{ {province_list} }}\n"
            "\tweather={\n"
            f"{periods}"
            "\t}\n"
            "}\n"
        )
        _write(target / f"{territory_id}-Territory_{territory_id}.txt", content)
        result.append((territory_id, f"STRATEGICREGION_{territory_id}"))
    return result


def export_states(
    territory_data: list[dict[str, Any]],
    mod_root: str | Path,
    coastal: set[int] | None = None,
) -> list[Path]:
    """Write a valid state for every generated land territory."""
    state_dir = Path(mod_root) / "history" / "states"
    state_dir.mkdir(parents=True, exist_ok=True)
    coastal = coastal or set()
    written: list[Path] = []
    categories = (
        (300, "megalopolis"),
        (150, "metropolis"),
        (80, "large_city"),
        (40, "city"),
        (20, "large_town"),
        (10, "town"),
        (5, "rural"),
        (3, "pastoral"),
        (2, "small_island"),
        (0, "wasteland"),
    )
    for territory in territory_data:
        if territory.get("territory_type", "land") in {"ocean", "sea", "lake"}:
            continue
        territory_id = int(territory["territory_id"])
        provinces = [int(value) for value in territory.get("province_ids", [])]
        category = next(name for threshold, name in categories if len(provinces) >= threshold)
        province_list = " ".join(str(value) for value in sorted(provinces))
        history: list[str] = []
        if provinces:
            history.append(f"\t\tvictory_points = {{ {provinces[0]} 1 }}")
        history.append("\t\tbuildings = {")
        history.append(f"\t\t\tinfrastructure = {min(5, len(provinces) // 8)}")
        for province_id in provinces:
            if province_id in coastal:
                history.extend(
                    (
                        f"\t\t\t{province_id} = {{",
                        "\t\t\t\tnaval_base = 1",
                        "\t\t\t}",
                    )
                )
        history.append("\t\t}")
        content = (
            "state={\n"
            f"\tid={territory_id}\n"
            f'\tname="STATE_{territory_id}"\n'
            f"\tmanpower = {len(provinces) * 25000}\n"
            f"\tstate_category = {category}\n"
            "\tresources={ aluminium=0 chromium=0 oil=0 rubber=0 steel=0 tungsten=0 }\n"
            "\thistory={\n" + "\n".join(history) + "\n\t}\n"
            f"\tprovinces={{ {province_list} }}\n"
            "\tlocal_supplies=0.0\n"
            "}\n"
        )
        path = state_dir / f"{territory_id}.txt"
        _write(path, content)
        written.append(path)
    return written


def export_buildings_txt(
    province_data: list[dict[str, Any]],
    territory_data: list[dict[str, Any]],
    path: str | Path,
    coastal: set[int] | None = None,
) -> None:
    coastal = coastal or set()
    province_to_state: dict[int, int] = {}
    capitals: set[int] = set()
    for territory in territory_data:
        if territory.get("territory_type", "land") in {"ocean", "sea", "lake"}:
            continue
        province_ids = [int(value) for value in territory.get("province_ids", [])]
        if province_ids:
            capitals.add(province_ids[0])
        for province_id in province_ids:
            province_to_state[province_id] = int(territory["territory_id"])
    lines: list[str] = []
    for province in province_data:
        if province.get("province_type") in {"ocean", "sea", "lake"}:
            continue
        province_id = int(province["province_id"])
        state_id = province_to_state.get(province_id, 0)
        if state_id <= 0:
            continue
        x, y = float(province.get("x", 0)), float(province.get("y", 0))
        if province_id in capitals:
            lines.append(f"{state_id};arms_factory;{x:.2f};10.00;{y:.2f};0.00;{province_id}")
            lines.append(f"{state_id};industrial_complex;{x:.2f};10.00;{y:.2f};0.00;{province_id}")
        if province_id in coastal:
            lines.append(f"{state_id};naval_base_spawn;{x:.2f};10.00;{y:.2f};0.00;{province_id}")
            lines.append(f"{state_id};coastal_bunker;{x:.2f};10.00;{y:.2f};0.00;{province_id}")
    _write(path, "\n".join(lines) + ("\n" if lines else ""))


def export_supply_nodes(
    province_data: list[dict[str, Any]],
    territory_data: list[dict[str, Any]],
    path: str | Path,
) -> None:
    del province_data
    centers = sorted(
        int(territory["province_ids"][0])
        for territory in territory_data
        if territory.get("province_ids")
        and territory.get("territory_type", "land") not in {"ocean", "sea", "lake"}
    )
    _write(path, "".join(f"1 {province_id}\n" for province_id in centers))


def _shortest_path(
    graph: dict[int, set[int]], start: int, end: int, max_depth: int = 50
) -> list[int] | None:
    queue = deque([(start, 0)])
    parent = {start: start}
    while queue:
        current, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for neighbour in graph.get(current, set()):
            if neighbour in parent:
                continue
            parent[neighbour] = current
            if neighbour == end:
                path = [end]
                while path[-1] != start:
                    path.append(parent[path[-1]])
                return list(reversed(path))
            queue.append((neighbour, depth + 1))
    return None


def export_railways_txt(
    path: str | Path,
    province_data: list[dict[str, Any]] | None = None,
    territory_data: list[dict[str, Any]] | None = None,
    adjacencies: set[tuple[int, int]] | None = None,
) -> None:
    if not province_data or not territory_data or not adjacencies:
        _write(path, "")
        return
    water = {
        int(item["province_id"])
        for item in province_data
        if item.get("province_type") in {"ocean", "sea", "lake"}
    }
    graph: dict[int, set[int]] = {}
    for a, b in adjacencies:
        if a in water or b in water:
            continue
        graph.setdefault(a, set()).add(b)
        graph.setdefault(b, set()).add(a)
    province_to_territory: dict[int, int] = {}
    centers: dict[int, int] = {}
    for territory in territory_data:
        if territory.get("territory_type") in {"ocean", "sea", "lake"}:
            continue
        territory_id = int(territory["territory_id"])
        province_ids = [int(value) for value in territory.get("province_ids", [])]
        if province_ids:
            centers[territory_id] = province_ids[0]
        for province_id in province_ids:
            province_to_territory[province_id] = territory_id
    territory_pairs = {
        tuple(sorted((province_to_territory[a], province_to_territory[b])))
        for a, b in adjacencies
        if a in province_to_territory
        and b in province_to_territory
        and province_to_territory[a] != province_to_territory[b]
    }
    lines: list[str] = []
    for territory_a, territory_b in sorted(territory_pairs):
        start, end = centers.get(territory_a), centers.get(territory_b)
        if start is None or end is None:
            continue
        province_path = _shortest_path(graph, start, end)
        if province_path and len(province_path) >= 2:
            lines.append(
                f"1 {len(province_path)} " + " ".join(str(value) for value in province_path)
            )
    _write(path, "\n".join(lines) + ("\n" if lines else ""))


def export_territory_definitions(
    metadata: list[dict[str, Any]], path: str | Path, fmt: str = "json"
) -> None:
    rows = {
        str(item["territory_id"]): {
            key: item[key] for key in ("territory_type", "R", "G", "B", "x", "y")
        }
        for item in metadata
    }
    _export_records(rows, path, fmt, "id")


def export_province_definitions(
    metadata: list[dict[str, Any]], path: str | Path, fmt: str = "json"
) -> None:
    fields = ["province_type", "R", "G", "B", "x", "y"]
    if any("province_terrain" in item for item in metadata):
        fields.append("province_terrain")
    rows = {
        str(item["province_id"]): {field: item.get(field, "unknown") for field in fields}
        for item in metadata
    }
    _export_records(rows, path, fmt, "id")


def _export_records(
    records: dict[str, dict[str, Any]], path: str | Path, fmt: str, id_label: str
) -> None:
    if fmt == "json":
        _write(path, json.dumps(records, indent=2) + "\n")
        return
    if fmt != "csv":
        raise ValueError("format must be 'json' or 'csv'")
    fields = list(next(iter(records.values()), {}).keys())
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\n")
    writer.writerow([id_label, *fields])
    for record_id, record in records.items():
        writer.writerow([record_id, *(record[field] for field in fields)])
    _write(path, output.getvalue())


def export_territory_history(
    metadata: list[dict[str, Any]], path: str | Path, fmt: str = "json"
) -> None:
    if fmt == "json":
        data = {
            str(item["territory_id"]): {"provinces": item.get("province_ids", [])}
            for item in metadata
        }
        _write(path, json.dumps(data, indent=2) + "\n")
        return
    if fmt != "csv":
        raise ValueError("format must be 'json' or 'csv'")
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\n")
    writer.writerow(["id", "provinces"])
    for item in metadata:
        writer.writerow(
            [item["territory_id"], ",".join(str(value) for value in item.get("province_ids", []))]
        )
    _write(path, output.getvalue())


def _export_localisation(
    province_data: list[dict[str, Any]],
    territory_data: list[dict[str, Any]],
    localisation_dir: Path,
) -> None:
    province_lines = ["l_english:"] + [
        f' PROV{item["province_id"]}:0 "Province {item["province_id"]}"' for item in province_data
    ]
    region_lines = ["l_english:"] + [
        f' STRATEGICREGION_{item["territory_id"]}:0 "Region {item["territory_id"]}"'
        for item in territory_data
    ]
    _write(
        localisation_dir / "generated_provinces_l_english.yml",
        "\n".join(province_lines) + "\n",
        bom=True,
    )
    _write(
        localisation_dir / "generated_strategic_regions_l_english.yml",
        "\n".join(region_lines) + "\n",
        bom=True,
    )


def _write_map_text_files(
    map_dir: Path,
    province_data: list[dict[str, Any]],
    territory_data: list[dict[str, Any]],
    adjacencies: set[tuple[int, int]],
    coastal: set[int],
) -> None:
    _write(map_dir / "default.map", DEFAULT_MAP)
    _write(map_dir / "continent.txt", "continents = { continent_1 }\n")
    _write(map_dir / "positions.txt", "")
    _write(map_dir / "adjacency_rules.txt", "# No custom adjacency rules.\n")
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\n")
    writer.writerow(
        [
            "From",
            "To",
            "Type",
            "Through",
            "start_x",
            "start_y",
            "stop_x",
            "stop_y",
            "adjacency_rule_name",
            "Comment",
        ]
    )
    _write(map_dir / "adjacencies.csv", output.getvalue())
    _write(map_dir / "seasons.txt", SEASONS)
    _write(map_dir / "ambient_object.txt", "# Generated map: no ambient objects.\n")
    weather = "".join(
        f"{item['territory_id']};{float(item.get('x', 0)):.2f};9.90;"
        f"{float(item.get('y', 0)):.2f};small\n"
        for item in territory_data
    )
    _write(map_dir / "weatherpositions.txt", weather)
    export_railways_txt(map_dir / "railways.txt", province_data, territory_data, adjacencies)
    export_supply_nodes(province_data, territory_data, map_dir / "supply_nodes.txt")
    export_buildings_txt(province_data, territory_data, map_dir / "buildings.txt", coastal)
    _write(map_dir / "unitstacks.txt", "")
    _write(map_dir / "colors.txt", "color = { 86 124 27 }\n")
    _write(map_dir / "cities.txt", 'types_source = "map/cities.bmp"\n')


def _write_supply_area(territory_data: list[dict[str, Any]], directory: Path) -> None:
    land_ids = sorted(
        int(item["territory_id"])
        for item in territory_data
        if item.get("territory_type", "land") not in {"ocean", "sea", "lake"}
    )
    if land_ids:
        ids = " ".join(str(value) for value in land_ids)
        _write(
            directory / "1-SupplyArea.txt",
            f'supply_area={{ id=1 name="SUPPLYAREA_1" value=12 states={{ {ids} }} }}\n',
        )


def _commit_staged_tree(staged_root: Path, destination: Path) -> None:
    """Replace staged files as one rollback-capable filesystem transaction."""

    destination_resolved = destination.resolve(strict=False)
    staged_files = sorted(path for path in staged_root.rglob("*") if path.is_file())
    targets: list[tuple[Path, Path]] = []
    for staged in staged_files:
        target = destination / staged.relative_to(staged_root)
        resolved = target.resolve(strict=False)
        if not resolved.is_relative_to(destination_resolved):
            raise ValueError(f"Generated map target escapes mod root: {target}")
        targets.append((staged, target))

    stale_targets: list[Path] = []
    old_manifest = destination / _GENERATED_MANIFEST
    new_manifest = staged_root / _GENERATED_MANIFEST
    if old_manifest.is_file() and new_manifest.is_file():
        try:
            old_files = set(json.loads(old_manifest.read_text(encoding="utf-8"))["files"])
            new_files = set(json.loads(new_manifest.read_text(encoding="utf-8"))["files"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError(f"Invalid generated-map manifest: {old_manifest}") from error
        for relative_text in sorted(old_files - new_files):
            relative = Path(relative_text)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"Unsafe generated-map manifest path: {relative_text}")
            target = destination / relative
            resolved = target.resolve(strict=False)
            if not resolved.is_relative_to(destination_resolved):
                raise ValueError(f"Generated map target escapes mod root: {target}")
            if target.exists() or target.is_symlink():
                stale_targets.append(target)

    with tempfile.TemporaryDirectory(prefix=".hoi4-map-backup-", dir=destination.parent) as temp:
        backup_root = Path(temp)
        backups: list[tuple[Path, Path]] = []
        committed: list[Path] = []
        try:
            for target in [target for _, target in targets] + stale_targets:
                if not target.exists() and not target.is_symlink():
                    continue
                backup = backup_root / target.relative_to(destination)
                backup.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, backup)
                backups.append((backup, target))
            for staged, target in targets:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged, target)
                committed.append(target)
        except Exception:
            for target in reversed(committed):
                target.unlink(missing_ok=True)
            for backup, target in reversed(backups):
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(backup, target)
            raise


def export_all_map_files(
    province_data: list[dict[str, Any]],
    province_image: Image.Image,
    territory_data: list[dict[str, Any]],
    mod_root: str | Path,
    *,
    coastal: set[int] | None = None,
    adjacencies: set[tuple[int, int]] | None = None,
    hoi4_install: str | Path | None = None,
    total_conversion: TotalConversionProfile | None = None,
    progress_fn: ProgressCallback | None = None,
    cancel_fn: CancelCallback | None = None,
) -> dict[str, str]:
    """Export a generated map, checking cancellation between each major phase.

    Files are built in a sibling staging directory. Cancellation or a failed
    generation phase therefore leaves an existing mod untouched; completed
    files are moved into place only after every phase succeeds.
    """
    destination = Path(mod_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    provinces, territories = _normalise_records(province_data, territory_data)
    progress = Progress(10, progress_fn)
    progress.report(0)
    check_cancelled(cancel_fn)
    with tempfile.TemporaryDirectory(prefix=".hoi4-map-", dir=destination.parent) as temp:
        root = Path(temp)
        map_dir = root / "map"
        localisation_dir = root / "localisation" / "english"
        map_dir.mkdir(parents=True, exist_ok=True)
        localisation_dir.mkdir(parents=True, exist_ok=True)

        scaffold_files = 0
        if total_conversion is not None:
            scaffold_files = _stage_total_conversion_profile(
                root, total_conversion, hoi4_install
            )

        if adjacencies is None:
            adjacencies = compute_adjacencies(provinces, province_image)
        if coastal is None:
            coastal = compute_coastal_provinces(provinces, province_image)
        progress.advance()
        check_cancelled(cancel_fn)

        export_definition_csv(provinces, map_dir / "definition.csv", coastal)
        export_provinces_bmp(province_image, map_dir / "provinces.bmp")
        progress.advance()
        check_cancelled(cancel_fn)

        export_flat_map_bitmaps(province_image.size, map_dir, hoi4_install)
        progress.advance()
        check_cancelled(cancel_fn)

        terrain_dir = map_dir / "terrain"
        width, height = province_image.size
        _write_flat_dds(
            terrain_dir / "colormap_rgb_cityemissivemask_a.dds",
            width,
            height,
            (127, 140, 80, 255),
        )
        for level, factor in enumerate((1, 2, 4)):
            _write_flat_dds(
                terrain_dir / f"colormap_water_{level}.dds",
                max(1, width // factor),
                max(1, height // factor),
                (30, 50, 120, 255),
            )
        progress.advance()
        check_cancelled(cancel_fn)

        _write_map_text_files(map_dir, provinces, territories, adjacencies, coastal)
        progress.advance()
        check_cancelled(cancel_fn)

        regions = export_strategic_regions(territories, provinces, map_dir / "strategicregions")
        _write_supply_area(territories, map_dir / "supplyareas")
        progress.advance()
        check_cancelled(cancel_fn)

        states = export_states(territories, root, coastal)
        progress.advance()
        check_cancelled(cancel_fn)

        _export_localisation(provinces, territories, localisation_dir)
        progress.advance()
        check_cancelled(cancel_fn)

        _write(root / "tutorial" / "tutorial.txt", "tutorial = { }\n")
        progress.advance()
        check_cancelled(cancel_fn)

        generated_files = sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() and path.relative_to(root) != _GENERATED_MANIFEST
        )
        _write(
            root / _GENERATED_MANIFEST,
            json.dumps({"version": 1, "files": generated_files}, indent=2) + "\n",
        )

        _commit_staged_tree(root, destination)
        progress.advance()
    progress.finish()
    return {
        "definition.csv": "ok",
        "provinces.bmp": "ok",
        "map_bitmaps": "ok",
        "strategicregions": f"{len(regions)} files",
        "states": f"{len(states)} files",
        "localisation": "ok",
        **(
            {"total_conversion": f"enabled ({scaffold_files} scaffold files)"}
            if total_conversion is not None
            else {}
        ),
    }
