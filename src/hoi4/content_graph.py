"""Semantic liveness analysis for authored HOI4 content."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Protocol

from .localisation import parse_localization_dir
from .script_references import (
    COUNTRY,
    Definitions,
    FileReferences,
    event_key,
    fragment_owner,
    name_pattern,
)
from .syntax import Scalar, parse
from .types import ValidationError

if TYPE_CHECKING:
    from .bookmarks import Bookmark
    from .characters import Character
    from .dynamic_modifiers import DynamicModifier
    from .ideologies import Ideology
    from .layers import ContentLayers
    from .script_vocabulary import ScriptSource
    from .types import Country, Decision, DecisionCategory, Event, FocusTree, Idea


class LivenessSource(Protocol):
    """What the liveness analysis reads from a loaded mod. ``Mod`` satisfies it."""

    mod_root: Path
    hoi4_install: Path | None
    _content_layers: ContentLayers | None
    _scan_cache: dict[str, set[str]]
    _vanilla_tags: set[str]
    _loc_entries: dict[str, str]
    _loc_sources: dict[str, Path]
    _dirty_loc_keys: set[str]
    _countries: dict[str, Country]
    _characters: dict[str, Character]
    _focus_trees: dict[str, FocusTree]
    _events: dict[str, Event]
    _ideas: dict[str, Idea]
    _ideologies: dict[str, Ideology]
    _dynamic_modifiers: dict[str, DynamicModifier]
    _decisions: dict[str, Decision]
    _decision_categories: dict[str, DecisionCategory]
    _bookmarks: list[Bookmark]

    def _semantic_script_sources(self) -> list[ScriptSource]: ...

    def _reference_corpus(self, *, inherited: bool = True) -> list[FileReferences]: ...

    def _localisation_value_names(self) -> set[str]: ...


_BARE_TOKEN_RE = re.compile(r"[A-Za-z0-9_.:-]+")


def _script_tokens(fragments: Iterable[str]) -> set[str]:
    """Every key and value of some script, read from its syntax tree.

    Comments never count. A quoted value also counts word by word, so a key
    named inside a quoted tooltip text is found.
    """

    tokens: set[str] = set()

    def add_value(value: str) -> None:
        tokens.add(value)
        if not _BARE_TOKEN_RE.fullmatch(value):
            tokens.update(_BARE_TOKEN_RE.findall(value))

    for fragment in fragments:
        if not fragment.strip():
            continue
        root = parse(fragment).root
        for item in root.items:
            if type(item) is Scalar:
                add_value(item.value)
        for assignment, _ in root.walk():
            tokens.add(assignment.key.value)
            value = assignment.value
            if type(value) is Scalar:
                add_value(value.value)
            elif assignment.block is not None:
                for entry in assignment.block.items:
                    if type(entry) is Scalar:
                        add_value(entry.value)
    return tokens


def _localization_keys_in_script(keys: Iterable[str], tokens: set[str]) -> set[str]:
    """The localization keys that appear as a key or value in script."""

    return {key for key in keys if key in tokens}


@dataclass(frozen=True)
class ContentLivenessReport:
    """Immutable structural reachability and use report.

    This graph follows references and prerequisite topology. It does not solve
    dynamic trigger arithmetic, mutually dependent thresholds, or player-state
    achievability.
    """

    reachable_focuses: tuple[str, ...]
    unreachable_focuses: tuple[str, ...]
    fired_events: tuple[str, ...]
    unfired_events: tuple[str, ...]
    granted_ideas: tuple[str, ...]
    ungranted_ideas: tuple[str, ...]
    flags_written: tuple[str, ...]
    flags_read: tuple[str, ...]
    flags_set_only: tuple[str, ...]
    flags_read_only: tuple[str, ...]
    used_localization: tuple[str, ...]
    unused_localization: tuple[str, ...]
    findings: tuple[ValidationError, ...]
    analysis_scope: str = "structural"
    proves_dynamic_achievability: bool = False

    @property
    def clean(self) -> bool:
        return not self.findings

    def to_dict(self) -> dict[str, object]:
        return {
            "clean": self.clean,
            "analysis_scope": self.analysis_scope,
            "proves_dynamic_achievability": self.proves_dynamic_achievability,
            "reachable_focuses": list(self.reachable_focuses),
            "unreachable_focuses": list(self.unreachable_focuses),
            "fired_events": list(self.fired_events),
            "unfired_events": list(self.unfired_events),
            "granted_ideas": list(self.granted_ideas),
            "ungranted_ideas": list(self.ungranted_ideas),
            "flags_written": list(self.flags_written),
            "flags_read": list(self.flags_read),
            "flags_set_only": list(self.flags_set_only),
            "flags_read_only": list(self.flags_read_only),
            "used_localization": list(self.used_localization),
            "unused_localization": list(self.unused_localization),
            "findings": [
                {
                    "code": finding.code,
                    "severity": finding.severity,
                    "message": finding.message,
                    "file_path": finding.file_path,
                    "focus_id": finding.focus_id,
                    "event_id": finding.event_id,
                    "idea_id": finding.idea_id,
                }
                for finding in self.findings
            ],
        }


def analyze_content_liveness(
    mod: LivenessSource,
    *,
    flag_allowlist: Iterable[str] = (),
    localization_allowlist: Iterable[str] = (),
) -> ContentLivenessReport:
    """Analyze reachability and use across the mod's semantic content graph."""

    all_focuses = {
        focus.id: (focus, tree.path)
        for tree in mod._focus_trees.values()
        for focus in tree.focuses
    }
    reachable = {
        focus_id
        for focus_id, (focus, _) in all_focuses.items()
        if not focus.prerequisites
    }
    changed = True
    while changed:
        changed = False
        for focus_id, (focus, _) in all_focuses.items():
            if focus_id in reachable:
                continue
            if all(
                any(parent not in all_focuses or parent in reachable for parent in group)
                for group in focus.prerequisites
            ):
                reachable.add(focus_id)
                changed = True
    unreachable = set(all_focuses) - reachable

    corpus = mod._reference_corpus() + _country_history_references(mod)
    defined_at = _definition_sites(corpus)

    # Events: fired by an effect, an on-action or a border war anywhere in the
    # mod or the game. A name the game builds while running is a pattern. The
    # game matches an event's namespace without regard to case.
    event_calls, event_patterns = _referenced(corpus, "event", {"fire"})
    called = {event_key(event_id) for event_id in event_calls}
    folded_patterns = tuple(event_key(pattern) for pattern in event_patterns)
    fired_events = {
        event.id
        for event in mod._events.values()
        if not event.is_triggered_only
        or event_key(event.id) in called
        or _matches(event_key(event.id), folded_patterns)
    }
    unfired_events = {
        event.id
        for event in mod._events.values()
        if event.is_triggered_only and event.id not in fired_events
    }

    # Ideas: granted by an effect or country history, or shown on purpose
    # (show_ideas_tooltip, a bookmark's idea list).
    grants, grant_patterns = _referenced(corpus, "idea", {"add", "show"})
    grants.update(idea_id for country in mod._countries.values() for idea_id in country.ideas)
    country_ideas = {
        idea.id for idea in mod._ideas.values() if idea.category == "country"
    }
    granted_ideas = {
        idea_id for idea_id in country_ideas
        if idea_id in grants or _matches(idea_id, grant_patterns)
    }
    ungranted_ideas = country_ideas - granted_ideas

    # Flags: the mod's own writes and reads, checked against every file.
    written, written_patterns = _referenced(corpus, "flag", {"set", "clear"})
    read, read_patterns = _referenced(corpus, "flag", {"read"})
    # A localisation text reads a flag too: "[?TAG_flag:days_left] days".
    read |= mod._localisation_value_names()
    own_written, _ = _referenced(corpus, "flag", {"set", "clear"}, own_only=True)
    own_read, _ = _referenced(corpus, "flag", {"read"}, own_only=True)
    allowed_flags = tuple(flag_allowlist)
    set_only = {
        flag
        for flag in own_written - read
        if not _matches(flag, read_patterns) and not _matches(flag, allowed_flags)
    }
    read_only = {
        flag
        for flag in own_read - written
        if not _matches(flag, written_patterns) and not _matches(flag, allowed_flags)
    }
    written, read = own_written, own_read

    localization_keys = {key.split(":", 1)[0] for key in mod._loc_entries}
    authored_localization = _authored_localization_keys(mod)
    used_localization = _known_localization_uses(mod)
    used_localization.update(
        _localization_keys_in_script(localization_keys, _script_tokens(_script_fragments(mod)))
    )
    allowed_localization = tuple(localization_allowlist)
    unused_localization = {
        key
        for key in authored_localization - used_localization
        if not _matches(key, allowed_localization)
    }

    write_sites = _first_sites(corpus, "flag", {"set", "clear"})
    read_sites = _first_sites(corpus, "flag", {"read"})

    def site(kind: str, name: str, fallback: Path | None) -> tuple[str | None, int | None]:
        found = defined_at.get((kind, name))
        if found is not None:
            return found
        return (str(fallback) if fallback is not None else None), None

    findings: list[ValidationError] = []
    for focus_id in sorted(unreachable):
        focus, path = all_focuses[focus_id]
        file_path, line = site("focus", focus_id, path)
        findings.append(
            ValidationError(
                message=(
                    f"Focus '{focus_id}' is unreachable from every root focus. "
                    "Check prerequisite groups and mutually exclusive branches."
                ),
                severity="warning",
                code="unreachable_focus",
                focus_id=focus_id,
                file_path=file_path,
                line=line,
            )
        )
    for event_id in sorted(unfired_events):
        file_path, line = site("event", event_id, mod._events[event_id].path)
        findings.append(
            ValidationError(
                message=(
                    f"Triggered-only event '{event_id}' is never fired: no effect, "
                    "on-action or border war in the mod or the game names it."
                ),
                severity="warning",
                code="unfired_event",
                event_id=event_id,
                file_path=file_path,
                line=line,
            )
        )
    for idea_id in sorted(ungranted_ideas):
        file_path, line = site("idea", idea_id, mod._ideas[idea_id].path)
        findings.append(
            ValidationError(
                message=(
                    f"Country idea '{idea_id}' is never granted: no country history "
                    "or effect in the mod or the game adds it, and nothing shows it."
                ),
                severity="warning",
                code="ungranted_idea",
                idea_id=idea_id,
                file_path=file_path,
                line=line,
            )
        )
    for flag in sorted(set_only):
        file_path, line = write_sites.get(flag, (None, None))
        findings.append(
            ValidationError(
                message=f"Flag '{flag}' is written but never read.",
                severity="warning",
                code="flag_set_never_read",
                file_path=file_path,
                line=line,
            )
        )
    for flag in sorted(read_only):
        file_path, line = read_sites.get(flag, (None, None))
        findings.append(
            ValidationError(
                message=f"Flag '{flag}' is read but never written by the mod or the game.",
                severity="warning",
                code="flag_read_never_set",
                file_path=file_path,
                line=line,
            )
        )
    for key in sorted(unused_localization):
        findings.append(
            ValidationError(
                message=f"Localization key '{key}' is not referenced by modeled content.",
                severity="warning",
                code="unused_localization",
                file_path=(
                    str(mod._loc_sources[key])
                    if key in mod._loc_sources
                    else None
                ),
            )
        )

    return ContentLivenessReport(
        reachable_focuses=tuple(sorted(reachable)),
        unreachable_focuses=tuple(sorted(unreachable)),
        fired_events=tuple(sorted(fired_events)),
        unfired_events=tuple(sorted(unfired_events)),
        granted_ideas=tuple(sorted(granted_ideas)),
        ungranted_ideas=tuple(sorted(ungranted_ideas)),
        flags_written=tuple(sorted(written)),
        flags_read=tuple(sorted(read)),
        flags_set_only=tuple(sorted(set_only)),
        flags_read_only=tuple(sorted(read_only)),
        used_localization=tuple(sorted(used_localization)),
        unused_localization=tuple(sorted(unused_localization)),
        findings=tuple(findings),
    )


