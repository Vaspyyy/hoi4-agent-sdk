from __future__ import annotations

from typing import Any

import numpy as np  # type: ignore[import-not-found]
from PIL import Image  # type: ignore[import-not-found]
from scipy.ndimage import label as ndlabel  # type: ignore[import-not-found,import-untyped]

from . import config
from .numb_gen import NumberSeries
from .progress import CancelCallback, Progress, ProgressCallback, check_cancelled
from .territory_generator import GenerationResult
from .utils import clear_used_colors, color_from_id, create_region_map


def _distribute(
    territories: list[dict[str, Any]],
    total_provinces: int,
    pixel_counts: dict[int, int],
    density_weights: dict[int, float],
) -> list[int]:
    if not territories or total_provinces <= 0:
        return [0] * len(territories)
    weights = [
        pixel_counts.get(int(item["_pmap_index"]), 0)
        * density_weights.get(int(item["_pmap_index"]), 1.0)
        for item in territories
    ]
    total_weight = sum(weights)
    if total_weight <= 0:
        return [1] * len(territories)

    allocations = [max(1, round(value / total_weight * total_provinces)) for value in weights]
    difference = sum(allocations) - total_provinces
    if total_provinces >= len(territories):
        indexes = sorted(
            range(len(territories)), key=lambda index: weights[index], reverse=difference > 0
        )
        while difference:
            changed = False
            for index in indexes:
                if difference > 0 and allocations[index] > 1:
                    allocations[index] -= 1
                    difference -= 1
                    changed = True
                elif difference < 0:
                    allocations[index] += 1
                    difference += 1
                    changed = True
                if difference == 0:
                    break
            if not changed:
                break
    return allocations


def _assign_terrain(metadata: list[dict[str, Any]], terrain: np.ndarray) -> None:
    height, width = terrain.shape[:2]
    lookups = {
        "land": {color: name for name, color in config.LAND_TERRAIN_TYPES.items()},
        "ocean": {color: name for name, color in config.NAVAL_TERRAIN_TYPES.items()},
        "lake": {color: name for name, color in config.LAKE_TERRAIN_TYPES.items()},
    }
    defaults = {
        "land": config.DEFAULT_TERRAIN_LAND,
        "ocean": config.DEFAULT_TERRAIN_OCEAN,
        "lake": config.DEFAULT_TERRAIN_LAKE,
    }
    for province in metadata:
        x = max(0, min(int(round(province["x"])), width - 1))
        y = max(0, min(int(round(province["y"])), height - 1))
        pixel = (
            int(terrain[y, x, 0]),
            int(terrain[y, x, 1]),
            int(terrain[y, x, 2]),
        )
        province_type = str(province["province_type"])
        province["province_terrain"] = lookups[province_type].get(pixel, defaults[province_type])


