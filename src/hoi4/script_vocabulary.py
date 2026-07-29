"""Installed-game effect, trigger, and modifier vocabulary support."""

from __future__ import annotations

import fnmatch
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Literal, Mapping, cast

from .parser import ParseError
from .patching import top_level_assignments
from .types import ValidationError

ScriptTokenKind = Literal["effect", "trigger", "modifier"]

_HEADING_RE = re.compile(r"(?m)^## ([A-Za-z0-9_.:-]+)\s*$")
_ASSIGNMENT_RE = re.compile(r"(?m)^[ \t]*([A-Za-z0-9_.:@%-]+)[ \t]*(?:=|[<>!]=?)")
_SCOPE_RE = re.compile(
    r"^(?:[A-Z0-9]{3}|[0-9]+|ROOT|THIS|PREV(?:\.PREV)*|FROM(?:\.FROM)*|"
    r"OWNER|CONTROLLER|CAPITAL_SCOPE|owner|controller|capital_scope|overlord|"
    r"faction_leader|occupied_country|country|state|character|operative|"
    r"division|unit_leader|event_target:.+|global_event_target:.+|scope:.+|var:.+)$"
)
_CONTROL_BLOCKS = {
    "AND",
    "OR",
    "NOT",
    "NAND",
    "NOR",
    "if",
    "else",
    "else_if",
    "hidden_effect",
    "while",
    "for_loop_effect",
    "random_list",
}
_RECURSIVE_PREFIXES = (
    "all_",
    "any_",
    "every_",
    "random_",
    "ordered_",
)
_NESTED_EFFECT_BLOCKS = {
    "effect",
    "effects",
    "on_cancel",
    "on_complete",
    "on_failure",
    "on_lose",
    "on_success",
    "on_win",
}
_NESTED_TRIGGER_BLOCKS = {
    "allowed",
    "available",
    "trigger",
    "visible",
}


@dataclass(frozen=True)
class ScriptTokenInfo:
    """One token documented by the configured HOI4 installation."""

    name: str
    kind: ScriptTokenKind
    supported_scopes: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    usage_count: int = 0
    usage_by_domain: Mapping[str, int] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind,
            "supported_scopes": list(self.supported_scopes),
            "categories": list(self.categories),
            "usage_count": self.usage_count,
            "usage_by_domain": dict(self.usage_by_domain),
        }


@dataclass(frozen=True)
class GameScriptVocabulary:
    """Documented script tokens plus observed installed-game usage."""

    hoi4_install: Path
    effects: Mapping[str, ScriptTokenInfo]
    triggers: Mapping[str, ScriptTokenInfo]
    modifiers: Mapping[str, ScriptTokenInfo]

    def tokens(self, kind: ScriptTokenKind) -> Mapping[str, ScriptTokenInfo]:
        if kind == "effect":
            return self.effects
        if kind == "trigger":
            return self.triggers
        return self.modifiers

    def to_dict(self) -> dict[str, object]:
        return {
            "hoi4_install": str(self.hoi4_install),
            "effects": {
                key: value.to_dict() for key, value in self.effects.items()
            },
            "triggers": {
                key: value.to_dict() for key, value in self.triggers.items()
            },
            "modifiers": {
                key: value.to_dict() for key, value in self.modifiers.items()
            },
        }


@dataclass(frozen=True)
class ScriptSource:
    """A semantic script fragment emitted or loaded by the SDK."""

    text: str
    kind: ScriptTokenKind
    owner_kind: str = ""
    owner_id: str = ""
    file_path: str | None = None


@dataclass(frozen=True)
class _Command:
    name: str
    line: int
    kind: ScriptTokenKind


def load_game_script_vocabulary(hoi4_install: str | Path) -> GameScriptVocabulary:
    """Load documentation tokens and count their use in installed scripts."""

    root = Path(hoi4_install).resolve()
    documentation = root / "documentation"
    metadata: dict[
        ScriptTokenKind, dict[str, tuple[tuple[str, ...], tuple[str, ...]]]
    ] = {
        "effect": _documentation_entries(
            documentation / "effects_documentation.md"
        ),
        "trigger": _documentation_entries(
            documentation / "triggers_documentation.md"
        ),
        "modifier": _documentation_entries(
            documentation / "modifiers_documentation.md"
        ),
    }
    names = {kind: set(entries) for kind, entries in metadata.items()}
    all_names = set().union(*names.values())
    counts: Counter[str] = Counter()
    domains: dict[str, Counter[str]] = defaultdict(Counter)
    for top_level in ("common", "events", "history"):
        directory = root / top_level
        if not directory.is_dir():
            continue
        for path in directory.rglob("*.txt"):
            try:
                text = path.read_text(encoding="utf-8-sig", errors="ignore")
            except OSError:
                continue
            relative = path.relative_to(root)
            domain = "/".join(relative.parts[:2])
            for match in _ASSIGNMENT_RE.finditer(_without_comments(text)):
                token = match.group(1)
                if token not in all_names:
                    continue
                counts[token] += 1
                domains[token][domain] += 1

    def build(kind: ScriptTokenKind) -> Mapping[str, ScriptTokenInfo]:
        return MappingProxyType(
            {
                name: ScriptTokenInfo(
                    name=name,
                    kind=kind,
                    supported_scopes=metadata[kind][name][0],
                    categories=metadata[kind][name][1],
                    usage_count=counts[name],
                    usage_by_domain=MappingProxyType(dict(domains[name])),
                )
                for name in sorted(names[kind])
            }
        )

    return GameScriptVocabulary(
        hoi4_install=root,
        effects=build("effect"),
        triggers=build("trigger"),
        modifiers=build("modifier"),
    )