def _script_fragments(mod: LivenessSource) -> list[str]:
    """The script of the mod, for the localization use check."""

    fragments = [
        source.text
        for source in mod._semantic_script_sources()
        if source.kind != "modifier"
    ]
    for country in mod._countries.values():
        fragments.append(country.raw_history)
    return fragments


def _country_history_references(mod: LivenessSource) -> list[FileReferences]:
    """The in-memory history of the mod's countries, read like a history file.

    A country's history is only written back on save, and not every field of
    ``raw_history`` is kept by the writer, so it is read from the model too.
    """

    found: list[FileReferences] = []
    for country in mod._countries.values():
        if not country.raw_history.strip():
            continue
        owner = fragment_owner(country.raw_history, "country_history", country.tag, COUNTRY)
        path = country.history_path or mod.mod_root / "history" / "countries" / f"{country.tag}.txt"
        found.append(
            FileReferences(
                str(path),
                f"history/countries/{country.tag}.txt",
                True,
                [owner],
                Definitions(),
            )
        )
    return found


def _referenced(
    corpus: list[FileReferences],
    kind: str,
    actions: set[str],
    *,
    own_only: bool = False,
) -> tuple[set[str], tuple[str, ...]]:
    """Names of one kind the script uses, and patterns for built names.

    ``set_country_flag = PRC_infiltration_[ROOT.GetTag]`` gives the pattern
    ``PRC_infiltration_*``. A name read from a variable (``var:x``) could be
    anything, so it gives ``*``.
    """

    names: set[str] = set()
    patterns: set[str] = set()
    for found in corpus:
        if own_only and not found.own:
            continue
        for _, ref in found.refs():
            if ref.kind != kind or ref.action not in actions:
                continue
            if not ref.dynamic:
                names.add(ref.value)
            elif ":" in ref.value:
                patterns.add("*")
            else:
                patterns.add(name_pattern(ref.value))
    return names, tuple(sorted(patterns))


