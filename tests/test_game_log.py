from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from hoi4 import Mod, format_game_log_report, parse_hoi4_error_log
from hoi4.release_gate import run_release_gate


def _write_log(path: Path) -> None:
    path.write_text(
        '[12:00:00][no_game_date][persistent.cpp:67]: Error: "Unexpected token: desc, '
        'near line: 5" in file: "common/ideas/OWN_ideas.txt" near line: 5\n'
        '[12:00:01][no_game_date][trigger.cpp:568]: Error: "Unknown trigger-type: '
        'other_trigger, near line: 9" in file: "common/ideas/OTHER_ideas.txt" '
        "near line: 9\n"
        "[12:00:02][no_game_date][effect.cpp:358]: Error: invalid database object\n"
        "continued details without a file path\n",
        encoding="utf-8",
    )
    noon = datetime.now().astimezone().replace(
        hour=12, minute=1, second=0, microsecond=0
    )
    os.utime(path, (noon.timestamp(), noon.timestamp()))


def test_game_log_filters_to_files_owned_by_target_mod(tmp_path: Path) -> None:
    mod_root = tmp_path / "mod"
    idea = mod_root / "common/ideas/OWN_ideas.txt"
    idea.parent.mkdir(parents=True)
    idea.write_text("ideas = { country = { } }\n", encoding="utf-8")
    log_path = tmp_path / "error.log"
    _write_log(log_path)

    report = parse_hoi4_error_log(log_path, mod_root)

    assert len(report.entries) == 1
    assert report.entries[0].relative_path == "common/ideas/OWN_ideas.txt"
    assert report.entries[0].line == 5
    assert report.groups == {"unexpected_token": 1}
    assert report.ignored_entry_count == 2
    assert report.unscoped_entry_count == 1
    assert report.next_offset == log_path.stat().st_size
    assert "mod-owned errors: 1" in format_game_log_report(report)
    issue = report.validation_errors[0]
    assert issue.code == "game_log_error"
    assert issue.file_path == str(idea)


def test_game_log_supports_offsets_and_time_filtering(tmp_path: Path) -> None:
    mod_root = tmp_path / "mod"
    idea = mod_root / "common/ideas/OWN_ideas.txt"
    idea.parent.mkdir(parents=True)
    idea.write_text("ideas = { country = { } }\n", encoding="utf-8")
    log_path = tmp_path / "error.log"
    _write_log(log_path)

    report = parse_hoi4_error_log(
        log_path,
        mod_root,
        since=datetime.now().astimezone().replace(
            hour=12, minute=0, second=1, microsecond=0
        ),
    )
    empty = parse_hoi4_error_log(
        log_path,
        mod_root,
        start_offset=log_path.stat().st_size,
    )

    assert not report.entries
    assert not empty.entries


def test_mod_validate_game_log_rejects_stale_log(tmp_path: Path) -> None:
    mod_root = tmp_path / "mod"
    idea = mod_root / "common/ideas/OWN_ideas.txt"
    idea.parent.mkdir(parents=True)
    idea.write_text("ideas = { country = { } }\n", encoding="utf-8")
    log_path = tmp_path / "error.log"
    _write_log(log_path)
    newer = log_path.stat().st_mtime + 60
    os.utime(idea, (newer, newer))

    issues = Mod(mod_root).validate_game_log(log_path, require_fresh=True)

    assert {issue.code for issue in issues} == {
        "stale_game_log",
        "game_log_error",
    }


def test_release_gate_fails_on_mod_owned_engine_error(tmp_path: Path) -> None:
    mod_root = tmp_path / "mod"
    idea = mod_root / "common/ideas/OWN_ideas.txt"
    idea.parent.mkdir(parents=True)
    idea.write_text("ideas = { country = { } }\n", encoding="utf-8")
    log_path = tmp_path / "error.log"
    _write_log(log_path)

    report = run_release_gate(
        mod_root,
        min_probes=0,
        error_log=log_path,
    )

    assert not report.success
    assert report.game_log is not None
    assert len(report.game_log.entries) == 1
    assert report.to_dict()["game_log"] is not None
