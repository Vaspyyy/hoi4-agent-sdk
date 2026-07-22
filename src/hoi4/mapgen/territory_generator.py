from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np  # type: ignore[import-not-found]
from PIL import Image  # type: ignore[import-not-found]

from .numb_gen import NumberSeries
from .progress import CancelCallback, Progress, ProgressCallback, check_cancelled
from .utils import (
    STEPS_PER_REGION_MAP,
    clear_used_colors,
    combine_maps,
    create_region_map,
    extract_masks,
)


@dataclass(slots=True)
class GenerationResult:
    image: Image.Image
    pmap: np.ndarray
    metadata: list[dict[str, Any]]
    masks: dict[str, Any]


def generate_territories(
    land_image: Image.Image | None,
    boundary_image: Image.Image | None = None,
    density_image: Image.Image | None = None,
    *,
    density_strength: float = 2.0,
    exclude_ocean_density: bool = False,
    jagged_land: bool = False,
    jagged_ocean: bool = False,
    land_count: int = 3000,
    ocean_count: int = 300,
    seed: int | None = None,
    progress_fn: ProgressCallback | None = None,
    cancel_fn: CancelCallback | None = None,
) -> GenerationResult:
    """Generate land and ocean strategic territories from source images.

    ``land_image`` uses :data:`hoi4.mapgen.config.OCEAN_COLOR` for ocean and
    :data:`hoi4.mapgen.config.LAKE_COLOR` for lakes. Black pixels in an optional
    ``boundary_image`` are treated as hand-drawn borders.
    """
    if land_count < 0 or ocean_count < 0:
        raise ValueError("territory counts must be non-negative")
    clear_used_colors()
    check_cancelled(cancel_fn)
    masks = extract_masks(boundary_image, land_image)
    if bool(masks["land_mask"].any()) and land_count == 0:
        raise ValueError("land_count must be positive when the map contains land pixels")
    if bool(masks["sea_mask"].any()) and ocean_count == 0:
        raise ValueError("ocean_count must be positive when the map contains ocean pixels")
    if density_image is not None and density_image.size != (masks["map_w"], masks["map_h"]):
        raise ValueError("density image dimensions must match the map")
    density = np.asarray(density_image.convert("L")) if density_image is not None else None

    has_sea = land_image is not None and bool(masks["sea_mask"].any())
    sea_steps = STEPS_PER_REGION_MAP if has_sea else 2
    progress = Progress(2 + STEPS_PER_REGION_MAP + sea_steps + 2, progress_fn)
    progress.report(0)
    progress.advance(2)

    series = NumberSeries("TRT", 1, 999999)
    land_map, land_metadata, next_index = create_region_map(
        masks["land_fill"],
        masks["land_border"],
        land_count,
        0,
        "land",
        series,
        "territory_id",
        "territory_type",
        step_fn=progress.advance,
        density=density,
        density_strength=density_strength,
        jagged=jagged_land,
        rng_seed=seed,
        cancel_fn=cancel_fn,
    )

    if has_sea:
        sea_map, sea_metadata, _ = create_region_map(
            masks["sea_fill"],
            masks["sea_border"],
            ocean_count,
            next_index,
            "ocean",
            series,
            "territory_id",
            "territory_type",
            step_fn=progress.advance,
            density=None if exclude_ocean_density else density,
            density_strength=1.0 if exclude_ocean_density else density_strength,
            jagged=jagged_ocean,
            rng_seed=None if seed is None else seed + 1,
            cancel_fn=cancel_fn,
        )
    else:
        sea_map = np.full((masks["map_h"], masks["map_w"]), -1, np.int32)
        sea_metadata = []
        progress.advance(2)

    check_cancelled(cancel_fn)
    metadata = land_metadata + sea_metadata
    image, combined = combine_maps(
        land_map,
        sea_map,
        metadata,
        masks["land_mask"],
        masks["sea_mask"],
    )
    progress.advance(2)
    progress.finish()
    return GenerationResult(image=image, pmap=combined, metadata=metadata, masks=masks)
