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
)
from ..patching import (
    top_level_assignments,
)
from ..script_references import (
    Definitions,
    FileReferences,
    definitions_of,
    file_references,
    resolve_equipment,
)
from ..syntax import parse
from ..tags import load_all_tags
from ..textio import decode_text, read_text
from ..validation import (
    collect_character_ids,
)
from ._sources import SourcesLayer

# Folders whose script the game reads as effects, triggers and definitions.
_CORPUS_DIRECTORIES = ("common", "events", "history")
# References of inherited files, by path. A file is read again only when its
# size or modification time changes, so a second Mod over the same game, or a
# second validation, does not parse the whole game again.
_INHERITED_REFERENCES: dict[str, tuple[int, int, FileReferences]] = {}


def _inherited_references(path: Path, relative: str) -> FileReferences | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    key = str(path)
    cached = _INHERITED_REFERENCES.get(key)
    if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return cached[2]
    try:
        text = read_text(path)
    except (OSError, ValueError):
        return None
    found = file_references(key, relative, text, own=False)
    _INHERITED_REFERENCES[key] = (stat.st_mtime_ns, stat.st_size, found)
    return found


def _sprite_names(text: str) -> set[str]:
    """The ``name = GFX_...`` of every sprite declared in a ``.gfx`` text."""

    names: set[str] = set()
    for assignment, _ in parse(text).root.walk():
        if assignment.op != "=" or assignment.name.lower() != "name":
            continue
        name = assignment.scalar
        if name is not None and name.startswith("GFX_"):
            names.add(name)
    return names