def validate_script_sources(
    sources: Iterable[ScriptSource],
    vocabulary: GameScriptVocabulary,
    *,
    mod_root: str | Path | None = None,
    allowlist: Iterable[str] = (),
) -> list[ValidationError]:
    """Warn about script tokens absent from installed-game documentation."""

    allowed = tuple(dict.fromkeys(allowlist))
    custom = _custom_script_tokens(Path(mod_root)) if mod_root is not None else {}
    findings: list[ValidationError] = []
    seen: set[tuple[ScriptTokenKind, str, str, int]] = set()
    for source in sources:
        if not source.text.strip():
            continue
        commands = (
            _modifier_commands(source.text)
            if source.kind == "modifier"
            else _script_commands(source.text, source.kind)
        )
        for command in commands:
            known = vocabulary.tokens(command.kind)
            if not known:
                continue
            custom_for_kind = custom.get(command.kind, set())
            identity = (
                command.kind,
                command.name,
                source.file_path or source.owner_id,
                command.line,
            )
            if identity in seen:
                continue
            seen.add(identity)
            if command.name in custom_for_kind or _is_allowed(
                command.name, command.kind, allowed
            ):
                continue
            if command.name in known:
                info = known[command.name]
                if info.usage_count:
                    continue
                findings.append(
                    ValidationError(
                        message=(
                            f"{source.owner_kind} '{source.owner_id}' uses documented "
                            f"{command.kind} token '{command.name}', but it has 0 "
                            "installed-game uses. This may be a legal unexercised token; "
                            f"verify it or allow '{command.kind}:{command.name}'."
                        ),
                        severity="warning",
                        code=f"unseen_{command.kind}_token",
                        file_path=source.file_path,
                        line=command.line,
                        focus_id=(
                            source.owner_id
                            if source.owner_kind == "focus"
                            else None
                        ),
                        event_id=(
                            source.owner_id
                            if source.owner_kind == "event"
                            else None
                        ),
                        idea_id=(
                            source.owner_id if source.owner_kind == "idea" else None
                        ),
                        decision_id=(
                            source.owner_id
                            if source.owner_kind == "decision"
                            else None
                        ),
                    )
                )
                continue
            suggestion = _nearest(command.name, known)
            suggestion_text = ""
            if suggestion is not None:
                info = known[suggestion]
                suggestion_text = (
                    f" Did you mean '{suggestion}' "
                    f"({info.usage_count} installed-game uses)?"
                )
            owner = (
                f"{source.owner_kind} '{source.owner_id}' "
                if source.owner_kind and source.owner_id
                else ""
            )
            findings.append(
                ValidationError(
                    message=(
                        f"{owner}uses undocumented {command.kind} token "
                        f"'{command.name}' (0 installed-game uses)."
                        f"{suggestion_text} Allow an intentional extension with "
                        f"'{command.kind}:{command.name}'."
                    ),
                    severity="warning",
                    code=f"unknown_{command.kind}_token",
                    file_path=source.file_path,
                    line=command.line,
                    focus_id=(
                        source.owner_id if source.owner_kind == "focus" else None
                    ),
                    event_id=(
                        source.owner_id if source.owner_kind == "event" else None
                    ),
                    idea_id=(
                        source.owner_id if source.owner_kind == "idea" else None
                    ),
                    decision_id=(
                        source.owner_id if source.owner_kind == "decision" else None
                    ),
                )
            )
    return findings


def _documentation_entries(
    path: Path,
) -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8", errors="ignore")
    matches = list(_HEADING_RE.finditer(text))
    result: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
    for index, match in enumerate(matches):
        body = text[
            match.end() : matches[index + 1].start()
            if index + 1 < len(matches)
            else len(text)
        ]
        scopes = _metadata_values(body, "Supported Scopes")
        categories = _metadata_values(body, "Categories")
        result[match.group(1)] = (scopes, categories)
    return result


