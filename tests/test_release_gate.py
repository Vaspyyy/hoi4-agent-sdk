from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from hoi4 import Mod
from hoi4.release_gate import DiffBudget, format_report, measure_unified_diff, run_release_gate


FIXTURE = Path(__file__).parent / "fixtures" / "source_stability_mod"


def _file_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_measure_unified_diff_counts_content_not_headers() -> None:
    diff = """--- a/common/test.txt
+++ b/common/test.txt
@@ -1,2 +1,2 @@
-cost = 5
+cost = 6
 keep = yes
--- a/events/test.txt
+++ b/events/test.txt
@@ -3 +3,2 @@
 event = yes
+extra = yes
"""

    stats = measure_unified_diff(diff)

    assert stats.files == ("common/test.txt", "events/test.txt")
    assert stats.additions == 2
    assert stats.deletions == 1
    assert stats.changed_lines == 3
    assert stats.file_count == 2
    assert stats.hunks == 2


def test_release_gate_is_read_only_and_exercises_every_fixture_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod_root = tmp_path / "source_stability_mod"
    shutil.copytree(FIXTURE, mod_root)
    before = _file_bytes(mod_root)

    def reject_save(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("The read-only release gate must never call Mod.save()")

    monkeypatch.setattr(Mod, "save", reject_save)

    report = run_release_gate(
        mod_root,
        budget=DiffBudget(max_changed_lines=20, max_files=2),
    )

    assert report.success, format_report(report)
    assert {probe.name for probe in report.probes if probe.status == "passed"} == {
        "focus",
        "event",
        "decision",
        "idea",
        "on_action",
        "localization",
        "country",
        "state",
        "ideology",
        "dynamic_modifier",
        "bookmark",
    }
    assert not report.filesystem_changes
    assert _file_bytes(mod_root) == before
    assert all(probe.diff.changed_lines > 0 for probe in report.probes)


def test_release_gate_fails_when_diff_budget_is_exceeded(tmp_path: Path) -> None:
    mod_root = tmp_path / "source_stability_mod"
    shutil.copytree(FIXTURE, mod_root)

    report = run_release_gate(
        mod_root,
        budget=DiffBudget(max_changed_lines=0, max_files=0),
    )

    assert not report.success
    assert any(probe.status == "failed" for probe in report.probes)
    assert any("exceed budget" in probe.reason for probe in report.probes)


def test_diff_budget_rejects_negative_limits() -> None:
    with pytest.raises(ValueError, match="max_changed_lines"):
        DiffBudget(max_changed_lines=-1)
    with pytest.raises(ValueError, match="max_files"):
        DiffBudget(max_files=-1)


def test_default_gate_rejects_a_nearly_empty_probe_corpus(tmp_path: Path) -> None:
    localization = tmp_path / "localisation/english/minimal_l_english.yml"
    localization.parent.mkdir(parents=True)
    localization.write_text("\ufeffl_english:\n key:0 \"Value\"\n", encoding="utf-8")

    report = run_release_gate(tmp_path)

    assert report.completed_probe_count == 1
    assert not report.success
    assert "minimum 6" in format_report(report)


def test_required_probes_must_pass(tmp_path: Path) -> None:
    localization = tmp_path / "localisation/english/minimal_l_english.yml"
    localization.parent.mkdir(parents=True)
    localization.write_text("\ufeffl_english:\n key:0 \"Value\"\n", encoding="utf-8")

    report = run_release_gate(
        tmp_path,
        min_probes=1,
        required_probes=("localization", "dynamic_modifier"),
    )

    assert not report.success
    assert report.missing_required_probes == ("dynamic_modifier",)
    assert report.to_dict()["missing_required_probes"] == ["dynamic_modifier"]
    assert "missing required probes: dynamic_modifier" in format_report(report)


def test_required_probes_reject_unknown_names(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown required probes: imaginary"):
        run_release_gate(tmp_path, required_probes=("imaginary",))
