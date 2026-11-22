"""Sections load on first use; save() and discard() reload only what changed."""

from __future__ import annotations

import copy
import dataclasses
import os
import time
import warnings
from pathlib import Path

import pytest

from hoi4 import ExternalModificationError, Mod
from hoi4.mod._core import SECTIONS, Section, SectionRegistry

EVENTS = """add_namespace = test
country_event = {
\tid = test.1
\ttitle = test.1.t
\tdesc = test.1.d
\toption = { name = test.1.a }
}
"""

STATE = """state = {
\tid = 1
\tname = "STATE_1"
\tmanpower = 100
\tstate_category = town
\thistory = { owner = GER }
\tprovinces = { 1 }
}
"""

IDEAS = """ideas = {
\tcountry = {
\t\ttest_spirit = { modifier = { stability_factor = 0.1 } }
\t}
}
"""

HOUR_NS = 3_600_000_000_000


def _write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _age(path: Path) -> None:
    """Give a file an old timestamp, as files of a mod that is not being edited have."""

    old = time.time_ns() - HOUR_NS
    os.utime(path, ns=(old, old))


@pytest.fixture
def mod_root(tmp_path: Path) -> Path:
    _write(tmp_path, "events/test.txt", EVENTS)
    _write(tmp_path, "history/states/1-Test.txt", STATE)
    _write(tmp_path, "common/ideas/test.txt", IDEAS)
    return tmp_path


# ── Lazy loading ─────────────────────────────────────────────────


def test_construction_loads_no_section(mod_root: Path) -> None:
    mod = Mod(mod_root)

    assert mod._section_loaded == set()
    assert "_events" not in vars(mod)


def test_a_section_loads_on_first_use_and_only_that_section(mod_root: Path) -> None:
    mod = Mod(mod_root)

    assert mod.list_events() == ["test.1"]

    assert mod._section_loaded == {"events"}
    assert "_states" not in vars(mod)


def test_strict_loading_loads_every_section_at_construction(mod_root: Path) -> None:
    mod = Mod(mod_root, strict_loading=True)

    assert mod._section_loaded == {section.name for section in SECTIONS.sections}


def test_strict_loading_still_fails_at_construction(mod_root: Path) -> None:
    _write(mod_root, "common/ideas/broken.txt", "ideas = { country = {\n")

    with pytest.raises(RuntimeError, match="broken.txt"):
        Mod(mod_root, strict_loading=True)


def test_load_diagnostics_cover_every_section_in_load_order(mod_root: Path) -> None:
    _write(mod_root, "events/broken.txt", "country_event = {\n")
    _write(mod_root, "common/ideas/broken.txt", "ideas = { country = {\n")
    _write(mod_root, "common/characters/broken.txt", "characters = {\n")
    reference = [(d.section, d.path) for d in Mod(mod_root).load_diagnostics]

    mod = Mod(mod_root)
    mod.list_ideas()  # load the last of them first
    mod.list_events()
    diagnostics = [(d.section, d.path) for d in mod.load_diagnostics]

    assert [section for section, _ in diagnostics] == ["character", "event", "idea"]
    assert diagnostics == reference


