"""Catalogs of known identifiers (ideas, events, technologies, equipment, sprites)."""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from ..events import scan_event_ids_file
from ..ideas import scan_idea_ids_file
from ..parser import (
    iter_assignment_blocks,
    strip_comments,
)
from ..patching import (
    top_level_assignments,
)
from ..textio import decode_text, read_text
from ..validation import (
    collect_character_ids,
)
from ._shared import (
    _EQUIPMENT_STOCKPILE_RE,
    _TECH_BLOCK_RE,
)
from ._sources import SourcesLayer

_SCRIPT_BLOCK_ID_RE = re.compile(r"(?m)^\s*([A-Za-z0-9_.:-]+)\s*=\s*\{")


_GFX_NAME_RE = re.compile(r"\bname\s*=\s*\"?([A-Za-z0-9_.:-]+)\"?")


class CatalogsLayer(SourcesLayer):
    """Catalogs of known identifiers (ideas, events, technologies, equipment, sprites)."""

    @staticmethod
    def _technology_refs(script: str) -> list[str]:
        refs: list[str] = []
        for body in _TECH_BLOCK_RE.findall(script):
            refs.extend(
                key for key in re.findall(r"\b([A-Za-z0-9_.:-]+)\s*=", body) if key != "popup"
            )
        return refs

    @staticmethod
    def _equipment_refs(script: str) -> list[str]:
        refs: list[str] = []
        for body in _EQUIPMENT_STOCKPILE_RE.findall(script):
            refs.extend(re.findall(r"\btype\s*=\s*([A-Za-z0-9_.:-]+)", body))
        return refs

    def _known_idea_ids(self) -> set[str]:
        ids = set(self._ideas)
        # Dirty files will be serialized from their surviving models. Never
        # recover deleted IDs from their on-disk contents (or a warmed scan).
        replaced = set(self._group_ideas_by_file(dirty_only=True))
        replaced.update(self._script_file_updates)
        files: dict[Path, Path] = {}
        for base in reversed(self._data_roots()):
            for rel in ("common/ideas", "common/national_ideas"):
                for source in (base / rel).glob("*.txt"):
                    files[self.mod_root / source.relative_to(base)] = source
        for target, source in files.items():
            if target in replaced:
                continue
            # Cache file contributions separately so replacing one file does
            # not hide a legitimate definition of the same ID in another.
            key = f"ideas:{source}"
            scanned = self._scan_cache.get(key)
            if scanned is None:
                try:
                    scanned = scan_idea_ids_file(source)
                except (OSError, ValueError):
                    continue
                self._scan_cache[key] = scanned
            ids.update(scanned)
        return ids

    def _known_event_ids(self) -> set[str]:
        ids = set(self._events)
        cached = self._scan_cache.get("events")
        if cached is not None:
            ids.update(cached)
            return ids
        scanned: set[str] = set()
        for base in self._data_roots():
            events_dir = base / "events"
            if not events_dir.exists():
                continue
            for path in events_dir.glob("*.txt"):
                try:
                    scanned.update(scan_event_ids_file(path))
                except (OSError, ValueError):
                    continue
        self._scan_cache["events"] = scanned
        ids.update(scanned)
        return ids

    def _known_character_ids(self) -> set[str]:
        cached = self._scan_cache.get("characters")
        if cached is not None:
            return set(cached)
        character_ids = collect_character_ids(self.mod_root, self._base_content_root)
        self._scan_cache["characters"] = character_ids
        return set(character_ids)

    def _known_focus_icons(self) -> set[str]:
        cached = self._scan_cache.get("focus_icons")
        if cached is None:
            cached = set()
            for base in self._data_roots():
                interface_dir = base / "interface"
                if not interface_dir.exists():
                    continue
                for path in interface_dir.rglob("*.gfx"):
                    try:
                        text = read_text(path)
                    except Exception:
                        continue
                    cached.update(
                        name for name in _GFX_NAME_RE.findall(text) if name.startswith("GFX_")
                    )
            self._scan_cache["focus_icons"] = cached
        icons = set(cached)
        # Staged sprite declarations are visible before save; they are not cached.
        for path, content in self._pending_asset_writes.items():
            if path.suffix.lower() == ".gfx":
                text = decode_text(content)
                icons.update(
                    name for name in _GFX_NAME_RE.findall(text) if name.startswith("GFX_")
                )
        return icons

    def _known_sprite_textures(self) -> dict[str, str]:
        def scan_text(text: str, textures: dict[str, str]) -> None:
            cached = self._sprite_text_parse_cache.get(text)
            if cached is not None:
                textures.update(cached)
                return
            parsed: dict[str, str] = {}
            for body, _, _ in iter_assignment_blocks(text, "spriteType"):
                assignments = {
                    span.key: span
                    for span in top_level_assignments(body)
                    if not span.is_block
                }
                name_span = assignments.get("name")
                texture_span = assignments.get("texturefile")
                if name_span is None or texture_span is None:
                    continue
                name = body[name_span.value_start : name_span.value_end].strip().strip('"')
                texture = (
                    body[texture_span.value_start : texture_span.value_end]
                    .strip()
                    .strip('"')
                    .replace("\\", "/")
                )
                if name and texture:
                    parsed[name] = texture
            self._sprite_text_parse_cache[text] = parsed
            textures.update(parsed)

        def scan(base: Path, textures: dict[str, str]) -> None:
            interface_dir = base / "interface"
            if not interface_dir.is_dir():
                return
            for path in interface_dir.rglob("*.gfx"):
                try:
                    text = read_text(path)
                    scan_text(text, textures)
                except (OSError, ValueError):
                    continue

        if self._sprite_texture_cache is None:
            vanilla: dict[str, str] = {}
            if self._base_content_root is not None:
                scan(self._base_content_root, vanilla)
            self._sprite_texture_cache = vanilla
        textures = dict(self._sprite_texture_cache)
        scan(self.mod_root, textures)
        for path, content in self._pending_asset_writes.items():
            if path.suffix.lower() == ".gfx":
                scan_text(decode_text(content), textures)
        return dict(textures)

    def _texture_exists(self, relative_path: str) -> bool:
        normalized = relative_path.replace("\\", "/").lstrip("/")
        return any(
            (root / normalized).is_file()
            for root in (self.mod_root, self._base_content_root)
            if root is not None
        ) or (self.mod_root / normalized) in self._pending_asset_writes

    def _known_technology_ids(self) -> set[str]:
        cached = self._scan_cache.get("technologies")
        if cached is not None:
            return set(cached)
        ids: set[str] = set()
        ignored = {
            "technologies",
            "folder",
            "path",
            "xor",
            "research_cost",
            "start_year",
            "categories",
        }
        for _, text in self._effective_script_texts("common/technologies"):
            ids.update(
                candidate
                for candidate in _SCRIPT_BLOCK_ID_RE.findall(text)
                if candidate not in ignored
            )
        self._scan_cache["technologies"] = ids
        return ids

    def _equipment_unlock_technologies(self) -> dict[str, tuple[str, ...]]:
        if self._equipment_unlock_cache is not None:
            return dict(self._equipment_unlock_cache)
        unlocks: dict[str, list[str]] = {}
        for _, text in self._effective_script_texts("common/technologies"):
            try:
                wrappers = [
                    span
                    for span in top_level_assignments(text)
                    if span.key == "technologies"
                    and span.is_block
                    and span.body_start is not None
                    and span.body_end is not None
                ]
                for wrapper in wrappers:
                    assert wrapper.body_start is not None
                    assert wrapper.body_end is not None
                    body = text[wrapper.body_start : wrapper.body_end]
                    for technology in top_level_assignments(body):
                        if (
                            not technology.is_block
                            or technology.body_start is None
                            or technology.body_end is None
                        ):
                            continue
                        tech_body = body[
                            technology.body_start : technology.body_end
                        ]
                        equipment_span = next(
                            (
                                span
                                for span in top_level_assignments(tech_body)
                                if span.key == "enable_equipments"
                                and span.is_block
                                and span.body_start is not None
                                and span.body_end is not None
                            ),
                            None,
                        )
                        if equipment_span is None:
                            continue
                        assert equipment_span.body_start is not None
                        assert equipment_span.body_end is not None
                        equipment_body = strip_comments(
                            tech_body[
                                equipment_span.body_start : equipment_span.body_end
                            ]
                        )
                        for equipment in re.findall(
                            r"\b[A-Za-z][A-Za-z0-9_.:-]*\b",
                            equipment_body,
                        ):
                            unlocks.setdefault(equipment, []).append(
                                technology.key
                            )
            except (OSError, ValueError):
                continue
        self._equipment_unlock_cache = {
            equipment: tuple(dict.fromkeys(technologies))
            for equipment, technologies in unlocks.items()
        }
        return dict(self._equipment_unlock_cache)

    def _known_equipment_ids(self) -> set[str]:
        cached = self._scan_cache.get("equipment")
        if cached is not None:
            return set(cached)
        ids: set[str] = set()
        for _, text in self._effective_script_texts("common/units/equipment"):
            ids.update(_SCRIPT_BLOCK_ID_RE.findall(text))
        self._scan_cache["equipment"] = ids
        return ids

    def _known_country_name_pool_tags(self) -> set[str]:
        cached = self._scan_cache.get("country_name_pools")
        if cached is not None:
            return set(cached)
        tags: set[str] = set()
        for _, text in self._effective_script_texts("common/names"):
            try:
                tags.update(
                    span.key
                    for span in top_level_assignments(text)
                    if span.is_block
                    and (span.key == "default" or re.fullmatch(r"[A-Z0-9]{3}", span.key))
                )
            except ValueError:
                continue
        self._scan_cache["country_name_pools"] = tags
        return set(tags)

    def _known_sub_unit_types(self) -> set[str]:
        cached = self._scan_cache.get("sub_units")
        if cached is not None:
            return set(cached)
        result: set[str] = set()
        for root in self._data_roots():
            directory = root / "common" / "units"
            if not directory.is_dir():
                continue
            for path in directory.glob("*.txt"):
                try:
                    text = read_text(path)
                    for body, _, _ in iter_assignment_blocks(text, "sub_units"):
                        result.update(
                            span.key
                            for span in top_level_assignments(body)
                            if span.is_block
                        )
                except (OSError, ValueError):
                    continue
        self._scan_cache["sub_units"] = result
        return result

    def _effective_province_types(self) -> dict[int, str]:
        cached = self._scan_cache.get("province_type_rows")
        if cached is not None:
            # The scan cache stores strings, so encode ``id:type`` pairs.
            cached_result: dict[int, str] = {}
            for item in cached:
                id_text, separator, province_type = item.partition(":")
                if separator and id_text.isdigit():
                    cached_result[int(id_text)] = province_type
            return cached_result
        path: Path | None = None
        for root in (self.mod_root, self._base_content_root):
            if root is None:
                continue
            candidate = root / "map" / "definition.csv"
            if candidate.is_file():
                path = candidate
                break
        province_types: dict[int, str] = {}
        if path is not None:
            for row in csv.reader(io.StringIO(read_text(path)), delimiter=";"):
                if len(row) < 5:
                    continue
                try:
                    numeric_id = int(row[0].strip())
                except ValueError:
                    continue
                province_types[numeric_id] = row[4].strip().lower()
        self._scan_cache["province_type_rows"] = {
            f"{province_id}:{province_type}"
            for province_id, province_type in province_types.items()
        }
        return province_types