def _first_sites(
    corpus: list[FileReferences], kind: str, actions: set[str]
) -> dict[str, tuple[str | None, int | None]]:
    """Where the mod first uses each name: ``{name: (file, line)}``."""

    sites: dict[str, tuple[str | None, int | None]] = {}
    for found in corpus:
        if not found.own:
            continue
        for _, ref in found.refs():
            if ref.kind == kind and ref.action in actions and not ref.dynamic:
                sites.setdefault(ref.value, (found.path, ref.line))
    return sites


def _definition_sites(
    corpus: list[FileReferences],
) -> dict[tuple[str, str], tuple[str | None, int | None]]:
    """Where the mod defines each focus, event and idea."""

    sites: dict[tuple[str, str], tuple[str | None, int | None]] = {}
    for found in corpus:
        if not found.own:
            continue
        for owner in found.owners:
            if owner.kind == "focus":
                sites.setdefault(("focus", owner.id), (found.path, owner.line))
        for event_id, (_, line) in found.defines.events.items():
            sites.setdefault(("event", event_id), (found.path, line))
        for idea_id, (_, line) in found.defines.ideas.items():
            sites.setdefault(("idea", idea_id), (found.path, line))
    return sites


def _authored_localization_keys(mod: LivenessSource) -> set[str]:
    """Keep inherited lookup vocabulary outside the authored-content audit.

    Layered localization sources are retargeted to prospective top-mod paths,
    so ``_loc_sources`` alone cannot distinguish fallback data from authored
    entries. Read the actual top-mod files and include pending key mutations.
    Intersect with live entries so deletions are not reported as unused.
    """
    if mod._content_layers is None:
        return {key.split(":", 1)[0] for key in mod._loc_entries}
    entries, _ = parse_localization_dir(mod.mod_root / "localisation" / "english")
    authored = set(entries) | mod._dirty_loc_keys
    return {key.split(":", 1)[0] for key in authored if key in mod._loc_entries}


