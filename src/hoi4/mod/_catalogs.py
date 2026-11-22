"""Catalogs of known identifiers (ideas, events, technologies, equipment, sprites)."""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Collection
from pathlib import Path

from .._file_cache import cached_file_result
from ..characters import AdvisorRole
from ..events import VALID_EVENT_TYPES
from ..focus_layout import allow_branch_condition, block_allow_branch_condition
from ..ideas import idea_ids_in_text
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
from ..textio import decode_text, read_text
from ._sources import SourcesLayer

# ``[?name]`` in a localisation text reads a variable or a flag
# (``[?SIA_flag:days_left]``); every dotted part may be the name.
_LOC_VALUE_RE = re.compile(r"\[\?([A-Za-z_@][A-Za-z0-9_.@]*)")
# Folders whose script the game reads as effects, triggers and definitions.
_CORPUS_DIRECTORIES = ("common", "events", "history")


def _inherited_references(path: Path, relative: str) -> FileReferences | None:
    """The references of an inherited file, kept until the file changes.

    A second Mod over the same game, or a second validation, does not parse
    the whole game again.
    """

    key = str(path)
    try:
        return cached_file_result(
            f"file_references:{relative}",
            path,
            lambda text: file_references(key, relative, text, own=False),
        )
    except (OSError, ValueError):
        return None


_FOCUS_KEYS = ("focus", "shared_focus", "joint_focus")


def _focus_ids(text: str) -> frozenset[str]:
    """The ``id`` of every focus in a focus file.

    Read from the focuses of each ``focus_tree`` and from the top-level
    ``focus``, ``shared_focus`` and ``joint_focus`` blocks.
    """

    ids: set[str] = set()
    root = parse(text).root
    blocks = [block for key in _FOCUS_KEYS for block in root.blocks(key, fold=True)]
    for tree in root.blocks("focus_tree", fold=True):
        blocks.extend(block for key in _FOCUS_KEYS for block in tree.blocks(key, fold=True))
    for block in blocks:
        focus_id = block.scalar("id", fold=True)
        if focus_id:
            ids.add(focus_id)
    return frozenset(ids)


def _shared_focuses_in(text: str) -> dict[str, tuple[frozenset[str], str | None]]:
    """What a tree needs to know of every top-level focus of a focus file, by id.

    These are the ``shared_focus`` and ``joint_focus`` blocks (and a bare
    ``focus``) that a tree pulls in with ``shared_focus = ID``. For each: its
    prerequisites and its ``allow_branch`` condition (``None`` without one,
    ``""`` when it always holds).
    """

    found: dict[str, tuple[frozenset[str], str | None]] = {}
    root = parse(text).root
    for key in _FOCUS_KEYS:
        for block in root.blocks(key, fold=True):
            focus_id = block.scalar("id", fold=True)
            if focus_id:
                parents = frozenset(
                    item.scalar
                    for group in block.blocks("prerequisite", fold=True)
                    for item in group.assignments("focus", fold=True)
                    if item.scalar
                )
                found[focus_id] = (parents, block_allow_branch_condition(block))
    return found


def _sprite_names(text: str) -> frozenset[str]:
    """The ``name = GFX_...`` of every sprite declared in a ``.gfx`` text."""

    names: set[str] = set()
    for assignment, _ in parse(text).root.walk():
        if assignment.op != "=" or assignment.name.lower() != "name":
            continue
        name = assignment.scalar
        if name is not None and name.startswith("GFX_"):
            names.add(name)
    return frozenset(names)


def _name_pool_tags(text: str) -> frozenset[str]:
    """Tags (and ``default``) with a name pool block in a ``common/names`` file."""

    return frozenset(
        span.key
        for span in top_level_assignments(text)
        if span.is_block and (span.key == "default" or re.fullmatch(r"[A-Z0-9]{3}", span.key))
    )


def _sub_unit_types(text: str) -> frozenset[str]:
    """The sub-unit types the ``sub_units`` blocks of a units file define."""

    return frozenset(
        span.key
        for body, _, _ in iter_assignment_blocks(text, "sub_units")
        for span in top_level_assignments(body)
        if span.is_block
    )