class CatalogsLayer(SourcesLayer):
    """Catalogs of known identifiers (ideas, events, technologies, equipment, sprites)."""

    def _definitions_in(self, relative: str) -> Definitions:
        """What the effective files of one folder define, read from the tree."""

        merged = Definitions()
        for path, text in self._effective_script_texts(relative):
            try:
                inner = path.relative_to(self.mod_root).as_posix()
            except ValueError:
                inner = f"{relative}/{path.name}"
            found = definitions_of(parse(text), inner)
            merged.events.update(found.events)
            merged.ideas.update(found.ideas)
            merged.advisor_ideas |= found.advisor_ideas
            merged.focus_trees |= found.focus_trees
            merged.technologies |= found.technologies
            merged.equipment.update(found.equipment)
            merged.equipment_copies.extend(found.equipment_copies)
            merged.tags |= found.tags
            merged.organizations |= found.organizations
        return merged

    def _cached_names(self, key: str, relative: str, pick: str) -> set[str]:
        cached = self._scan_cache.get(key)
        if cached is None:
            cached = set(getattr(self._definitions_in(relative), pick))
            self._scan_cache[key] = cached
        return set(cached)

    def _known_advisor_idea_ids(self) -> set[str]:
        """``idea_token`` of every advisor: hired, removed and checked as ideas."""

        return self._cached_names("advisor_ideas", "common/characters", "advisor_ideas")

    def _known_organization_ids(self) -> set[str]:
        """Military-industrial organizations; ``show_ideas_tooltip`` can name them."""

        return self._cached_names(
            "organizations", "common/military_industrial_organization", "organizations"
        )

    def _known_country_tags(self) -> set[str]:
        """Every tag a script may scope into.

        Tags from ``common/country_tags`` of the game and the mod, the aliases of
        ``common/country_tag_aliases`` (``SOU``, ``SPD``, ``VIC``, ...) and the
        countries this mod creates. Dynamic tags ``D01`` to ``D99`` are checked
        by their shape.
        """

        tags = set(load_all_tags(self._base_content_root, self.mod_root))
        tags |= self._cached_names("tag_aliases", "common/country_tag_aliases", "tags")
        tags.update(self._countries)
        return tags

    def _known_focus_tree_ids(self) -> set[str]:
        """Focus trees of the mod (with pending edits) and the ones it inherits."""

        trees = set(self._focus_trees)
        trees |= self._cached_names("focus_trees", "common/national_focus", "focus_trees")
        return trees

    def _pending_script_texts(self) -> dict[Path, str | None]:
        """Text every changed file will have after ``save()``; ``None`` deletes it."""

        render = getattr(self, "_render_dirty_files", None)
        pending: dict[Path, str | None] = dict(self._script_file_updates)
        if render is not None:
            try:
                pending.update(render())
            except Exception:
                # Validation must still run when a pending edit cannot be
                # rendered; save() reports that problem on its own.
                pass
        return pending

    def _reference_corpus(self, *, inherited: bool = True) -> list[FileReferences]:
        """The references of every script file the game will read.

        The mod's own files come with their pending edits. With ``inherited``,
        the files of the game and base mods that the mod does not override (or
        hide with ``replace_path``) come too, marked ``own=False``.
        """

        pending = self._pending_script_texts()
        sources: dict[str, Path] = {}
        for top in _CORPUS_DIRECTORIES:
            if inherited:
                sources.update(
                    (Path(relative).as_posix(), path)
                    for relative, path in self.content_files(top).items()
                )
            else:
                folder = self.mod_root / top
                if folder.is_dir():
                    for path in folder.rglob("*.txt"):
                        sources[path.relative_to(self.mod_root).as_posix()] = path
        for path in pending:
            if path.is_relative_to(self.mod_root):
                relative = path.relative_to(self.mod_root).as_posix()
                if relative.split("/", 1)[0] in _CORPUS_DIRECTORIES:
                    sources[relative] = path
        cache: dict[Path, tuple[str, FileReferences]] = self.__dict__.setdefault(
            "_own_reference_cache", {}
        )
        corpus: list[FileReferences] = []
        for relative, path in sorted(sources.items()):
            if not relative.endswith(".txt"):
                continue
            if not path.is_relative_to(self.mod_root):
                found = _inherited_references(path, relative)
                if found is not None:
                    corpus.append(found)
                continue
            if path in pending:
                text = pending[path]
                if text is None:
                    continue
            else:
                try:
                    text = read_text(path)
                except (OSError, ValueError):
                    continue
            cached = cache.get(path)
            if cached is None or cached[0] != text:
                cached = (text, file_references(str(path), relative, text, own=True))
                cache[path] = cached
            corpus.append(cached[1])
        return corpus

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
            for path in events_dir.rglob("*.txt"):
                try:
                    scanned.update(scan_event_ids_file(path))
                    text = read_text(path)
                except (OSError, ValueError):
                    continue
                scanned.update(definitions_of(parse(text), "events/" + path.name).events)
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
                    cached.update(_sprite_names(text))
            self._scan_cache["focus_icons"] = cached
        icons = set(cached)
        # Staged sprite declarations are visible before save; they are not cached.
        for path, content in self._pending_asset_writes.items():
            if path.suffix.lower() == ".gfx":
                text = decode_text(content)
                icons.update(_sprite_names(text))
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
        ids = set(self._definitions_in("common/technologies").technologies)
        self._scan_cache["technologies"] = ids
        return ids

    def _equipment_unlock_technologies(self) -> dict[str, tuple[str, ...]]:
        if self._equipment_unlock_cache is not None:
            return dict(self._equipment_unlock_cache)
        unlocks: dict[str, list[str]] = {}
        for _, text in self._effective_script_texts("common/technologies"):
            for wrapper in parse(text).root.assignments("technologies"):
                if wrapper.block is None:
                    continue
                for technology in wrapper.block.assignments():
                    if technology.block is None:
                        continue
                    for enabled in technology.block.blocks("enable_equipments"):
                        for equipment in enabled.values():
                            unlocks.setdefault(equipment.value, []).append(
                                technology.key.value
                            )
        self._equipment_unlock_cache = {
            equipment: tuple(dict.fromkeys(technologies))
            for equipment, technologies in unlocks.items()
        }
        return dict(self._equipment_unlock_cache)

    def _known_equipment_ids(self) -> set[str]:
        cached = self._scan_cache.get("equipment")
        if cached is not None:
            return set(cached)
        found = self._definitions_in("common/units/equipment")
        ids = resolve_equipment(found.equipment, found.equipment_copies)
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
