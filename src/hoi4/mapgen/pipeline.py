from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image  # type: ignore[import-not-found]

from ..progress import ProgressCallback as EventProgressCallback
from ..progress import report_progress
from .hoi4_export import TotalConversionProfile, export_all_map_files
from .progress import CancelCallback, ProgressCallback
from .province_generator import generate_provinces
from .territory_generator import GenerationResult, generate_territories


@dataclass(slots=True)
class MapGenerationConfig:
    land_territories: int = 3000
    ocean_territories: int = 300
    land_provinces: int = 3000
    ocean_provinces: int = 300
    density_strength: float = 2.0
    exclude_ocean_density: bool = False
    jagged_land: bool = False
    jagged_ocean: bool = False
    seed: int | None = None


@dataclass(slots=True)
class GeneratedMap:
    territories: GenerationResult
    provinces: GenerationResult
    export_report: dict[str, str]


def _phase_callback(
    callback: ProgressCallback | None,
    event_callback: EventProgressCallback | None,
    start: int,
    width: int,
    phase: str,
) -> ProgressCallback | None:
    if callback is None and event_callback is None:
        return None

    def report(percent: int) -> None:
        overall = start + round(percent * width / 100)
        if callback is not None:
            callback(overall)
        report_progress(
            event_callback,
            operation="map_generation",
            phase=phase,
            current=overall,
            total=100,
            message=f"{phase.replace('_', ' ').title()}: {percent}%",
        )

    return report


def generate_and_export_map(
    land_image: Image.Image,
    mod_root: str | Path,
    *,
    boundary_image: Image.Image | None = None,
    density_image: Image.Image | None = None,
    terrain_image: Image.Image | None = None,
    config: MapGenerationConfig | None = None,
    hoi4_install: str | Path | None = None,
    total_conversion: TotalConversionProfile | None = None,
    progress_fn: ProgressCallback | None = None,
    event_progress: EventProgressCallback | None = None,
    cancel_fn: CancelCallback | None = None,
) -> GeneratedMap:
    """Generate territories and provinces, then export a complete map tree."""
    options = config or MapGenerationConfig()
    if progress_fn is not None:
        progress_fn(0)
    report_progress(
        event_progress,
        operation="map_generation",
        phase="territories",
        current=0,
        total=100,
        message="Generating strategic territories",
    )
    territories = generate_territories(
        land_image,
        boundary_image,
        density_image,
        density_strength=options.density_strength,
        exclude_ocean_density=options.exclude_ocean_density,
        jagged_land=options.jagged_land,
        jagged_ocean=options.jagged_ocean,
        land_count=options.land_territories,
        ocean_count=options.ocean_territories,
        seed=options.seed,
        progress_fn=_phase_callback(progress_fn, event_progress, 0, 35, "territories"),
        cancel_fn=cancel_fn,
    )
    provinces = generate_provinces(
        territories.pmap,
        territories.metadata,
        territories.masks,
        density_image,
        density_strength=options.density_strength,
        exclude_ocean_density=options.exclude_ocean_density,
        jagged_land=options.jagged_land,
        jagged_ocean=options.jagged_ocean,
        land_count=options.land_provinces,
        ocean_count=options.ocean_provinces,
        terrain_image=terrain_image,
        seed=options.seed,
        progress_fn=_phase_callback(progress_fn, event_progress, 35, 35, "provinces"),
        cancel_fn=cancel_fn,
    )
    report = export_all_map_files(
        provinces.metadata,
        provinces.image,
        territories.metadata,
        mod_root,
        hoi4_install=hoi4_install,
        total_conversion=total_conversion,
        progress_fn=_phase_callback(progress_fn, event_progress, 70, 30, "export"),
        cancel_fn=cancel_fn,
    )
    if progress_fn is not None:
        progress_fn(100)
    report_progress(
        event_progress,
        operation="map_generation",
        phase="done",
        current=100,
        total=100,
        message="Map generation complete",
    )
    return GeneratedMap(territories=territories, provinces=provinces, export_report=report)
