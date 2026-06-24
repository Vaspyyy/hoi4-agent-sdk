"""
Mod facade - the primary entry point for AI agents.

Usage:
    from hoi4 import Mod

    mod = Mod("/path/to/my_mod", hoi4_install="/opt/steam/hoi4")
    tree = mod.get_focus_tree("german_focus")
    mod.add_focus("german_focus", Focus(id="GER_new", x=5, y=3))
    errors = mod.validate()
    diff = mod.preview()
    mod.save()
"""

from __future__ import annotations

import copy
import dataclasses
import re
import warnings
from contextlib import contextmanager
from pathlib import Path
from difflib import SequenceMatcher
from typing import Optional

from .config import find_config
from .countries import country_file_paths, read_country, serialize_country_files, write_all_country_files
from .decisions import load_decisions_file, serialize_decisions_file, write_decisions_file
from .diff import unified_diff
from .events import load_events_file, serialize_events_file, write_events_file
from .effects_catalog import TECHNOLOGY_CATEGORIES
from .focus import load_focus_tree, serialize_focus_tree, write_focus_tree
from .ideas import read_ideas_file, serialize_ideas_file, write_ideas_file
from .localisation import (
    parse_localization_dir,
    serialize_localization_file,
    write_localization_file,
)
from .states import (
    build_state_index,
    ensure_state_in_mod,
    find_state_file,
    read_state,
    serialize_state,
    write_state,
)
from .script import effect_block, normalize_block_body, scope_block
from .tags import load_all_tags, load_mod_tags, load_vanilla_tags
from .types import (
    Country,
    Decision,
    DecisionCategory,
    Event,
    EventOption,
    Focus,
    FocusTree,
    Idea,
    Leader,
    SaveResult,
    State,
    ValidationError,
)
from .validation import validate_country, validate_event, validate_focus_tree, validate_idea, validate_state


def _infer_event_namespace(event_id: str) -> str | None:
    if "." not in event_id:
        return None
    namespace = event_id.split(".", 1)[0]
    if namespace.replace("_", "").isalnum() and not namespace[0].isdigit():
        return namespace
    return None


def _set_fields(obj: object, kwargs: dict) -> None:
    from .types import _ensure_nested
    valid = {f.name for f in dataclasses.fields(obj)}
    unknown = set(kwargs) - valid
    if unknown:
        raise TypeError(f"Unknown fields: {sorted(unknown)}. Valid: {sorted(valid)}")
    for key, value in kwargs.items():
        if key in ("prerequisites", "mutually_exclusive") and isinstance(value, list):
            value = _ensure_nested(value)
        setattr(obj, key, value)


def _append_unique(existing: list, values: list) -> list:
    out = list(existing)
    for value in values:
        if value not in out:
            out.append(value)
    return out


def _normalize_event_option(option: EventOption) -> EventOption:
    option.trigger = normalize_block_body(option.trigger)
    option.ai_chance = normalize_block_body(option.ai_chance)
    return option


