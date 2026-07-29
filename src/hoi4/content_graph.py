"""Semantic liveness analysis for authored HOI4 content."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable

from .types import ValidationError

if TYPE_CHECKING:
    from .mod import Mod


_EVENT_CALL_RE = re.compile(
    r"\b(?:country_event|state_event|news_event|unit_leader_event|"
    r"operative_leader_event)\s*=\s*(?:\{[^{}]*?\bid\s*=\s*)?"
    r'"?([A-Za-z0-9_.:-]+)"?',
    re.DOTALL,
)
_IDEA_SCALAR_RE = re.compile(
    r"\b(?:add_ideas|add_timed_idea|swap_ideas)\s*=\s*"
    r'"?([A-Za-z0-9_.:-]+)"?'
)
_IDEA_BLOCK_RE = re.compile(r"\badd_ideas\s*=\s*\{([^{}]*)\}", re.DOTALL)
_SWAP_IDEA_RE = re.compile(
    r"\bswap_ideas\s*=\s*\{[^{}]*?\badd_idea\s*=\s*"
    r'"?([A-Za-z0-9_.:-]+)"?',
    re.DOTALL,
)
_TIMED_IDEA_RE = re.compile(
    r"\badd_timed_idea\s*=\s*\{[^{}]*?\bidea\s*=\s*"
    r'"?([A-Za-z0-9_.:-]+)"?',
    re.DOTALL,
)
_FLAG_RE = re.compile(
    r"\b((?:set|clr|modify|set_timed|has)_"
    r"(?:country|global|state|character|unit_leader)_flag)\s*=\s*"
    r'(?:\{[^{}]*?\bflag\s*=\s*)?"?([A-Za-z0-9_.:-]+)"?',
    re.DOTALL,
)
_BARE_TOKEN_RE = re.compile(r"[A-Za-z0-9_.:-]+")


@dataclass(frozen=True)
class ContentLivenessReport:
    """Immutable semantic reachability and use report."""

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

    @property
    def clean(self) -> bool:
        return not self.findings

    def to_dict(self) -> dict[str, object]:
        return {
            "clean": self.clean,
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
    mod: Mod,
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

    scripts = _all_script_text(mod)
    event_calls = set(_EVENT_CALL_RE.findall(scripts))
    autonomous_events = {
        event.id for event in mod._events.values() if not event.is_triggered_only
    }
    fired_events = (event_calls | autonomous_events) & set(mod._events)
    unfired_events = {
        event.id
        for event in mod._events.values()
        if event.is_triggered_only and event.id not in event_calls
    }

    grants = {
        idea_id
        for country in mod._countries.values()
        for idea_id in country.ideas
    }
    grants.update(_IDEA_SCALAR_RE.findall(scripts))
    grants.update(_SWAP_IDEA_RE.findall(scripts))
    grants.update(_TIMED_IDEA_RE.findall(scripts))
    for block in _IDEA_BLOCK_RE.findall(scripts):
        grants.update(_BARE_TOKEN_RE.findall(block))
    country_ideas = {
        idea.id for idea in mod._ideas.values() if idea.category == "country"
    }
    granted_ideas = grants & country_ideas
    ungranted_ideas = country_ideas - grants

    written: set[str] = set()
    read: set[str] = set()
    for operation, flag in _FLAG_RE.findall(scripts):
        if operation.startswith("has_"):
            read.add(flag)
        else:
            written.add(flag)
    allowed_flags = tuple(flag_allowlist)
    set_only = {
        flag
        for flag in written - read
        if not _matches(flag, allowed_flags)
    }
    read_only = {
        flag
        for flag in read - written
        if not _matches(flag, allowed_flags)
    }

    localization_keys = {
        key.split(":", 1)[0] for key in mod._loc_entries
    }
    used_localization = _known_localization_uses(mod)
    for key in localization_keys:
        if re.search(rf"(?<![A-Za-z0-9_.:-]){re.escape(key)}(?![A-Za-z0-9_.:-])", scripts):
            used_localization.add(key)
    allowed_localization = tuple(localization_allowlist)
    unused_localization = {
        key
        for key in localization_keys - used_localization
        if not _matches(key, allowed_localization)
    }

    findings: list[ValidationError] = []
    for focus_id in sorted(unreachable):
        focus, path = all_focuses[focus_id]
        findings.append(
            ValidationError(
                message=(
                    f"Focus '{focus_id}' is unreachable from every root focus. "
                    "Check prerequisite groups and mutually exclusive branches."
                ),
                severity="warning",
                code="unreachable_focus",
                focus_id=focus_id,
                file_path=str(path) if path is not None else None,
            )
        )
    for event_id in sorted(unfired_events):
        event = mod._events[event_id]
        findings.append(
            ValidationError(
                message=(
                    f"Triggered-only event '{event_id}' has no incoming event, "
                    "on-action, focus, decision, or other modeled script reference."
                ),
                severity="warning",
                code="unfired_event",
                event_id=event_id,
                file_path=str(event.path) if event.path is not None else None,
            )
        )
    for idea_id in sorted(ungranted_ideas):
        idea = mod._ideas[idea_id]
        findings.append(
            ValidationError(
                message=(
                    f"Country idea '{idea_id}' is never granted by country history "
                    "or modeled effect script."
                ),
                severity="warning",
                code="ungranted_idea",
                idea_id=idea_id,
                file_path=str(idea.path) if idea.path is not None else None,
            )
        )
    for flag in sorted(set_only):
        findings.append(
            ValidationError(
                message=f"Flag '{flag}' is written but never read.",
                severity="warning",
                code="flag_set_never_read",
            )
        )
    for flag in sorted(read_only):
        findings.append(
            ValidationError(
                message=f"Flag '{flag}' is read but never written by this mod.",
                severity="warning",
                code="flag_read_never_set",
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


def _all_script_text(mod: Mod) -> str:
    flattened = [
        source.text
        for source in mod._semantic_script_sources()
        if source.kind != "modifier"
    ]
    for idea in mod._ideas.values():
        flattened.extend((idea.allowed, idea.ai_will_do))
    for country in mod._countries.values():
        flattened.append(country.raw_history)
    for modifier in mod._dynamic_modifiers.values():
        flattened.extend((modifier.enable, modifier.remove_trigger))
    for bookmark in mod._bookmarks:
        flattened.append(bookmark.effect)
    return "\n".join(flattened)


def _known_localization_uses(mod: Mod) -> set[str]:
    result: set[str] = set()
    for tree in mod._focus_trees.values():
        for focus in tree.focuses:
            result.update((focus.id, f"{focus.id}_desc"))
    for event in mod._events.values():
        result.update((event.title, event.description))
        result.update(option.name for option in event.options)
    for idea in mod._ideas.values():
        result.update((idea.id, f"{idea.id}_desc"))
    for decision in mod._decisions.values():
        result.update((decision.id, f"{decision.id}_desc"))
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
