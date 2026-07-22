from __future__ import annotations

OCEAN_COLOR = (5, 20, 18)
LAKE_COLOR = (0, 255, 0)
BOUNDARY_COLOR = (0, 0, 0)

LLOYD_ITERATIONS = 4
JAGGED_BORDER_AMPLITUDE = 0.12
DEFAULT_DENSITY_GREY = 128

LAND_TERRAIN_TYPES: dict[str, tuple[int, int, int]] = {
    "forest": (89, 199, 85),
    "hills": (248, 255, 153),
    "mountain": (157, 192, 208),
    "plains": (255, 129, 66),
    "urban": (120, 120, 120),
    "jungle": (127, 191, 0),
    "marsh": (76, 96, 35),
    "desert": (255, 127, 0),
}

NAVAL_TERRAIN_TYPES: dict[str, tuple[int, int, int]] = {
    "deep_ocean": (2, 38, 150),
    "shallow_sea": (56, 118, 217),
    "fjords": (75, 162, 198),
}

LAKE_TERRAIN_TYPES: dict[str, tuple[int, int, int]] = {
    "lakes": (58, 91, 255),
}

DEFAULT_TERRAIN_LAND = "plains"
DEFAULT_TERRAIN_OCEAN = "deep_ocean"
DEFAULT_TERRAIN_LAKE = "lakes"
