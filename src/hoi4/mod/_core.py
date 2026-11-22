"""Shared state, content-layer lookup, raw readers and transaction snapshots."""

from __future__ import annotations

import copy
import hashlib
import os
import threading
import time
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Optional, Self, Sequence, TypeVar

from .._disk_cache import DiskAnalysis
from .._file_cache import cached_file_result
from ..bookmarks import (
    Bookmark,
)
from ..characters import (
    Character,
)
from ..dynamic_modifiers import (
    DynamicModifier,
)
from ..events import load_events_file
from ..focus import load_focus_trees
from ..ideas import read_ideas_file
from ..ideologies import Ideology
from ..layers import ContentLayers, ContentView, FolderView, relative_content_path, replace_paths
from ..localisation import LOCALISATION_ON_DISK, _localization_entries, _read_localization
from ..on_actions import load_on_actions_file
from ..oob import (
    OrderOfBattle,
)
from ..parser import (
    PdxNode,
)
from ..paths import (
    resolve_mod_output_path,
)
from ..script_vocabulary import (
    GameScriptVocabulary,
)
from ..states import state_index_header
from ..syntax import Document, require_well_formed
from ..tags import (
    load_vanilla_tags,
)
from ..textio import read_text

if TYPE_CHECKING:
    from ..script_references import FileReferences
from ..types import (
    Country,
    Decision,
    DecisionCategory,
    Event,
    FocusTree,
    Idea,
    LoadDiagnostic,
    OnAction,
    State,
)

def _decode_state_header(data: object) -> tuple[int, str, str | None] | None:
    """A stored state header. Anything else is a damaged entry and is read again."""

    if data is None:
        return None
    if (
        not isinstance(data, list)
        or len(data) != 3
        or type(data[0]) is not int
        or not isinstance(data[1], str)
        or not (data[2] is None or isinstance(data[2], str))
    ):
        raise TypeError("a stored state header must be [id, name, owner]")
    return data[0], data[1], data[2]


_STATE_HEADERS_ON_DISK: DiskAnalysis[tuple[int, str, str | None] | None] = DiskAnalysis(
    "state_index_header",
    lambda header: list(header) if header is not None else None,
    _decode_state_header,
)

def _path_under(path: Path, root: Path) -> bool:
    """``path.is_relative_to(root)``, compared as text (much faster)."""

    base = os.path.normcase(os.fspath(root))
    text = os.path.normcase(os.fspath(path))
    return text == base or text.startswith(os.path.join(base, ""))


_InheritedModel = TypeVar("_InheritedModel", FocusTree, Event, Idea)
_Built = TypeVar("_Built")


@dataclass(frozen=True)
class Section:
    """One kind of content the mod loads on first use.

    ``data`` lists the attributes the loader fills, each with the factory of
    its empty value. They are not set until the section loads: the first read
    of one runs ``loader``. ``pending`` lists the attributes that hold unsaved
    edits; they always exist and are empty after a load. ``folders`` are the
    mod-relative folders the section's own files live in, and ``reads`` other
    folders whose files its loader also reads. ``depends_on`` names sections
    whose data this one is built from: unloading one of them unloads this one
    too. ``caches`` lists data attributes that lookups fill later on demand,
    with the lookup that fills one entry (used by the reload check).
    """

    name: str
    loader: str
    data: tuple[tuple[str, Callable[[], object]], ...]
    pending: tuple[tuple[str, Callable[[], object]], ...] = ()
    dirty_names: tuple[str, ...] = ()
    folders: tuple[str, ...] = ()
    reads: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    caches: tuple[tuple[str, str], ...] = ()


def _none() -> None:
    return None


# Dirty names whose edits are staged as whole files (script imports, scripted
# triggers, binary assets). They map to sections through the staged paths.
STAGED_FILE_DIRTY_NAMES = frozenset({"script_files", "scripted_triggers", "assets"})


class SectionRegistry:
    """Lookups over the sections, in load order."""

    def __init__(self, sections: Sequence[Section]):
        self.sections = tuple(sections)
        self.by_name = {section.name: section for section in self.sections}
        self.by_attribute: dict[str, Section] = {}
        self.by_dirty_name: dict[str, set[str]] = {}
        self.order = {section.name: index for index, section in enumerate(self.sections)}
        for section in self.sections:
            for attribute, _ in section.data:
                self.by_attribute[attribute] = section
            for dirty_name in section.dirty_names:
                self.by_dirty_name.setdefault(dirty_name, set()).add(section.name)
            unknown = set(section.depends_on) - set(self.by_name)
            if unknown:
                raise ValueError(f"Section {section.name} depends on unknown {sorted(unknown)}")
        if len(self.by_name) != len(self.sections):
            raise ValueError("Two sections share a name")
        attributes = [
            attribute
            for section in self.sections
            for attribute, _ in (*section.data, *section.pending)
        ]
        if len(attributes) != len(set(attributes)):
            raise ValueError("An attribute belongs to two sections")

    def with_dependents(self, names: set[str]) -> set[str]:
        """``names`` plus every section that depends on one of them, transitively."""

        result = set(names)
        changed = True
        while changed:
            changed = False
            for section in self.sections:
                if section.name not in result and result & set(section.depends_on):
                    result.add(section.name)
                    changed = True
        return result

    def owner(self, relative: Path) -> str | None:
        """The section whose own folders hold a content-relative path."""

        for section in self.sections:
            if any(relative.is_relative_to(folder) for folder in section.folders):
                return section.name
        return None

    def readers(self, relative: Path) -> set[str]:
        """Sections whose loader reads a file at this content-relative path."""

        return {
            section.name
            for section in self.sections
            if any(relative.is_relative_to(folder) for folder in (*section.folders, *section.reads))
        }


