from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np  # type: ignore[import-not-found]
from PIL import Image  # type: ignore[import-not-found]
from scipy.ndimage import (  # type: ignore[import-not-found,import-untyped]
    binary_dilation,
    distance_transform_edt,
    label as ndlabel,
)
from scipy.spatial import cKDTree  # type: ignore[import-not-found,import-untyped]

from . import config
from .numb_gen import NumberSeries
from .progress import CancelCallback, check_cancelled

MAX_LLOYD_SAMPLE = 100_000
STEPS_PER_REGION_MAP = 1 + config.LLOYD_ITERATIONS + 1 + 1

_used_colors: set[tuple[int, int, int]] = set()


def clear_used_colors() -> None:
    _used_colors.clear()


def color_from_id(index: int, region_type: str) -> tuple[int, int, int]:
    """Return a stable, unique color in a range suited to the region type."""
    rng = np.random.default_rng(index + 1)
    while True:
        if region_type in {"ocean", "sea"}:
            color = (
                int(rng.integers(0, 60)),
                int(rng.integers(0, 80)),
                int(rng.integers(100, 180)),
            )
        elif region_type == "lake":
            color = (
                int(rng.integers(0, 80)),
                int(rng.integers(80, 180)),
                int(rng.integers(100, 200)),
            )
        else:
            values = rng.integers(0, 256, 3)
            color = (int(values[0]), int(values[1]), int(values[2]))
        if color != (0, 0, 0) and color not in _used_colors:
            _used_colors.add(color)
            return color


def random_seeds(
    mask: np.ndarray,
    num_points: int,
    *,
    rng_seed: int | None = None,
    density: np.ndarray | None = None,
    density_strength: float = 1.0,
) -> list[tuple[int, int]]:
    coords_yx = np.column_stack(np.where(mask))
    if coords_yx.size == 0 or num_points <= 0:
        return []
    if density is not None and density.shape[:2] != mask.shape:
        raise ValueError("density image dimensions must match the map")

    rng = np.random.default_rng(rng_seed)
    count = min(num_points, len(coords_yx))
    probability: np.ndarray | None = None
    if density is not None:
        weights = 256.0 - density[coords_yx[:, 0], coords_yx[:, 1]].astype(np.float64)
        weights = np.maximum(weights, 0.0) ** max(0.0, density_strength)
        total = float(weights.sum())
        if total > 0:
            probability = weights / total
    indexes = rng.choice(len(coords_yx), size=count, replace=False, p=probability)
    return [(int(x), int(y)) for y, x in coords_yx[indexes]]


def lloyd_relaxation(
    mask: np.ndarray,
    seeds: list[tuple[int, int]],
    *,
    rng_seed: int | None = None,
    iterations: int = config.LLOYD_ITERATIONS,
    step_fn: Callable[[int], None] | None = None,
    cancel_fn: CancelCallback | None = None,
) -> list[tuple[int, int]]:
    if iterations <= 0 or not seeds:
        return seeds
    coords_yx = np.column_stack(np.where(mask))
    if coords_yx.size == 0:
        return seeds

    coords_xy = np.flip(coords_yx, axis=1).astype(np.float32)
    rng = np.random.default_rng(rng_seed)
    if len(coords_xy) > MAX_LLOYD_SAMPLE:
        indexes = rng.choice(len(coords_xy), size=MAX_LLOYD_SAMPLE, replace=False)
        sample_xy = coords_xy[indexes]
    else:
        sample_xy = coords_xy
    seed_array = np.array(seeds, dtype=np.float32)

    for _ in range(iterations):
        check_cancelled(cancel_fn)
        tree = cKDTree(seed_array)
        _, labels = tree.query(sample_xy, k=1)
        counts = np.bincount(labels, minlength=len(seed_array))
        sum_x = np.bincount(labels, weights=sample_xy[:, 0], minlength=len(seed_array))
        sum_y = np.bincount(labels, weights=sample_xy[:, 1], minlength=len(seed_array))

        for index in range(len(seed_array)):
            if counts[index] <= 0:
                seed_array[index] = sample_xy[int(rng.integers(0, len(sample_xy)))]
                continue
            x = int(round(sum_x[index] / counts[index]))
            y = int(round(sum_y[index] / counts[index]))
            x = max(0, min(x, mask.shape[1] - 1))
            y = max(0, min(y, mask.shape[0] - 1))
            if mask[y, x]:
                seed_array[index] = (x, y)
        if step_fn is not None:
            step_fn(1)

    return [(int(x), int(y)) for x, y in seed_array]