def _idea_ids(text: str) -> frozenset[str]:
    """The id of every idea in an ideas file."""

    return frozenset(idea_ids_in_text(text))


def _event_ids(text: str) -> frozenset[str]:
    """The id of every event an event file defines.

    A top-level block counts when its key is an event type the game knows or
    ends in ``_event``, like the loader reads it.
    """

    doc = parse(text)
    ids = {
        event_id
        for item in doc.root.assignments()
        if item.block is not None
        and (item.name in VALID_EVENT_TYPES or item.name.endswith("_event"))
        and (event_id := item.block.scalar("id"))
    }
    ids.update(definitions_of(doc, "events/file.txt").events)
    return frozenset(ids)


def _character_ids(text: str) -> frozenset[str]:
    """The id of every character in the ``characters = { ... }`` blocks of a file."""

    return frozenset(
        character.name
        for container in parse(text).root.blocks("characters", fold=True)
        for character in container.assignments()
        if character.block is not None
    )


class CatalogsLayer(SourcesLayer):
    """Catalogs of known identifiers (ideas, events, technologies, equipment, sprites)."""

    def _definitions_in(self, relative: str, *, skip: Collection[Path] = ()) -> Definitions:
        """What the effective files of one folder define, read from the tree.

        Each file is read once while it is unchanged (``hoi4._file_cache``);
        a file with a pending import is read from its pending text. ``skip``
        leaves out mod files whose content the loaded models hold.
        """

        # definitions_of routes by folder, so any file name inside it works.
        label = f"{relative}/file.txt"
        merged = Definitions()
        for _, found in self._effective_script_results(
            relative,
            f"definitions:{relative}",
            lambda text: definitions_of(parse(text), label),
            skip=skip,
        ):
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

    # Every catalog below has one shape. The mod's own content comes from the
    # loaded models, which hold unsaved creations, edits and deletions. The
    # files of the game and the base mods, and the mod's files whose content
    # no model edit replaces (also files that failed to load), come from a
    # scan that hoi4._file_cache keeps per file until the file changes. A
    # mod file with unsaved model edits is not scanned, so a deleted id does
    # not come back, and nothing about the mod's content is kept in
    # _scan_cache, so save() and discard() never leave a catalog stale.

    def _known_advisor_idea_ids(self) -> set[str]:
        """``idea_token`` of every advisor: hired, removed and checked as ideas."""

        tokens = {
            role.idea_token
            for character in self._characters.values()
            for role in (
                *character.roles,
                *(role for instance in character.instances for role in instance.roles),
            )
            if isinstance(role, AdvisorRole) and role.idea_token
        }
        replaced = set(self._group_characters_by_file(dirty_only=True))
        tokens |= self._definitions_in("common/characters", skip=replaced).advisor_ideas
        return tokens

    def _known_organization_ids(self) -> set[str]:
        """Military-industrial organizations; ``show_ideas_tooltip`` can name them."""

        return set(self._definitions_in("common/military_industrial_organization").organizations)

    def _defined_country_tags(self) -> set[str]:
        """Every country tag the game will know with this mod, pending edits included.

        The tags of the effective ``common/country_tags`` files: the game's
        and the base mods' files unless the mod overrides them or hides the
        folder with ``replace_path``, and the mod's own. A country deleted
        through the API is gone from the mod's files before ``save()``, and
        a created one is there.
        """

        purpose = "definitions:common/country_tags"
        label = "common/country_tags/file.txt"
        deleted = set(self._deleted_countries)
        tags: set[str] = set()
        for _, source, pending in self._effective_script_files("common/country_tags"):
            try:
                if pending is not None:
                    found = definitions_of(parse(pending()), label).tags
                else:
                    found = cached_file_result(
                        purpose, source, lambda text: definitions_of(parse(text), label)
                    ).tags
            except (OSError, ValueError):
                continue
            own = pending is not None or source.is_relative_to(self.mod_root)
            tags |= found - deleted if own else found
        tags.update(self._countries)
        return tags

    def _known_country_tags(self) -> set[str]:
        """Every tag a script may scope into.

        The defined tags and the aliases of ``common/country_tag_aliases``
        (``SOU``, ``SPD``, ``VIC``, ...). Dynamic tags ``D01`` to ``D99`` are
        checked by their shape.
        """

        tags = self._defined_country_tags()
        tags |= self._definitions_in("common/country_tag_aliases").tags
        return tags

    def _known_focus_tree_ids(self) -> set[str]:
        """Focus trees of the mod (with pending edits) and the ones it inherits.

        The container of a file's top-level focuses (``kind == "file"``) is
        not a tree the game knows.
        """

        trees = self._own_focus_tree_ids()
        replaced = set(self._group_focus_trees_by_file(dirty_only=True))
        trees |= self._definitions_in("common/national_focus", skip=replaced).focus_trees
        return trees

    def _shared_focuses(
        self,
    ) -> tuple[dict[str, frozenset[str]], dict[str, str | None]]:
        """The prerequisites and the ``allow_branch`` condition of every shared or joint focus.

        Two maps by focus id, for every such focus the game will know.
        ``shared_focus = ID`` in a tree puts ``ID`` on its canvas, and with it
        the shared focuses below it; a focus of the tree that hangs below one
        is hidden with it. The mod's own come from the models (with pending
        edits), the inherited ones from a scan of each file.
        """

        parents: dict[str, frozenset[str]] = {}
        conditions: dict[str, str | None] = {}
        for tree in self._focus_trees.values():
            if tree.kind != "file":
                continue
            for focus in tree.focuses:
                if focus.id:
                    parents[focus.id] = frozenset(
                        ref for group in focus.prerequisites for ref in group
                    )
                    conditions[focus.id] = allow_branch_condition(focus)
        replaced = set(self._group_focus_trees_by_file(dirty_only=True))
        for _, found in self._effective_script_results(
            "common/national_focus", "shared_focuses", _shared_focuses_in, skip=replaced
        ):
            for focus_id, (refs, condition) in found.items():
                if focus_id not in parents:
                    parents[focus_id] = refs
                    conditions[focus_id] = condition
        return parents, conditions

    def _own_focus_tree_ids(self) -> set[str]:
        """The mod's own focus trees, with pending edits.

        The container of a file's top-level focuses is left out: it is how
        the SDK holds them, and ``load_focus_tree`` cannot name it.
        """

        return {tree_id for tree_id, tree in self._focus_trees.items() if tree.kind != "file"}

    def _known_focus_ids(self) -> set[str]:
        """Every focus id of the mod (with pending edits) and of the files it inherits.

        Shared and joint focuses count, so a mod's focus may require or
        anchor to a focus the game defines. Each file is scanned once while
        it is unchanged. The mod's files with unsaved focus edits are read
        from the models only, so a deleted focus does not come back.
        """

        ids = {focus.id for tree in self._focus_trees.values() for focus in tree.focuses}
        replaced = set(self._group_focus_trees_by_file(dirty_only=True))
        for _, found in self._effective_script_results(
            "common/national_focus", "focus_ids", _focus_ids, skip=replaced
        ):
            ids |= found
        return ids

    def _localisation_value_names(self) -> set[str]:
        """Names that localisation texts read with ``[?name]``: variables and flags.

        Read from the mod's entries (with pending edits) and the English
        localisation it inherits.
        """

        def names_in(text: str) -> set[str]:
            found: set[str] = set()
            for match in _LOC_VALUE_RE.finditer(text):
                found.update(part for part in match.group(1).split(".") if part)
            return found

        inherited = self._scan_cache.get("localisation_value_names")
        if inherited is None:
            inherited = set()
            base = self._base_content_root
            folder = base / "localisation" if base is not None else None
            if folder is not None and folder.is_dir():
                for path in folder.rglob("*english*.yml"):
                    try:
                        inherited |= names_in(read_text(path))
                    except (OSError, ValueError):
                        continue
            self._scan_cache["localisation_value_names"] = inherited
        names = set(inherited)
        for value in self._loc_entries.values():
            if "[?" in value:
                names |= names_in(value)
        return names

    def _pending_script_texts(self) -> dict[Path, str | None]:
        """Text every changed file will have after ``save()``; ``None`` deletes it.

        Rendered once per validate() run; the result is shared, never change it.
        """

        return self._run_cached("pending_script_texts", self._render_pending_script_texts)

    def _render_pending_script_texts(self) -> dict[Path, str | None]:
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
        hide with ``replace_path``) come too, marked ``own=False``. The checks
        of one validate() run share the list.
        """

        return list(
            self._run_cached(
                f"reference_corpus:{inherited}",
                lambda: self._build_reference_corpus(inherited=inherited),
            )
        )

    def _build_reference_corpus(self, *, inherited: bool) -> list[FileReferences]:
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
        cache = self._own_reference_cache
        characters = self._character_scope_names()
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
            if cached is None or cached[0] != text or cached[1] is not characters:
                found = file_references(
                    str(path), relative, text, own=True, characters=characters
                )
                cached = (text, characters, found)
                cache[path] = cached
            corpus.append(cached[2])
        return corpus

    def _character_scope_names(self) -> frozenset[str]:
        """The ids of every character, lower case: used as a key, one opens its scope.

        The references of the mod's own files are read with them, so what
        stands inside ``GER_erwin_rommel = { owner = { ... } }`` keeps its
        scope. The files of the game are read without: their references are
        only counted as uses.
        """

        def build() -> frozenset[str]:
            names = frozenset(name.lower() for name in self._known_character_ids())
            # The same object as long as the names are the same, so that the
            # cached references of a file are checked by identity.
            if names != self._character_scope_names_seen:
                self._character_scope_names_seen = names
            return self._character_scope_names_seen

        return self._run_cached("character_scope_names", build)

    def _known_idea_ids(self) -> set[str]:
        ids = set(self._ideas)
        # Dirty files will be serialized from their surviving models, and an
        # imported idea file is loaded into them. Never recover deleted IDs
        # from their on-disk contents.
        replaced = set(self._group_ideas_by_file(dirty_only=True))
        replaced.update(self._script_file_updates)
        for relative in ("common/ideas", "common/national_ideas"):
            for _, found in self._effective_script_results(
                relative, "idea_ids", _idea_ids, skip=replaced
            ):
                ids |= found
        return ids

    def _known_event_ids(self) -> set[str]:
        ids = set(self._events)
        replaced = set(self._group_events_by_file(dirty_only=True)[0])
        for _, found in self._effective_script_results(
            "events", "event_ids", _event_ids, skip=replaced
        ):
            ids |= found
        return ids

    def _known_character_ids(self) -> set[str]:
        ids = set(self._characters)
        replaced = set(self._group_characters_by_file(dirty_only=True))
        for _, found in self._effective_script_results(
            "common/characters", "character_ids", _character_ids, skip=replaced
        ):
            ids |= found
        return ids

    def _known_focus_icons(self) -> set[str]:
        """Every ``GFX_`` sprite of the effective ``interface/*.gfx`` files.

        A staged sprite file (not saved yet) replaces the file on disk.
        """

        icons: set[str] = set()
        for name, source in self.content_files("interface").items():
            if not name.lower().endswith(".gfx"):
                continue
            if self.mod_root / name in self._pending_asset_writes:
                continue
            try:
                icons |= cached_file_result("sprite_names", source, _sprite_names)
            except (OSError, ValueError):
                continue
        for path, content in self._pending_asset_writes.items():
            if path.suffix.lower() == ".gfx":
                icons |= _sprite_names(decode_text(content))
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
        return set(self._definitions_in("common/technologies").technologies)

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
        found = self._definitions_in("common/units/equipment")
        return set(resolve_equipment(found.equipment, found.equipment_copies))

    def _known_country_name_pool_tags(self) -> set[str]:
        """Tags with a name pool; the pending ``set_country_name_pool`` edits count."""

        tags: set[str] = set()
        for _, found in self._effective_script_results(
            "common/names", "name_pool_tags", _name_pool_tags
        ):
            tags |= found
        return tags

    def _known_sub_unit_types(self) -> set[str]:
        """Sub-unit types of ``common/units/*.txt`` (not its sub-folders)."""

        units = self.mod_root / "common" / "units"
        nested = {
            target
            for target, _, _ in self._effective_script_files("common/units")
            if target.parent != units
        }
        result: set[str] = set()
        for _, found in self._effective_script_results(
            "common/units", "sub_unit_types", _sub_unit_types, skip=nested
        ):
            result |= found
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