# Load order. ``load_diagnostics`` lists diagnostics in this order.
SECTIONS = SectionRegistry((
    Section(
        "countries", "_load_countries",
        data=(
            ("_countries", dict),
            ("_country_color_sources", dict),
            ("_country_tag_mappings", dict),
            ("_created_country_tags", set),
        ),
        pending=(
            ("_dirty_countries", set),
            ("_deleted_countries", dict),
            ("_country_name_pool_updates", dict),
        ),
        dirty_names=("countries", "country_names"),
        folders=("common/countries", "common/country_tags", "history/countries"),
        # Country names come from localisation and the leader from the
        # character files.
        reads=("common/characters", "localisation"),
        depends_on=("characters",),
        caches=(("_countries", "get_country"),),
    ),
    Section(
        "characters", "_load_characters",
        data=(("_characters", dict),),
        pending=(("_dirty_characters", set), ("_dirty_character_files", set)),
        dirty_names=("characters",),
        folders=("common/characters",),
        caches=(("_characters", "get_character"),),
    ),
    Section(
        "oobs", "_load_oobs",
        data=(("_oobs", dict),),
        pending=(("_dirty_oobs", set), ("_dirty_oob_files", set)),
        dirty_names=("oobs",),
        folders=("history/units",),
        # Each OOB file is assigned to the country whose history names it.
        depends_on=("countries",),
        caches=(("_oobs", "get_oob"),),
    ),
    Section(
        "states", "_load_states",
        data=(
            ("_states", dict),
            ("_state_ids", list),
            ("_state_source_paths", dict),
            ("_writable_state_directory_stamp", _none),
            ("_inherited_state_source_paths", _none),
        ),
        pending=(
            ("_dirty_states", set),
            ("_deleted_state_ids", set),
            ("_deleted_state_files", dict),
            ("_map_file_updates", dict),
        ),
        dirty_names=("states", "map"),
        folders=("history/states",),
        caches=(("_states", "get_state"), ("_state_source_paths", "get_state")),
    ),
    Section(
        "focus", "_load_focus_trees",
        data=(("_focus_trees", dict),),
        pending=(("_dirty_focus_trees", set), ("_dirty_focus_files", set)),
        dirty_names=("focus",),
        folders=("common/national_focus",),
    ),
    Section(
        "events", "_load_events",
        data=(
            ("_events", dict),
            ("_event_namespaces", dict),
            ("_event_file_namespaces", dict),
        ),
        pending=(("_dirty_events", set), ("_dirty_event_files", set)),
        dirty_names=("events",),
        folders=("events",),
    ),
    Section(
        "on_actions", "_load_on_actions",
        data=(("_on_actions", dict), ("_on_action_occurrences", dict)),
        pending=(("_dirty_on_actions", set), ("_dirty_on_action_files", set)),
        dirty_names=("on_actions",),
        folders=("common/on_actions",),
    ),
    Section(
        "decisions", "_load_decisions",
        data=(("_decisions", dict), ("_decision_categories", dict)),
        pending=(
            ("_dirty_decision_categories", set),
            ("_dirty_decision_files", set),
            ("_dirty_decision_category_files", set),
        ),
        dirty_names=("decisions",),
        folders=("common/decisions",),
    ),
    Section(
        "ideas", "_load_ideas",
        data=(
            ("_ideas", dict),
            ("_idea_file_containers", dict),
            ("_cached_idea_file", dict),
        ),
        pending=(("_dirty_ideas", set), ("_dirty_idea_files", set)),
        dirty_names=("ideas",),
        folders=("common/ideas", "common/national_ideas"),
        # Idea files are matched to countries by tag and name.
        depends_on=("countries",),
    ),
    Section(
        "ideologies", "_load_ideologies",
        data=(("_ideologies", dict), ("_vanilla_ideologies", dict)),
        pending=(("_dirty_ideologies", set), ("_dirty_ideology_files", set)),
        dirty_names=("ideologies",),
        folders=("common/ideologies",),
    ),
    Section(
        "dynamic_modifiers", "_load_dynamic_modifiers",
        data=(("_dynamic_modifiers", dict), ("_dynamic_modifier_sources", dict)),
        pending=(
            ("_dirty_dynamic_modifiers", set),
            ("_dirty_dynamic_modifier_files", set),
        ),
        dirty_names=("dynamic_modifiers",),
        folders=("common/dynamic_modifiers",),
    ),
    Section(
        "bookmarks", "_load_bookmarks",
        data=(("_bookmarks", list),),
        pending=(
            ("_dirty_bookmarks", set),
            ("_dirty_bookmark_files", set),
            ("_bookmark_date_defines", _none),
        ),
        dirty_names=("bookmarks", "bookmark_dates"),
        folders=("common/bookmarks",),
    ),
    Section(
        "localisation", "_load_localization",
        data=(
            ("_loc_entries", dict),
            ("_loc_sources", dict),
            # English is loaded with the section; every other language is
            # indexed as a whole (inherited layers plus the mod) on first use.
            ("_foreign_loc_entries", dict),
            ("_foreign_loc_sources", dict),
            ("_loaded_loc_languages", set),
            # The game's own entries per language, read on the first lookup
            # the mod's tables miss (only without base mods; see ``_game_loc``).
            # A mod file of the same name hides a game file, so they belong
            # to this section.
            ("_game_loc_entries", dict),
        ),
        pending=(
            ("_dirty_loc_keys", set),
            ("_dirty_loc_files", set),
            ("_foreign_dirty_loc_keys", dict),
        ),
        dirty_names=("localization",),
        folders=("localisation",),
        caches=(("_loaded_loc_languages", "_loc_tables"), ("_game_loc_entries", "_game_loc")),
    ),
))

# Edit buffers that belong to no section. They are copied by transactions and
# emptied by discard().
_GLOBAL_PENDING: tuple[tuple[str, Callable[[], object]], ...] = (
    ("_pending_asset_writes", dict),
    ("_pending_asset_baselines", dict),
    ("_script_file_updates", dict),
    # Bytes whose encoding, byte-order mark and line endings a staged file
    # copies when it is written (an imported script keeps its source's).
    ("_format_donors", dict),
    ("_dirty", set),
    # Sections changed in memory without a dirty mark (inherited content
    # loaded for editing); discard() unloads them.
    ("_reload_on_discard", set),
)

