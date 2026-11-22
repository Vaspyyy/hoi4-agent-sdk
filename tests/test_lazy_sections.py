"""Sections load on first use; save() and discard() reload only what changed."""

from __future__ import annotations

import copy
import dataclasses
import os
import threading
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


def test_the_reload_check_is_not_the_first_reader_of_a_file(mod_root: Path) -> None:
    """The check loads every section to compare; without it the states are first read later.

    The test suite runs with ``HOI4_VERIFY_RELOAD=1``. If the reads of the
    check counted, a change after a save and before the first real read
    would be refused under test and built on everywhere else.
    """

    path = mod_root / "history/states/1-Test.txt"
    for file in mod_root.rglob("*.txt"):
        _age(file)
    mod = Mod(mod_root)
    mod.update_event("test.1", title="mine")
    mod.save()
    assert "states" not in mod._section_loaded
    assert path not in mod._read_digests

    path.write_text(STATE.replace("manpower = 100", "manpower = 777"), encoding="utf-8")
    mod.set_state_properties(1, state_category="city")
    mod.save()

    saved = path.read_text(encoding="utf-8")
    assert "manpower = 777" in saved and "city" in saved


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


def test_a_file_is_taken_as_it_is_when_its_section_first_loads(mod_root: Path) -> None:
    """Touched, changed or created after Mod() and before first use: read as a fresh Mod would."""

    path = mod_root / "events/test.txt"
    other = mod_root / "events/other.txt"
    _age(path)
    mod = Mod(mod_root)
    assert mod._section_loaded == set()
    # All three happen behind the SDK's back, before the events are loaded.
    path.write_text(EVENTS.replace("test.1.d", "changed.d"), encoding="utf-8")
    os.utime(path, None)
    other.write_text(EVENTS.replace("test.1", "test.2"), encoding="utf-8")

    assert mod.get_event("test.1").description == "changed.d"
    mod.update_event("test.1", title="new_title")
    mod.update_event("test.2", title="other_title")
    mod.save()

    saved = path.read_text(encoding="utf-8")
    assert "new_title" in saved and "changed.d" in saved
    assert "other_title" in other.read_text(encoding="utf-8")


def test_a_touch_before_the_section_loads_is_not_an_edit(mod_root: Path) -> None:
    path = mod_root / "events/test.txt"
    _age(path)
    mod = Mod(mod_root)
    path.touch()
    mod.update_event("test.1", title="new_title")

    mod.save()

    assert "new_title" in path.read_text(encoding="utf-8")


def test_a_change_after_the_section_loaded_stays_refused_on_a_second_read(mod_root: Path) -> None:
    """A later read must not make a change look safe while older text is still in memory."""

    path = mod_root / "events/test.txt"
    _age(path)
    mod = Mod(mod_root)
    mod.update_event("test.1", title="new_title")
    path.write_text(EVENTS.replace("test.1.d", "longer.changed.d"), encoding="utf-8")
    # Something reads the file again (here directly; a lookup or a second
    # section does the same).
    mod._read_source(path)

    with pytest.raises(ExternalModificationError, match="events/test.txt"):
        mod.save()


@pytest.mark.parametrize("way_out", ["discard", "reload"])
@pytest.mark.parametrize("same_time", [False, True], ids=["new time", "old time set back"])
@pytest.mark.parametrize("kind", ["event", "scripted trigger"])
def test_after_a_refused_save_discard_takes_the_changed_file(
    mod_root: Path, kind: str, same_time: bool, way_out: str
) -> None:
    """The refusal saw the change in the text; ``stat`` alone may not show it to discard()."""

    if kind == "event":
        path = mod_root / "events/test.txt"
        old, new = "test.1.d", "test.9.d"

        def edit(mod: Mod) -> None:
            mod.update_event("test.1", title="mine")
    else:
        path = _write(mod_root, "common/scripted_triggers/st.txt", "my_trig = { always = yes }\n")
        old, new = "always = yes", "has_war = no"

        def edit(mod: Mod) -> None:
            mod.update_definition("scripted_trigger", "my_trig", "always = no")

    _age(path)
    mod = Mod(mod_root)
    edit(mod)
    before = path.stat()
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
    if same_time:
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(ExternalModificationError):
        mod.save()

    getattr(mod, way_out)()
    if kind == "event":
        assert mod.get_event("test.1").description == "test.9.d"
        edit(mod)
        mod.save()
        saved = path.read_text(encoding="utf-8")
        assert "mine" in saved and "test.9.d" in saved
    else:
        edit(mod)
        mod.save()
        assert "always = no" in path.read_text(encoding="utf-8")