def generate_provinces(
    territory_pmap: np.ndarray,
    territory_data: list[dict[str, Any]],
    masks: dict[str, Any],
    density_image: Image.Image | None = None,
    *,
    density_strength: float = 2.0,
    exclude_ocean_density: bool = False,
    jagged_land: bool = False,
    jagged_ocean: bool = False,
    land_count: int = 3000,
    ocean_count: int = 300,
    terrain_image: Image.Image | None = None,
    seed: int | None = None,
    progress_fn: ProgressCallback | None = None,
    cancel_fn: CancelCallback | None = None,
) -> GenerationResult:
    """Subdivide generated territories into HOI4 provinces.

    The supplied territory metadata is enriched in place with ``province_ids``
    so it can be passed directly to :func:`export_all_map_files`.
    """
    if land_count < 0 or ocean_count < 0:
        raise ValueError("province counts must be non-negative")
    clear_used_colors()
    check_cancelled(cancel_fn)
    height, width = int(masks["map_h"]), int(masks["map_w"])
    if territory_pmap.shape != (height, width):
        raise ValueError("territory map dimensions do not match its masks")
    for image, name in ((density_image, "density"), (terrain_image, "terrain")):
        if image is not None and image.size != (width, height):
            raise ValueError(f"{name} image dimensions must match the map")

    density = np.asarray(density_image.convert("L")) if density_image is not None else None
    land_territories = [d for d in territory_data if d["territory_type"] == "land"]
    ocean_territories = [d for d in territory_data if d["territory_type"] == "ocean"]
    if land_territories and land_count == 0:
        raise ValueError("land_count must be positive when land territories exist")
    if ocean_territories and ocean_count == 0:
        raise ValueError("ocean_count must be positive when ocean territories exist")
    ocean_indexes = {int(d["_pmap_index"]) for d in ocean_territories if exclude_ocean_density}

    unique, counts = np.unique(territory_pmap[territory_pmap >= 0], return_counts=True)
    pixel_counts = dict(zip((int(v) for v in unique), (int(v) for v in counts)))
    density_weights: dict[int, float] = {}
    for raw_index in unique:
        index = int(raw_index)
        if index in ocean_indexes or density is None:
            density_weights[index] = 1.0
        else:
            mean = float(density[territory_pmap == index].mean())
            density_weights[index] = (256.0 - mean) ** max(0.0, density_strength)

    land_allocations = _distribute(land_territories, land_count, pixel_counts, density_weights)
    ocean_allocations = _distribute(ocean_territories, ocean_count, pixel_counts, density_weights)
    work = list(zip(land_territories, land_allocations)) + list(
        zip(ocean_territories, ocean_allocations)
    )
    progress = Progress(2 + len(work) + 2, progress_fn)
    progress.report(0)
    progress.advance(2)

    series = NumberSeries("PRV", 1, 999999)
    province_map = np.full((height, width), -1, np.int32)
    metadata: list[dict[str, Any]] = []
    next_index = 0
    boundary_mask = masks.get("boundary_mask")
    if boundary_mask is None:
        boundary_mask = np.zeros((height, width), dtype=bool)
    lake_mask = masks.get("lake_mask")
    territories_by_index = {int(d["_pmap_index"]): d for d in territory_data}
    for territory in territory_data:
        territory["province_ids"] = []

    if lake_mask is not None and lake_mask.any():
        labeled, lake_count = ndlabel(lake_mask)
        for component_id in range(1, lake_count + 1):
            check_cancelled(cancel_fn)
            component = labeled == component_id
            province_id = series.get_id()
            if province_id is None:
                raise OverflowError("province ID space exhausted")
            red, green, blue = color_from_id(next_index, "lake")
            ys, xs = np.where(component)
            center_x, center_y = float(xs.mean()), float(ys.mean())
            territory_index = int(territory_pmap[round(center_y), round(center_x)])
            lake_territory = territories_by_index.get(territory_index)
            entry: dict[str, Any] = {
                "province_id": province_id,
                "province_type": "lake",
                "R": red,
                "G": green,
                "B": blue,
                "x": center_x,
                "y": center_y,
                "territory_id": lake_territory["territory_id"] if lake_territory else "",
                "_pmap_index": next_index,
            }
            province_map[component] = next_index
            metadata.append(entry)
            if lake_territory is not None:
                lake_territory.setdefault("province_ids", []).append(province_id)
            next_index += 1

    for work_index, (territory, province_count) in enumerate(work):
        check_cancelled(cancel_fn)
        territory_mask = territory_pmap == int(territory["_pmap_index"])
        province_type = str(territory["territory_type"])
        fill = territory_mask & ~boundary_mask
        border = territory_mask & boundary_mask
        if lake_mask is not None:
            fill &= ~lake_mask
            border |= territory_mask & lake_mask
        omit_density = exclude_ocean_density and province_type == "ocean"
        region_map, entries, next_value = create_region_map(
            fill,
            border,
            province_count,
            next_index,
            province_type,
            series,
            "province_id",
            "province_type",
            density=None if omit_density else density,
            density_strength=1.0 if omit_density else density_strength,
            jagged=jagged_land if province_type == "land" else jagged_ocean,
            rng_seed=None if seed is None else seed + work_index,
            cancel_fn=cancel_fn,
        )
        for entry in entries:
            entry["territory_id"] = territory["territory_id"]
        valid = (region_map >= 0) & (province_map < 0)
        province_map[valid] = region_map[valid]
        territory["province_ids"] = list(territory.get("province_ids", [])) + [
            entry["province_id"] for entry in entries
        ]
        metadata.extend(entries)
        next_index = next_value
        progress.advance()

    output = np.zeros((height, width, 3), dtype=np.uint8)
    if metadata:
        max_index = max(int(entry["_pmap_index"]) for entry in metadata)
        colors = np.zeros((max_index + 1, 3), dtype=np.uint8)
        for entry in metadata:
            colors[int(entry["_pmap_index"])] = entry["R"], entry["G"], entry["B"]
        valid = (province_map >= 0) & (province_map <= max_index)
        output[valid] = colors[province_map[valid]]
    progress.advance()

    if terrain_image is None:
        defaults = {
            "land": config.DEFAULT_TERRAIN_LAND,
            "ocean": config.DEFAULT_TERRAIN_OCEAN,
            "lake": config.DEFAULT_TERRAIN_LAKE,
        }
        for entry in metadata:
            entry["province_terrain"] = defaults[str(entry["province_type"])]
    else:
        _assign_terrain(metadata, np.asarray(terrain_image.convert("RGB")))
    progress.advance()
    progress.finish()
    return GenerationResult(
        image=Image.fromarray(output, mode="RGB"),
        pmap=province_map,
        metadata=metadata,
        masks=masks,
    )
