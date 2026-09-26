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


def test_game_log_attributes_all_observed_engine_file_shapes(tmp_path: Path) -> None:
    mod_root = tmp_path / "mod"
    paths = (
        "common/ideas/quoted.txt",
        "history/units/plain.txt",
        "mod/local.mod",
    )
    for relative in paths:
        target = mod_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("test\n", encoding="utf-8")
    log_path = tmp_path / "error.log"
    log_path.write_text(
        (
            '[12:00:00][no_game_date][persistent.cpp:67]: Error in file: '
            '"common/ideas/quoted.txt" near line: 5\n'
            '[12:00:01][1936.01.01.12][taskforce.cpp:1153]: file: '
            "history/units/plain.txt line: 389: Could not find proper "
            "equipment variant. Skip creating Ship\n"
            '[12:00:02][no_game_date][dlc.cpp:218]: Invalid version in  file: '
            "mod/local.mod line: 7\n"
        ),
        encoding="utf-8",
    )

    report = parse_hoi4_error_log(log_path, mod_root)

    assert [entry.relative_path for entry in report.entries] == list(paths)
    assert [entry.line for entry in report.entries] == [5, 389, 7]
    assert report.entries[1].error_class == "equipment_variant"
    assert report.unscoped_entry_count == 0


def test_game_log_attributes_runtime_and_equipment_messages(tmp_path: Path) -> None:
    mod_root = tmp_path / "mod"
    event = mod_root / "events/owned.txt"
    history = mod_root / "history/countries/ABC - Test_Country.txt"
    event.parent.mkdir(parents=True)
    history.parent.mkdir(parents=True)
    event.write_text("country_event = { }\n", encoding="utf-8")
    history.write_text("capital = 1\n", encoding="utf-8")
    log_path = tmp_path / "error.log"
    log_path.write_text(
        "[12:00:00][no_game_date][effectimplementation.cpp:5524]: "
        "events/owned.txt:24: recruit_character should only happen in "
        "game/history files in order to be executed only at game start\n"
        "[12:00:01][1936.01.01.12][equipment_effects.cpp:814]: "
        "'history/countries/ABC - Test_Country.txt:7: create_equipment_variant': "
        "'Test Class' - Modular type 'ship_hull_light_1' appears to not have "
        "been unlocked for ABC as no chassis variant exists.\n"
        "[12:00:02][1936.01.01.12][character_manager.cpp:261]: Failed to "
        "generate a name for a character of origins Test Country and for "
        "country Test Country\n",
        encoding="utf-8",
    )

    report = parse_hoi4_error_log(log_path, mod_root)

    assert len(report.entries) == 3
    assert report.groups == {
        "character_name_generation": 1,
        "equipment_variant_unlock": 1,
        "runtime_recruit_character": 1,
    }
    assert [entry.line for entry in report.entries] == [24, 7, None]
    assert report.unscoped_entry_count == 0


def test_game_log_path_extraction_does_not_whitelist_directories(
    tmp_path: Path,
) -> None:
    mod_root = tmp_path / "mod"
    owned = mod_root / "future_domain" / "owned.asset"
    owned.parent.mkdir(parents=True)
    owned.write_text("test\n", encoding="utf-8")
    log_path = tmp_path / "error.log"
    log_path.write_text(
        "[12:00:00][no_game_date][future.cpp:1]: file: "
        "future_domain/owned.asset line: 2: broken\n",
        encoding="utf-8",
    )

    report = parse_hoi4_error_log(log_path, mod_root)

    assert len(report.entries) == 1
    assert report.entries[0].relative_path == "future_domain/owned.asset"


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


def _incremental_log(tmp_path: Path) -> tuple[Path, Path, bytes, bytes]:
    mod_root = tmp_path / "mod"
    target = mod_root / "events/café.txt"
    target.parent.mkdir(parents=True)
    target.write_text("test\n", encoding="utf-8")
    first = (
        '[12:00:00][no_game_date][persistent.cpp:67]: Unknown effect: café\r\n'
        'continued in file: "events/café.txt" near line: 5\r\n'
    ).encode()
    last = (
        '[12:00:01][no_game_date][effect.cpp:358]: Second error '
        'in file: "events/café.txt" near line: 9'
    ).encode()
    return mod_root, tmp_path / "error.log", first, last


def test_incremental_all_byte_splits_and_final_flush(tmp_path: Path) -> None:
    """Every split includes headers, paths, UTF-8, and CRLF continuations."""
    mod_root, log_path, first, last = _incremental_log(tmp_path)
    complete = first + last
    log_path.write_bytes(complete)
    expected = parse_hoi4_error_log(log_path, mod_root)
    mtime = log_path.stat().st_mtime
    for split in range(len(complete) + 1):
        log_path.write_bytes(complete[:split])
        os.utime(log_path, (mtime, mtime))
        initial = parse_hoi4_error_log(log_path, mod_root, incremental=True)
        poll = parse_hoi4_error_log(
            log_path, mod_root, start_offset=initial.next_offset, incremental=True
        )
        assert poll.next_offset == initial.next_offset
        assert not poll.entries
        assert poll.ignored_entry_count == poll.unscoped_entry_count == 0
        with log_path.open("ab") as stream:
            stream.write(complete[split:])
        os.utime(log_path, (mtime, mtime))
        resumed = parse_hoi4_error_log(
            log_path, mod_root, start_offset=poll.next_offset, incremental=True
        )
        assert resumed.next_offset == len(first)
        final = parse_hoi4_error_log(log_path, mod_root, start_offset=resumed.next_offset)
        assert initial.entries + resumed.entries + final.entries == expected.entries
        assert final.next_offset == len(complete)
        assert not parse_hoi4_error_log(
            log_path, mod_root, start_offset=final.next_offset
        ).entries