def _metadata_values(body: str, label: str) -> tuple[str, ...]:
    match = re.search(
        rf"(?mi)^\s*\*?\s*(?:\*\*)?{re.escape(label)}(?:\*\*)?\s*:\s*(.+)$",
        body,
    )
    if match is None:
        return ()
    return tuple(
        value.strip().strip("`*")
        for value in re.split(r"[,|]", match.group(1))
        if value.strip().strip("`*")
    )


def _script_commands(text: str, kind: ScriptTokenKind) -> list[_Command]:
    commands: list[_Command] = []

    def walk(body: str, base_line: int, semantic_kind: ScriptTokenKind) -> None:
        try:
            spans = top_level_assignments(body)
        except (ParseError, ValueError):
            return
        for span in spans:
            line = base_line + body.count("\n", 0, span.start)
            key = span.key
            nested_kind: ScriptTokenKind | None = None
            if key in _NESTED_TRIGGER_BLOCKS:
                nested_kind = "trigger"
            elif key in _NESTED_EFFECT_BLOCKS:
                nested_kind = "effect"
            if nested_kind is not None:
                if (
                    span.is_block
                    and span.body_start is not None
                    and span.body_end is not None
                ):
                    walk(
                        body[span.body_start : span.body_end],
                        base_line + body.count("\n", 0, span.body_start),
                        nested_kind,
                    )
                continue
            if key == "limit" and semantic_kind == "effect":
                if span.is_block and span.body_start is not None and span.body_end is not None:
                    walk(
                        body[span.body_start : span.body_end],
                        base_line + body.count("\n", 0, span.body_start),
                        "trigger",
                    )
                continue
            recursive = (
                key in _CONTROL_BLOCKS
                or key.startswith(_RECURSIVE_PREFIXES)
                or bool(_SCOPE_RE.match(key))
            )
            if not bool(_SCOPE_RE.match(key)) and key not in {
                "AND",
                "OR",
                "NOT",
                "NAND",
                "NOR",
                "else",
                "hidden_effect",
            }:
                commands.append(_Command(key, line, semantic_kind))
            if (
                recursive
                and span.is_block
                and span.body_start is not None
                and span.body_end is not None
            ):
                walk(
                    body[span.body_start : span.body_end],
                    base_line + body.count("\n", 0, span.body_start),
                    semantic_kind,
                )

    walk(text, 1, kind)
    return commands


def _modifier_commands(text: str) -> list[_Command]:
    try:
        return [
            _Command(span.key, 1 + text.count("\n", 0, span.start), "modifier")
            for span in top_level_assignments(text)
            if not span.is_block
        ]
    except (ParseError, ValueError):
        return []


def _custom_script_tokens(mod_root: Path) -> dict[ScriptTokenKind, set[str]]:
    result: dict[ScriptTokenKind, set[str]] = {
        "effect": set(),
        "trigger": set(),
        "modifier": set(),
    }
    for kind, relative in (
        ("effect", Path("common/scripted_effects")),
        ("trigger", Path("common/scripted_triggers")),
    ):
        typed_kind = cast(ScriptTokenKind, kind)
        directory = mod_root / relative
        if not directory.is_dir():
            continue
        for path in directory.glob("*.txt"):
            try:
                result[typed_kind].update(
                    span.key
                    for span in top_level_assignments(
                        path.read_text(encoding="utf-8-sig", errors="ignore")
                    )
                    if span.is_block
                )
            except (OSError, ParseError, ValueError):
                continue
    return result


def _is_allowed(name: str, kind: ScriptTokenKind, patterns: tuple[str, ...]) -> bool:
    return any(
        fnmatch.fnmatch(name, pattern)
        or fnmatch.fnmatch(f"{kind}:{name}", pattern)
        for pattern in patterns
    )


def _nearest(name: str, known: Mapping[str, ScriptTokenInfo]) -> str | None:
    from difflib import get_close_matches

    candidates = get_close_matches(name, known.keys(), n=3, cutoff=0.55)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda candidate: (
            _similarity(name, candidate),
            known[candidate].usage_count,
        ),
    )


def _similarity(left: str, right: str) -> float:
    from difflib import SequenceMatcher

    return SequenceMatcher(None, left, right).ratio()


def _without_comments(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        quote = False
        escaped = False
        output: list[str] = []
        for character in line:
            if escaped:
                output.append(character)
                escaped = False
                continue
            if character == "\\" and quote:
                output.append(character)
                escaped = True
                continue
            if character == '"':
                quote = not quote
            if character == "#" and not quote:
                break
            output.append(character)
        lines.append("".join(output))
    return "\n".join(lines)