# Text files whose state on disk is recorded, so a save does not overwrite a
# change made behind the SDK's back.
_BASELINE_SUFFIXES = frozenset({".txt", ".yml", ".gfx", ".lua", ".mod"})


# ``(size, mtime_ns, racy)`` of a file. File times have a coarse resolution:
# a file changed again within the same tick keeps its time, and a same-size
# rewrite would look unchanged. A file whose time is this close to when its
# fingerprint was taken is "racy" (git's word): its fingerprint never proves
# it unchanged, and its content digest is taken instead.
_Fingerprint = tuple[int, int, bool]
_RACY_NS = 2_000_000_000

# One section loader at a time, in the whole process (loaders are plain
# Python, so nothing is lost). A second thread that needs a section which is
# loading waits here and then finds it complete (see ``CoreLayer.__getattr__``
# and ``docs/internals/mod_sections.md``).
_SECTION_LOCK = threading.RLock()


def _same_file_state(recorded: _Fingerprint, current: _Fingerprint | None) -> bool:
    """Whether a fingerprint proves the file unchanged since it was recorded."""

    return current is not None and not recorded[2] and recorded[:2] == current[:2]


def _text_digest(text: str) -> bytes:
    return hashlib.blake2b(text.encode("utf-8", "surrogatepass"), digest_size=16).digest()


def _changed_files(
    recorded: dict[Path, _Fingerprint],
    known_digests: dict[Path, bytes],
    fingerprints: dict[Path, _Fingerprint],
    digests: dict[Path, bytes],
) -> list[Path]:
    """Files created, deleted or changed between a recorded scan and one taken now.

    A file whose fingerprint changed but whose text did not (a ``touch``)
    does not count.
    """

    changed = []
    for path in recorded.keys() | fingerprints.keys():
        before = recorded.get(path)
        current = fingerprints.get(path)
        if before is not None and _same_file_state(before, current):
            continue
        known = known_digests.get(path)
        if before is not None and current is not None and known is not None:
            try:
                now = digests.get(path) or _text_digest(read_text(path))
            except OSError:
                now = None
            if now == known:
                continue
        changed.append(path)
    return sorted(changed)


