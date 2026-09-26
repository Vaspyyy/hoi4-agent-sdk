"""Pure-Python entry points for rendering a political map.

The heavy image dependencies are optional and imported only by the render
function, so content-only SDK users do not need NumPy or Pillow.
"""

from __future__ import annotations

import csv
import hashlib
import re
from colorsys import hsv_to_rgb
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .layers import replace_paths
from .progress import ProgressCallback as EventProgressCallback
from .progress import report_progress
from .parser import find_assignment_block
from .patching import top_level_assignments
from .states import read_state


ProgressCallback = Callable[[int, int], None]
CancelCallback = Callable[[], bool]

_TAG_LINE = re.compile(r'^\s*([A-Z0-9]{3})\s*=\s*"([^"]+)"')
_COUNTRY_TAG = re.compile(r"^[A-Z0-9]{3}$")
_COLOR = re.compile(
    r"\bcolor\s*=\s*(?:rgb\s*)?\{\s*(\d+)\s+(\d+)\s+(\d+)\s*\}"
)


class MapRenderCancelled(RuntimeError):
    """Raised when a caller-provided cancellation callback requests a stop."""


@dataclass
class MapStateData:
    owners: dict[int, str] = field(default_factory=dict)
    province_to_state: dict[int, int] = field(default_factory=dict)
    names: dict[int, str] = field(default_factory=dict)


@dataclass
class PoliticalMapResult:
    """Rendered pixels plus useful map counts and resolved country colours."""

    pixels: Any
    state_count: int
    country_count: int
    unassigned_state_count: int
    resolved_colors: dict[str, tuple[int, int, int]]
    output_path: Path | None = None


def parse_definition_csv(path: Path) -> dict[tuple[int, int, int], int]:
    """Return the RGB-to-province mapping from ``map/definition.csv``."""

    rows = csv.reader(path.read_text(encoding="utf-8", errors="ignore").splitlines(), delimiter=";")
    result: dict[tuple[int, int, int], int] = {}
    for row in rows:
        if len(row) < 4:
            continue
        try:
            province_id, red, green, blue = (int(row[index].strip()) for index in range(4))
        except ValueError:
            continue
        if any(channel < 0 or channel > 255 for channel in (red, green, blue)):
            continue
        result[(red, green, blue)] = province_id
    return result


def load_map_state_data(
    mod_root: Path,
    hoi4_install: Path | None = None,
) -> MapStateData:
    """Load states honoring descriptor replacements and mod overrides by ID."""

    hidden_paths = replace_paths(mod_root)
    data = MapStateData()
    state_provinces: dict[int, set[int]] = {}
    for root in (hoi4_install, mod_root):
        if root is None:
            continue
        directory = root / "history" / "states"
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.txt")):
            if root != mod_root and any(
                path.relative_to(root).is_relative_to(hidden) for hidden in hidden_paths
            ):
                continue
            try:
                state = read_state(path)
            except (OSError, ValueError):
                continue
            for province in state_provinces.get(state.id, set()):
                if data.province_to_state.get(province) == state.id:
                    data.province_to_state.pop(province, None)
            provinces = set(state.provinces)
            state_provinces[state.id] = provinces
            for province in provinces:
                data.province_to_state[province] = state.id
            if state.owner:
                data.owners[state.id] = state.owner
            else:
                data.owners.pop(state.id, None)
            if state.name:
                data.names[state.id] = state.name
            else:
                data.names.pop(state.id, None)
    return data


def load_country_colors(
    mod_root: Path,
    hoi4_install: Path | None = None,
) -> dict[str, tuple[int, int, int]]:
    """Load country colours with mod definitions overriding vanilla ones."""

    result: dict[str, tuple[int, int, int]] = {}
    for root in (hoi4_install, mod_root):
        if root is None:
            continue
        countries_dir = root / "common" / "countries"
        tags_dir = root / "common" / "country_tags"
        for tag, relative in _parse_tag_mappings(tags_dir).items():
            country_path = countries_dir / Path(relative).name
            if not country_path.is_file():
                continue
            match = _COLOR.search(country_path.read_text(encoding="utf-8", errors="ignore"))
            if match:
                result[tag] = tuple(int(part) for part in match.groups())  # type: ignore[assignment]
        colors_path = countries_dir / "colors.txt"
        if colors_path.is_file():
            result.update(_parse_colors_file(colors_path))
    return result


def resolve_country_colors(
    country_colors: dict[str, tuple[int, int, int]],
    tags: set[str],
) -> dict[str, tuple[int, int, int]]:
    """Make every owner's colour distinct and stable across Python processes."""

    result: dict[str, tuple[int, int, int]] = {}
    used: set[tuple[int, int, int]] = set()
    for tag in sorted(tags):
        preferred = country_colors.get(tag)
        if preferred is not None and preferred not in used:
            color = preferred
        else:
            color = _stable_fallback_color(tag, used)
        result[tag] = color
        used.add(color)
    return result