def _normalize_name_token(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", name.upper())


RULING_PARTIES = ("democratic", "fascism", "communism", "neutrality")

LEADER_IDEOLOGIES_BY_PARTY: dict[str, tuple[str, ...]] = {
    "democratic": ("liberalism", "conservatism", "socialism"),
    "communism": ("marxism", "leninism", "stalinism", "anti_revisionism", "anarchist_communism"),
    "fascism": ("nazism", "fascism_ideology", "falangism", "rexism"),
    "neutrality": ("despotism", "oligarchism", "moderate", "centrism"),
}


def _filter_validation_errors(
    errors: list[ValidationError],
    suppress_warnings: list[str] | tuple[str, ...] | set[str] | None = None,
) -> list[ValidationError]:
    if not suppress_warnings:
        return errors
    suppress = set(suppress_warnings)
    return [
        error for error in errors
        if not (
            error.severity == "warning"
            and (error.code in suppress or any(token in error.message for token in suppress))
        )
    ]


class Mod:
    def __init__(
        self,
        mod_root: str | Path,
        hoi4_install: str | Path | None = None,
    ):
        self.mod_root = Path(mod_root)
        self.hoi4_install = Path(hoi4_install) if hoi4_install else None

        self._focus_trees: dict[str, FocusTree] = {}
        self._dirty_focus_trees: set[str] = set()
        self._countries: dict[str, Country] = {}
        self._dirty_countries: set[str] = set()
        self._states: dict[int, State] = {}
        self._state_ids: list[int] = []
        self._dirty_states: set[int] = set()
        self._events: dict[str, Event] = {}
        self._event_namespaces: dict[str, Optional[str]] = {}
        self._dirty_events: set[str] = set()
        self._decisions: dict[str, Decision] = {}
        self._decision_categories: dict[str, DecisionCategory] = {}
        self._dirty_decision_categories: set[str] = set()
        self._ideas: dict[str, Idea] = {}
        self._dirty_ideas: set[str] = set()
        self._idea_file_containers: dict[Path, str] = {}
        self._cached_idea_file: dict[str, Path] = {}
        self._loc_entries: dict[str, str] = {}
        self._loc_sources: dict[str, Path] = {}
        self._dirty_loc_keys: set[str] = set()
        self._original_files: dict[Path, str] = {}
        self._dirty: set[str] = set()
        self._vanilla_tags: set[str] = load_vanilla_tags(self.hoi4_install) if self.hoi4_install else set()

        self._load()

    @classmethod
    def from_config(cls, start: str | Path | None = None) -> Mod:
        """Create a Mod instance from a .hoi4.json config file.

        Searches for .hoi4.json starting from `start` (defaults to cwd)
        and walks up to the filesystem root.
        """
        cfg = find_config(start)
        if cfg is None or cfg.mod_path is None:
            raise FileNotFoundError(
                "No .hoi4.json found (or mod_path not set). "
                "Create one with: {\"mod_path\": \"/path/to/mod\", \"hoi4_install\": \"/path/to/hoi4\"}"
            )
        return cls(cfg.mod_path, hoi4_install=cfg.hoi4_install)

    def _load(self) -> None:
        self._load_countries()
        self._load_states()
        self._load_focus_trees()
        self._load_events()
        self._load_decisions()
        self._load_ideas()
        self._load_localization()

    def _load_countries(self) -> None:
        from .countries import _build_loc_cache
        loc_cache = _build_loc_cache(self.mod_root)
        for tag in load_mod_tags(self.mod_root):
            try:
                self._countries[tag] = read_country(self.mod_root, tag, _loc_cache=loc_cache)
            except Exception:
                self._countries[tag] = Country(tag=tag)
            for path in country_file_paths(self.mod_root, self._countries[tag]):
                if path.exists():
                    self._original_files.setdefault(path, path.read_text(encoding="utf-8", errors="ignore"))

    def _load_states(self) -> None:
        states_dir = self.mod_root / "history" / "states"
        if not states_dir.exists():
            return
        self._state_ids = [entry["id"] for entry in build_state_index(states_dir)]

    def _load_focus_trees(self) -> None:
        focus_dir = self.mod_root / "common" / "national_focus"
        if not focus_dir.exists():
            return
        for f in sorted(focus_dir.glob("*.txt")):
            try:
                tree = load_focus_tree(f)
                if tree is None:
                    continue
                self._focus_trees[tree.id] = tree
                self._original_files[f] = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

    def _load_localization(self) -> None:
        loc_dir = self.mod_root / "localisation" / "english"
        if not loc_dir.exists():
            return
        self._loc_entries, self._loc_sources = parse_localization_dir(loc_dir)
        for path in set(self._loc_sources.values()):
            if path.exists():
                self._original_files[path] = path.read_text(encoding="utf-8-sig", errors="ignore")

    def _load_events(self) -> None:
        events_dir = self.mod_root / "events"
        if not events_dir.exists():
            return
        for f in sorted(events_dir.glob("*.txt")):
            try:
                namespace, events = load_events_file(f)
                for event in events:
                    self._events[event.id] = event
                    self._event_namespaces[event.id] = namespace
                self._original_files[f] = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

    def _load_decisions(self) -> None:
        decisions_dir = self.mod_root / "common" / "decisions"
        if not decisions_dir.exists():
            return
        for f in sorted(decisions_dir.glob("*.txt")):
            try:
                categories = load_decisions_file(f)
                for category in categories:
                    self._decision_categories[category.id] = category
                    for decision in category.decisions:
                        self._decisions[decision.id] = decision
                self._original_files[f] = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

    def _load_ideas(self) -> None:
        dirs = [
            self.mod_root / "common" / "national_ideas",
            self.mod_root / "common" / "ideas",
        ]
        for ideas_dir in dirs:
            if not ideas_dir.exists():
                continue
            for f in sorted(ideas_dir.glob("*.txt")):
                try:
                    ideas, container = read_ideas_file(f)
                    for idea in ideas:
                        self._ideas[idea.id] = idea
                    self._idea_file_containers[f] = container
                    self._original_files[f] = f.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue

        self._match_idea_files_to_countries()

    def _match_idea_files_to_countries(self) -> None:
        ideas_dir = self.mod_root / "common" / "ideas"
        if not ideas_dir.exists():
            return
        for f in ideas_dir.glob("*.txt"):
            stem = f.stem.lower()
            for tag, country in self._countries.items():
                name_lower = country.name.lower()
                if stem == name_lower or stem == name_lower.replace(" ", "_"):
                    self._cached_idea_file[tag] = f
                    break

    @property
    def default_loc_file(self) -> Path:
        return self.mod_root / "localisation" / "english" / "mod_l_english.yml"

    # ── Countries ────────────────────────────────────────────────

    def list_countries(self) -> list[str]:
        return sorted(self._countries.keys())

    def is_country_tag_available(self, tag: str) -> bool:
        tag = tag.upper()
        return tag not in self._countries and tag not in self._vanilla_tags

    def country_tag_conflicts(self, tag: str) -> list[str]:
        tag = tag.upper()
        conflicts: list[str] = []
        if tag in self._countries:
            country = self._countries[tag]
            label = country.name or tag
            conflicts.append(f"mod: {tag} ({label})")
        if tag in self._vanilla_tags:
            country = read_country(self.mod_root, tag, self.hoi4_install)
            label = country.name or tag
            conflicts.append(f"vanilla: {tag} ({label})")
        return conflicts

    def suggest_tag(self, name: str) -> str:
        suggestions = self.suggest_tags(name, count=1)
        if not suggestions:
            raise ValueError("No available 3-character country tags remain")
        return suggestions[0]

    def suggest_tags(self, name: str, count: int = 5) -> list[str]:
        taken = set(self._countries) | self._vanilla_tags
        token = _normalize_name_token(name)
        candidates: list[str] = []

        def add(candidate: str) -> None:
            candidate = _normalize_name_token(candidate)[:3]
            if len(candidate) == 3 and candidate not in candidates and candidate not in taken:
                candidates.append(candidate)

        if len(token) >= 3:
            add(token[:3])
            consonants = "".join(ch for ch in token if ch not in "AEIOU")
            if len(consonants) >= 3:
                add(consonants[:3])
            add(token[0] + token[1] + token[-1])
            add(token[0] + token[-2:])
            for i in range(1, len(token) - 1):
                add(token[0] + token[i] + token[-1])
            for i in range(len(token) - 2):
                add(token[i:i + 3])

        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        prefix = token[:1] or "X"
        for second in alphabet:
            for third in alphabet:
                add(prefix + second + third)
                if len(candidates) >= count:
                    return candidates[:count]
        for first in alphabet:
            for second in alphabet:
                for third in alphabet:
                    add(first + second + third)
                    if len(candidates) >= count:
                        return candidates[:count]
        return candidates[:count]

    def get_country(self, tag: str) -> Country:
        tag = tag.upper()
        if tag in self._countries:
            return self._countries[tag]
        country = read_country(self.mod_root, tag, self.hoi4_install)
        self._countries[tag] = country
        for path in country_file_paths(self.mod_root, country):
            if path.exists():
                self._original_files.setdefault(path, path.read_text(encoding="utf-8", errors="ignore"))
        return country

    def create_country(
        self,
        tag: str,
        name: str,
        adjective: str = "",
        color: tuple[int, int, int] = (128, 128, 128),
        capital: int = 1,
        ruling_party: str = "democratic",
        popularities: dict[str, int] | None = None,
        leader_name: str = "Leader",
        leader_ideology: str | None = None,
        ideas: list[str] | None = None,
        overwrite: bool = False,
        allow_vanilla_override: bool = False,
    ) -> Country:
        tag = tag.upper()
        if tag in self._countries and not overwrite:
            raise ValueError(
                f"Country tag '{tag}' already exists in the mod. "
                "Use overwrite=True only when intentionally replacing that mod country."
            )
        if tag in self._vanilla_tags and not allow_vanilla_override:
            raise ValueError(
                f"Country tag '{tag}' is already used by vanilla HOI4. "
                "Choose an unused tag or pass allow_vanilla_override=True intentionally."
            )
        if leader_ideology is None:
            leader_ideology = self.default_leader_ideology(ruling_party)
        leader = Leader(
            name=leader_name,
            character_id=f"{tag}_leader_1",
            ideology=leader_ideology,
        )
        country = Country(
            tag=tag,
            name=name,
            adjective=adjective or name,
            color=color,
            capital=capital,
            ruling_party=ruling_party,
            popularities=popularities or None,
            leader=leader,
            ideas=ideas or [],
        )
        self._countries[tag] = country
        self._dirty.add("countries")
        self._dirty_countries.add(tag)

        ideas_dir = self.mod_root / "common" / "ideas"
        if ideas_dir.exists():
            name_lower = name.lower()
            candidates = [
                name_lower,
                name_lower.replace(" ", "_"),
                tag.lower(),
            ]
            for stem in candidates:
                p = ideas_dir / f"{stem}.txt"
                if p.exists():
                    self._cached_idea_file[tag] = p
                    break

        return country

    def update_country(self, tag: str, **kwargs) -> bool:
        tag = tag.upper()
        country = self._countries.get(tag)
        if country is None:
            return False
        leader_kwargs = {}
        other_kwargs = {}
        for key, value in kwargs.items():
            if key.startswith("leader_"):
                leader_kwargs[key.removeprefix("leader_")] = value
            else:
                other_kwargs[key] = value
        if other_kwargs:
            _set_fields(country, other_kwargs)
        if leader_kwargs:
            if country.leader is None:
                country.leader = Leader(name="Leader", character_id=f"{tag}_leader_1")
            _set_fields(country.leader, leader_kwargs)
        self._dirty.add("countries")
        self._dirty_countries.add(tag)
        return True

    def delete_country(self, tag: str) -> bool:
        tag = tag.upper()
        if tag not in self._countries:
            return False
        del self._countries[tag]
        self._dirty.add("countries")
        self._dirty_countries.discard(tag)
        return True

    # ── States ──────────────────────────────────────────────────

    def list_states(self) -> list[int]:
        all_ids = set(self._state_ids) | set(self._states.keys())
        return sorted(all_ids)

    def state_index(self, include_vanilla: bool = True) -> list[dict]:
        entries: list[dict] = []
        for source, base in [("mod", self.mod_root), ("vanilla", self.hoi4_install)]:
            if base is None:
                continue
            if source == "vanilla" and not include_vanilla:
                continue
            states_dir = base / "history" / "states"
            for entry in build_state_index(states_dir):
                item = dict(entry)
                item["source"] = source
                entries.append(item)
        by_id: dict[int, dict] = {}
        for entry in entries:
            if entry["id"] not in by_id or entry["source"] == "mod":
                by_id[entry["id"]] = entry
        return sorted(by_id.values(), key=lambda item: item["id"])

    def get_state_name_map(self, include_vanilla: bool = True) -> dict[int, str]:
        return {
            entry["id"]: entry.get("display_name") or entry.get("name") or str(entry["id"])
            for entry in self.state_index(include_vanilla=include_vanilla)
        }

    def find_state(self, query: str, include_vanilla: bool = True, limit: int = 10) -> list[dict]:
        needle = query.strip().lower()
        if not needle:
            return []
        scored: list[tuple[float, dict]] = []
        for entry in self.state_index(include_vanilla=include_vanilla):
            haystacks = [
                str(entry.get("id", "")),
                str(entry.get("name", "")),
                str(entry.get("display_name", "")),
                Path(str(entry.get("path", ""))).stem,
            ]
            best = 0.0
            for haystack in haystacks:
                text = haystack.lower()
                if text == needle:
                    best = max(best, 1.0)
                elif needle in text:
                    best = max(best, 0.85)
                else:
                    best = max(best, SequenceMatcher(None, needle, text).ratio())
            if best >= 0.45:
                scored.append((best, entry))
        scored.sort(key=lambda item: (-item[0], item[1]["id"]))
        return [entry for _, entry in scored[:limit]]

    def get_state(self, state_id: int) -> State:
        if state_id in self._states:
            return self._states[state_id]
        states_dir = self.mod_root / "history" / "states"
        f = find_state_file(states_dir, state_id)
        if not f and self.hoi4_install:
            f = ensure_state_in_mod(self.mod_root, self.hoi4_install, state_id)
        if f:
            state = read_state(f)
            self._states[state_id] = state
            self._original_files.setdefault(f, f.read_text(encoding="utf-8", errors="ignore"))
            return state
        raise KeyError(f"State {state_id} not found")

    def set_state_owner(self, state_id: int, tag: str, add_core: bool = True) -> State:
        state = self.get_state(state_id)
        state.owner = tag.upper()
        if add_core and tag.upper() not in state.cores:
            state.cores.append(tag.upper())
        self._dirty.add("states")
        self._dirty_states.add(state_id)
        return state

    def set_state_properties(self, state_id: int, **kwargs) -> bool:
        state = self.get_state(state_id)
        for list_key in ("cores", "provinces"):
            if list_key in kwargs and isinstance(kwargs[list_key], list):
                kwargs[list_key] = _append_unique(getattr(state, list_key), kwargs[list_key])
        _set_fields(state, kwargs)
        self._dirty.add("states")
        self._dirty_states.add(state_id)
        return True

    def add_state_core(self, state_id: int, tag: str) -> State:
        state = self.get_state(state_id)
        tag = tag.upper()
        if tag not in state.cores:
            state.cores.append(tag)
        self._dirty.add("states")
        self._dirty_states.add(state_id)
        return state

    def remove_state_core(self, state_id: int, tag: str) -> State:
        state = self.get_state(state_id)
        tag = tag.upper()
        state.cores = [core for core in state.cores if core != tag]
        self._dirty.add("states")
        self._dirty_states.add(state_id)
        return state

    def batch_set_owner(self, state_ids: list[int], tag: str, add_core: bool = True) -> list[State]:
        results = []
        for sid in state_ids:
            results.append(self.set_state_owner(sid, tag, add_core))
        return results

    # ── Events ──────────────────────────────────────────────────

    def list_events(self) -> list[str]:
        return sorted(self._events.keys())

    def get_event(self, event_id: str) -> Event:
        if event_id not in self._events:
            raise KeyError(f"Event '{event_id}' not found. Available: {self.list_events()}")
        return self._events[event_id]

    def create_event(
        self,
        event_id: str,
        title: str = "",
        description: str = "",
        event_type: str = "country_event",
        picture: str = "GFX_report_event_generic",
        is_triggered_only: bool = False,
        fire_only_once: bool | None = None,
        trigger: str = "",
        immediate: str = "",
        mean_time_to_happen: str = "",
        options: list[EventOption] | None = None,
        overwrite: bool = False,
    ) -> Event:
        existing = self._events.get(event_id)
        if existing is not None and not overwrite:
            raise ValueError(
                f"Event '{event_id}' already exists. Use overwrite=True to replace it, "
                "or update_event()/update_event_option() to patch it."
            )
        existing_namespace = self._event_namespaces.get(event_id)
        event = Event(
            id=event_id,
            title=title or f"{event_id}.t",
            description=description or f"{event_id}.d",
            event_type=event_type,
            picture=picture,
            is_triggered_only=is_triggered_only,
            fire_only_once=fire_only_once,
            trigger=normalize_block_body(trigger),
            immediate=normalize_block_body(immediate),
            mean_time_to_happen=normalize_block_body(mean_time_to_happen),
            options=[_normalize_event_option(option) for option in (options or [EventOption(name=f"{event_id}.a", effect="")])],
            path=existing.path if existing is not None else None,
        )
        self._events[event_id] = event
        self._event_namespaces[event_id] = existing_namespace if existing_namespace is not None else _infer_event_namespace(event_id)
        self._dirty.add("events")
        self._dirty_events.add(event_id)
        return event

    def update_event(self, event_id: str, **kwargs) -> bool:
        event = self._events.get(event_id)
        if event is None:
            return False
        event.raw_block = ""
        for key in ("trigger", "immediate", "mean_time_to_happen"):
            if key in kwargs and isinstance(kwargs[key], str):
                kwargs[key] = normalize_block_body(kwargs[key])
        if "options" in kwargs and isinstance(kwargs["options"], list):
            kwargs["options"] = [_normalize_event_option(option) for option in kwargs["options"]]
        _set_fields(event, kwargs)
        self._dirty.add("events")
        self._dirty_events.add(event_id)
        return True

    def delete_event(self, event_id: str) -> bool:
        if event_id not in self._events:
            return False
        del self._events[event_id]
        self._event_namespaces.pop(event_id, None)
        self._dirty.add("events")
        self._dirty_events.discard(event_id)
        return True

    def set_event_namespace(self, event_id: str, namespace: str) -> None:
        if event_id in self._events:
            self._event_namespaces[event_id] = namespace
            self._dirty.add("events")
            self._dirty_events.add(event_id)

    def add_event_option(self, event_id: str, option: EventOption) -> bool:
        event = self._events.get(event_id)
        if event is None:
            return False
        event.raw_block = ""
        event.options.append(_normalize_event_option(option))
        self._dirty.add("events")
        self._dirty_events.add(event_id)
        return True

    def update_event_option(self, event_id: str, option: int | str, **kwargs) -> bool:
        event = self._events.get(event_id)
        if event is None:
            return False
        if isinstance(option, int):
            if option < 0 or option >= len(event.options):
                return False
            event_option = event.options[option]
        else:
            event_option = next((candidate for candidate in event.options if candidate.name == option), None)
            if event_option is None:
                return False
        for key in ("trigger", "ai_chance"):
            if key in kwargs and isinstance(kwargs[key], str):
                kwargs[key] = normalize_block_body(kwargs[key])
        _set_fields(event_option, kwargs)
        _normalize_event_option(event_option)
        event.raw_block = ""
        self._dirty.add("events")
        self._dirty_events.add(event_id)
        return True

    # ── Decisions ────────────────────────────────────────────────

    def list_decision_categories(self) -> list[str]:
        return sorted(self._decision_categories.keys())

    def list_decisions(self) -> list[str]:
        return sorted(self._decisions.keys())

    def get_decision(self, decision_id: str) -> Decision:
        if decision_id not in self._decisions:
            raise KeyError(f"Decision '{decision_id}' not found. Available: {self.list_decisions()}")
        return self._decisions[decision_id]

    def get_decision_category(self, category_id: str) -> DecisionCategory:
        if category_id not in self._decision_categories:
            raise KeyError(
                f"Decision category '{category_id}' not found. Available: {self.list_decision_categories()}"
            )
        return self._decision_categories[category_id]

    def create_decision_category(
        self,
        category_id: str,
        *,
        icon: str = "",
        allowed: str = "",
        visible: str = "",
        path: str | Path | None = None,
        overwrite: bool = False,
    ) -> DecisionCategory:
        if category_id in self._decision_categories and not overwrite:
            raise ValueError(
                f"Decision category '{category_id}' already exists. "
                "Use overwrite=True to replace it or update existing decisions."
            )
        target = Path(path) if path is not None else self.mod_root / "common" / "decisions" / "mod_decisions.txt"
        category = DecisionCategory(id=category_id, icon=icon, allowed=allowed, visible=visible, path=target)
        self._decision_categories[category_id] = category
        self._dirty.add("decisions")
        self._dirty_decision_categories.add(category_id)
        return category

    def create_decision(
        self,
        category_id: str,
        decision_id: str,
        *,
        icon: str = "",
        cost: int | None = None,
        days_remove: int | None = None,
        fire_only_once: bool | None = None,
        available: str = "",
        visible: str = "",
        complete_effect: str = "",
        remove_effect: str = "",
        ai_will_do: str = "",
        path: str | Path | None = None,
        overwrite: bool = False,
    ) -> Decision:
        existing = self._decisions.get(decision_id)
        if existing is not None and not overwrite:
            raise ValueError(
                f"Decision '{decision_id}' already exists. "
                "Use overwrite=True to replace it or update_decision() to patch it."
            )
        category = self._decision_categories.get(category_id)
        if category is None:
            category = self.create_decision_category(category_id, path=path)
        elif path is not None:
            category.path = Path(path)
        if existing is not None:
            old_category = self._decision_categories.get(existing.category)
            if old_category is not None:
                old_category.decisions = [candidate for candidate in old_category.decisions if candidate.id != decision_id]
                self._dirty_decision_categories.add(old_category.id)
        decision = Decision(
            id=decision_id,
            category=category_id,
            icon=icon,
            cost=cost,
            days_remove=days_remove,
            fire_only_once=fire_only_once,
            available=available,
            visible=visible,
            complete_effect=complete_effect,
            remove_effect=remove_effect,
            ai_will_do=ai_will_do,
            path=category.path,
        )
        category.decisions.append(decision)
        self._decisions[decision_id] = decision
        self._dirty.add("decisions")
        self._dirty_decision_categories.add(category_id)
        return decision

    def update_decision(self, decision_id: str, **kwargs) -> bool:
        decision = self._decisions.get(decision_id)
        if decision is None:
            return False
        decision.raw_block = ""
        _set_fields(decision, kwargs)
        self._dirty.add("decisions")
        self._dirty_decision_categories.add(decision.category)
        return True

    def delete_decision(self, decision_id: str) -> bool:
        decision = self._decisions.pop(decision_id, None)
        if decision is None:
            return False
        category = self._decision_categories.get(decision.category)
        if category:
            category.decisions = [candidate for candidate in category.decisions if candidate.id != decision_id]
            self._dirty_decision_categories.add(category.id)
        self._dirty.add("decisions")
        return True

    # ── Ideas ───────────────────────────────────────────────────

    def list_ideas(self) -> list[str]:
        return sorted(self._ideas.keys())

    def get_idea(self, idea_id: str) -> Idea:
        if idea_id not in self._ideas:
            raise KeyError(f"Idea '{idea_id}' not found. Available: {self.list_ideas()}")
        return self._ideas[idea_id]

    def create_idea(
        self,
        idea_id: str,
        icon: str = "GFX_idea_generic",
        modifier: dict[str, str | int | float | bool] | None = None,
        path: str | Path | None = None,
        overwrite: bool = False,
    ) -> Idea:
        existing = self._ideas.get(idea_id)
        if existing is not None and not overwrite:
            raise ValueError(f"Idea '{idea_id}' already exists. Use overwrite=True to replace it or update_idea() to patch it.")
        idea = Idea(id=idea_id, icon=icon, modifier=modifier or {})
        if path is not None:
            idea.path = Path(path) if not isinstance(path, Path) else path
        elif existing is not None and existing.path is not None:
            idea.path = existing.path
        else:
            prefix = idea_id.split("_")[0] if "_" in idea_id else idea_id
            if prefix in self._cached_idea_file:
                idea.path = self._cached_idea_file[prefix]
        self._ideas[idea_id] = idea
        self._dirty.add("ideas")
        self._dirty_ideas.add(idea_id)
        return idea

    def update_idea(self, idea_id: str, **kwargs) -> bool:
        idea = self._ideas.get(idea_id)
        if idea is None:
            return False
        modifier_val = kwargs.pop("modifier", None)
        if modifier_val is not None:
            if not isinstance(modifier_val, dict):
                raise TypeError(f"modifier must be a dict, got {type(modifier_val).__name__}")
            idea.modifier.update(modifier_val)
        if kwargs:
            _set_fields(idea, kwargs)
        self._dirty.add("ideas")
        self._dirty_ideas.add(idea_id)
        return True

    def delete_idea(self, idea_id: str) -> bool:
        if idea_id not in self._ideas:
            return False
        del self._ideas[idea_id]
        self._dirty.add("ideas")
        self._dirty_ideas.discard(idea_id)
        return True

    def set_idea_path(self, tag: str, path: str | Path) -> None:
        tag = tag.upper()
        self._cached_idea_file[tag] = Path(path)

    # ── Focus Trees ──────────────────────────────────────────────

    def list_focus_trees(self) -> list[str]:
        return sorted(self._focus_trees.keys())

    def get_focus_tree(self, tree_id: str) -> FocusTree:
        if tree_id not in self._focus_trees:
            raise KeyError(f"Focus tree '{tree_id}' not found. Available: {self.list_focus_trees()}")
        return self._focus_trees[tree_id]

    def create_focus_tree(self, tree_id: str, country_tag: str, overwrite: bool = False) -> FocusTree:
        existing = self._focus_trees.get(tree_id)
        if existing is not None and not overwrite:
            raise ValueError(
                f"Focus tree '{tree_id}' already exists. "
                "Use overwrite=True to replace it or update_focus_tree()/add_focus() to patch it."
            )
        tag = country_tag.upper()
        path = existing.path if existing is not None and existing.path is not None else self.mod_root / "common" / "national_focus" / f"{tag}_focus.txt"
        tree = FocusTree(id=tree_id, country_tag=tag, path=path)
        self._focus_trees[tree_id] = tree
        self._original_files.setdefault(path, "")
        self._dirty.add("focus")
        self._dirty_focus_trees.add(tree_id)
        return tree

    def delete_focus_tree(self, tree_id: str) -> bool:
        if tree_id not in self._focus_trees:
            return False
        tree = self._focus_trees.pop(tree_id)
        if tree.path and tree.path in self._original_files:
            del self._original_files[tree.path]
        self._dirty.add("focus")
        self._dirty_focus_trees.discard(tree_id)
        return True

    def update_focus_tree(self, tree_id: str, **kwargs) -> bool:
        tree = self._focus_trees.get(tree_id)
        if tree is None:
            return False
        _set_fields(tree, kwargs)
        self._dirty.add("focus")
        self._dirty_focus_trees.add(tree_id)
        return True

    # ── Focuses ──────────────────────────────────────────────────

    def add_focus(self, tree_id: str, focus: Focus) -> None:
        tree = self.get_focus_tree(tree_id)
        focus.touched = True
        tree.focuses.append(focus)
        self._dirty.add("focus")
        self._dirty_focus_trees.add(tree_id)

    def remove_focus(self, tree_id: str, focus_id: str) -> bool:
        tree = self.get_focus_tree(tree_id)
        for i, f in enumerate(tree.focuses):
            if f.id == focus_id:
                tree.focuses.pop(i)
                self._dirty.add("focus")
                self._dirty_focus_trees.add(tree_id)
                return True
        return False

    def get_focus(self, tree_id: str, focus_id: str) -> Optional[Focus]:
        tree = self.get_focus_tree(tree_id)
        for f in tree.focuses:
            if f.id == focus_id:
                return f
        return None

    def update_focus(self, tree_id: str, focus_id: str, **kwargs) -> bool:
        focus = self.get_focus(tree_id, focus_id)
        if focus is None:
            return False
        _set_fields(focus, kwargs)
        focus.touched = True
        self._dirty.add("focus")
        self._dirty_focus_trees.add(tree_id)
        return True

    def insert_focus_after(
        self,
        tree_id: str,
        anchor_focus_id: str,
        focus: Focus,
        *,
        add_prerequisite: bool = True,
        relative_position: bool = True,
    ) -> None:
        tree = self.get_focus_tree(tree_id)
        for idx, existing in enumerate(tree.focuses):
            if existing.id == anchor_focus_id:
                if add_prerequisite and not focus.prerequisites:
                    focus.prerequisites = [[anchor_focus_id]]
                if relative_position and not focus.relative_position_id:
                    focus.relative_position_id = anchor_focus_id
                focus.touched = True
                tree.focuses.insert(idx + 1, focus)
                self._dirty.add("focus")
                self._dirty_focus_trees.add(tree_id)
                return
        raise KeyError(f"Focus '{anchor_focus_id}' not found in tree '{tree_id}'")

    def insert_branch(
        self,
        tree_id: str,
        anchor_focus_id: str,
        focuses: list[Focus],
        *,
        chain_prerequisites: bool = True,
    ) -> None:
        previous = anchor_focus_id
        for focus in focuses:
            if chain_prerequisites and not focus.prerequisites:
                focus.prerequisites = [[previous]]
            if not focus.relative_position_id:
                focus.relative_position_id = previous
            self.insert_focus_after(tree_id, previous, focus, add_prerequisite=False)
            previous = focus.id

    def append_to_focus_reward(self, tree_id: str, focus_id: str, effect: str) -> bool:
        focus = self.get_focus(tree_id, focus_id)
        if focus is None:
            return False
        focus.completion_reward = "\n".join(
            part for part in [focus.completion_reward.strip(), effect.strip()] if part
        )
        focus.touched = True
        self._dirty.add("focus")
        self._dirty_focus_trees.add(tree_id)
        return True

    def set_focuses_mutually_exclusive(self, tree_id: str, focus_a_id: str, focus_b_id: str) -> bool:
        focus_a = self.get_focus(tree_id, focus_a_id)
        focus_b = self.get_focus(tree_id, focus_b_id)
        if focus_a is None or focus_b is None:
            return False
        if [focus_b_id] not in focus_a.mutually_exclusive:
            focus_a.mutually_exclusive.append([focus_b_id])
        if [focus_a_id] not in focus_b.mutually_exclusive:
            focus_b.mutually_exclusive.append([focus_a_id])
        focus_a.touched = True
        focus_b.touched = True
        self._dirty.add("focus")
        self._dirty_focus_trees.add(tree_id)
        return True

    def set_focus_loc(
        self,
        focus_id: str,
        name: str,
        description: str,
        file_path: str | Path | None = None,
    ) -> None:
        self.set_loc(focus_id, name, file_path=file_path)
        self.set_loc(f"{focus_id}_desc", description, file_path=file_path)

    @staticmethod
    def effect_add_building(state_id: int, building_type: str, level: int = 1) -> str:
        return (
            f"{state_id} = {{ "
            f"add_extra_state_shared_building_slots = {level} "
            "add_building_construction = { "
            f"type = {building_type} level = {level} instant_build = yes "
            "} "
            "}"
        )

    @staticmethod
    def effect_block(name: str, fields: dict[str, object] | None = None, **kwargs: object) -> str:
        return effect_block(name, fields, **kwargs)

    @staticmethod
    def scope_block(scope: str | int, *effects: str) -> str:
        return scope_block(scope, *effects)

    @staticmethod
    def effect_add_state_building(
        state_id: int,
        building_type: str,
        level: int = 1,
        *,
        add_slots: int = 0,
        province: int | None = None,
    ) -> str:
        fields: dict[str, object] = {
            "type": building_type,
            "level": level,
            "instant_build": True,
        }
        if province is not None:
            fields["province"] = province
        effects: list[str] = []
        if add_slots:
            effects.append(f"add_extra_state_shared_building_slots = {add_slots}")
        effects.append(effect_block("add_building_construction", fields))
        return scope_block(state_id, *effects)

    @classmethod
    def effect_add_civilian_factory(cls, state_id: int, level: int = 1) -> str:
        return cls.effect_add_building(state_id, "industrial_complex", level)

    @classmethod
    def effect_add_military_factory(cls, state_id: int, level: int = 1) -> str:
        return cls.effect_add_building(state_id, "arms_factory", level)

    @staticmethod
    def effect_add_infrastructure(state_id: int, level: int = 1) -> str:
        return (
            f"{state_id} = {{ "
            "add_building_construction = { "
            f"type = infrastructure level = {level} instant_build = yes "
            "} "
            "}"
        )

    @classmethod
    def effect_add_bunker(cls, state_id: int, level: int = 1, province: int | None = None) -> str:
        return cls.effect_add_state_building(state_id, "bunker", level, province=province)

    @classmethod
    def effect_add_coastal_bunker(cls, state_id: int, level: int = 1, province: int | None = None) -> str:
        return cls.effect_add_state_building(state_id, "coastal_bunker", level, province=province)

    @staticmethod
    def effect_add_tech_bonus(
        name: str,
        category: str = "industry",
        uses: int = 1,
        bonus: float = 0.5,
    ) -> str:
        if category not in TECHNOLOGY_CATEGORIES:
            examples = ", ".join(("industry", "infantry_weapons", "artillery", "armor", "electronics", "land_doctrine"))
            raise ValueError(f"Unknown technology category '{category}'. Common valid categories: {examples}")
        return f"add_tech_bonus = {{ name = {name} bonus = {bonus} uses = {uses} category = {category} }}"

    @classmethod
    def effect_add_industry_bonus(cls, name: str, uses: int = 1, bonus: float = 0.5) -> str:
        return cls.effect_add_tech_bonus(name, category="industry", uses=uses, bonus=bonus)

    @staticmethod
    def effect_add_state_core(state_id: int, tag: str) -> str:
        return f"{state_id} = {{ add_core_of = {tag.upper()} }}"

    @staticmethod
    def effect_remove_state_core(state_id: int, tag: str) -> str:
        return f"{state_id} = {{ remove_core_of = {tag.upper()} }}"

    @staticmethod
    def effect_transfer_state(state_id: int, target: str) -> str:
        return f"{target.upper()} = {{ transfer_state = {state_id} }}"

    @classmethod
    def effect_transfer_state_with_core(cls, state_id: int, target: str) -> str:
        return "\n".join([
            cls.effect_transfer_state(state_id, target),
            cls.effect_add_state_core(state_id, target),
        ])

    @staticmethod
    def effect_add_political_power(amount: int) -> str:
        return f"add_political_power = {amount}"

    @staticmethod
    def effect_add_war_support(amount: float) -> str:
        return f"add_war_support = {amount}"

    @staticmethod
    def effect_add_stability(amount: float) -> str:
        return f"add_stability = {amount}"

    @staticmethod
    def effect_add_manpower(amount: int) -> str:
        return f"add_manpower = {amount}"

    @staticmethod
    def effect_add_equipment(
        equipment_type: str,
        amount: int,
        producer: str | None = None,
        variant_name: str | None = None,
    ) -> str:
        fields: dict[str, object] = {"type": equipment_type, "amount": amount}
        if producer:
            fields["producer"] = producer.upper()
        if variant_name:
            fields["variant_name"] = variant_name
        return effect_block("add_equipment_to_stockpile", fields)

    @staticmethod
    def effect_set_technology(technology: str, level: int = 1, popup: bool | None = None) -> str:
        fields: dict[str, object] = {technology: level}
        if popup is not None:
            fields["popup"] = popup
        return effect_block("set_technology", fields)

    @classmethod
    def effect_set_technologies(cls, technologies: dict[str, int]) -> str:
        return effect_block("set_technology", technologies)

    @staticmethod
    def effect_add_timed_idea(idea_id: str, days: int) -> str:
        return f"add_timed_idea = {{ idea = {idea_id} days = {days} }}"

    @staticmethod
    def effect_create_wargoal(target: str, war_goal_type: str = "annex_everything") -> str:
        return f"create_wargoal = {{ type = {war_goal_type} target = {target.upper()} }}"

    @staticmethod
    def effect_declare_war(target: str, war_goal_type: str = "annex_everything") -> str:
        return f"declare_war_on = {{ type = {war_goal_type} target = {target.upper()} }}"

    @classmethod
    def effect_declare_war_from(
        cls,
        actor: str,
        target: str,
        war_goal_type: str = "annex_everything",
    ) -> str:
        return cls.scope_block(actor.upper(), cls.effect_declare_war(target, war_goal_type))

    @staticmethod
    def effect_start_civil_war(ideology: str, size: float = 0.5, capital: int | None = None) -> str:
        parts = [f"ideology = {ideology}", f"size = {size}"]
        if capital is not None:
            parts.append(f"capital = {capital}")
        return f"start_civil_war = {{ {' '.join(parts)} }}"

    @staticmethod
    def default_leader_ideology(ruling_party: str) -> str:
        return LEADER_IDEOLOGIES_BY_PARTY.get(ruling_party, LEADER_IDEOLOGIES_BY_PARTY["democratic"])[0]

    @staticmethod
    def leader_ideologies_for_party(ruling_party: str) -> tuple[str, ...]:
        return LEADER_IDEOLOGIES_BY_PARTY.get(ruling_party, ())

    @staticmethod
    def ruling_parties() -> tuple[str, ...]:
        return RULING_PARTIES

    def create_wargoal(self, target: str, war_goal_type: str = "annex_everything") -> str:
        return self.effect_create_wargoal(target, war_goal_type)

    def declare_war(self, target: str, war_goal_type: str = "annex_everything") -> str:
        return self.effect_declare_war(target, war_goal_type)

    def start_civil_war(self, ideology: str, size: float = 0.5, capital: int | None = None) -> str:
        return self.effect_start_civil_war(ideology, size, capital)

    def validate_effect(
        self,
        script: str,
        suppress_warnings: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> list[ValidationError]:
        known_tags = set(load_all_tags(self.hoi4_install, self.mod_root))
        known_tags.update(self._countries.keys())
        probe = Event(id="effect_probe.1", title="Effect Probe", description="Effect Probe", options=[
            EventOption(name="effect_probe.1.a", effect=script),
        ])
        return _filter_validation_errors(
            validate_event(probe, namespace="effect_probe", known_tags=known_tags),
            suppress_warnings=suppress_warnings,
        )

    def create_industrial_branch(
        self,
        tree_id: str,
        tag: str,
        *,
        anchor_focus_id: str | None = None,
        state_id: int | None = None,
        grounded: bool = True,
    ) -> list[Focus]:
        tag = tag.upper()
        tree = self.get_focus_tree(tree_id) if tree_id in self._focus_trees else self.create_focus_tree(tree_id, tag)
        context = self.get_country_context(tag)
        target_state = state_id or context.get("capital") or (context.get("states") or [{}])[0].get("id") or 1
        y_base = max((focus.y for focus in tree.focuses), default=-1) + 1
        x_base = 0 if not tree.focuses else max(focus.x for focus in tree.focuses) + 2
        prefix = tag
        focuses = [
            Focus(
                id=f"{prefix}_map_domestic_industry",
                icon="GFX_goal_generic_construct_civ_factory",
                x=x_base,
                y=y_base,
                cost=5 if grounded else 10,
                search_filters=["FOCUS_FILTER_INDUSTRY"],
                completion_reward="add_political_power = 50",
            ),
            Focus(
                id=f"{prefix}_modernize_transport_links",
                icon="GFX_goal_generic_construct_infrastructure",
                x=0,
                y=1,
                cost=10,
                search_filters=["FOCUS_FILTER_INDUSTRY"],
                completion_reward=self.effect_add_infrastructure(int(target_state), 1),
            ),
            Focus(
                id=f"{prefix}_support_domestic_steel",
                icon="GFX_goal_generic_production",
                x=0,
                y=1,
                cost=10,
                search_filters=["FOCUS_FILTER_INDUSTRY"],
                completion_reward="\n".join([
                    self.effect_add_civilian_factory(int(target_state), 1),
                    self.effect_add_industry_bonus(f"{prefix}_steel_industry_bonus", uses=1, bonus=0.5),
                ]),
            ),
            Focus(
                id=f"{prefix}_precision_workshops",
                icon="GFX_goal_generic_construct_mil_factory",
                x=0,
                y=1,
                cost=10,
                search_filters=["FOCUS_FILTER_INDUSTRY", "FOCUS_FILTER_RESEARCH"],
                completion_reward="\n".join([
                    self.effect_add_military_factory(int(target_state), 1),
                    self.effect_add_industry_bonus(f"{prefix}_precision_tools_bonus", uses=1, bonus=0.5),
                ]),
            ),
        ]
        loc_text = {
            focuses[0].id: ("Map Domestic Industry", "A careful survey of local workshops, transport bottlenecks, and available capital will let us expand without pretending we are a great power."),
            focuses[1].id: ("Modernize Transport Links", "Industry depends on reliable roads and rail connections. Targeted improvements can make our small economy more resilient."),
            focuses[2].id: ("Support Domestic Steel", "Steel remains the backbone of our heavy industry. State support can help established firms modernize production."),
            focuses[3].id: ("Precision Workshops", "Small states must compete through skilled labor and precision tools rather than sheer scale."),
        }
        for focus in focuses:
            name, desc = loc_text[focus.id]
            self.set_focus_loc(focus.id, name, desc)

        if anchor_focus_id:
            self.insert_branch(tree_id, anchor_focus_id, focuses)
        else:
            for offset, focus in enumerate(focuses):
                focus.x = x_base
                focus.y = y_base + offset
                if offset and not focus.prerequisites:
                    focus.prerequisites = [[focuses[offset - 1].id]]
                self.add_focus(tree_id, focus)
        return focuses

    # ── Localization ─────────────────────────────────────────────

    def get_loc(self, key: str) -> Optional[str]:
        return self._loc_entries.get(key)

    def set_loc(self, key: str, value: str, file_path: str | Path | None = None) -> None:
        self._loc_entries[key] = value
        if file_path is not None:
            self._loc_sources[key] = Path(file_path)
        elif key not in self._loc_sources:
            self._loc_sources[key] = self.default_loc_file
        self._dirty.add("localization")
        self._dirty_loc_keys.add(key)

    def delete_loc(self, key: str) -> bool:
        if key in self._loc_entries:
            del self._loc_entries[key]
            self._loc_sources.pop(key, None)
            self._dirty.add("localization")
            self._dirty_loc_keys.add(key)
            return True
        return False

    def search_loc(self, query: str) -> dict[str, str]:
        q = query.lower()
        return {k: v for k, v in self._loc_entries.items() if q in k.lower() or q in v.lower()}

    def all_loc(self) -> dict[str, str]:
        return dict(self._loc_entries)

    def get_country_context(self, tag: str, *, copy_states: bool = False) -> dict:
        tag = tag.upper()
        country = self.get_country(tag)
        context: dict = {
            "tag": tag,
            "name": country.name,
            "adjective": country.adjective,
            "capital": country.capital,
            "ruling_party": country.ruling_party,
            "popularities": dict(country.popularities),
            "leader": dataclasses.asdict(country.leader) if country.leader else None,
            "states": [],
            "ideas": [],
            "localization": {},
            "focus_trees": [],
        }

        for base in [self.mod_root, self.hoi4_install]:
            if base is None:
                continue
            states_dir = base / "history" / "states"
            if states_dir.exists():
                for state_path in sorted(states_dir.glob("*.txt")):
                    try:
                        state_text = state_path.read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        continue
                    if f"owner = {tag}" not in state_text and f"add_core_of = {tag}" not in state_text:
                        continue
                    try:
                        state = read_state(state_path)
                    except Exception:
                        continue
                    if state.owner == tag or tag in state.cores:
                        if copy_states and state.id not in self._states:
                            try:
                                state = self.get_state(state.id)
                            except KeyError:
                                pass
                        context["states"].append(dataclasses.asdict(state))

            for ideas_dir in [base / "common" / "ideas", base / "common" / "national_ideas"]:
                if not ideas_dir.exists():
                    continue
                candidates = [ideas_dir / f"{tag}.txt"]
                if country.name:
                    candidates.append(ideas_dir / f"{country.name.lower().replace(' ', '_')}.txt")
                for path in candidates:
                    if not path.exists():
                        continue
                    try:
                        ideas, _ = read_ideas_file(path)
                    except Exception:
                        continue
                    for idea in ideas:
                        context["ideas"].append(dataclasses.asdict(idea))

            focus_dir = base / "common" / "national_focus"
            if focus_dir.exists():
                for path in sorted(focus_dir.glob("*.txt")):
                    try:
                        raw_focus_text = path.read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        continue
                    focus_header = raw_focus_text.split("focus = {", 1)[0]
                    if f"tag = {tag}" not in focus_header and f"original_tag = {tag}" not in focus_header:
                        continue
                    try:
                        tree = load_focus_tree(path)
                    except Exception:
                        continue
                    if tree and tree.country_tag == tag:
                        context["focus_trees"].append({
                            "id": tree.id,
                            "path": str(path),
                            "focus_ids": [focus.id for focus in tree.focuses],
                        })

        for key, value in self._loc_entries.items():
            if key == tag or key.startswith(f"{tag}_"):
                context["localization"][key] = value

        # Deduplicate repeated vanilla/mod fallback entries by stable IDs.
        context["states"] = list({state["id"]: state for state in context["states"]}.values())
        context["ideas"] = list({idea["id"]: idea for idea in context["ideas"]}.values())
        return context

    def ensure_country_states_in_mod(self, tag: str) -> list[State]:
        context = self.get_country_context(tag, copy_states=True)
        return [self.get_state(state["id"]) for state in context["states"]]

    # ── Validation ───────────────────────────────────────────────

    def validate(
        self,
        suppress_warnings: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> list[ValidationError]:
        errors: list[ValidationError] = []

        known_tags = set(load_all_tags(self.hoi4_install, self.mod_root))
        known_tags.update(self._countries.keys())
        for country in self._countries.values():
            errors.extend(validate_country(country))
        for state in self._states.values():
            errors.extend(validate_state(state, known_tags))

        for event_id, event in self._events.items():
            errors.extend(validate_event(event, namespace=self._event_namespaces.get(event_id), known_tags=known_tags))

        for idea in self._ideas.values():
            errors.extend(validate_idea(idea))

        all_focus_ids: set[str] = set()
        for tree in self._focus_trees.values():
            all_focus_ids.update(f.id for f in tree.focuses)

        known_state_ids = set(self.list_states())
        if self.hoi4_install:
            known_state_ids.update(entry["id"] for entry in build_state_index(self.hoi4_install / "history" / "states"))

        for tree in self._focus_trees.values():
            tree_errors = validate_focus_tree(
                tree,
                known_focus_ids=all_focus_ids,
                known_state_ids=known_state_ids if known_state_ids else None,
                known_tags=known_tags,
            )
            errors.extend(tree_errors)

        for tree in self._focus_trees.values():
            for focus in tree.focuses:
                loc_key = f"{focus.id}:0"
                if loc_key not in self._loc_entries and focus.id not in self._loc_entries:
                    errors.append(ValidationError(
                        message=f"No localization found for focus '{focus.id}'",
                        severity="warning",
                        focus_id=focus.id,
                        file_path=str(tree.path) if tree.path else None,
                    ))
                desc_key = f"{focus.id}_desc"
                desc_colon_key = f"{focus.id}_desc:0"
                if desc_key not in self._loc_entries and desc_colon_key not in self._loc_entries:
                    errors.append(ValidationError(
                        message=f"No description localization found for focus '{focus.id}'",
                        severity="warning",
                        focus_id=focus.id,
                        file_path=str(tree.path) if tree.path else None,
                    ))

        return _filter_validation_errors(errors, suppress_warnings=suppress_warnings)

    # ── Preview & Save ───────────────────────────────────────────

    def preview(self) -> str:
        diffs: list[str] = []

        if "countries" in self._dirty:
            for tag in self._dirty_countries:
                country = self._countries.get(tag)
                if country is None:
                    continue
                for path, current in serialize_country_files(self.mod_root, country).items():
                    rel = str(path.relative_to(self.mod_root)) if path.is_relative_to(self.mod_root) else str(path)
                    original = self._original_files.get(path, "")
                    d = unified_diff(original, current, rel)
                    if d:
                        diffs.append(d)

        if "states" in self._dirty:
            for sid in self._dirty_states:
                state = self._states.get(sid)
                if state is None:
                    continue
                current = serialize_state(state)
                path = state.path or self.mod_root / "history" / "states" / f"{sid}-STATE.txt"
                rel = str(path.relative_to(self.mod_root)) if path.is_relative_to(self.mod_root) else str(path)
                original = self._original_files.get(path, "")
                d = unified_diff(original, current, rel)
                if d:
                    diffs.append(d)

        if "events" in self._dirty:
            file_events, file_ns = self._group_events_by_file(dirty_only=True)
            for path, events in file_events.items():
                rel = str(path.relative_to(self.mod_root)) if path.is_relative_to(self.mod_root) else str(path)
                current = serialize_events_file(file_ns.get(path), events)
                original = self._original_files.get(path, "")
                d = unified_diff(original, current, rel)
                if d:
                    diffs.append(d)

        if "decisions" in self._dirty:
            for path, categories in self._group_decisions_by_file(dirty_only=True).items():
                rel = str(path.relative_to(self.mod_root)) if path.is_relative_to(self.mod_root) else str(path)
                current = serialize_decisions_file(categories)
                original = self._original_files.get(path, "")
                d = unified_diff(original, current, rel)
                if d:
                    diffs.append(d)

        if "ideas" in self._dirty:
            file_ideas = self._group_ideas_by_file(dirty_only=True)
            for path, ideas in file_ideas.items():
                rel = str(path.relative_to(self.mod_root)) if path.is_relative_to(self.mod_root) else str(path)
                container = self._idea_file_containers.get(path)
                if container is None:
                    container = "ideas" if path.parent.name == "ideas" else "country_ideas"
                current = serialize_ideas_file(ideas, container_name=container)
                original = self._original_files.get(path, "")
                d = unified_diff(original, current, rel)
                if d:
                    diffs.append(d)

        if "focus" in self._dirty:
            for tree_id in self._dirty_focus_trees:
                tree = self._focus_trees.get(tree_id)
                if tree is None:
                    continue
                current = serialize_focus_tree(tree)
                path = tree.path or self.mod_root / "common" / "national_focus" / f"{tree.country_tag}_focus.txt"
                rel = str(path.relative_to(self.mod_root)) if path.is_relative_to(self.mod_root) else str(path)
                original = self._original_files.get(path, "")
                d = unified_diff(original, current, rel)
                if d:
                    diffs.append(d)

        if "localization" in self._dirty:
            file_entries = self._group_loc_by_file(dirty_only=True)
            for path, entries in file_entries.items():
                rel = str(path.relative_to(self.mod_root)) if path.is_relative_to(self.mod_root) else str(path)
                current = serialize_localization_file(entries)
                original = self._original_files.get(path, "")
                d = unified_diff(original, current, rel)
                if d:
                    diffs.append(d)

        return "\n".join(diffs)

    def save(self, require_changes: bool = False) -> SaveResult:
        if not self._dirty:
            message = "No changes written: save() was called with no dirty changes"
            warnings.warn(message, RuntimeWarning, stacklevel=2)
            if require_changes:
                raise RuntimeError(message)
            return SaveResult(written_files=[], dirty_sections=[], no_changes=True, message=message)

        dirty_sections = sorted(self._dirty)
        written_files: list[Path] = []

        if "countries" in self._dirty:
            for tag in self._dirty_countries:
                country = self._countries.get(tag)
                if country is None:
                    continue
                write_all_country_files(self.mod_root, country)
                self._sync_country_loc(country)
                for path, content in serialize_country_files(self.mod_root, country).items():
                    self._original_files[path] = content
                    written_files.append(path)

        if "states" in self._dirty:
            for sid in self._dirty_states:
                state = self._states.get(sid)
                if state is None:
                    continue
                path = write_state(self.mod_root, state)
                state.path = path
                self._original_files[path] = serialize_state(state)
                written_files.append(path)

        if "events" in self._dirty:
            file_events, file_ns = self._group_events_by_file(dirty_only=True)
            for path, events in file_events.items():
                write_events_file(path, file_ns.get(path), events)
                self._original_files[path] = serialize_events_file(file_ns.get(path), events)
                written_files.append(path)

        if "decisions" in self._dirty:
            for path, categories in self._group_decisions_by_file(dirty_only=True).items():
                write_decisions_file(path, categories)
                self._original_files[path] = serialize_decisions_file(categories)
                written_files.append(path)

        if "ideas" in self._dirty:
            file_ideas = self._group_ideas_by_file(dirty_only=True)
            for path, ideas in file_ideas.items():
                container = self._idea_file_containers.get(path)
                if container is None:
                    container = "ideas" if path.parent.name == "ideas" else "country_ideas"
                write_ideas_file(path, ideas, container_name=container)
                self._original_files[path] = serialize_ideas_file(ideas, container_name=container)
                written_files.append(path)

        if "focus" in self._dirty:
            for tree_id in self._dirty_focus_trees:
                tree = self._focus_trees.get(tree_id)
                if tree is None:
                    continue
                path = write_focus_tree(tree, self.mod_root)
                tree.path = path
                self._original_files[path] = serialize_focus_tree(tree)
                written_files.append(path)

        if "localization" in self._dirty:
            file_entries = self._group_loc_by_file(dirty_only=True)
            for path, entries in file_entries.items():
                write_localization_file(path, entries)
                self._original_files[path] = serialize_localization_file(entries)
                written_files.append(path)

        self._dirty.clear()
        self._dirty_loc_keys.clear()
        self._dirty_focus_trees.clear()
        self._dirty_countries.clear()
        self._dirty_states.clear()
        self._dirty_events.clear()
        self._dirty_decision_categories.clear()
        self._dirty_ideas.clear()

        result = SaveResult(
            written_files=sorted(set(written_files)),
            dirty_sections=dirty_sections,
            no_changes=not written_files,
            message=(
                "No files written after processing dirty sections"
                if not written_files
                else f"Saved {len(set(written_files))} file(s)"
            ),
        )
        if require_changes and result.no_changes:
            raise RuntimeError(result.message)
        return result

    @contextmanager
    def transaction(self, *, save: bool = False):
        """Temporarily mutate the mod, then save or restore in-memory state.

        Use ``with mod.transaction():`` for dry runs that call ``preview()`` inside
        the block. On normal exit the transaction is discarded unless
        ``save=True`` is passed. On exceptions, in-memory state is restored.
        """
        snapshot = self._snapshot()
        try:
            yield self
            if save:
                self.save()
            else:
                self._restore(snapshot)
        except Exception:
            self._restore(snapshot)
            raise

    def discard(self) -> None:
        self._countries.clear()
        self._states.clear()
        self._state_ids.clear()
        self._events.clear()
        self._event_namespaces.clear()
        self._decisions.clear()
        self._decision_categories.clear()
        self._ideas.clear()
        self._focus_trees.clear()
        self._dirty_focus_trees.clear()
        self._dirty_countries.clear()
        self._dirty_states.clear()
        self._dirty_events.clear()
        self._dirty_decision_categories.clear()
        self._dirty_ideas.clear()
        self._loc_entries.clear()
        self._loc_sources.clear()
        self._dirty_loc_keys.clear()
        self._original_files.clear()
        self._dirty.clear()
        self._load()

    def _snapshot(self) -> dict[str, object]:
        keys = [
            "_focus_trees",
            "_dirty_focus_trees",
            "_countries",
            "_dirty_countries",
            "_states",
            "_state_ids",
            "_dirty_states",
            "_events",
            "_event_namespaces",
            "_dirty_events",
            "_decisions",
            "_decision_categories",
            "_dirty_decision_categories",
            "_ideas",
            "_dirty_ideas",
            "_idea_file_containers",
            "_cached_idea_file",
            "_loc_entries",
            "_loc_sources",
            "_dirty_loc_keys",
            "_original_files",
            "_dirty",
        ]
        return {key: copy.deepcopy(getattr(self, key)) for key in keys}

    def _restore(self, snapshot: dict[str, object]) -> None:
        for key, value in snapshot.items():
            setattr(self, key, value)

    def _sync_country_loc(self, country: Country) -> None:
        name = country.name or country.tag
        adj = country.adjective or name
        dirty_keys: list[str] = []
        for suffix in ["", "_neutrality", "_democratic", "_fascism", "_communism"]:
            k = f"{country.tag}{suffix}"
            self._loc_entries[k] = name
            self._loc_entries[f"{k}_DEF"] = name
            dirty_keys.extend([k, f"{k}_DEF"])
        self._loc_entries[f"{country.tag}_ADJ"] = adj
        dirty_keys.append(f"{country.tag}_ADJ")
        if country.leader:
            self._loc_entries[country.leader.character_id] = country.leader.name
            self._loc_entries[f"{country.leader.character_id}_desc"] = f"{country.leader.name} (leader)"
            dirty_keys.extend([country.leader.character_id, f"{country.leader.character_id}_desc"])
        self._dirty_loc_keys.update(dirty_keys)
        self._dirty.add("localization")

    def _group_loc_by_file(self, dirty_only: bool = False) -> dict[Path, dict[str, str]]:
        dirty_files: set[Path] = set()
        if dirty_only:
            for key in self._dirty_loc_keys:
                source = self._loc_sources.get(key, self.default_loc_file)
                dirty_files.add(source)

        file_entries: dict[Path, dict[str, str]] = {}
        for key, value in self._loc_entries.items():
            source = self._loc_sources.get(key, self.default_loc_file)
            if dirty_only and source not in dirty_files:
                continue
            if source not in file_entries:
                file_entries[source] = {}
            file_entries[source][key] = value
        return file_entries

    def _group_events_by_file(self, dirty_only: bool = False) -> tuple[dict[Path, list[Event]], dict[Path, Optional[str]]]:
        dirty_ids = self._dirty_events if dirty_only else None
        file_events: dict[Path, list[Event]] = {}
        file_ns: dict[Path, Optional[str]] = {}
        dirty_files: set[Path] = set()
        if dirty_only:
            for eid in dirty_ids:
                event = self._events.get(eid)
                if event is None:
                    continue
                p = event.path or self.mod_root / "events" / f"{self._event_namespaces.get(eid) or 'mod'}_events.txt"
                dirty_files.add(p)
        for eid, event in self._events.items():
            p = event.path or self.mod_root / "events" / f"{self._event_namespaces.get(eid) or 'mod'}_events.txt"
            if dirty_only and p not in dirty_files:
                continue
            if p not in file_events:
                file_events[p] = []
                file_ns[p] = self._event_namespaces.get(eid)
            file_events[p].append(event)
        return file_events, file_ns

    def _group_decisions_by_file(self, dirty_only: bool = False) -> dict[Path, list[DecisionCategory]]:
        dirty_categories = self._dirty_decision_categories if dirty_only else None
        file_categories: dict[Path, list[DecisionCategory]] = {}
        dirty_files: set[Path] = set()
        if dirty_only:
            for category_id in dirty_categories:
                category = self._decision_categories.get(category_id)
                if category is None:
                    continue
                dirty_files.add(category.path or self.mod_root / "common" / "decisions" / "mod_decisions.txt")
        for category in self._decision_categories.values():
            p = category.path or self.mod_root / "common" / "decisions" / "mod_decisions.txt"
            if dirty_only and p not in dirty_files:
                continue
            if p not in file_categories:
                file_categories[p] = []
            file_categories[p].append(category)
        return file_categories

    def _group_ideas_by_file(self, dirty_only: bool = False) -> dict[Path, list[Idea]]:
        dirty_ids = self._dirty_ideas if dirty_only else None
        file_ideas: dict[Path, list[Idea]] = {}
        dirty_files: set[Path] = set()
        if dirty_only:
            for iid in dirty_ids:
                idea = self._ideas.get(iid)
                if idea is None:
                    continue
                dirty_files.add(idea.path or self.mod_root / "common" / "national_ideas" / "mod_ideas.txt")
        for idea in self._ideas.values():
            p = idea.path or self.mod_root / "common" / "national_ideas" / "mod_ideas.txt"
            if dirty_only and p not in dirty_files:
                continue
            if p not in file_ideas:
                file_ideas[p] = []
            file_ideas[p].append(idea)
        return file_ideas
