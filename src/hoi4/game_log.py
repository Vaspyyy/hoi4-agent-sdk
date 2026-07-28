"""Parse HOI4's error log and attribute engine failures to one mod."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .types import ValidationError

_RECORD_RE = re.compile(
    r"^\[(?P<time>\d{2}:\d{2}:\d{2})\]"
    r"\[(?P<game_date>[^\]]+)\]"
    r"\[(?P<source>[^\]]+)\]:\s*(?P<message>.*)$"
)
_QUOTED_FILE_RE = re.compile(r'\bin file:\s*"(?P<path>[^"]+)"', re.IGNORECASE)
_PLAIN_FILE_RE = re.compile(
    r"\bin (?P<path>(?:common|events|history|interface|localisation)/.+?)"
    r"(?:\s+line\s*:?\s*\d+|$)",
    re.IGNORECASE,
)
_LINE_RE = re.compile(r"(?:near\s+)?line\s*:?\s*(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class GameLogEntry:
    """One parsed engine-log record."""

    timestamp: datetime | None
    source: str
    message: str
    relative_path: str | None
    line: int | None
    error_class: str
    raw: str


@dataclass(frozen=True)
class GameLogReport:
    """Mod-owned records plus attribution and freshness metadata."""

    log_path: Path
    mod_root: Path
    entries: tuple[GameLogEntry, ...]
    ignored_entry_count: int
    unscoped_entry_count: int
    start_offset: int
    next_offset: int
    log_mtime: datetime
    fresh_after: datetime | None = None

    @property
    def is_fresh(self) -> bool:
        return self.fresh_after is None or self.log_mtime >= self.fresh_after

    @property
    def groups(self) -> dict[str, int]:
        return dict(sorted(Counter(entry.error_class for entry in self.entries).items()))

    @property
    def validation_errors(self) -> tuple[ValidationError, ...]:
        issues: list[ValidationError] = []
        if not self.is_fresh:
            assert self.fresh_after is not None
            issues.append(
                ValidationError(
                    message=(
                        f"HOI4 error log predates the audited mod: {self.log_mtime.isoformat()} "
                        f"< {self.fresh_after.isoformat()}"
                    ),
                    severity="error",
                    code="stale_game_log",
                    file_path=str(self.log_path),
                )
            )
        for entry in self.entries:
            assert entry.relative_path is not None
            issues.append(
                ValidationError(
                    message=f"HOI4 [{entry.error_class}] {entry.message}",
                    severity="error",
                    code="game_log_error",
                    file_path=str(self.mod_root / entry.relative_path),
                    line=entry.line,
                )
            )
        return tuple(issues)

    def to_dict(self) -> dict[str, object]:
        return {
            "log_path": str(self.log_path),
            "mod_root": str(self.mod_root),
            "owned_errors": len(self.entries),
            "ignored_entries": self.ignored_entry_count,
            "unscoped_entries": self.unscoped_entry_count,
            "groups": self.groups,
            "start_offset": self.start_offset,
            "next_offset": self.next_offset,
            "log_mtime": self.log_mtime.isoformat(),
            "fresh_after": (
                self.fresh_after.isoformat() if self.fresh_after is not None else None
            ),
            "is_fresh": self.is_fresh,
            "entries": [
                {
                    "timestamp": (
                        entry.timestamp.isoformat()
                        if entry.timestamp is not None
                        else None
                    ),
                    "source": entry.source,
                    "message": entry.message,
                    "relative_path": entry.relative_path,
                    "line": entry.line,
                    "error_class": entry.error_class,
                }
                for entry in self.entries
            ],
        }


def _classify(message: str) -> str:
    lowered = message.casefold()
    patterns = (
        ("unexpected_token", "unexpected token"),
        ("unknown_trigger", "unknown trigger"),
        ("unknown_effect", "unknown effect"),
        ("unknown_category", "unknown category"),
        ("invalid_database_object", "invalid database object"),
        ("missing_localization", "no localization"),
        ("invalid_trigger", "invalid trigger"),
        ("invalid_effect", "invalid effect"),
    )
    for name, token in patterns:
        if token in lowered:
            return name
    return "engine_error"


def _entry_timestamp(clock: str, log_mtime: datetime) -> datetime:
    parsed = datetime.strptime(clock, "%H:%M:%S").time()
    candidate = datetime.combine(log_mtime.date(), parsed, tzinfo=log_mtime.tzinfo)
    if candidate - log_mtime > timedelta(hours=12):
        candidate -= timedelta(days=1)
    return candidate


def _extract_relative_path(message: str) -> str | None:
    match = _QUOTED_FILE_RE.search(message) or _PLAIN_FILE_RE.search(message)
    if match is None:
        return None
    value = match.group("path").replace("\\", "/").strip()
    while value.startswith("./"):
        value = value[2:]
    return value


def _parse_records(text: str, log_mtime: datetime) -> list[GameLogEntry]:
    records: list[GameLogEntry] = []
    current: list[str] = []
    metadata: tuple[datetime, str, str] | None = None

    def flush() -> None:
        nonlocal current, metadata
        if metadata is None:
            current = []
            return
        timestamp, source, first_message = metadata
        message = "\n".join([first_message, *current]).strip()
        relative_path = _extract_relative_path(message)
        line_match = _LINE_RE.search(message)
        records.append(
            GameLogEntry(
                timestamp=timestamp,
                source=source,
                message=message,
                relative_path=relative_path,
                line=int(line_match.group(1)) if line_match else None,
                error_class=_classify(message),
                raw=message,
            )
        )
        current = []
        metadata = None

    for line in text.splitlines():
        match = _RECORD_RE.match(line)
        if match is None:
            if metadata is not None:
                current.append(line)
            continue
        flush()
        metadata = (
            _entry_timestamp(match.group("time"), log_mtime),
            match.group("source"),
            match.group("message"),
        )
    flush()
    return records


def parse_hoi4_error_log(
    log_path: str | Path,
    mod_root: str | Path,
    *,
    since: datetime | None = None,
    start_offset: int = 0,
    fresh_after: datetime | None = None,
) -> GameLogReport:
    """Return engine errors whose referenced relative path exists in ``mod_root``."""

    path = Path(log_path).resolve()
    root = Path(mod_root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Mod root does not exist: {root}")
    raw = path.read_bytes()
    if start_offset < 0 or start_offset > len(raw):
        raise ValueError(
            f"start_offset must be between 0 and {len(raw)}, got {start_offset}"
        )
    log_mtime = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
    normalized_since = since
    if normalized_since is not None and normalized_since.tzinfo is None:
        normalized_since = normalized_since.astimezone()
    normalized_fresh_after = fresh_after
    if normalized_fresh_after is not None and normalized_fresh_after.tzinfo is None:
        normalized_fresh_after = normalized_fresh_after.astimezone()

    records = _parse_records(raw[start_offset:].decode("utf-8", errors="replace"), log_mtime)
    entries: list[GameLogEntry] = []
    ignored = 0
    unscoped = 0
    for entry in records:
        if (
            normalized_since is not None
            and entry.timestamp is not None
            and entry.timestamp < normalized_since
        ):
            ignored += 1
            continue
        if entry.relative_path is None:
            ignored += 1
            unscoped += 1
            continue
        target = (root / entry.relative_path).resolve(strict=False)
        try:
            target.relative_to(root)
        except ValueError:
            ignored += 1
            continue
        if not target.is_file():
            ignored += 1
            continue
        entries.append(entry)

    return GameLogReport(
        log_path=path,
        mod_root=root,
        entries=tuple(entries),
        ignored_entry_count=ignored,
        unscoped_entry_count=unscoped,
        start_offset=start_offset,
        next_offset=len(raw),
        log_mtime=log_mtime,
        fresh_after=normalized_fresh_after,
    )


def format_game_log_report(report: GameLogReport) -> str:
    """Render a concise, grouped report for agents and build logs."""

    lines = [
        f"HOI4 mod log: {'PASS' if report.is_fresh and not report.entries else 'FAIL'}",
        f"log: {report.log_path}",
        f"mod: {report.mod_root}",
        f"fresh: {'yes' if report.is_fresh else 'no'}",
        f"mod-owned errors: {len(report.entries)}",
        f"ignored other/unscoped entries: {report.ignored_entry_count}",
    ]
    lines.extend(f"  {name}: {count}" for name, count in report.groups.items())
    for entry in report.entries:
        location = entry.relative_path or "<unscoped>"
        if entry.line is not None:
            location += f":{entry.line}"
        first_line = entry.message.splitlines()[0]
        lines.append(f"  {location}: {first_line}")
    return "\n".join(lines)