class CoreLayer:
    """Shared state, content-layer lookup, raw readers and transaction snapshots."""

    _sections: ClassVar[SectionRegistry] = SECTIONS

    # Section data: set when the section loads (see ``SECTIONS``), so these
    # are declared here instead of assigned in ``__init__``.
    _countries: dict[str, Country]
    _country_color_sources: dict[Path, str | None]
    _country_tag_mappings: dict[ContentView, dict[str, str]]
    _created_country_tags: set[str]
    _characters: dict[str, Character]
    _oobs: dict[str, OrderOfBattle]
    _states: dict[int, State]
    _state_ids: list[int]
    _state_source_paths: dict[int, Path]
    _writable_state_directory_stamp: int | None
    _inherited_state_source_paths: dict[int, Path] | None
    _focus_trees: dict[str, FocusTree]
    _events: dict[str, Event]
    _event_namespaces: dict[str, Optional[str]]
    _event_file_namespaces: dict[Path, Optional[str]]
    _on_actions: dict[str, OnAction]
    _on_action_occurrences: dict[str, list[OnAction]]
    _decisions: dict[str, Decision]
    _decision_categories: dict[str, DecisionCategory]
    _ideas: dict[str, Idea]
    _idea_file_containers: dict[Path, str]
    _cached_idea_file: dict[str, Path]
    _ideologies: dict[str, Ideology]
    _vanilla_ideologies: dict[str, Ideology]
    _dynamic_modifiers: dict[str, DynamicModifier]
    _dynamic_modifier_sources: dict[str, Path]
    _bookmarks: list[Bookmark]
    _loc_entries: dict[str, str]
    _loc_sources: dict[str, Path]
    _foreign_loc_entries: dict[str, dict[str, str]]
    _foreign_loc_sources: dict[str, dict[str, Path]]
    _loaded_loc_languages: set[str]
    _game_loc_entries: dict[str, dict[str, str]]

    # Edit buffers: set in ``__init__`` from ``SECTIONS`` and ``_GLOBAL_PENDING``.
    _dirty_countries: set[str]
    _deleted_countries: dict[str, Country]
    _country_name_pool_updates: dict[str, str]
    _dirty_characters: set[str]
    _dirty_character_files: set[Path]
    _dirty_oobs: set[str]
    _dirty_oob_files: set[Path]
    _dirty_states: set[int]
    _deleted_state_ids: set[int]
    _deleted_state_files: dict[Path, str | None]
    _map_file_updates: dict[str, str]
    _dirty_focus_trees: set[str]
    _dirty_focus_files: set[Path]
    _dirty_events: set[str]
    _dirty_event_files: set[Path]
    _dirty_on_actions: set[str]
    _dirty_on_action_files: set[Path]
    _dirty_decision_categories: set[str]
    _dirty_decision_files: set[Path]
    _dirty_decision_category_files: set[Path]
    _dirty_ideas: set[str]
    _dirty_idea_files: set[Path]
    _dirty_ideologies: set[str]
    _dirty_ideology_files: set[Path]
    _dirty_dynamic_modifiers: set[str]
    _dirty_dynamic_modifier_files: set[Path]
    _dirty_bookmarks: set[tuple[Path, int | None]]
    _dirty_bookmark_files: set[Path]
    _bookmark_date_defines: tuple[Path, str, str] | None
    _dirty_loc_keys: set[str]
    _dirty_loc_files: set[Path]
    _foreign_dirty_loc_keys: dict[str, set[str]]
    _pending_asset_writes: dict[Path, bytes]
    _pending_asset_baselines: dict[Path, bytes | None]
    _script_file_updates: dict[Path, str]
    _format_donors: dict[Path, bytes]
    _dirty: set[str]
    _reload_on_discard: set[str]

    def __init__(
        self,
        mod_root: str | Path,
        hoi4_install: str | Path | None = None,
        *,
        strict_loading: bool = False,
        base_mod_paths: Sequence[str | Path] = (),
    ):
        # Section bookkeeping comes first: ``__getattr__`` reads it.
        self._section_loaded: set[str] = set()
        self._section_loading: list[str] = []
        # The values of a section while its loader runs, and the thread that
        # runs it (see ``__getattr__``).
        self._section_staged: dict[str, dict[str, object]] = {}
        self._section_thread: int | None = None
        self._section_diagnostics: dict[str, list[LoadDiagnostic]] = {}
        # Files each section's loader put in ``_original_files``.
        self._section_files: dict[str, set[Path]] = {}
        # The files each section's loader was the first to read (``_note_read``).
        self._section_reads: dict[str, set[Path]] = {}
        self.mod_root = Path(mod_root).resolve()
        self.hoi4_install = Path(hoi4_install).resolve() if hoi4_install else None
        if isinstance(base_mod_paths, (str, bytes)):
            raise ValueError("base_mod_paths must be an ordered sequence of paths")
        self.base_mod_paths = tuple(Path(path).resolve() for path in base_mod_paths)
        self._content_layers: ContentLayers | None = None
        self._refresh_content_layers()
        self.strict_loading = strict_loading
        self._reset_pending_edits()
        self._original_files: dict[Path, str] = {}
        # ``(size, mtime_ns)`` of every text file of the mod when it was last
        # known to match what the SDK holds, and a digest of the text the SDK
        # read from a file while it still had that fingerprint.
        self._fingerprints: dict[Path, _Fingerprint] = {}
        self._read_digests: dict[Path, bytes] = {}
        # Digests the scan itself took of files that were fresh then (their
        # time proves nothing). Not a noted read: see ``_note_read``.
        self._scan_digests: dict[Path, bytes] = {}
        # Files a refused save or preview found changed by comparing their
        # text. ``stat`` may not show such a change (same size, time set
        # back), so the next discard() is told about them.
        self._seen_changed: set[Path] = set()
        # The same for the base mods' text files, so discard() can tell
        # whether the inherited content changed (the game is not watched).
        self._base_fingerprints: dict[Path, _Fingerprint] = {}
        self._base_digests: dict[Path, bytes] = {}
        self._vanilla_tags: set[str] = (
            load_vanilla_tags(self._base_content) if self._base_content is not None else set()
        )
        self._scan_cache: dict[str, set[str]] = {}
        # The references of each of the mod's own script files, with the text
        # they were read from; a changed text is read again.
        # ``path -> (text, character ids, references)`` of the mod's own files.
        self._own_reference_cache: dict[Path, tuple[str, frozenset[str], FileReferences]] = {}
        self._character_scope_names_seen: frozenset[str] = frozenset()
        # Data built once per validate() call and shared by its checks; None
        # outside a run, because edits between runs change it.
        self._validation_run: dict[str, object] | None = None
        self._script_vocabulary_cache: GameScriptVocabulary | None = None
        self._equipment_unlock_cache: dict[str, tuple[str, ...]] | None = None
        self._sprite_texture_cache: dict[str, str] | None = None
        self._sprite_text_parse_cache: dict[str, dict[str, str]] = {}
        self._runtime_activation_parse_cache: dict[str, PdxNode | None] = {}
        self._script_trigger_index_cache: dict[str, dict[str, PdxNode]] = {}
        self._state_index_cache: dict[bool, list[dict]] = {}
        self._vanilla_state_loc_entries: dict[str, str] | None = None
        self._country_context_cache: dict[str, dict] = {}

    # ── Sections ─────────────────────────────────────────────────

    if not TYPE_CHECKING:
        # Hidden from mypy, which would otherwise accept any attribute name
        # and stop checking which layer a method belongs to.
        def __getattr__(self, name: str) -> object:
            """Load a section on the first read of one of its attributes.

            Python calls this only when normal lookup fails, so loaded
            attributes cost nothing. Names with two leading underscores and
            objects still being built (``copy``, ``pickle``) fail as usual.

            While a section loads, its attributes are not on the object yet:
            the loader's own thread is handed the half-filled values from
            here, and any other thread waits until the section is complete.
            """

            if not name.startswith("__"):
                state = self.__dict__
                loaded = state.get("_section_loaded")
                section = type(self)._sections.by_attribute.get(name)
                if loaded is not None and section is not None:
                    staged = state["_section_staged"].get(section.name)
                    if staged is not None and state["_section_thread"] == threading.get_ident():
                        return staged[name]
                    if staged is not None or section.name not in loaded:
                        self._load_section(section.name)
                        return state[name]
            raise AttributeError(f"{type(self).__name__!r} object has no attribute {name!r}")

    def _load_section(self, name: str) -> None:
        """Run one section's loader, once. Its data is empty until then.

        One loader runs at a time (``_SECTION_LOCK``). A second thread that
        needs the section waits here and finds it loaded.
        """

        with _SECTION_LOCK:
            outermost = not self._section_loading
            if outermost:
                self._section_thread = threading.get_ident()
            try:
                self._load_section_locked(name)
            finally:
                if outermost:
                    self._section_thread = None

    def _load_section_locked(self, name: str) -> None:
        if name in self._section_loaded:
            return
        if name in self._section_loading:
            chain = " -> ".join((*self._section_loading, name))
            raise RuntimeError(f"Loading the {name} section needs the section itself: {chain}")
        section = self._sections.by_name[name]
        # The loader fills these through ``__getattr__``. They become
        # attributes of the object only when the section is complete, so no
        # other thread ever reads a half-filled section.
        staged = {attribute: factory() for attribute, factory in section.data}
        self._section_staged[name] = staged
        self._section_diagnostics[name] = []
        held_before = set(self._original_files)
        noted_before = set(self._read_digests)
        loaded_before = set(self._section_loaded)

        def own(now: Iterable[Path], earlier: set[Path], of_sections: dict[str, set[Path]]) -> set[Path]:
            # A section this loader needed (countries for the OOBs) loaded
            # inside it and keeps what is its own.
            nested: set[Path] = set()
            for other in self._section_loaded - loaded_before:
                nested |= of_sections.get(other, set())
            return set(now) - earlier - nested

        self._section_loading.append(name)
        try:
            getattr(self, section.loader)()
        except BaseException as error:
            self._section_loading.pop()
            del self._section_staged[name]
            for path in own(self._original_files, held_before, self._section_files):
                del self._original_files[path]
            # Nothing is held of what this attempt read, so the next attempt
            # reads the files as they are then (after a repair, say).
            for path in own(self._read_digests, noted_before, self._section_reads):
                del self._read_digests[path]
            self._forget_sections({name})
            if isinstance(error, AttributeError):
                # Raised from ``__getattr__``, it would read as a missing name.
                raise RuntimeError(f"Loading the {name} section failed: {error}") from error
            raise
        self._section_loading.pop()
        self._section_files[name] = own(self._original_files, held_before, self._section_files)
        self._section_reads[name] = own(self._read_digests, noted_before, self._section_reads)
        for attribute, value in staged.items():
            self.__dict__.setdefault(attribute, value)
        del self._section_staged[name]
        self._section_loaded.add(name)

    def __copy__(self) -> Self:
        """A shallow copy that keeps its own account of what is loaded.

        The copy shares the content that is loaded now, as a shallow copy
        does. What loads later loads for one of the two only, so neither
        believes it holds a section that the other one loaded.
        """

        clone = object.__new__(type(self))
        clone.__dict__.update(self.__dict__)
        clone._section_loaded = set(self._section_loaded)
        clone._section_loading = []
        clone._section_staged = {}
        clone._section_thread = None
        clone._section_diagnostics = dict(self._section_diagnostics)
        clone._section_files = {name: set(paths) for name, paths in self._section_files.items()}
        clone._section_reads = {name: set(paths) for name, paths in self._section_reads.items()}
        return clone

    def _load_all_sections(self) -> None:
        for section in self._sections.sections:
            self._load_section(section.name)

    def _stage(self, attribute: str, value: object) -> None:
        """Set a plain value of a section from inside its loader.

        Assigned as an attribute it would be on the object at once, and
        another thread could read it before the section is complete.
        """

        section = self._sections.by_attribute[attribute]
        self._section_staged[section.name][attribute] = value

    def _forget_sections(self, names: Collection[str]) -> None:
        """Drop the data of sections; the next read loads them from disk again."""

        for name in names:
            for attribute, _ in self._sections.by_name[name].data:
                self.__dict__.pop(attribute, None)
            self._section_loaded.discard(name)
            self._section_diagnostics.pop(name, None)
            self._section_files.pop(name, None)
            self._section_reads.pop(name, None)

    def _unload_sections(self, names: Collection[str], dropped_files: Collection[Path] = ()) -> None:
        """Forget sections and the original texts nothing loaded still owns.

        ``dropped_files`` are removed from ``_original_files`` in any case
        (files that were written or staged).
        """

        self._forget_sections(names)
        dropped = set(dropped_files)
        recorded: dict[Path, set[str]] = {}
        for section_name, paths in self._section_files.items():
            for path in paths:
                recorded.setdefault(path, set()).add(section_name)
        loaded = self._section_loaded
        for path in list(self._original_files):
            if path in dropped:
                del self._original_files[path]
                continue
            if recorded.get(path, set()) & loaded:
                continue
            owner = self._section_owning(path)
            if owner is None or owner not in loaded:
                del self._original_files[path]

    def _relative_content_path(self, path: Path) -> Path | None:
        """``path`` relative to the mod or to the game or base mod it is in."""

        roots = [self.mod_root]
        if self.hoi4_install is not None:
            roots.append(self.hoi4_install)
        roots.extend(self.base_mod_paths)
        for root in roots:
            if path.is_relative_to(root):
                return path.relative_to(root)
        return None

    def _section_owning(self, path: Path) -> str | None:
        relative = self._relative_content_path(path)
        return None if relative is None else self._sections.owner(relative)

    def _sections_reading(self, path: Path) -> set[str] | None:
        """Sections whose loaded data may come from the mod file ``path``.

        ``None`` means the path cannot be mapped: it is outside the mod, or it
        is the descriptor, whose ``replace_path`` lines change what every
        section sees.
        """

        resolved = path.resolve(strict=False)
        if not resolved.is_relative_to(self.mod_root):
            return None
        relative = resolved.relative_to(self.mod_root)
        if relative == Path("descriptor.mod"):
            return None
        names = self._sections.readers(relative)
        names.update(
            name for name, paths in self._section_files.items() if path in paths or resolved in paths
        )
        return names

    def _reset_pending_edits(self) -> None:
        for section in self._sections.sections:
            for attribute, factory in section.pending:
                setattr(self, attribute, factory())
        for attribute, factory in _GLOBAL_PENDING:
            setattr(self, attribute, factory())

    @property
    def _load_diagnostics(self) -> list[LoadDiagnostic]:
        """Every section's load diagnostics, in load order; loads what is not loaded."""

        self._load_all_sections()
        return self._loaded_diagnostics()

    def _loaded_diagnostics(self) -> list[LoadDiagnostic]:
        """Load diagnostics of the sections loaded so far, in load order."""

        order = self._sections.order
        return [
            diagnostic
            for name in sorted(self._section_diagnostics, key=lambda key: order.get(key, len(order)))
            for diagnostic in self._section_diagnostics[name]
        ]

    def _add_load_diagnostic(self, diagnostic: LoadDiagnostic) -> None:
        """File a diagnostic under the section being loaded."""

        name = self._section_loading[-1] if self._section_loading else ""
        self._section_diagnostics.setdefault(name, []).append(diagnostic)

    def _load_sections_for_paths(self, paths: Iterable[Path]) -> None:
        """Load the sections that own the folders of ``paths``.

        Their load diagnostics tell whether a file next to one of the paths
        failed to load.
        """

        for path in paths:
            for folder in (path, path.parent):
                owner = self._section_owning(folder.resolve(strict=False))
                if owner is not None:
                    self._load_section(owner)

    # ── Files on disk ────────────────────────────────────────────

    def _capture_source_baseline(self) -> None:
        """Take the current state of every text file of the mod as the baseline."""

        self._fingerprints, self._scan_digests = self._scan_fingerprints()
        self._read_digests = {}
        self._seen_changed = set()
        self._base_fingerprints, self._base_digests = self._scan_fingerprints(self.base_mod_paths)

    def _base_mods_changed(self) -> bool:
        """Whether a base mod's text file changed, appeared or went away."""

        if not self.base_mod_paths:
            return False
        fingerprints, digests = self._scan_fingerprints(self.base_mod_paths)
        return bool(
            _changed_files(self._base_fingerprints, self._base_digests, fingerprints, digests)
        )

    def _scan_fingerprints(
        self, roots: Sequence[Path] | None = None
    ) -> tuple[dict[Path, _Fingerprint], dict[Path, bytes]]:
        """Fingerprints of the text files below ``roots`` (the mod), and digests of the racy ones.

        Only files changed in the last moments are read (see ``_RACY_NS``);
        the rest is known from ``stat`` alone.
        """

        start = time.time_ns()
        fingerprints: dict[Path, _Fingerprint] = {}
        digests: dict[Path, bytes] = {}
        folders = (self.mod_root,) if roots is None else roots
        stack = [str(root) for root in folders if root.is_dir()]
        while stack:
            try:
                entries = list(os.scandir(stack.pop()))
            except OSError:
                continue
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                        continue
                    if not entry.is_file() or os.path.splitext(entry.name)[1].lower() not in _BASELINE_SUFFIXES:
                        continue
                    stat = entry.stat()
                    path = Path(entry.path)
                    if entry.is_symlink():
                        path = path.resolve(strict=False)
                    racy = stat.st_mtime_ns >= start - _RACY_NS
                    fingerprints[path] = (stat.st_size, stat.st_mtime_ns, racy)
                    if racy:
                        digests[path] = _text_digest(read_text(path))
                except OSError:
                    continue
        return fingerprints, digests

    @staticmethod
    def _fingerprint(path: Path) -> _Fingerprint | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return (stat.st_size, stat.st_mtime_ns, stat.st_mtime_ns >= time.time_ns() - _RACY_NS)

    def _note_read(self, path: Path, text: str) -> None:
        """Remember what the SDK read from a mod file: its baseline from then on.

        Only the FIRST read since the baseline was last taken counts (``Mod()``,
        ``save()`` of that file, ``discard()``, ``reload()``). It takes the
        file as it is then: a file that was touched, changed or created after
        ``Mod()`` and before anything read it is simply read, as a fresh
        ``Mod`` would read it.

        A later read never moves the baseline. What is in memory, or staged
        for a save, was built from the first text, so a change behind the
        SDK's back has to keep looking like one until ``save()`` refuses it.
        """

        key = path if path in self._fingerprints else path.resolve(strict=False)
        if key in self._read_digests:
            return
        if key not in self._fingerprints and not key.is_relative_to(self.mod_root):
            return
        current = self._fingerprint(key)
        if current is None:
            return
        self._fingerprints[key] = current
        self._read_digests[key] = _text_digest(text)
        self._scan_digests.pop(key, None)

    def _output_path(self, relative: str | Path) -> Path:
        """The mod file an edit is going to write, noted as it is now.

        An edit may name a file that nothing has read: one in a subfolder
        no loader looks into, or a leftover no tag file registers. What is
        there is replaced by the save, so it is noted when the edit is made
        and a change of it before ``save()`` is refused.
        """

        target = resolve_mod_output_path(self.mod_root, relative)
        self._note_target(target)
        return target

    def _note_target(self, path: Path) -> None:
        """Note the text of a mod file that an edit is about to replace or patch.

        Called when the edit is made, not when it is saved: a change of the
        file between the two is then refused by ``save()``. A file that does
        not exist (yet) has nothing to note.
        """

        key = path if path in self._fingerprints else path.resolve(strict=False)
        if key in self._read_digests or not key.is_relative_to(self.mod_root):
            return
        try:
            text = read_text(key)
        except (OSError, ValueError):
            return
        self._note_read(key, text)

    def _read_source(self, path: Path) -> str:
        """Read a mod file for loading, remembering what was read."""

        text = read_text(path)
        self._note_read(path, text)
        return text

    def _note_written(self, paths: Iterable[Path]) -> None:
        """Take files the SDK has just written or deleted into the baseline."""

        for path in paths:
            key = path.resolve(strict=False)
            current = self._fingerprint(key)
            if current is None or key.suffix.lower() not in _BASELINE_SUFFIXES:
                self._fingerprints.pop(key, None)
                self._read_digests.pop(key, None)
                continue
            self._fingerprints[key] = current
            self._read_digests[key] = _text_digest(read_text(key))
            self._scan_digests.pop(key, None)

    def _changed_behind_our_back(self, path: Path) -> bool:
        """Whether a mod file that is about to be overwritten changed since its baseline.

        The text itself is compared with the digest of what the SDK read, so a
        ``touch`` that keeps the text is no change, and a rewrite of the same
        size whose time was set back is one: size and time cannot prove a
        file unchanged, and this is the last check before its content is
        replaced. Every reader and every edit notes what it found (with
        ``HOI4_VERIFY_RELOAD=1`` a file without a noted read is an error).
        Should one be missing, the check uses what else is known: the digest
        the scan took of a fresh file, a text a section holds, or size and
        time alone. A file without a baseline counts as changed if it exists.
        """

        key = path.resolve(strict=False)
        recorded = self._fingerprints.get(key)
        current = self._fingerprint(key)
        if recorded is None:
            return key.exists()
        if current is None or not key.is_file():
            return True
        digest = self._read_digests.get(key)
        if digest is None:
            if os.environ.get("HOI4_VERIFY_RELOAD") == "1":
                # The test suite runs with this switch. Every loader that
                # reads a file, and every edit that stages a rewrite of one,
                # has to note what was there (``_note_read``, ``_note_target``).
                raise AssertionError(
                    f"{key} is about to be overwritten, but nothing noted what was read "
                    "from it: a touch would be refused and a change missed"
                )
            # Nothing noted a read. Fall back to what is known: the text at
            # the last scan (a file that was fresh then), a text a section
            # holds, or size and time alone.
            digest = self._scan_digests.get(key)
            held = self._original_files.get(key)
            if digest is None and held is not None:
                digest = _text_digest(held)
            if digest is None:
                return not _same_file_state(recorded, current)
        try:
            return digest != _text_digest(read_text(key))
        except (OSError, ValueError):
            # Unreadable now: not the text the SDK read.
            return True

    def _refresh_content_layers(self) -> None:
        """Build the view of what the mod inherits (rereads the descriptors)."""

        self._content_layers = (
            ContentLayers(self.hoi4_install, self.base_mod_paths, self.mod_root)
            if self.base_mod_paths else None
        )
        # The game and base mods as the mod inherits them. Without base mods
        # this is the game folder as it is, files the mod overrides included.
        self._base_content: ContentView | None = (
            self._content_layers if self._content_layers is not None
            else FolderView(self.hoi4_install) if self.hoi4_install is not None
            else None
        )

    def content_source(self, relative_path: str | Path) -> Path | None:
        """The winning file of the game, the base mods or the mod, where it is on disk."""
        relative = relative_content_path(relative_path)
        if self._content_layers is not None:
            return self._content_layers.source(relative)
        hidden_paths = replace_paths(self.mod_root) if self.hoi4_install is not None else ()
        writable = self.mod_root / relative
        if writable.is_file():
            return writable
        if self.hoi4_install is not None and not any(
            relative.is_relative_to(hidden) for hidden in hidden_paths
        ):
            vanilla = self.hoi4_install / relative
            if vanilla.is_file():
                return vanilla
        return None

    def content_files(self, directory: str | Path) -> dict[str, Path]:
        """Map effective relative filenames to original winning source paths."""
        relative = relative_content_path(directory)
        if self._content_layers is not None:
            files = {
                str(path): source for path, source in self._content_layers.rglob(relative).items()
            }
        else:
            files = {}
            if self.hoi4_install is not None:
                hidden_paths = replace_paths(self.mod_root)
                if not any(relative.is_relative_to(hidden) for hidden in hidden_paths):
                    # ``is_relative_to`` as a text test: the replace paths are
                    # normalised relative paths, never empty.
                    hidden_names = {hidden.as_posix() for hidden in hidden_paths}
                    hidden_prefixes = tuple(name + "/" for name in hidden_names)
                    for path in (self.hoi4_install / relative).rglob("*"):
                        if path.is_file():
                            path_relative = path.relative_to(self.hoi4_install)
                            name = path_relative.as_posix()
                            if name not in hidden_names and not name.startswith(hidden_prefixes):
                                files[str(path_relative)] = path
        for path in (self.mod_root / relative).rglob("*"):
            if path.is_file():
                files[str(path.relative_to(self.mod_root))] = path
        return dict(sorted(files.items()))

    def load_inherited_content(self, relative_path: str | Path) -> None:
        """Load one effective inherited script file for facade editing.

        Focus, event, idea and on-action files are opt-in, like vanilla country
        reads. All siblings in the file are retained. Reading alone writes
        nothing; edits serialize a complete override into this mod on save.
        Duplicate focus IDs within an inherited tree reject the entire import
        before any trees are installed, regardless of ``strict_loading``.
        """
        relative = relative_content_path(relative_path)
        source = self.content_source(relative)
        if source is None:
            raise FileNotFoundError(relative)
        if source.is_relative_to(self.mod_root):
            return
        target = self._output_path(relative)
        if target in self._original_files:
            return
        original = read_text(source)

        def install(models: Sequence[_InheritedModel], destination: dict[str, _InheritedModel]) -> None:
            seen: set[str] = set()
            for model in models:
                if model.id in destination or model.id in seen:
                    raise ValueError(f"Inherited ID {model.id!r} conflicts with another definition")
                seen.add(model.id)
            for model in models:
                model.path = target
                destination[model.id] = model

        directory = relative.parent.as_posix()
        if directory == "common/national_focus":
            trees = load_focus_trees(source)
            for tree in trees:
                seen_focus_ids: set[str] = set()
                for focus in tree.focuses:
                    if focus.id in seen_focus_ids:
                        raise ValueError(
                            f"Duplicate focus ID {focus.id!r} in inherited tree {tree.id!r} "
                            f"from {relative}"
                        )
                    seen_focus_ids.add(focus.id)
            install(trees, self._focus_trees)
        elif directory == "events":
            namespace, events = load_events_file(source)
            install(events, self._events)
            for event in events:
                self._event_namespaces[event.id] = namespace
            self._event_file_namespaces[target] = namespace
        elif directory in ("common/ideas", "common/national_ideas"):
            ideas, container = read_ideas_file(source)
            install(ideas, self._ideas)
            self._idea_file_containers[target] = container
        elif directory == "common/on_actions":
            actions = load_on_actions_file(source)
            for action in actions:
                action.path = target
                self._on_action_occurrences.setdefault(action.id, []).append(action)
                self._on_actions.setdefault(action.id, action)
        else:
            raise ValueError("Inherited loading supports focus, event, idea and on-action files")
        self._original_files[target] = original
        # Not a saved edit, but not on disk either: discard() drops it.
        section = self._section_owning(target)
        if section is not None:
            self._reload_on_discard.add(section)

    def _is_shadowed_by_mod(self, base: ContentView, path: Path) -> bool:
        relative = base.relative(path)
        return relative is not None and self._mod_hides_inherited(relative)

    def _run_cached(self, key: str, build: Callable[[], _Built]) -> _Built:
        """``build()``, once per validate() run; every call outside a run builds.

        The value is shared by the checks of one run: never change it.
        """

        run = self._validation_run
        if run is None:
            return build()
        if key not in run:
            run[key] = build()
        return run[key]  # type: ignore[return-value]

    def _mod_hides_inherited(self, relative: Path) -> bool:
        """Whether the mod hides an inherited file (same name or replace_path).

        The layered view of base mods already leaves hidden files out; this
        covers a direct ``hoi4_install`` base.
        """

        if self._content_layers is not None:
            return False
        if (self.mod_root / relative).is_file():
            return True
        hidden = self._scan_cache.get("mod_replace_paths")
        if hidden is None:
            try:
                hidden = {path.as_posix() for path in replace_paths(self.mod_root)}
            except (OSError, ValueError):
                hidden = set()
            self._scan_cache["mod_replace_paths"] = hidden
        return any(relative.is_relative_to(Path(path)) for path in hidden)

    def _read_current_text(self, path: Path) -> str:
        """The text of a file as it is now, for rendering and comparing.

        It notes nothing: a read made while a save is being rendered must
        not become the baseline the save is then checked against.
        """

        if not path.exists():
            return ""
        return read_text(path)

    def _read_noted_text(self, path: Path) -> str:
        """The text of a mod file that an edit builds on (``""`` when missing), noted."""

        if not path.exists():
            return ""
        text = read_text(path)
        self._note_read(path, text)
        return text

    def _overrides_inherited(self, path: Path | None) -> bool:
        """Return whether a mod path shadows a file from the game or a base mod."""

        if path is None or self._base_content is None:
            return False
        try:
            relative = path.resolve(strict=False).relative_to(self.mod_root.resolve(strict=False))
        except ValueError:
            return False
        return self._base_content.file(relative) is not None

    def _shadows_inherited(self, path: Path) -> bool:
        """Whether a NEW mod file at ``path`` would replace a game or base-mod file.

        The game loads a mod file instead of the inherited file of the same
        name, so a generated file must not pick such a name by accident.
        """

        if path.exists():
            return False
        try:
            source = self.content_source(path.relative_to(self.mod_root))
        except ValueError:
            return False
        return source is not None and source != path

    @staticmethod
    def _read_well_formed(path: Path) -> tuple[str, Document]:
        """Read and parse a script file of the mod's own folder, refusing a malformed one.

        Files inherited from the game or a base mod are read the way the game
        reads them. A file the mod owns is the author's to fix, so a missing or
        stray brace there is a load failure instead of a guess. The text and
        its tree are handed to the loader, so the file is read and parsed once.
        """

        text = read_text(path)
        return text, require_well_formed(text)

    def _keep_source(self, path: Path, text: str) -> str:
        """Remember the text a loader read from a mod file, like ``_read_source``."""

        self._note_read(path, text)
        return text

    def _snapshot(self) -> dict[str, object]:
        """Copy the in-memory state a transaction may change.

        Only loaded sections are copied: a section that is not loaded has
        nothing in memory to lose, and rollback simply unloads it again.
        """

        keys = [attribute for attribute, _ in _GLOBAL_PENDING]
        keys.extend(("_sprite_texture_cache", "_country_context_cache"))
        for section in self._sections.sections:
            keys.extend(attribute for attribute, _ in section.pending)
            if section.name in self._section_loaded:
                keys.extend(attribute for attribute, _ in section.data)
        snapshot = copy.deepcopy({key: self.__dict__[key] for key in keys})
        # Texts, paths and frozen diagnostics are immutable: shallow copies do.
        snapshot["_original_files"] = dict(self._original_files)
        snapshot["_section_loaded"] = set(self._section_loaded)
        snapshot["_section_diagnostics"] = {
            name: list(diagnostics) for name, diagnostics in self._section_diagnostics.items()
        }
        snapshot["_section_files"] = {
            name: set(paths) for name, paths in self._section_files.items()
        }
        return snapshot

    def _restore(self, snapshot: dict[str, object]) -> None:
        loaded = snapshot["_section_loaded"]
        assert isinstance(loaded, set)
        # Sections first loaded inside the transaction go back to unloaded.
        self._forget_sections(self._section_loaded - loaded)
        for key, value in snapshot.items():
            setattr(self, key, value)
        # Derived catalogs may reflect imports or localization rolled back above.
        self._state_index_cache.clear()
        self._scan_cache.clear()
        self._equipment_unlock_cache = None

    def _display_path(self, path: Path) -> str:
        return (
            str(path.relative_to(self.mod_root))
            if path.is_relative_to(self.mod_root)
            else str(path)
        )

    @staticmethod
    def _line_column(text: str, offset: int) -> tuple[int, int]:
        offset = max(0, min(offset, len(text)))
        line = text.count("\n", 0, offset) + 1
        previous_newline = text.rfind("\n", 0, offset)
        return line, offset - previous_newline

    def _data_views(self) -> list[ContentView]:
        """The mod folder, then what it inherits (if anything)."""

        views: list[ContentView] = [FolderView(self.mod_root)]
        if self._base_content is not None:
            views.append(self._base_content)
        return views

    def _cached_result(
        self,
        purpose: str,
        path: Path,
        compute: Callable[[str], _Built],
        disk: DiskAnalysis[_Built],
        *,
        read: Callable[[Path], str] = read_text,
    ) -> _Built:
        """``compute(text of path)``, kept until the file changes.

        For a file the mod inherits (the game or a base mod), the result is
        also kept on disk for later processes. A file of the mod itself is
        kept in memory only.
        """

        if self._in_mod_root(path):
            return cached_file_result(purpose, path, compute, read=read)
        return cached_file_result(purpose, path, compute, read=read, disk=disk)

    def _in_mod_root(self, path: Path) -> bool:
        """``path.is_relative_to(self.mod_root)``, by text.

        ``Path.is_relative_to`` builds and searches every parent of the path,
        which shows when it runs for each of the game's thousands of files.
        """

        return _path_under(path, self.mod_root)

    def _localisation_file_entries(self, path: Path) -> dict[str, str]:
        """The entries of one ``.yml`` file; the game's are kept on disk."""

        return self._cached_result(
            "localisation_entries",
            path,
            _localization_entries,
            LOCALISATION_ON_DISK,
            read=_read_localization,
        )

    def _state_index_header(self, path: Path) -> tuple[int, str, str | None] | None:
        """``(id, name, owner)`` of one state file; the game's are kept on disk."""

        return self._cached_result("state_index_header", path, state_index_header, _STATE_HEADERS_ON_DISK)