def test_incremental_byte_at_a_time_and_multiple_completed_records(tmp_path: Path) -> None:
    mod_root, log_path, first, last = _incremental_log(tmp_path)
    complete = first + first + last
    log_path.write_bytes(complete)
    expected = parse_hoi4_error_log(log_path, mod_root)
    mtime = log_path.stat().st_mtime
    batch = parse_hoi4_error_log(log_path, mod_root, incremental=True)
    assert batch.entries == expected.entries[:2]
    assert batch.next_offset == 2 * len(first)

    log_path.write_bytes(b"")
    offset = 0
    entries = ()
    for byte in complete:
        with log_path.open("ab") as stream:
            stream.write(bytes([byte]))
        os.utime(log_path, (mtime, mtime))
        report = parse_hoi4_error_log(
            log_path, mod_root, start_offset=offset, incremental=True
        )
        entries += report.entries
        offset = report.next_offset
    assert offset == 2 * len(first)
    assert entries == expected.entries[:2]
    final = parse_hoi4_error_log(log_path, mod_root, start_offset=offset)
    assert entries + final.entries == expected.entries


def test_incremental_boundaries_precede_attribution_and_time_filters(tmp_path: Path) -> None:
    mod_root, log_path, first, _ = _incremental_log(tmp_path)
    for tail, unscoped in [
        (b"Unscoped error", 1),
        (b'Error in file: "events/other.txt" near line: 2', 0),
    ]:
        last = b"[12:00:01][no_game_date][effect.cpp:1]: " + tail
        log_path.write_bytes(first + last)
        initial = parse_hoi4_error_log(log_path, mod_root, incremental=True)
        assert len(initial.entries) == 1
        assert initial.next_offset == len(first)
        assert initial.ignored_entry_count == initial.unscoped_entry_count == 0
        poll = parse_hoi4_error_log(
            log_path, mod_root, start_offset=initial.next_offset, incremental=True
        )
        assert not poll.entries
        assert poll.next_offset == initial.next_offset
        assert poll.ignored_entry_count == poll.unscoped_entry_count == 0
        final = parse_hoi4_error_log(log_path, mod_root, start_offset=poll.next_offset)
        assert not final.entries
        assert final.ignored_entry_count == 1
        assert final.unscoped_entry_count == unscoped
        assert final.next_offset == len(first + last)

        timestamp = initial.entries[0].timestamp
        assert timestamp is not None
        filtered = parse_hoi4_error_log(
            log_path, mod_root, incremental=True, since=timestamp.replace(second=1)
        )
        assert not filtered.entries
        assert filtered.ignored_entry_count == 1
        assert filtered.unscoped_entry_count == 0
        assert filtered.next_offset == len(first)


def test_incremental_preserves_invalid_offset_errors(tmp_path: Path) -> None:
    import pytest

    mod_root, log_path, first, last = _incremental_log(tmp_path)
    log_path.write_bytes(first + last)
    report = parse_hoi4_error_log(log_path, mod_root, incremental=True)
    log_path.write_bytes(b"")
    for incremental in (False, True):
        for offset in (-1, report.next_offset):
            with pytest.raises(ValueError, match="start_offset must be between"):
                parse_hoi4_error_log(
                    log_path, mod_root, start_offset=offset, incremental=incremental
                )


def test_incremental_facade_and_cli(tmp_path: Path) -> None:
    import json
    import subprocess
    import sys

    mod_root, log_path, first, last = _incremental_log(tmp_path)
    log_path.write_bytes(first)
    mod = Mod(mod_root)
    assert not mod.validate_game_log(log_path, incremental=True)
    assert len(mod.validate_game_log(log_path)) == 1
    log_path.write_bytes(first + last)
    assert len(mod.validate_game_log(log_path, incremental=True)) == 1
    assert not mod.validate_game_log(log_path, incremental=True, start_offset=len(first))
    assert len(mod.validate_game_log(log_path, start_offset=len(first))) == 1
    command = [
        sys.executable, str(Path(__file__).parents[1] / "scripts/parse_hoi4_log.py"),
        str(mod_root), "--log", str(log_path), "--json",
    ]
    initial = subprocess.run(command + ["--incremental"], capture_output=True, text=True)
    assert initial.returncode == 1, initial.stderr
    data = json.loads(initial.stdout)
    assert data["owned_errors"] == 1
    assert data["next_offset"] == len(first)
    resume = command + ["--start-offset", str(data["next_offset"])]
    poll = subprocess.run(resume + ["--incremental"], capture_output=True, text=True)
    assert poll.returncode == 0, poll.stderr
    assert json.loads(poll.stdout)["next_offset"] == len(first)
    final = subprocess.run(resume, capture_output=True, text=True)
    assert final.returncode == 1, final.stderr
    assert json.loads(final.stdout)["owned_errors"] == 1
    assert json.loads(final.stdout)["next_offset"] == len(first + last)