def _build_jitter_maps(
    height: int,
    width: int,
    seeds: np.ndarray,
    rng_seed: int | None,
) -> tuple[np.ndarray | None, np.ndarray | None]:
    if len(seeds) < 2:
        return None, None
    from scipy.ndimage import zoom as ndzoom

    rng = np.random.default_rng(rng_seed)
    tree = cKDTree(seeds)
    distances, _ = tree.query(seeds, k=2)
    average = float(distances[:, 1].mean())
    amplitude = average * config.JAGGED_BORDER_AMPLITUDE
    cell = max(4, int(average / 4))
    coarse_h = (height + cell - 1) // cell + 1
    coarse_w = (width + cell - 1) // cell + 1
    jitter_x = ndzoom(rng.uniform(-amplitude, amplitude, (coarse_h, coarse_w)), cell, order=1)[
        :height, :width
    ].astype(np.float32)
    jitter_y = ndzoom(rng.uniform(-amplitude, amplitude, (coarse_h, coarse_w)), cell, order=1)[
        :height, :width
    ].astype(np.float32)
    return jitter_x, jitter_y


def _jitter_coordinates(
    coords_xy: np.ndarray,
    coords_yx: np.ndarray,
    jitter_x: np.ndarray | None,
    jitter_y: np.ndarray | None,
) -> np.ndarray:
    if jitter_x is None or jitter_y is None:
        return coords_xy
    result = coords_xy.copy()
    result[:, 0] += jitter_x[coords_yx[:, 0], coords_yx[:, 1]]
    result[:, 1] += jitter_y[coords_yx[:, 0], coords_yx[:, 1]]
    return result


def _remove_enclaves(region_map: np.ndarray, mask: np.ndarray) -> None:
    cleared = np.zeros(region_map.shape, dtype=bool)
    for region_id in np.unique(region_map[mask]):
        if region_id < 0:
            continue
        region_mask = region_map == region_id
        labeled, count = ndlabel(region_mask)
        if count <= 1:
            continue
        sizes = np.bincount(labeled.ravel())[1:]
        largest = int(sizes.argmax()) + 1
        small = region_mask & (labeled != largest)
        region_map[small] = -1
        cleared |= small

    fragments, fragment_count = ndlabel(cleared & mask)
    for fragment_id in range(1, fragment_count + 1):
        fragment = fragments == fragment_id
        neighbours = binary_dilation(fragment) & (region_map >= 0) & mask
        ids, counts = np.unique(region_map[neighbours], return_counts=True)
        if len(ids):
            region_map[fragment] = ids[int(counts.argmax())]


