"""State lookup, search and history edits."""

from __future__ import annotations

import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

from ..localisation import (
    parse_localization_files,
)
from ..paths import (
    require_country_tag,
    resolve_mod_output_path,
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
from ..errors import NotFoundError, not_found
from ._countries import CountriesLayer
from ._shared import (
    _set_fields,
)


def _append_unique(existing: list, values: list) -> list:
    out = list(existing)
    for value in values:
        if value not in out:
            out.append(value)
    return out


def _fold_search_text(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value).strip().casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


class StatesLayer(CountriesLayer):
    """State lookup, search and history edits."""

    # ── States ──────────────────────────────────────────────────

    def list_states(self) -> list[int]:
        all_ids = set(self._state_ids) | set(self._states.keys())
        return sorted(all_ids)

    def state_index(self, include_vanilla: bool = True) -> list[dict]:
        cached = self._state_index_cache.get(include_vanilla)
        if cached is not None:
            return [
                dict(entry) for entry in cached
                if entry["id"] not in self._deleted_state_ids
            ]
        state_localization = self._state_localization_entries()
        entries: list[dict] = []
        for source, base in zip(("mod", "vanilla"), self._data_views()):
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
                    original = self.content_source(relative)
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
        by_id: dict[int, dict] = {}
        for entry in entries:
            existing = by_id.get(entry["id"])
            if existing is None or (
                existing["source"] == "vanilla" and entry["source"] == "mod"
            ):
                by_id[entry["id"]] = entry
        result = sorted(by_id.values(), key=lambda item: item["id"])
        self._state_index_cache[include_vanilla] = [dict(entry) for entry in result]
        return [entry for entry in result if entry["id"] not in self._deleted_state_ids]

    def get_state_name_map(self, include_vanilla: bool = True) -> dict[int, str]:
        return {
            entry["id"]: entry.get("display_name") or entry.get("name") or str(entry["id"])
            for entry in self.state_index(include_vanilla=include_vanilla)
        }

    def find_state(self, query: str, include_vanilla: bool = True, limit: int = 10) -> list[dict]:
        needle = _fold_search_text(query)
        if not needle:
            return []
        scored: list[tuple[float, dict, str]] = []
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
        results: list[dict] = []
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

    def get_state(self, state_id: int) -> State:
        """Load a state without changing the mod directory.

        Vanilla fallback states remain backed by their vanilla source until a
        mutating state API explicitly materializes an override under the mod.
        """
        if state_id in self._states:
            return self._states[state_id]
        if state_id in self._deleted_state_ids:
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
        f = self._state_source_paths.get(state_id)
        if f is not None and not f.exists():
            f = None
        if f is None and self._base_content is not None:
            f = self._inherited_state_paths().get(state_id)
        if f:
            state = read_state(f)
            self._states[state_id] = state
            self._state_source_paths.setdefault(state_id, f)
            text = read_text(f)
            self._original_files.setdefault(f, text)
            self._note_read(f, text, adopt=True)
            return state
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
        target = resolve_mod_output_path(self.mod_root, relative)
        if state.raw_text and parse(state.raw_text).problems:
            # Like the game, read the inherited file forgivingly, but write the
            # mod's own copy well formed.
            state.raw_text = repair(state.raw_text)
        state.path = target
        self._state_source_paths[state.id] = target
        self._original_files.setdefault(target, self._read_current_text(target))
        return True

    def set_state_owner(self, state_id: int, tag: str, add_core: bool = True) -> State:
        tag = self._require_country_reference(tag)
        state = self.get_state(state_id)
        self._materialize_state_override(state)
        state.owner = tag
        if add_core and tag not in state.cores:
            state.cores.append(tag)
        self._dirty.add("states")
        self._dirty_states.add(state_id)
        return state

    def set_state_properties(self, state_id: int, **kwargs) -> bool:
        state = self.get_state(state_id)
        self._materialize_state_override(state)
        for list_key in ("cores", "provinces"):
            if list_key in kwargs and isinstance(kwargs[list_key], list):
                kwargs[list_key] = _append_unique(getattr(state, list_key), kwargs[list_key])
        _set_fields(state, kwargs)
        if "victory_points" in kwargs:
            state._victory_points_requested = True
        self._dirty.add("states")
        self._dirty_states.add(state_id)
        return True

    def add_state_core(self, state_id: int, tag: str) -> State:
        """Add ``tag`` to a state's history cores.

        Returns:
            The changed state.

        Raises:
            NotFoundError: The state does not exist, or a game install is
                configured and no one defines ``tag``.
        """
        tag = self._require_country_reference(tag)
        state = self.get_state(state_id)
        self._materialize_state_override(state)
        if tag not in state.cores:
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
        if tag not in state.cores:
            raise not_found(
                "core",
                tag,
                state.cores,
                where=f"state {state_id}",
            )
        self._materialize_state_override(state)
        state.cores = [core for core in state.cores if core != tag]
        self._dirty.add("states")
        self._dirty_states.add(state_id)

    def patch_state_history(
        self,
        state_id: int,
        *,
        owner: str | None = None,
        add_cores: list[str] | None = None,
        remove_cores: list[str] | None = None,
    ) -> State:
        """Queue a minimal owner/core history patch for the next ``save()``.

        Repeated patches and other state setters compose in call order on the
        same pending State. Serialization patches its original source text.
        """
        state = self.get_state(state_id)
        self._materialize_state_override(state)
        removed = {tag.upper() for tag in (remove_cores or [])}
        state.cores = [core for core in state.cores if core.upper() not in removed]
        for core in add_cores or []:
            normalized = require_country_tag(core)
            if normalized not in state.cores:
                state.cores.append(normalized)
        if owner is not None:
            state.owner = require_country_tag(owner)
        self._dirty.add("states")
        self._dirty_states.add(state_id)
        return state

    def batch_set_owner(self, state_ids: list[int], tag: str, add_core: bool = True) -> list[State]:
        results = []
        for sid in state_ids:
            results.append(self.set_state_owner(sid, tag, add_core))
        return results

    def _effective_states(self) -> list[State]:
        result: list[State] = []
        for state_id in sorted(set(self._state_ids) | set(self._states)):
            try:
                result.append(self.get_state(state_id))
            except (KeyError, OSError, ValueError):
                continue
        return result