def _known_localization_uses(mod: LivenessSource) -> set[str]:
    result: set[str] = set()
    for tree in mod._focus_trees.values():
        for focus in tree.focuses:
            result.update((focus.id, f"{focus.id}_desc"))
    for event in mod._events.values():
        result.update((event.title, event.description))
        result.update(e.text for e in (*event.conditional_titles, *event.conditional_descriptions))
        result.update(option.name for option in event.options)
        result.update(e.text for option in event.options for e in option.conditional_names)
    for idea in mod._ideas.values():
        result.update((idea.id, f"{idea.id}_desc"))
    for decision in mod._decisions.values():
        result.update((decision.id, f"{decision.id}_desc"))
        if decision.custom_cost_text:
            result.add(decision.custom_cost_text)
    for category in mod._decision_categories.values():
        result.add(category.id)
    for character in mod._characters.values():
        result.update((character.id, f"{character.id}_desc"))
    for country in mod._countries.values():
        result.update(
            {
                country.tag,
                f"{country.tag}_DEF",
                f"{country.tag}_ADJ",
            }
        )
    for bookmark in mod._bookmarks:
        result.update((bookmark.name, bookmark.description))
        result.update(country.history for country in bookmark.countries)
    for ideology in mod._ideologies.values():
        result.update((ideology.id, f"{ideology.id}_desc"))
        for subtype in ideology.types:
            result.update((subtype.name, f"{subtype.name}_desc"))
    known_tags = set(mod._vanilla_tags) | set(mod._countries)
    for key in mod._loc_entries:
        normalized = key.split(":", 1)[0]
        prefix = normalized.split("_", 1)[0]
        if prefix in known_tags:
            result.add(normalized)
        if normalized.startswith(("FACTION_NAME_", "STATE_", "VICTORY_POINTS_")):
            result.add(normalized)
    return {key for key in result if key}


def _matches(value: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(value, pattern) for pattern in patterns)