def assign_regions(
    mask: np.ndarray,
    seeds: list[tuple[int, int]],
    start_index: int,
    *,
    jagged: bool = False,
    rng_seed: int | None = None,
    cancel_fn: CancelCallback | None = None,
) -> np.ndarray:
    """Assign every true pixel in *mask* to its nearest seed."""
    height, width = mask.shape
    region_map = np.full((height, width), -1, np.int32)
    if not seeds or not mask.any():
        return region_map
    check_cancelled(cancel_fn)

    seed_array = np.array(seeds, dtype=np.float32)
    jitter_x = jitter_y = None
    if jagged:
        jitter_x, jitter_y = _build_jitter_maps(height, width, seed_array, rng_seed)

    components, component_count = ndlabel(mask)
    seed_components: dict[int, list[int]] = {}
    for index, (x, y) in enumerate(seeds):
        component = int(components[y, x])
        if component:
            seed_components.setdefault(component, []).append(index)

    for component_id in range(1, component_count + 1):
        check_cancelled(cancel_fn)
        coords_yx = np.column_stack(np.where(components == component_id))
        indexes = seed_components.get(component_id)
        if not indexes:
            continue
        coords_xy = np.flip(coords_yx, axis=1).astype(np.float32)
        query = _jitter_coordinates(coords_xy, coords_yx, jitter_x, jitter_y)
        _, local_labels = cKDTree(seed_array[indexes]).query(query, k=1)
        global_indexes = np.array(indexes, dtype=np.int32)
        region_map[coords_yx[:, 0], coords_yx[:, 1]] = global_indexes[local_labels] + start_index

    unassigned = mask & (region_map < 0)
    if unassigned.any() and (region_map >= 0).any():
        _, (nearest_y, nearest_x) = distance_transform_edt(region_map < 0, return_indices=True)
        region_map[unassigned] = region_map[nearest_y[unassigned], nearest_x[unassigned]]
    if jagged:
        _remove_enclaves(region_map, mask)
        unassigned = mask & (region_map < 0)
        if unassigned.any() and (region_map >= 0).any():
            _, (nearest_y, nearest_x) = distance_transform_edt(region_map < 0, return_indices=True)
            region_map[unassigned] = region_map[nearest_y[unassigned], nearest_x[unassigned]]
    return region_map


def assign_borders(region_map: np.ndarray, border_mask: np.ndarray) -> None:
    valid = region_map >= 0
    if not valid.any() or not border_mask.any():
        return
    _, (nearest_y, nearest_x) = distance_transform_edt(~valid, return_indices=True)
    region_map[border_mask] = region_map[nearest_y[border_mask], nearest_x[border_mask]]


def extract_masks(
    boundary_image: Image.Image | None,
    land_image: Image.Image | None,
) -> dict[str, Any]:
    """Extract land, sea, lake, and optional hand-drawn boundary masks."""
    if boundary_image is None and land_image is None:
        raise ValueError("Need a boundary image or land/ocean image")

    boundary_mask: np.ndarray | None = None
    if boundary_image is not None:
        boundary_array = np.asarray(boundary_image.convert("RGB"))
        boundary_mask = np.all(boundary_array == config.BOUNDARY_COLOR, axis=2)
        height, width = boundary_mask.shape

    if land_image is not None:
        land_array = np.asarray(land_image.convert("RGB"))
        sea_mask = np.all(land_array == config.OCEAN_COLOR, axis=2)
        lake_mask = np.all(land_array == config.LAKE_COLOR, axis=2)
        land_mask = ~sea_mask
        image_height, image_width = sea_mask.shape
        if boundary_mask is not None and (image_height, image_width) != (height, width):
            raise ValueError("boundary and land images must have identical dimensions")
        height, width = image_height, image_width
    else:
        assert boundary_mask is not None
        sea_mask = np.zeros((height, width), dtype=bool)
        lake_mask = np.zeros((height, width), dtype=bool)
        land_mask = np.ones((height, width), dtype=bool)

    if boundary_mask is None:
        land_fill = land_mask
        land_border = sea_mask
        sea_fill = sea_mask
        sea_border = land_mask
    else:
        land_fill = land_mask & ~boundary_mask
        land_border = boundary_mask | sea_mask
        sea_fill = sea_mask & ~boundary_mask
        sea_border = boundary_mask | land_mask

    return {
        "boundary_mask": boundary_mask,
        "land_mask": land_mask,
        "sea_mask": sea_mask,
        "lake_mask": lake_mask,
        "land_fill": land_fill,
        "land_border": land_border,
        "sea_fill": sea_fill,
        "sea_border": sea_border,
        "map_h": height,
        "map_w": width,
    }


