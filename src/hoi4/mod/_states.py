"""State lookup, search and history edits."""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Unpack

from ..fields import StateFields
from ..localisation import (
    parse_localization_files,
)
from ..paths import (
    require_country_tag,
)
from ..states import (
    build_state_index,
    read_state,
)
from ..syntax import parse, repair
from ..textio import read_text
from ..types import (
    State,
)
from ..errors import NotFoundError, already_exists, not_found
from ._countries import CountriesLayer
from ._shared import MISSING, _D, _Missing
from ._shared import (
    _check_fields,
    _set_fields,
)


def _fold_search_text(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value).strip().casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


class StatesLayer(CountriesLayer):
    """State lookup, search and history edits."""

    # ── States ──────────────────────────────────────────────────

    def list_states(self, *, include_vanilla: bool = False) -> list[int]:
        """Return the mod's state IDs, or all effective IDs when requested."""
        ids = set(self._state_ids)
        ids.update(self._dirty_states)
        if include_vanilla and self._base_content is not None:
            ids.update(self._inherited_state_paths())
        return sorted(ids - set(self._deleted_state_ids))

    def state_index(self, *, include_vanilla: bool = True) -> list[dict[str, Any]]:
        """Return one summary per state file, sorted by id, without loading whole states.

        Each entry has ``id``, ``name`` (the localisation key), ``display_name``
        (the localised name, else the name from the file name), ``file_name``,
        ``owner`` (``None`` when unset), ``path``, ``source`` (``"mod"`` or
        ``"vanilla"``) and ``localized`` (whether the name has localisation).
        With base mods there are also ``source_path`` and ``source_layer``
        (``"mod"``, ``"base_mod"`` or ``"vanilla"``). A mod file wins over an
        inherited one with the same id. The entries are copies.

        Args:
            include_vanilla: Also list the states of the game and the base mods.
        """
        cached = self._state_index_cache.get(include_vanilla)
        if cached is not None:
            return [
                dict(entry) for entry in cached
                if entry["id"] not in self._deleted_state_ids
            ]
        state_localization = self._state_localization_entries()
        entries: list[dict[str, Any]] = []
        for source, base in zip(("mod", "vanilla"), self._data_views(), strict=False):
            if source == "vanilla" and not include_vanilla:
                continue
            for entry in build_state_index(
                base.glob("history/states", "*.txt").values(), header=self._state_index_header
            ):
                if source == "vanilla" and self._is_shadowed_by_mod(base, Path(entry["path"])):
                    continue
                item = dict(entry)
                item["source"] = source
                if self._content_layers is not None:
                    relative = base.relative(Path(item["path"]))
                    assert relative is not None
                    original = self.resolve_content_path(relative)
                    item["source_path"] = str(original) if original else item["path"]
                    item["source_layer"] = (
                        "mod" if original and original.is_relative_to(self.mod_root)
                        else "base_mod" if original and any(original.is_relative_to(root) for root in self.base_mod_paths)
                        else "vanilla"
                    )
                loc_key = str(item.get("name", ""))
                localized_name = state_localization.get(loc_key)
                item["localized"] = localized_name is not None
                if localized_name is not None:
                    item["display_name"] = localized_name
                entries.append(item)
        by_id: dict[int, dict[str, Any]] = {}
        for entry in entries:
            existing = by_id.get(entry["id"])
            if existing is None or (
                existing["source"] == "vanilla" and entry["source"] == "mod"
            ):
                by_id[entry["id"]] = entry
        result = sorted(by_id.values(), key=lambda item: item["id"])
        self._state_index_cache[include_vanilla] = [dict(entry) for entry in result]
        return [entry for entry in result if entry["id"] not in self._deleted_state_ids]

    def get_state_name_map(self, *, include_vanilla: bool = True) -> dict[int, str]:
        """Return a readable name for every state id, from ``state_index()``.

        The name is the ``display_name``, else the localisation key, else the id.
        """
        return {
            entry["id"]: entry.get("display_name") or entry.get("name") or str(entry["id"])
            for entry in self.state_index(include_vanilla=include_vanilla)
        }

    def find_state(
        self, query: str, *, include_vanilla: bool = True, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Find states whose id, name, localised name or file name match ``query``.

        Matching ignores case and accents and allows near misses. The result
        holds up to ``limit`` ``state_index()`` entries, best match first,
        each with a ``matched`` key naming the field that matched. An empty
        query finds nothing.
        """
        needle = _fold_search_text(query)
        if not needle:
            return []
        scored: list[tuple[float, dict[str, Any], str]] = []
        for entry in self.state_index(include_vanilla=include_vanilla):
            haystacks = [
                ("id", str(entry.get("id", "")), 1.0),
                ("name", str(entry.get("name", "")), 1.0),
                ("display_name", str(entry.get("display_name", "")), 1.0),
            ]
            best = 0.0
            matched = ""
            for field_name, haystack, weight in haystacks:
                text = _fold_search_text(haystack)
                if text == needle:
                    score = 1.0 * weight
                elif needle in text:
                    score = 0.85 * weight
                else:
                    score = SequenceMatcher(None, needle, text).ratio() * weight
                if score > best:
                    best = score
                    matched = field_name
            file_name = str(entry.get("file_name", ""))
            if file_name:
                file_weight = 0.7 if entry.get("localized") else 1.0
                text = _fold_search_text(file_name)
                if text == needle:
                    score = 1.0 * file_weight
                elif needle in text:
                    score = 0.85 * file_weight
                else:
                    score = SequenceMatcher(None, needle, text).ratio() * file_weight
                if score > best:
                    best = score
                    matched = "file_name"
            if best >= 0.45:
                scored.append((best, entry, matched))
        scored.sort(key=lambda item: (-item[0], item[1]["id"]))
        results: list[dict[str, Any]] = []
        for _, entry, matched in scored[:limit]:
            result = dict(entry)
            result["matched"] = matched
            results.append(result)
        return results

    def _state_localization_entries(self) -> dict[str, str]:
        if self._vanilla_state_loc_entries is None:
            vanilla_entries: dict[str, str] = {}
            if self._base_content is not None:
                loaded, _ = parse_localization_files(
                    self._base_content.rglob("localisation/english", "*.yml").values(),
                    file_entries=self._localisation_file_entries,
                )
                vanilla_entries = {
                    key: value for key, value in loaded.items() if key.startswith("STATE_")
                }
            self._vanilla_state_loc_entries = vanilla_entries
        entries = dict(self._vanilla_state_loc_entries)
        entries.update(
            {
                key: value
                for key, value in self._loc_entries.items()
                if key.startswith("STATE_")
            }
        )
        return entries

    def get_state(self, state_id: int, *, include_vanilla: bool = True, default: _D | _Missing = MISSING) -> State | _D:
        """Load a state without changing the mod directory.

        Vanilla fallback states remain backed by their vanilla source until a
        mutating state API explicitly materializes an override under the mod.

        Args:
            default: Returned instead of raising ``NotFoundError`` when the
                state does not exist or is queued for deletion.

        Raises:
            NotFoundError: The state does not exist and no default was given.
        """
        if state_id is None:
            raise TypeError("State ID cannot be None")
        if state_id in self._states:
            return self._states[state_id]
        if state_id in self._deleted_state_ids:
            if default is not MISSING:
                return default
            raise NotFoundError(
                f"State {state_id} is queued for deletion.", kind="state", id=state_id
            )
        # Scan inherited content once, never once per missing filename prefix.
        # Writable directory additions retain existing live-discovery behavior;
        # directory timestamps avoid scanning unchanged files for every state.
        states_dir = self.mod_root / "history" / "states"
        stamp = states_dir.stat().st_mtime_ns if states_dir.is_dir() else None
        if stamp != self._writable_state_directory_stamp:
            paths = {entry["id"]: Path(entry["path"]) for entry in build_state_index(states_dir)}
            self._state_source_paths = {
                sid: path for sid, path in self._state_source_paths.items()
                if not path.is_relative_to(states_dir)
            }
            self._state_source_paths.update(paths)
            self._writable_state_directory_stamp = stamp
            # The folder changed after the section loaded. What this lookup
            # finds there is not what the loader knows (its ids, its
            # diagnostics): discard() and save() load the section again.
            self._reload_on_discard.add("states")
        f = self._state_source_paths.get(state_id)
        if f is not None and not f.exists():
            f = None
        if f is None and include_vanilla and self._base_content is not None:
            f = self._inherited_state_paths().get(state_id)
        if f:
            if not f.is_relative_to(self.mod_root):
                state = self._inherited_copy("state", state_id, lambda: read_state(f))
                assert state is not None
                return state
            state = read_state(f)
            self._states[state_id] = state
            self._state_source_paths.setdefault(state_id, f)
            if not f.is_relative_to(self.mod_root):
                # A state of the game or a base mod, cached by this lookup:
                # save() and discard() drop it with the section, as a full
                # reload used to, so list_states() names the mod's again.
                self._reload_on_discard.add("states")
            text = read_text(f)
            self._original_files.setdefault(f, text)
            self._note_read(f, text)
            return state
        if default is not MISSING:
            return default
        raise not_found("state", state_id, self._known_state_ids())

    def _known_state_ids(self) -> set[int]:
        """Every state id the mod or inherited content defines, minus queued deletions."""
        ids = set(self._states) | set(self._state_source_paths)
        if self._base_content is not None:
            ids.update(self._inherited_state_paths())
        return ids - set(self._deleted_state_ids)

    def _inherited_state_paths(self) -> dict[int, Path]:
        """State id to inherited state file, scanned once until the next reload."""

        if self._inherited_state_source_paths is None:
            assert self._base_content is not None
            paths: dict[int, Path] = {}
            files = list(self._base_content.glob("history/states", "*.txt").values())
            for entry in build_state_index(files, header=self._state_index_header):
                if self._is_shadowed_by_mod(self._base_content, Path(entry["path"])):
                    continue
                paths.setdefault(entry["id"], Path(entry["path"]))
            self._inherited_state_source_paths = paths
        return self._inherited_state_source_paths

    def _materialize_state_override(self, state: State) -> bool:
        """Retarget a vanilla-backed state to its equivalent in-mod path.

        This only changes queued in-memory state. The source file is copied by
        the normal preview/save rendering path, so reads and validation never
        create files as a side effect.
        """

        source = state.path or state.source_path or self._state_source_paths.get(state.id)
        if source is None:
            relative = Path("history") / "states" / f"{state.id} - {state.name or state.id}.txt"
        elif source.resolve(strict=False).is_relative_to(self.mod_root):
            return False
        else:
            relative = Path("history") / "states" / source.name
            if self._base_content is not None:
                relative = self._base_content.relative(source.resolve(strict=False)) or relative
        target = self._output_path(relative)
        if state.raw_text and parse(state.raw_text).problems:
            # Like the game, read the inherited file forgivingly, but write the
            # mod's own copy well formed.
            state.raw_text = repair(state.raw_text)
        state.path = target
        self._states[state.id] = state
        self._state_source_paths[state.id] = target
        self._original_files.setdefault(target, self._read_noted_text(target))
        return True

    def set_state_owner(
        self, state_ids: int | Sequence[int], tag: str, *, add_core: bool = True
    ) -> None:
        """Give one state or several to ``tag`` in their history (``owner = TAG``).

        ``add_core`` also adds ``tag`` to the cores. For a runtime change in
        an event or focus, use ``effects.transfer_state()`` instead. A state
        that already has this owner (and this core, with ``add_core``) is left
        alone: nothing is staged for it.

        Raises:
            NotFoundError: A state does not exist (nothing changes then), or a
                game install is configured and no one defines ``tag``.
        """
        ids = [state_ids] if isinstance(state_ids, int) else list(state_ids)
        tag = self._require_country_reference(tag)
        states = [self.get_state(state_id) for state_id in ids]
        changed = False
        for state in states:
            has_core = tag.upper() in {core.upper() for core in state.cores}
            if state.owner == tag and (has_core or not add_core):
                continue
            self._materialize_state_override(state)
            state.owner = tag
            if add_core and not has_core:
                state.cores.append(tag)
            self._dirty_states.add(state.id)
            changed = True
        if changed:
            self._dirty.add("states")

    def update_state(
        self,
        state_id: int,
        *,
        add_cores: Sequence[str] = (),
        remove_cores: Sequence[str] = (),
        **fields: Unpack[StateFields],
    ) -> State:
        """Set fields of a state; list fields such as ``cores`` are replaced.

        ``add_cores`` and ``remove_cores`` change single cores and keep the
        rest. Only the statements that change are rewritten on save. A call
        that changes nothing stages nothing (an explicit ``victory_points``
        always counts as a change: it rewrites the points one pair per block).
        ``history`` is the script inside the state's ``history = { }`` block;
        setting it replaces that block.

        Returns:
            The updated state.

        Raises:
            NotFoundError: The state does not exist, a core in
                ``remove_cores`` is not one of its cores, or (with a game
                install) a tag no one defines is set as owner or core.
            TypeError: A field name is unknown; nothing changed.
        """
        values: dict[str, Any] = dict(fields)
        state = self.get_state(state_id)
        _check_fields("update_state", state, values)
        added = [self._require_country_reference(tag) for tag in add_cores]
        removed = {require_country_tag(tag) for tag in remove_cores}
        for tag in sorted(removed):
            if tag not in {core.upper() for core in state.cores}:
                raise not_found("core", tag, state.cores, where=f"state {state_id}")
        if values.get("owner"):
            values["owner"] = self._require_country_reference(values["owner"])
        if "cores" in values:
            values["cores"] = [self._require_country_reference(tag) for tag in values["cores"]]
        current_cores = {core.upper() for core in values.get("cores", state.cores)}
        if (
            "victory_points" not in values
            # A loaded state keeps the file's spelling ("1"), a caller may pass 1.
            and all(
                getattr(state, key) == value or str(getattr(state, key)) == str(value)
                for key, value in values.items()
            )
            and not (removed & current_cores)
            and all(tag.upper() in current_cores for tag in added)
        ):
            return state
        self._materialize_state_override(state)
        _set_fields(state, values, method="update_state")
        if removed:
            state.cores = [core for core in state.cores if core.upper() not in removed]
        for tag in added:
            if tag not in state.cores:
                state.cores.append(tag)
        if "victory_points" in values:
            state._victory_points_requested = True
        self._dirty.add("states")
        self._dirty_states.add(state_id)
        return state

    def add_state_core(self, state_id: int, tag: str) -> State:
        """Add ``tag`` to a state's history cores.

        Returns:
            The changed state.

        Raises:
            NotFoundError: The state does not exist, or a game install is
                configured and no one defines ``tag``.
            AlreadyExistsError: ``tag`` is a core of the state already.
                Nothing is copied into the mod then.
        """
        tag = self._require_country_reference(tag)
        state = self.get_state(state_id)
        if tag.upper() in {core.upper() for core in state.cores}:
            raise already_exists("core", tag, hint=f"State {state_id} has it.")
        self._materialize_state_override(state)
        state.cores.append(tag)
        self._dirty.add("states")
        self._dirty_states.add(state_id)
        return state

    def remove_state_core(self, state_id: int, tag: str) -> None:
        """Remove ``tag`` from a state's history cores.

        Raises:
            NotFoundError: The state does not exist, or ``tag`` is not one of
                its cores. Nothing is copied into the mod then.
        """
        tag = require_country_tag(tag)
        state = self.get_state(state_id)
        if tag not in {core.upper() for core in state.cores}:
            raise not_found(
                "core",
                tag,
                state.cores,
                where=f"state {state_id}",
            )
        self._materialize_state_override(state)
        state.cores = [core for core in state.cores if core.upper() != tag]
        self._dirty.add("states")
        self._dirty_states.add(state_id)

    def _effective_states(self) -> list[State]:
        result: list[State] = []
        for state_id in self.list_states(include_vanilla=True):
            try:
                result.append(self.get_state(state_id))
            except (KeyError, OSError, ValueError):
                continue
        return result
