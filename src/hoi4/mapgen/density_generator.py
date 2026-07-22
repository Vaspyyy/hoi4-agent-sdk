from __future__ import annotations

import numpy as np  # type: ignore[import-not-found]
from PIL import Image  # type: ignore[import-not-found]

from .config import DEFAULT_DENSITY_GREY


def create_uniform_density(width: int, height: int) -> Image.Image:
    if width <= 0 or height <= 0:
        raise ValueError("density image dimensions must be positive")
    return Image.new("L", (width, height), DEFAULT_DENSITY_GREY)


def create_equator_density(width: int, height: int) -> Image.Image:
    if width <= 0 or height <= 0:
        raise ValueError("density image dimensions must be positive")
    rows = np.linspace(0, 1, height)
    gradient = np.abs(rows - 0.5) * 2.0
    values = (gradient * 255).astype(np.uint8)
    return Image.fromarray(np.tile(values[:, np.newaxis], (1, width)), mode="L")