def test_a_shallow_copy_keeps_its_own_account_of_what_is_loaded(mod_root: Path) -> None:
    mod = Mod(mod_root)
    clone = copy.copy(mod)

    assert clone.list_events() == ["test.1"]
    # The copy's load must not make the original believe it holds the events.
    assert mod._section_loaded == set()
    assert mod.list_events() == ["test.1"]
    assert mod.list_states() == [1]
    assert clone.list_states() == [1]


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


# ── Threads ──────────────────────────────────────────────────────


def test_a_second_thread_waits_for_a_section_that_is_loading(
    mod_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No thread reads a half-filled section: reading from several threads works as before."""

    started = threading.Event()
    load_events = Mod._load_events

    def slow_load(self: Mod) -> None:
        started.set()
        time.sleep(0.2)  # the second thread asks for the events in this window
        load_events(self)

    monkeypatch.setattr(Mod, "_load_events", slow_load)
    mod = Mod(mod_root)
    results: dict[str, list[str]] = {}

    def first() -> None:
        results["first"] = mod.list_events()

    def second() -> None:
        assert started.wait(5)
        results["second"] = mod.list_events()

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert results == {"first": ["test.1"], "second": ["test.1"]}
    assert "_events" in vars(mod)


def test_a_loading_section_is_not_on_the_object_until_it_is_complete(
    mod_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}
    load_events = Mod._load_events

    def watching_load(self: Mod) -> None:
        seen["before"] = "_events" in vars(self)
        load_events(self)
        seen["filled"] = sorted(self._events)
        seen["after"] = "_events" in vars(self)

    monkeypatch.setattr(Mod, "_load_events", watching_load)
    mod = Mod(mod_root)

    assert mod.list_events() == ["test.1"]
    assert seen == {"before": False, "filled": ["test.1"], "after": False}
    assert "_events" in vars(mod)


# ── Files that appear after their section loaded ─────────────────

OOB = (
    'division_template = {\n\tname = "Inf"\n\tregiments = { infantry = { x = 0 y = 0 } }\n}\n'
    "units = { }\n"
)


def test_a_state_file_that_appeared_later_is_loaded_properly_after_discard(mod_root: Path) -> None:
    """A lookup finds the new file; the loader has to see it too once edits are dropped."""

    mod = Mod(mod_root)
    assert mod.list_states() == [1]
    _write(mod_root, "history/states/2-New.txt", STATE.replace("id = 1", "id = 2"))
    assert mod.get_state(2).id == 2

    mod.discard()  # with HOI4_VERIFY_RELOAD=1 this also compares with a fresh Mod

    assert mod.list_states() == [1, 2]
    assert mod.list_states() == Mod(mod_root).list_states()


def test_a_broken_oob_file_that_appeared_later_is_reported_after_discard(mod_root: Path) -> None:
    _write(mod_root, "history/units/TST_1936.txt", OOB)
    mod = Mod(mod_root)
    assert mod.list_oobs() == ["TST_1936"]
    _write(mod_root, "history/units/TST_new.txt", OOB + "units = {\n")
    mod.get_oob("TST_new")

    mod.discard()  # with HOI4_VERIFY_RELOAD=1 this also compares with a fresh Mod

    reported = [(d.section, d.path.name) for d in mod.load_diagnostics]
    assert reported == [(d.section, d.path.name) for d in Mod(mod_root).load_diagnostics]
    assert ("oob", "TST_new.txt") in reported


# ── A loader that fails ──────────────────────────────────────────


@pytest.fixture
def country_root(mod_root: Path) -> Path:
    _write(mod_root, "common/country_tags/00_tags.txt", 'TST = "countries/Test.txt"\n')
    _write(mod_root, "common/countries/Test.txt", "color = { 1 2 3 }\n")
    _write(mod_root, "history/countries/TST - Test.txt", "capital = 1\n")
    _write(mod_root, "history/units/TST_1936.txt", OOB)
    return mod_root


def test_a_section_loaded_inside_another_loader_keeps_its_own_files(country_root: Path) -> None:
    """The OOB loader needs the countries: their files are theirs, not the OOBs'."""

    country_file = country_root / "common/countries/Test.txt"
    mod = Mod(country_root)

    mod.list_oobs()

    assert country_file in mod._section_files["countries"]
    assert country_file not in mod._section_files["oobs"]
    assert country_file in mod._section_reads["countries"]
    assert country_file not in mod._section_reads["oobs"]


def test_a_failed_loader_leaves_a_section_it_loaded_on_the_way_intact(
    country_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    country_file = country_root / "common/countries/Test.txt"
    oob_file = country_root / "history/units/TST_1936.txt"

    original = Mod._load_oobs
    attempts: list[int] = []

    def load_oobs(self: Mod) -> None:
        attempts.append(1)
        if len(attempts) > 1:
            original(self)
            return
        assert self._countries  # loads the countries inside this loader
        self._original_files[oob_file] = self._read_source(oob_file)
        raise RuntimeError("the OOBs cannot be read")

    monkeypatch.setattr(Mod, "_load_oobs", load_oobs, raising=False)
    mod = Mod(country_root)

    with pytest.raises(RuntimeError, match="the OOBs cannot be read"):
        mod.list_oobs()

    assert mod._section_loaded == {"countries"}
    # The countries are complete: their texts are held, their reads stand.
    assert country_file in mod._original_files
    assert country_file in mod._read_digests
    # Nothing is left of the loader that failed.
    assert oob_file not in mod._original_files
    assert oob_file not in mod._read_digests
    mod.update_country("TST", color=(9, 9, 9))
    mod.save()
    assert "9 9 9" in country_file.read_text(encoding="utf-8")


def test_a_section_that_failed_to_load_takes_the_repaired_file(mod_root: Path) -> None:
    """The attempt that failed holds nothing, so what it read is no baseline."""

    path = mod_root / "common/ideas/test.txt"
    for file in mod_root.rglob("*.txt"):
        _age(file)
    mod = Mod(mod_root, strict_loading=True)
    path.write_text("ideas = { country = {\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="test.txt"):
        mod.discard()
    assert "ideas" not in mod._section_loaded

    path.write_text(IDEAS.replace("0.1", "0.2"), encoding="utf-8")
    mod.update_idea("test_spirit", icon="repaired")
    mod.save()

    saved = path.read_text(encoding="utf-8")
    assert "repaired" in saved and "0.2" in saved


def test_a_plain_value_of_a_section_is_staged_like_the_rest(
    mod_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A loader sets it with ``_stage``: it is not on the object before the section is complete."""

    seen: list[bool] = []
    original = Mod._load_states

    def watching_load(self: Mod) -> None:
        original(self)
        seen.append("_writable_state_directory_stamp" in vars(self))

    monkeypatch.setattr(Mod, "_load_states", watching_load, raising=False)
    mod = Mod(mod_root)

    assert mod.list_states() == [1]
    assert seen == [False]
    assert mod._writable_state_directory_stamp is not None


# ── Lookups of game content ──────────────────────────────────────


@pytest.fixture
def game_root(tmp_path: Path) -> Path:
    game = tmp_path / "game"
    _write(game, "common/country_tags/00_countries.txt", 'GER = "countries/Germany.txt"\n')
    _write(game, "common/countries/Germany.txt", "color = { 1 2 3 }\n")
    _write(
        game,
        "history/countries/GER - Germany.txt",
        "capital = 1\noob = \"GER_1936\"\nset_popularities = { neutrality = 50 }\n"
        "recruit_character = GER_leader\n",
    )
    _write(game, "history/states/1-Game.txt", STATE)
    _write(
        game,
        "common/characters/GER.txt",
        "characters = {\n\tGER_leader = { name = GER_leader }\n}\n",
    )
    _write(
        game,
        "history/units/GER_1936.txt",
        'division_template = {\n\tname = "Inf"\n\tregiments = { infantry = { x = 0 y = 0 } }\n}\n'
        "units = { }\n",
    )
    return game


@pytest.mark.parametrize("ending", ["save", "discard"])
def test_game_content_a_lookup_cached_is_gone_after_save_and_discard(
    tmp_path: Path, game_root: Path, ending: str
) -> None:
    """A lookup caches what it read from the game; a list must not keep naming it."""

    root = tmp_path / "mod"
    _write(root, "events/test.txt", EVENTS)

    def listed(mod: Mod) -> tuple[object, ...]:
        return (
            mod.list_countries(),
            mod.list_states(),
            mod.list_characters(include_vanilla=False),
            mod.list_oobs(include_vanilla=False),
        )

    def findings(mod: Mod) -> list[tuple[str | None, str]]:
        return sorted((f.code, f.message) for f in mod.validate(stage="build"))

    fresh = Mod(root, hoi4_install=game_root)
    expected, expected_findings = listed(fresh), findings(Mod(root, hoi4_install=game_root))
    assert expected == ([], [], [], [])

    mod = Mod(root, hoi4_install=game_root)
    mod.get_country("GER")
    mod.get_state(1)
    mod.get_character("GER_leader")
    mod.get_oob("GER_1936")
    mod.update_event("test.1", title="new_title")
    if ending == "save":
        mod.save()
    else:
        mod.discard()

    assert listed(mod) == expected
    assert findings(mod) == expected_findings
    # validate() looks game content up as well: the same holds after it.
    mod.update_event("test.1", title="another_title")
    mod.discard()
    assert listed(mod) == expected