def test_a_loader_that_needs_its_own_section_fails_clearly(
    mod_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def load_events(self: Mod) -> None:
        self._load_section("events")

    monkeypatch.setattr(Mod, "_load_events", load_events, raising=False)
    mod = Mod(mod_root)

    with pytest.raises(RuntimeError, match="events section needs the section itself"):
        mod.list_events()
    assert "events" not in mod._section_loaded


def test_copy_and_repr_do_not_load_or_recurse(mod_root: Path) -> None:
    mod = Mod(mod_root)
    mod.list_events()

    duplicate = copy.deepcopy(mod)
    repr(mod)

    assert mod._section_loaded == {"events"}
    assert duplicate._section_loaded == {"events"}
    assert duplicate.list_ideas() == ["test_spirit"]
    assert not hasattr(mod, "__deepcopy__")
    # An object whose __init__ never ran has no sections to load.
    assert getattr(object.__new__(Mod), "_events", None) is None


# ── Partial reload ───────────────────────────────────────────────


def test_saving_an_event_keeps_other_sections_in_memory(mod_root: Path) -> None:
    mod = Mod(mod_root)
    states = mod._states
    state = mod.get_state(1)
    events = mod._events

    mod.update_event("test.1", title="new_title")
    mod.save()

    assert "events" not in mod._section_loaded
    assert mod._states is states
    assert mod.get_state(1) is state
    assert mod._events is not events
    assert mod.get_event("test.1").title == "new_title"


def test_discard_reloads_only_what_had_edits(mod_root: Path) -> None:
    mod = Mod(mod_root)
    ideas = mod._ideas
    mod.update_event("test.1", title="dropped_title")

    mod.discard()

    assert mod._ideas is ideas
    assert mod.get_event("test.1").title == "test.1.t"


def test_discard_reloads_a_section_whose_files_changed_on_disk(mod_root: Path) -> None:
    mod = Mod(mod_root)
    states = mod._states
    assert mod.list_events() == ["test.1"]
    _write(mod_root, "events/more.txt", EVENTS.replace("test.1", "test.2"))

    mod.discard()

    assert mod._states is states
    assert mod.list_events() == ["test.1", "test.2"]


def test_a_dirty_name_without_a_section_unloads_everything(mod_root: Path) -> None:
    mod = Mod(mod_root)
    mod.list_events()
    mod.list_states()
    mod._dirty.add("something_new")

    mod.discard()

    assert mod._section_loaded == set()


def test_a_written_descriptor_reloads_everything(mod_root: Path) -> None:
    mod = Mod(mod_root)
    mod.list_events()
    descriptor = mod_root / "descriptor.mod"
    mod._script_file_updates[descriptor] = 'name = "Test"\n'
    mod._dirty.add("script_files")

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mod.save()

    assert descriptor.is_file()
    assert mod._section_loaded == set()


# ── The reload check ─────────────────────────────────────────────


def test_the_reload_check_catches_a_wrong_registry_entry(
    mod_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOI4_VERIFY_RELOAD", "1")
    # Give the "events" dirty name to the ideas section. (A name no section
    # claims would unload everything, which is always right.)
    def wrong_entry(section: Section) -> Section:
        if section.name == "events":
            return dataclasses.replace(section, dirty_names=())
        if section.name == "ideas":
            return dataclasses.replace(section, dirty_names=("ideas", "events"))
        return section

    wrong = SectionRegistry([wrong_entry(section) for section in SECTIONS.sections])
    monkeypatch.setattr(Mod, "_sections", wrong)
    mod = Mod(mod_root)
    mod.update_event("test.1", title="dropped_title")

    with pytest.raises(AssertionError, match=r"section 'events', _events: first difference at \['test.1'\]"):
        mod.discard()


def test_the_reload_check_passes_and_keeps_what_was_unloaded(
    mod_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOI4_VERIFY_RELOAD", "1")
    mod = Mod(mod_root)
    mod.update_event("test.1", title="new_title")

    mod.save()

    # The check loaded every section to compare it, then unloaded them again.
    assert mod._section_loaded == set()


# ── Fingerprints ─────────────────────────────────────────────────


def test_an_external_edit_is_refused(mod_root: Path) -> None:
    path = mod_root / "events/test.txt"
    _age(path)
    mod = Mod(mod_root)
    mod.update_event("test.1", title="new_title")
    path.write_text(EVENTS.replace("test.1.d", "other.d"), encoding="utf-8")

    with pytest.raises(ExternalModificationError, match="events/test.txt"):
        mod.save()


def test_a_same_size_rewrite_within_the_timestamp_tick_is_refused(mod_root: Path) -> None:
    path = mod_root / "events/test.txt"
    mod = Mod(mod_root)
    mod.update_event("test.1", title="new_title")
    before = path.stat()
    path.write_text(EVENTS.replace("test.1.d", "test.1.x"), encoding="utf-8")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))

    with pytest.raises(ExternalModificationError, match="events/test.txt"):
        mod.save()


def test_a_same_size_rewrite_with_its_old_time_set_back_is_refused(mod_root: Path) -> None:
    """Size and time cannot prove a file unchanged: the text is compared before it is replaced."""

    path = mod_root / "events/test.txt"
    _age(path)
    before = path.stat()
    mod = Mod(mod_root)
    mod.update_event("test.1", title="new_title")
    path.write_text(EVENTS.replace("test.1.d", "test.1.x"), encoding="utf-8")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = path.stat()
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)

    with pytest.raises(ExternalModificationError, match="events/test.txt"):
        mod.preview()
    with pytest.raises(ExternalModificationError, match="events/test.txt"):
        mod.save()
    assert "test.1.x" in path.read_text(encoding="utf-8")


def test_a_touch_is_not_an_edit(mod_root: Path) -> None:
    path = mod_root / "events/test.txt"
    _age(path)
    mod = Mod(mod_root)
    mod.update_event("test.1", title="new_title")
    path.touch()

    mod.save()

    assert "new_title" in path.read_text(encoding="utf-8")


def test_a_file_the_sdk_wrote_is_not_an_external_edit(mod_root: Path) -> None:
    path = mod_root / "events/test.txt"
    _age(path)
    mod = Mod(mod_root)
    mod.update_event("test.1", title="first_title")
    mod.save()
    mod.update_event("test.1", title="second_title")
    mod.save()
    path.touch()
    mod.update_event("test.1", title="third_title")

    mod.save()

    assert "third_title" in path.read_text(encoding="utf-8")


def test_the_baseline_keeps_no_file_contents(mod_root: Path) -> None:
    for path in mod_root.rglob("*.txt"):
        _age(path)
    mod = Mod(mod_root)

    assert set(mod._fingerprints) == {path for path in mod_root.rglob("*.txt")}
    assert mod._read_digests == {}
    assert not hasattr(mod, "_source_baseline")


# ── Transactions ─────────────────────────────────────────────────


def test_rollback_unloads_a_section_first_loaded_inside(mod_root: Path) -> None:
    mod = Mod(mod_root)
    mod.list_states()

    with pytest.raises(ValueError, match="stop"):
        with mod.transaction():
            mod.update_event("test.1", title="inside_title")
            raise ValueError("stop")

    assert mod._section_loaded == {"states"}
    assert mod.get_event("test.1").title == "test.1.t"
    assert mod.preview() == ""


def test_rollback_keeps_unsaved_edits_made_before(mod_root: Path) -> None:
    mod = Mod(mod_root)
    mod.update_event("test.1", title="before_title")
    preview = mod.preview()

    with mod.transaction():
        mod.update_event("test.1", title="inside_title")
        mod.set_state_properties(1, manpower=5)

    assert mod.get_event("test.1").title == "before_title"
    assert mod.get_state(1).manpower == "100"
    assert "states" not in mod._section_loaded or not mod._dirty_states
    assert mod.preview() == preview
    mod.save()
    assert "before_title" in (mod_root / "events/test.txt").read_text(encoding="utf-8")