def render_political_map(
    provinces_bmp: Path,
    definition_csv: Path,
    mod_root: Path,
    *,
    hoi4_install: Path | None = None,
    output_path: Path | None = None,
    ocean_color: tuple[int, int, int] = (30, 80, 160),
    unowned_color: tuple[int, int, int] = (128, 128, 128),
    draw_borders: bool = False,
    border_color: tuple[int, int, int] = (48, 48, 48),
    progress: ProgressCallback | None = None,
    event_progress: EventProgressCallback | None = None,
    cancelled: CancelCallback | None = None,
) -> PoliticalMapResult:
    """Render provinces by state owner and optionally save the result.

    Install the SDK's ``map`` extra before calling this function.
    """

    try:
        import numpy as np  # type: ignore[import-not-found]
        from PIL import Image  # type: ignore[import-not-found]
    except ImportError as error:  # pragma: no cover - depends on optional environment
        raise RuntimeError(
            'Political map rendering requires the "map" extra: pip install hoi4-agent-sdk[map]'
        ) from error

    if cancelled and cancelled():
        raise MapRenderCancelled("Political map rendering cancelled")
    report_progress(
        event_progress,
        operation="map_render",
        phase="render",
        current=0,
        total=1,
        message="Loading map data",
    )
    rgb_to_province = parse_definition_csv(definition_csv)
    state_data = load_map_state_data(mod_root, hoi4_install)
    colors = load_country_colors(mod_root, hoi4_install)
    resolved = resolve_country_colors(colors, set(state_data.owners.values()))

    pixels = np.asarray(Image.open(provinces_bmp).convert("RGB"), dtype=np.uint8)
    height, width, _ = pixels.shape
    output = np.empty((height, width, 3), dtype=np.uint8)
    output[:, :] = ocean_color
    state_ids = np.zeros((height, width), dtype=np.int32)

    unique_colors = np.unique(pixels.reshape(-1, 3), axis=0)
    total = len(unique_colors)
    for index, raw_color in enumerate(unique_colors):
        if cancelled and cancelled():
            raise MapRenderCancelled("Political map rendering cancelled")
        red, green, blue = (int(value) for value in raw_color)
        province_id = rgb_to_province.get((red, green, blue))
        if province_id is not None and province_id != 0:
            state_id = state_data.province_to_state.get(province_id)
            if state_id is not None:
                mask = np.all(pixels == raw_color, axis=2)
                state_ids[mask] = state_id
                owner = state_data.owners.get(state_id)
                output[mask] = resolved.get(owner, unowned_color) if owner else unowned_color
        if progress and (index % 250 == 0 or index + 1 == total):
            progress(index + 1, total)
        if event_progress and (index % 250 == 0 or index + 1 == total):
            report_progress(
                event_progress,
                operation="map_render",
                phase="render",
                current=index + 1,
                total=total,
                message="Coloring political map",
            )

    if draw_borders:
        vertical = (state_ids[:, 1:] != state_ids[:, :-1]) & (
            (state_ids[:, 1:] != 0) | (state_ids[:, :-1] != 0)
        )
        horizontal = (state_ids[1:, :] != state_ids[:-1, :]) & (
            (state_ids[1:, :] != 0) | (state_ids[:-1, :] != 0)
        )
        output[:, 1:][vertical] = border_color
        output[1:, :][horizontal] = border_color

    saved_path: Path | None = None
    if output_path is not None:
        saved_path = output_path.resolve()
        saved_path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(output, mode="RGB").save(saved_path)

    all_states = set(state_data.province_to_state.values()) | set(state_data.owners)
    result = PoliticalMapResult(
        pixels=output,
        state_count=len(all_states),
        country_count=len(set(state_data.owners.values())),
        unassigned_state_count=len(all_states - set(state_data.owners)),
        resolved_colors=resolved,
        output_path=saved_path,
    )
    report_progress(
        event_progress,
        operation="map_render",
        phase="done",
        current=1,
        total=1,
        message="Political map render complete",
    )
    return result


def _parse_colors_file(path: Path) -> dict[str, tuple[int, int, int]]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    outer = find_assignment_block(text, "colors")
    body = outer[0] if outer is not None else text
    result: dict[str, tuple[int, int, int]] = {}
    for span in top_level_assignments(body):
        if not span.is_block or span.body_start is None or span.body_end is None:
            continue
        match = _COLOR.search(body[span.body_start : span.body_end])
        if match and _COUNTRY_TAG.fullmatch(span.key):
            result[span.key] = tuple(int(part) for part in match.groups())  # type: ignore[assignment]
    return result


def _parse_tag_mappings(directory: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not directory.is_dir():
        return result
    for path in sorted(directory.glob("*.txt")):
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            match = _TAG_LINE.match(line)
            if match:
                result[match.group(1)] = match.group(2)
    return result


def _stable_fallback_color(
    tag: str,
    used: set[tuple[int, int, int]],
) -> tuple[int, int, int]:
    seed = int.from_bytes(hashlib.sha256(tag.encode("ascii", errors="ignore")).digest()[:8], "big")
    for attempt in range(1024):
        hue = ((seed % 3600) / 3600.0 + attempt * 0.618033988749895) % 1.0
        red, green, blue = hsv_to_rgb(hue, 0.58, 0.72)
        color = (round(red * 255), round(green * 255), round(blue * 255))
        if color not in used:
            return color
    raise RuntimeError("Could not allocate a distinct country colour")
