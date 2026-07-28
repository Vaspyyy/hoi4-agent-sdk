"""Read-only release gates for source-preserving edits on representative mods.

The gate deliberately performs mutations only inside :meth:`Mod.transaction`
blocks and never calls :meth:`Mod.save`.  It is intended for maintainers who
want to catch a serializer regression against a large, real-world mod before a
release.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterable
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Callable, Literal, TypeVar, cast

from .mod import Mod
from .idea_icons import DEFAULT_IDEA_ICON
from .game_log import GameLogReport, parse_hoi4_error_log
from .types import Focus, LoadDiagnostic, ValidationError

ProbeStatus = Literal["passed", "failed", "skipped"]
_T = TypeVar("_T")
DEFAULT_MIN_PROBES = 6


@dataclass(frozen=True)
class DiffBudget:
    """Maximum source churn allowed for each one-field probe."""

    max_changed_lines: int = 20
    max_files: int = 1
    require_diff: bool = True

    def __post_init__(self) -> None:
        if self.max_changed_lines < 0:
            raise ValueError("max_changed_lines must be non-negative")
        if self.max_files < 0:
            raise ValueError("max_files must be non-negative")


@dataclass(frozen=True)
class DiffStats:
    """Compact measurements extracted from a unified diff."""

    files: tuple[str, ...] = ()
    additions: int = 0
    deletions: int = 0
    hunks: int = 0

    @property
    def changed_lines(self) -> int:
        return self.additions + self.deletions

    @property
    def file_count(self) -> int:
        return len(self.files)


@dataclass(frozen=True)
class ProbeResult:
    """Result of one dry-run mutation."""

    name: str
    status: ProbeStatus
    target: str = ""
    duration_seconds: float = 0.0
    diff: DiffStats = field(default_factory=DiffStats)
    reason: str = ""


@dataclass(frozen=True)
class GateReport:
    """Complete report from loading, validating, and probing a mod."""

    mod_root: Path
    hoi4_install: Path | None
    load_seconds: float
    validation_seconds: float
    load_diagnostics: tuple[LoadDiagnostic, ...]
    validation_issues: tuple[ValidationError, ...]
    probes: tuple[ProbeResult, ...]
    filesystem_changes: tuple[str, ...]
    game_log: GameLogReport | None = None
    min_probes: int = DEFAULT_MIN_PROBES
    required_probes: tuple[str, ...] = ()
    fail_on_load_diagnostics: bool = True
    fail_on_validation_errors: bool = True

    @property
    def completed_probe_count(self) -> int:
        return sum(probe.status != "skipped" for probe in self.probes)

    @property
    def validation_error_count(self) -> int:
        return sum(issue.severity == "error" for issue in self.validation_issues)

    @property
    def validation_warning_count(self) -> int:
        return sum(issue.severity == "warning" for issue in self.validation_issues)

    @property
    def missing_required_probes(self) -> tuple[str, ...]:
        passed = {probe.name for probe in self.probes if probe.status == "passed"}
        return tuple(name for name in self.required_probes if name not in passed)

    @property
    def success(self) -> bool:
        if self.filesystem_changes:
            return False
        if self.completed_probe_count < self.min_probes:
            return False
        if self.missing_required_probes:
            return False
        if any(probe.status == "failed" for probe in self.probes):
            return False
        if self.fail_on_load_diagnostics and self.load_diagnostics:
            return False
        if self.fail_on_validation_errors and self.validation_error_count:
            return False
        return True

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""

        return {
            "success": self.success,
            "mod_root": str(self.mod_root),
            "hoi4_install": str(self.hoi4_install) if self.hoi4_install else None,
            "timings": {
                "load_seconds": self.load_seconds,
                "validation_seconds": self.validation_seconds,
            },
            "load_diagnostics": [
                {
                    **asdict(diagnostic),
                    "path": str(diagnostic.path),
                    "related_path": (
                        str(diagnostic.related_path)
                        if diagnostic.related_path is not None
                        else None
                    ),
                }
                for diagnostic in self.load_diagnostics
            ],
            "validation": {
                "errors": self.validation_error_count,
                "warnings": self.validation_warning_count,
                "issues": [asdict(issue) for issue in self.validation_issues],
            },
            "probes": [
                {
                    **asdict(probe),
                    "diff": {
                        **asdict(probe.diff),
                        "changed_lines": probe.diff.changed_lines,
                        "file_count": probe.diff.file_count,
                    },
                }
                for probe in self.probes
            ],
            "filesystem_changes": list(self.filesystem_changes),
            "game_log": self.game_log.to_dict() if self.game_log is not None else None,
            "required_probes": list(self.required_probes),
            "missing_required_probes": list(self.missing_required_probes),
        }


@dataclass(frozen=True)
class _Probe:
    name: str
    mutate: Callable[[Mod], str | None]


def measure_unified_diff(diff: str) -> DiffStats:
    """Count changed lines, files, and hunks in a unified diff string."""

    additions = 0
    deletions = 0
    hunks = 0
    files: list[str] = []
    for line in diff.splitlines():
        if line.startswith("+++ "):
            filename = line[4:].strip()
            if filename.startswith("b/"):
                filename = filename[2:]
            if filename not in files:
                files.append(filename)
        elif line.startswith("--- "):
            continue
        elif line.startswith("@@"):
            hunks += 1
        elif line.startswith("+"):
            additions += 1
        elif line.startswith("-"):
            deletions += 1
    return DiffStats(tuple(files), additions, deletions, hunks)


def _fingerprint_tree(root: Path) -> dict[str, tuple[str, int, int, str]]:
    """Hash a directory without following symlink targets."""

    fingerprint: dict[str, tuple[str, int, int, str]] = {}
    if not root.exists():
        return fingerprint
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        try:
            stat = path.lstat()
            if path.is_symlink():
                fingerprint[relative] = ("symlink", 0, stat.st_mtime_ns, os.readlink(path))
                continue
            if not path.is_file():
                continue
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            fingerprint[relative] = ("file", stat.st_size, stat.st_mtime_ns, digest.hexdigest())
        except FileNotFoundError:
            # A concurrent deletion will be reported by the before/after comparison.
            continue
    return fingerprint


def _fingerprint_changes(
    before: dict[str, tuple[str, int, int, str]],
    after: dict[str, tuple[str, int, int, str]],
) -> tuple[str, ...]:
    changes: list[str] = []
    for path in sorted(before.keys() | after.keys()):
        if path not in before:
            changes.append(f"added: {path}")
        elif path not in after:
            changes.append(f"removed: {path}")
        elif before[path] != after[path]:
            changes.append(f"modified: {path}")
    return tuple(changes)


def _largest(items: list[tuple[int, str, _T]]) -> tuple[str, _T] | None:
    if not items:
        return None
    _, identifier, value = max(items, key=lambda item: (item[0], item[1]))
    return identifier, value


def _increment_number(value: object) -> object:
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, float):
        return value + 0.01
    text = str(value).strip()
    if re.fullmatch(r"[+-]?\d+", text):
        return str(int(text) + 1)
    if re.fullmatch(r"[+-]?(?:\d+\.\d*|\d*\.\d+)", text):
        return str(float(text) + 0.01)
    return f"{text}_sdk_gate"


def _probe_focus(mod: Mod) -> str | None:
    candidates: list[tuple[int, str, tuple[str, Focus]]] = []
    for tree_id in mod.list_focus_trees():
        tree = mod.get_focus_tree(tree_id)
        for focus in tree.focuses:
            target = f"{tree_id}/{focus.id}"
            candidates.append((len(focus.raw_block), target, (tree_id, focus)))
    selected = _largest(candidates)
    if selected is None:
        return None
    target, payload = selected
    tree_id, focus = payload
    if not mod.update_focus(tree_id, focus.id, cost=focus.cost + 1):
        raise RuntimeError(f"Could not update focus {target}")
    return f"{target}.cost"


def _probe_event(mod: Mod) -> str | None:
    selected = _largest(
        [
            (len(mod.get_event(event_id).raw_block), event_id, mod.get_event(event_id))
            for event_id in mod.list_events()
        ]
    )
    if selected is None:
        return None
    event_id, event = selected
    new_picture = f"{event.picture or 'GFX_report_event_generic'}_sdk_gate"
    if not mod.update_event(event_id, picture=new_picture):
        raise RuntimeError(f"Could not update event {event_id}")
    return f"{event_id}.picture"


def _probe_decision(mod: Mod) -> str | None:
    selected = _largest(
        [
            (
                len(mod.get_decision(decision_id).raw_block),
                decision_id,
                mod.get_decision(decision_id),
            )
            for decision_id in mod.list_decisions()
        ]
    )
    if selected is None:
        return None
    decision_id, decision = selected
    cost = decision.cost if decision.cost is not None else 0
    if not mod.update_decision(decision_id, cost=cost + 1):
        raise RuntimeError(f"Could not update decision {decision_id}")
    return f"{decision_id}.cost"


def _probe_idea(mod: Mod) -> str | None:
    selected = _largest(
        [
            (len(mod.get_idea(idea_id).raw_block), idea_id, mod.get_idea(idea_id))
            for idea_id in mod.list_ideas()
        ]
    )
    if selected is None:
        return None
    idea_id, idea = selected
    if idea.modifier:
        key = sorted(idea.modifier)[0]
        value = _increment_number(idea.modifier[key])
        if not mod.update_idea(idea_id, modifier={key: value}, merge_modifier=True):
            raise RuntimeError(f"Could not update idea {idea_id}")
        return f"{idea_id}.modifier.{key}"
    icon = idea.icon or DEFAULT_IDEA_ICON
    if not mod.update_idea(idea_id, icon=f"{icon}_sdk_gate"):
        raise RuntimeError(f"Could not update idea {idea_id}")
    return f"{idea_id}.icon"


def _probe_on_action(mod: Mod) -> str | None:
    selected = _largest(
        [
            (
                len(action.raw_block),
                f"{action_id}[{occurrence}]",
                (action_id, occurrence, action),
            )
            for action_id in mod.list_on_actions()
            for occurrence, action in enumerate(
                mod.get_on_action_occurrences(action_id)
            )
        ]
    )
    if selected is None:
        return None
    target, payload = selected
    action_id, occurrence, action = payload
    old_effect = action.effect.strip()
    new_effect = "\n".join(
        part for part in (old_effect, "set_country_flag = sdk_source_stability_probe") if part
    )
    if not mod.update_on_action(action_id, occurrence=occurrence, effect=new_effect):
        raise RuntimeError(f"Could not update on-action {target}")
    return f"{target}.effect"


def _probe_localization(mod: Mod) -> str | None:
    entries = mod.all_loc()
    if not entries:
        return None
    key = max(entries, key=lambda candidate: (len(entries[candidate]), candidate))
    mod.set_loc(key, f"{entries[key]} [sdk gate]")
    return key


def _probe_country(mod: Mod) -> str | None:
    selected = _largest(
        [
            (
                len(mod.get_country(tag).raw_definition)
                + len(mod.get_country(tag).raw_history)
                + len(mod.get_country(tag).raw_character),
                tag,
                mod.get_country(tag),
            )
            for tag in mod.list_countries()
        ]
    )
    if selected is None:
        return None
    tag, country = selected
    if not mod.update_country(tag, capital=country.capital + 1):
        raise RuntimeError(f"Could not update country {tag}")
    return f"{tag}.capital"


def _probe_state(mod: Mod) -> str | None:
    selected = _largest(
        [
            (len(mod.get_state(state_id).raw_text), str(state_id), mod.get_state(state_id))
            for state_id in mod.list_states()
        ]
    )
    if selected is None:
        return None
    state_id_text, state = selected
    state_id = int(state_id_text)
    if not mod.set_state_properties(state_id, manpower=_increment_number(state.manpower)):
        raise RuntimeError(f"Could not update state {state_id}")
    return f"{state_id}.manpower"


def _probe_ideology(mod: Mod) -> str | None:
    candidates = [
        ideology_id
        for ideology_id in mod.list_ideologies(include_vanilla=False)
        if mod.get_ideology(ideology_id, include_vanilla=False).raw_block
    ]
    if not candidates:
        return None
    ideology_id = max(
        candidates,
        key=lambda candidate: (
            len(mod.get_ideology(candidate, include_vanilla=False).raw_block),
            candidate,
        ),
    )
    ideology = mod.get_ideology(ideology_id, include_vanilla=False)
    red, green, blue = ideology.color
    if not mod.update_ideology(ideology_id, color=((red + 1) % 256, green, blue)):
        raise RuntimeError(f"Could not update ideology {ideology_id}")
    return f"{ideology_id}.color"


def _probe_dynamic_modifier(mod: Mod) -> str | None:
    candidates = [
        modifier_id
        for modifier_id in mod.list_dynamic_modifiers()
        if mod.get_dynamic_modifier(modifier_id).raw_block
    ]
    if not candidates:
        return None
    modifier_id = max(
        candidates,
        key=lambda candidate: (
            len(mod.get_dynamic_modifier(candidate).raw_block),
            candidate,
        ),
    )
    dynamic_modifier = mod.get_dynamic_modifier(modifier_id)
    values = dict(dynamic_modifier.modifier)
    if values:
        key = sorted(values)[0]
        values[key] = cast(
            str | int | float | bool,
            _increment_number(values[key]),
        )
        if not mod.update_dynamic_modifier(modifier_id, modifier=values):
            raise RuntimeError(f"Could not update dynamic modifier {modifier_id}")
        return f"{modifier_id}.modifier.{key}"
    if not mod.update_dynamic_modifier(modifier_id, enable="always = yes"):
        raise RuntimeError(f"Could not update dynamic modifier {modifier_id}")
    return f"{modifier_id}.enable"


def _probe_bookmark(mod: Mod) -> str | None:
    candidates = [name for name in mod.list_bookmarks() if mod.get_bookmark(name).raw_block]
    if not candidates:
        return None
    name = max(
        candidates,
        key=lambda candidate: (len(mod.get_bookmark(candidate).raw_block), candidate),
    )
    bookmark = mod.get_bookmark(name)
    picture = bookmark.picture or "GFX_select_date_1936"
    if not mod.update_bookmark(name, picture=f"{picture}_sdk_gate"):
        raise RuntimeError(f"Could not update bookmark {name}")
    return f"{name}.picture"


DEFAULT_PROBES: tuple[_Probe, ...] = (
    _Probe("focus", _probe_focus),
    _Probe("event", _probe_event),
    _Probe("decision", _probe_decision),
    _Probe("idea", _probe_idea),
    _Probe("on_action", _probe_on_action),
    _Probe("localization", _probe_localization),
    _Probe("country", _probe_country),
    _Probe("state", _probe_state),
    _Probe("ideology", _probe_ideology),
    _Probe("dynamic_modifier", _probe_dynamic_modifier),
    _Probe("bookmark", _probe_bookmark),
)


def _validation_signature(issue: ValidationError) -> tuple[object, ...]:
    return (
        issue.severity,
        issue.code,
        issue.message,
        issue.file_path,
        issue.focus_id,
        issue.country_tag,
        issue.state_id,
        issue.event_id,
        issue.idea_id,
        issue.decision_id,
        issue.focus_tree_id,
        issue.ideology_id,
        issue.dynamic_modifier_id,
        issue.bookmark_name,
    )


def _run_probe(
    mod: Mod,
    probe: _Probe,
    budget: DiffBudget,
    *,
    baseline_validation: tuple[ValidationError, ...],
    validate_icons: bool,
    strict_localization: bool,
) -> ProbeResult:
    started = perf_counter()
    target = ""
    try:
        with mod.transaction(save=False):
            selected = probe.mutate(mod)
            if selected is None:
                return ProbeResult(
                    name=probe.name,
                    status="skipped",
                    duration_seconds=perf_counter() - started,
                    reason="No compatible object was loaded",
                )
            target = selected
            diff = measure_unified_diff(mod.preview())
            baseline = {_validation_signature(issue) for issue in baseline_validation}
            introduced = [
                issue
                for issue in mod.validate(
                    validate_icons=validate_icons,
                    strict_localization=strict_localization,
                )
                if _validation_signature(issue) not in baseline
            ]
        reasons: list[str] = []
        if budget.require_diff and diff.changed_lines == 0:
            reasons.append("mutation produced no diff")
        if diff.changed_lines > budget.max_changed_lines:
            reasons.append(
                f"{diff.changed_lines} changed lines exceed budget {budget.max_changed_lines}"
            )
        if diff.file_count > budget.max_files:
            reasons.append(f"{diff.file_count} files exceed budget {budget.max_files}")
        if introduced:
            summary = "; ".join(
                f"{issue.code or issue.severity}: {issue.message}" for issue in introduced[:3]
            )
            reasons.append(f"mutation introduced validation issues: {summary}")
        return ProbeResult(
            name=probe.name,
            status="failed" if reasons else "passed",
            target=target,
            duration_seconds=perf_counter() - started,
            diff=diff,
            reason="; ".join(reasons),
        )
    except Exception as error:
        return ProbeResult(
            name=probe.name,
            status="failed",
            target=target,
            duration_seconds=perf_counter() - started,
            reason=f"{type(error).__name__}: {error}",
        )


def run_release_gate(
    mod_root: str | Path,
    *,
    hoi4_install: str | Path | None = None,
    budget: DiffBudget | None = None,
    validate_icons: bool = False,
    strict_localization: bool = False,
    fail_on_load_diagnostics: bool = True,
    fail_on_validation_errors: bool = True,
    min_probes: int = DEFAULT_MIN_PROBES,
    required_probes: Iterable[str] = (),
    strict_loading: bool = False,
    error_log: str | Path | None = None,
    error_log_since: datetime | None = None,
    require_fresh_game_log: bool = False,
) -> GateReport:
    """Run the read-only validation and source-stability release gate.

    Every mutation is rolled back by ``Mod.transaction(save=False)``.  A full
    content fingerprint is also checked before and after the run as a guard
    against accidental writes in load, preview, or transaction code.
    """

    root = Path(mod_root).resolve()
    install = Path(hoi4_install).resolve() if hoi4_install is not None else None
    if not root.is_dir():
        raise FileNotFoundError(f"Mod root does not exist or is not a directory: {root}")
    if install is not None and not install.is_dir():
        raise FileNotFoundError(f"HOI4 install does not exist or is not a directory: {install}")
    if min_probes < 0:
        raise ValueError("min_probes must be non-negative")
    if require_fresh_game_log and error_log is None:
        raise ValueError("require_fresh_game_log=True requires error_log")
    known_probes = {probe.name for probe in DEFAULT_PROBES}
    normalized_required = tuple(dict.fromkeys(required_probes))
    unknown_probes = sorted(set(normalized_required) - known_probes)
    if unknown_probes:
        raise ValueError(f"Unknown required probes: {', '.join(unknown_probes)}")

    active_budget = budget or DiffBudget()
    before = _fingerprint_tree(root)

    started = perf_counter()
    mod = Mod(root, hoi4_install=install, strict_loading=strict_loading)
    load_seconds = perf_counter() - started

    started = perf_counter()
    validation_issue_list = list(
        mod.validate(
            validate_icons=validate_icons,
            strict_localization=strict_localization,
        )
    )
    game_log_report: GameLogReport | None = None
    if error_log is not None:
        fresh_after: datetime | None = None
        if require_fresh_game_log:
            mtimes = [
                path.stat().st_mtime
                for path in root.rglob("*")
                if path.is_file()
            ]
            if mtimes:
                fresh_after = datetime.fromtimestamp(max(mtimes)).astimezone()
        game_log_report = parse_hoi4_error_log(
            error_log,
            root,
            since=error_log_since,
            fresh_after=fresh_after,
        )
        validation_issue_list.extend(game_log_report.validation_errors)
    validation_issues = tuple(validation_issue_list)
    validation_seconds = perf_counter() - started

    probes = tuple(
        _run_probe(
            mod,
            probe,
            active_budget,
            baseline_validation=validation_issues,
            validate_icons=validate_icons,
            strict_localization=strict_localization,
        )
        for probe in DEFAULT_PROBES
    )
    after = _fingerprint_tree(root)
    filesystem_changes = _fingerprint_changes(before, after)

    return GateReport(
        mod_root=root,
        hoi4_install=install,
        load_seconds=load_seconds,
        validation_seconds=validation_seconds,
        load_diagnostics=mod.load_diagnostics,
        validation_issues=validation_issues,
        probes=probes,
        filesystem_changes=filesystem_changes,
        game_log=game_log_report,
        min_probes=min_probes,
        required_probes=normalized_required,
        fail_on_load_diagnostics=fail_on_load_diagnostics,
        fail_on_validation_errors=fail_on_validation_errors,
    )


def format_report(report: GateReport, *, max_diagnostics: int = 20) -> str:
    """Format a concise human-readable gate report."""

    counts = Counter(issue.severity for issue in report.validation_issues)
    lines = [
        f"HOI4 SDK release gate: {'PASS' if report.success else 'FAIL'}",
        f"mod: {report.mod_root}",
        f"hoi4 install: {report.hoi4_install or '<none>'}",
        f"load: {report.load_seconds:.3f}s; validation: {report.validation_seconds:.3f}s",
        (
            "diagnostics: "
            f"{len(report.load_diagnostics)} load, "
            f"{counts.get('error', 0)} validation errors, "
            f"{counts.get('warning', 0)} warnings"
        ),
        (
            f"probe coverage: {report.completed_probe_count}/{len(report.probes)} "
            f"completed (minimum {report.min_probes})"
        ),
        "probes:",
    ]
    if report.game_log is not None:
        lines.insert(
            6,
            (
                "game log: "
                f"{len(report.game_log.entries)} mod-owned errors; "
                f"fresh={'yes' if report.game_log.is_fresh else 'no'}"
            ),
        )
    for probe in report.probes:
        detail = (
            f"{probe.diff.changed_lines} changed lines, "
            f"{probe.diff.file_count} files, {probe.duration_seconds:.3f}s"
        )
        target = f" ({probe.target})" if probe.target else ""
        reason = f" - {probe.reason}" if probe.reason else ""
        lines.append(f"  {probe.status.upper():7} {probe.name}{target}: {detail}{reason}")

    if report.missing_required_probes:
        lines.append(
            "missing required probes: " + ", ".join(report.missing_required_probes)
        )

    if report.filesystem_changes:
        lines.append("unexpected filesystem changes:")
        lines.extend(f"  {change}" for change in report.filesystem_changes)

    diagnostic_lines: list[str] = []
    for diagnostic in report.load_diagnostics:
        diagnostic_lines.append(
            f"load {diagnostic.section} {diagnostic.path}: "
            f"{diagnostic.error_type}: {diagnostic.message}"
        )
    for issue in report.validation_issues:
        diagnostic_lines.append(f"{issue.severity} {issue.code or '<uncoded>'}: {issue.message}")
    if diagnostic_lines:
        lines.append("diagnostic details:")
        lines.extend(f"  {line}" for line in diagnostic_lines[:max_diagnostics])
        hidden = len(diagnostic_lines) - max_diagnostics
        if hidden > 0:
            lines.append(f"  ... {hidden} more")
    return "\n".join(lines)