def _build_region_metadata(
    region_map: np.ndarray,
    seeds: list[tuple[int, int]],
    start_index: int,
    region_type: str,
    series: NumberSeries,
    id_key: str,
    type_key: str,
) -> list[dict[str, Any]]:
    valid = region_map >= 0
    ys, xs = np.where(valid)
    shifted = region_map[valid] - start_index
    counts = np.bincount(shifted, minlength=len(seeds))
    sum_x = np.bincount(shifted, weights=xs.astype(float), minlength=len(seeds))
    sum_y = np.bincount(shifted, weights=ys.astype(float), minlength=len(seeds))

    metadata: list[dict[str, Any]] = []
    for seed_index in range(len(seeds)):
        if counts[seed_index] <= 0:
            continue
        value = series.get_id()
        if value is None:
            raise OverflowError(f"{id_key} ID space exhausted")
        index = start_index + seed_index
        red, green, blue = color_from_id(index, region_type)
        metadata.append(
            {
                id_key: value,
                type_key: region_type,
                "R": red,
                "G": green,
                "B": blue,
                "x": float(sum_x[seed_index] / counts[seed_index]),
                "y": float(sum_y[seed_index] / counts[seed_index]),
                "_pmap_index": index,
            }
        )
    return metadata


def create_region_map(
    fill_mask: np.ndarray,
    border_mask: np.ndarray,
    num_points: int,
    start_index: int,
    region_type: str,
    series: NumberSeries,
    id_key: str,
    type_key: str,
    *,
    step_fn: Callable[[int], None] | None = None,
    density: np.ndarray | None = None,
    density_strength: float = 1.0,
    jagged: bool = False,
    rng_seed: int | None = None,
    cancel_fn: CancelCallback | None = None,
) -> tuple[np.ndarray, list[dict[str, Any]], int]:
    def no_step(_count: int = 1) -> None:
        return None

    step = step_fn or no_step
    check_cancelled(cancel_fn)
    if num_points <= 0 or not fill_mask.any():
        step(STEPS_PER_REGION_MAP)
        return np.full(fill_mask.shape, -1, np.int32), [], start_index

    seeds = random_seeds(
        fill_mask,
        num_points,
        rng_seed=rng_seed,
        density=density,
        density_strength=density_strength,
    )
    step(1)
    if not seeds:
        step(config.LLOYD_ITERATIONS + 2)
        return np.full(fill_mask.shape, -1, np.int32), [], start_index

    seeds = lloyd_relaxation(
        fill_mask,
        seeds,
        rng_seed=rng_seed,
        step_fn=step,
        cancel_fn=cancel_fn,
    )
    region_map = assign_regions(
        fill_mask,
        seeds,
        start_index,
        jagged=jagged,
        rng_seed=rng_seed,
        cancel_fn=cancel_fn,
    )
    step(1)
    metadata = _build_region_metadata(
        region_map, seeds, start_index, region_type, series, id_key, type_key
    )
    assign_borders(region_map, border_mask)
    step(1)
    return region_map, metadata, start_index + len(seeds)


def combine_maps(
    land_map: np.ndarray,
    sea_map: np.ndarray,
    metadata: list[dict[str, Any]],
    land_mask: np.ndarray,
    sea_mask: np.ndarray,
) -> tuple[Image.Image, np.ndarray]:
    combined = np.full(land_map.shape, -1, np.int32)
    valid_land = (land_map >= 0) & land_mask
    valid_sea = (sea_map >= 0) & sea_mask
    combined[valid_land] = land_map[valid_land]
    combined[valid_sea] = sea_map[valid_sea]
    if (combined >= 0).any():
        _, (nearest_y, nearest_x) = distance_transform_edt(combined < 0, return_indices=True)
        missing = combined < 0
        combined[missing] = combined[nearest_y[missing], nearest_x[missing]]

    output = np.zeros((*combined.shape, 3), dtype=np.uint8)
    if metadata:
        largest_index = max(int(entry["_pmap_index"]) for entry in metadata)
        colors = np.zeros((largest_index + 1, 3), dtype=np.uint8)
        for entry in metadata:
            colors[int(entry["_pmap_index"])] = (
                entry["R"],
                entry["G"],
                entry["B"],
            )
        valid = (combined >= 0) & (combined <= largest_index)
        output[valid] = colors[combined[valid]]
    return Image.fromarray(output, mode="RGB"), combined
