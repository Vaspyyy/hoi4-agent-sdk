"""The disk store of per-file results for inherited files (``hoi4._disk_cache``).

A "new process" is simulated by forgetting what this process holds in
memory (``_disk_cache.forget`` and ``clear_file_cache``), so the next read
must come from the store files.
"""

from __future__ import annotations

import dataclasses
import json
import os
import pathlib
import re
import threading
from collections.abc import Collection
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import hoi4._disk_cache as disk_cache
import hoi4._file_cache as file_cache
import hoi4.mod._catalogs as catalogs
from hoi4 import Mod
from hoi4._disk_cache import NAMES_ON_DISK, PAIRS_ON_DISK, DiskAnalysis
from hoi4._file_cache import cached_file_result, cached_files_result, clear_file_cache
from hoi4.countries import country_loc_entries
from hoi4.localisation import LOCALISATION_ON_DISK, localisation_keys_in, localization_file_entries
from hoi4.map_editing import (
    decode_map_buildings,
    decode_strategic_regions,
    encode_map_buildings,
    encode_strategic_regions,
    parse_map_buildings,
    parse_strategic_regions,
)
from hoi4.map_topology import _decode_topology, _encode_topology
from hoi4.mod._catalogs import (
    _PROVINCE_TYPES_ON_DISK,
    _REFERENCES_ON_DISK,
    _SHARED_FOCUSES_ON_DISK,
    _TEXTURES_ON_DISK,
    _province_types,
    _shared_focuses_in,
    _sprite_textures,
    _technology_unlocks,
)
from hoi4.mod._core import _STATE_HEADERS_ON_DISK
from hoi4.mod._equipment import _special_project_unlocks
from hoi4.mod._loading import _COUNTRY_LOC_ON_DISK
from hoi4.script_references import (
    DEFINITIONS_ON_DISK,
    Definitions,
    ScriptRef,
    decode_file_references,
    definitions_of,
    encode_file_references,
    file_references,
)
from hoi4.syntax import parse
from hoi4.states import _state_name_from_filename, state_index_header


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig" if relative.endswith(".yml") else "utf-8")
    return path


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private cache folder, with files counted as settled at once."""

    cache = tmp_path / "cache"
    monkeypatch.setenv("HOI4_SDK_CACHE_DIR", str(cache))
    monkeypatch.delenv("HOI4_SDK_DISK_CACHE", raising=False)
    monkeypatch.setattr(file_cache, "_SETTLED_NS", 0)
    new_process()
    yield cache / "file-results"
    new_process()


def new_process() -> None:
    disk_cache.forget()
    clear_file_cache()


class Counted:
    """A text analysis that counts its calls."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, text: str) -> frozenset[str]:
        self.calls += 1
        return frozenset(text.split())


def read_through(path: Path, compute: Counted) -> frozenset[str]:
    result: frozenset[str] = cached_file_result("words", path, compute, disk=NAMES_ON_DISK)
    return result


# ── hits and misses ────────────────────────────────────────────────


def test_a_new_process_reads_the_stored_result(store: Path, tmp_path: Path) -> None:
    path = write(tmp_path / "game", "common/a.txt", "alpha beta")
    compute = Counted()
    assert read_through(path, compute) == {"alpha", "beta"}
    disk_cache.flush()
    assert len(list(store.glob("names-*.json"))) == 1

    new_process()
    assert read_through(path, compute) == {"alpha", "beta"}
    assert compute.calls == 1


def test_a_changed_size_is_read_again(store: Path, tmp_path: Path) -> None:
    path = write(tmp_path / "game", "common/a.txt", "alpha beta")
    compute = Counted()
    read_through(path, compute)
    disk_cache.flush()
    stat = path.stat()
    path.write_text("alpha beta gamma")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))

    new_process()
    assert read_through(path, compute) == {"alpha", "beta", "gamma"}
    assert compute.calls == 2


def test_a_changed_modification_time_is_read_again(store: Path, tmp_path: Path) -> None:
    path = write(tmp_path / "game", "common/a.txt", "alpha beta")
    compute = Counted()
    read_through(path, compute)
    disk_cache.flush()
    stat = path.stat()
    path.write_text("gamma delta")  # the same size
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    new_process()
    assert read_through(path, compute) == {"gamma", "delta"}
    assert compute.calls == 2


def test_another_code_version_never_reads_old_results(
    store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write(tmp_path / "game", "common/a.txt", "alpha beta")
    compute = Counted()
    read_through(path, compute)
    disk_cache.flush()

    new_process()
    monkeypatch.setattr(disk_cache, "code_stamp", lambda: "0123456789abcdef")
    read_through(path, compute)
    assert compute.calls == 2


def test_the_code_stamp_covers_every_source_file_of_the_package() -> None:
    package = Path(disk_cache.__file__).parent
    sources = sorted(package.rglob("*.py"))
    assert package / "script_references.py" in sources
    assert package / "mod" / "_catalogs.py" in sources
    assert len(disk_cache.code_stamp()) == 16


def test_a_result_is_stored_under_its_purpose(store: Path, tmp_path: Path) -> None:
    path = write(tmp_path / "game", "common/a.txt", "alpha beta")
    cached_file_result("first", path, frozenset, disk=NAMES_ON_DISK)
    disk_cache.flush()
    new_process()
    compute = Counted()
    cached_file_result("second", path, compute, disk=NAMES_ON_DISK)
    assert compute.calls == 1


@pytest.mark.parametrize(
    "content",
    [b"", b"{not json", b'{"format": 1, "entries": {"x": [1, 2, 3]}', b"[1, 2, 3]", b"\xff\xfe"],
    ids=["empty", "broken", "truncated", "not an object", "not text"],
)
def test_a_broken_store_file_is_ignored_and_rebuilt(
    store: Path, tmp_path: Path, content: bytes
) -> None:
    path = write(tmp_path / "game", "common/a.txt", "alpha beta")
    compute = Counted()
    read_through(path, compute)
    disk_cache.flush()
    (target,) = store.glob("names-*.json")
    target.write_bytes(content)

    new_process()
    assert read_through(path, compute) == {"alpha", "beta"}
    assert compute.calls == 2
    disk_cache.flush()
    assert json.loads(target.read_text(encoding="utf-8"))["entries"]


def test_an_entry_of_the_wrong_shape_is_computed_again(store: Path, tmp_path: Path) -> None:
    path = write(tmp_path / "game", "common/a.txt", "alpha beta")
    analysis = DiskAnalysis("shapes", list, lambda data: data["missing"])
    calls: list[str] = []

    def compute(text: str) -> list[str]:
        calls.append(text)
        return text.split()

    cached_file_result("shape", path, compute, disk=analysis)
    disk_cache.flush()
    new_process()
    assert cached_file_result("shape", path, compute, disk=analysis) == ["alpha", "beta"]
    assert len(calls) == 2


def test_switched_off_nothing_is_read_or_written(
    store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write(tmp_path / "game", "common/a.txt", "alpha beta")
    compute = Counted()
    read_through(path, compute)
    disk_cache.flush()
    assert list(store.glob("names-*.json"))

    for value in ("off", "0", "false", "NO"):
        monkeypatch.setenv("HOI4_SDK_DISK_CACHE", value)
        new_process()
        read_through(path, compute)
    assert compute.calls == 5

    monkeypatch.setenv("HOI4_SDK_DISK_CACHE", "off")
    other = write(tmp_path / "game", "common/b.txt", "gamma")
    new_process()
    read_through(other, compute)
    disk_cache.flush()
    assert all(str(other) not in key for key in stored_keys(store))


def test_cache_folder_follows_the_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOI4_SDK_CACHE_DIR", str(tmp_path / "own"))
    assert disk_cache.cache_root() == tmp_path / "own"
    monkeypatch.delenv("HOI4_SDK_CACHE_DIR")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert disk_cache.cache_root() == tmp_path / "xdg" / "hoi4-sdk"
    monkeypatch.setenv("XDG_CACHE_HOME", "relative/path")
    assert disk_cache.cache_root() == Path.home() / ".cache" / "hoi4-sdk"
    monkeypatch.delenv("XDG_CACHE_HOME")
    assert disk_cache.cache_root() == Path.home() / ".cache" / "hoi4-sdk"


def test_an_unwritable_cache_folder_is_no_error(
    store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("not a folder")
    monkeypatch.setenv("HOI4_SDK_CACHE_DIR", str(blocker))
    path = write(tmp_path / "game", "common/a.txt", "alpha beta")
    compute = Counted()
    read_through(path, compute)
    disk_cache.flush()
    new_process()
    read_through(path, compute)
    assert compute.calls == 2


def test_writers_keep_each_others_results(store: Path, tmp_path: Path) -> None:
    first = write(tmp_path / "game", "common/a.txt", "alpha")
    second = write(tmp_path / "game", "common/b.txt", "beta")
    compute = Counted()
    read_through(first, compute)
    disk_cache.flush()
    # Another process that never saw the first result adds its own.
    new_process()
    store_object = disk_cache._store(NAMES_ON_DISK.name)
    store_object.loaded = {}
    read_through(second, compute)
    disk_cache.flush()

    new_process()
    read_through(first, compute)
    read_through(second, compute)
    assert compute.calls == 2


def test_writers_at_the_same_time_leave_a_whole_file(store: Path, tmp_path: Path) -> None:
    paths = [write(tmp_path / "game", f"common/{index}.txt", f"word{index}") for index in range(8)]
    errors: list[BaseException] = []

    def writer(index: int) -> None:
        # Each thread stands for a process with its own store.
        try:
            store_object = disk_cache._Store(NAMES_ON_DISK.name)
            for _ in range(5):
                for path in paths:
                    stat = path.stat()
                    store_object.put(f"{path}\0words", stat.st_size, stat.st_mtime_ns, [str(index)])
                store_object.flush()
        except BaseException as error:  # pragma: no cover - reported below
            errors.append(error)

    threads = [threading.Thread(target=writer, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    (target,) = store.glob("names-*.json")
    data = json.loads(target.read_text(encoding="utf-8"))
    assert len(data["entries"]) == len(paths)
    assert not list(store.glob("*.tmp"))


def test_entries_of_deleted_files_are_dropped(store: Path, tmp_path: Path) -> None:
    gone = write(tmp_path / "game", "common/a.txt", "alpha")
    kept = write(tmp_path / "game", "common/b.txt", "beta")
    compute = Counted()
    read_through(gone, compute)
    read_through(kept, compute)
    disk_cache.flush()
    gone.unlink()
    third = write(tmp_path / "game", "common/c.txt", "gamma")
    read_through(third, compute)
    disk_cache.flush()
    keys = stored_keys(store)
    assert not [key for key in keys if key.startswith(str(gone))]
    assert [key for key in keys if key.startswith(str(kept))]


def test_a_new_file_is_not_stored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOI4_SDK_CACHE_DIR", str(tmp_path / "cache"))
    new_process()
    path = write(tmp_path / "game", "common/a.txt", "alpha")  # seconds old
    read_through(path, Counted())
    disk_cache.flush()
    assert not (tmp_path / "cache" / "file-results").exists()
    new_process()


def test_several_files_are_one_entry(store: Path, tmp_path: Path) -> None:
    first = write(tmp_path / "game", "map/a.csv", "1")
    second = write(tmp_path / "game", "map/b.csv", "2")
    calls: list[int] = []

    def compute() -> frozenset[str]:
        calls.append(1)
        return frozenset({first.read_text(), second.read_text()})

    assert cached_files_result("pair", [first, second], compute, NAMES_ON_DISK) == {"1", "2"}
    disk_cache.flush()
    new_process()
    assert cached_files_result("pair", [first, second], compute, NAMES_ON_DISK) == {"1", "2"}
    assert len(calls) == 1
    stat = second.stat()
    second.write_text("3")
    os.utime(second, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    new_process()
    assert cached_files_result("pair", [first, second], compute, NAMES_ON_DISK) == {"1", "3"}
    assert len(calls) == 2


def stored_keys(store: Path) -> list[str]:
    keys: list[str] = []
    for path in store.glob("*.json"):
        keys.extend(json.loads(path.read_text(encoding="utf-8"))["entries"])
    return keys


# ── what a Mod stores ──────────────────────────────────────────────

GAME_FILES = {
    "events/game_events.txt": (
        "add_namespace = game_ns\n"
        "country_event = { id = game_ns.1 is_triggered_only = yes\n"
        " option = { name = game_ns.1.a set_country_flag = game_flag } }\n"
    ),
    "common/scripted_effects/game_effects.txt": (
        "game_make_template = {\n"
        ' division_template = { name = "Game Template" regiments = { infantry = { x = 0 y = 0 } } }\n'
        "}\n"
    ),
    "common/ideas/game_ideas.txt": "ideas = { country = { game_idea = { } } }\n",
    "common/characters/game_characters.txt": "characters = { GAM_leader = { name = GAM_leader } }\n",
    "common/characters/game_upper.txt": "CHARACTERS = { GAM_upper = { name = GAM_upper } }\n",
    "common/country_tags/00_countries.txt": 'GAM = "countries/Game.txt"\n',
    "common/countries/Game.txt": "color = { 1 2 3 }\n",
    "history/countries/GAM - Game.txt": "capital = 1\n",
    "history/states/1-Home.txt": (
        "state = { id = 1 name = STATE_1 manpower = 1 state_category = rural\n"
        " provinces = { 1 } history = { owner = GAM add_core_of = GAM } }\n"
    ),
    "interface/game.gfx": (
        "spriteTypes = { spriteType = { name = GFX_game texturefile = gfx/game.dds } }\n"
    ),
    "map/definition.csv": "0;0;0;0;land;false;unknown;0\n1;10;20;30;land;false;plains;1\n",
    "map/buildings.txt": "1;arms_factory;10.0;5.0;10.0;0.0;0\n",
    "map/strategicregions/1-Region.txt": (
        "strategic_region = { id = 1 name = REGION_1 provinces = { 1 } }\n"
    ),
    # One file for each catalog scan, so a warm run shows they are all stored.
    "common/national_focus/game_focus.txt": (
        "focus_tree = { id = game_tree focus = { id = GAM_focus x = 0 y = 0 cost = 1 } }\n"
        "shared_focus = { id = GAM_shared x = 0 y = 0 cost = 1 }\n"
        "shared_focus = { id = GAM_below x = 0 y = 1 cost = 1\n"
        " prerequisite = { focus = GAM_shared } allow_branch = { has_war = yes } }\n"
        "joint_focus = { id = GAM_joint x = 1 y = 0 cost = 1 allow_branch = { always = yes } }\n"
    ),
    "common/names/00_names.txt": "GAM = { male = { names = { A } } }\n",
    "common/units/infantry.txt": "sub_units = { infantry = { } }\n",
    "common/technologies/game_tech.txt": (
        "technologies = { game_tech = { enable_equipments = { game_rifle } } }\n"
    ),
    "common/special_projects/projects/game_project.txt": (
        "sp_game = { project_output = { enable_equipments = { game_gun } } }\n"
    ),
    "common/idea_tags/00_idea.txt": "idea_categories = { game_slot = { slot = game_slot } }\n",
    "common/country_tag_aliases/game_aliases.txt": "GAA = { }\n",
    "common/military_industrial_organization/organizations/game_mio.txt": "game_mio = { }\n",
    "interface/game_focus.gfx": "spriteTypes = { spriteType = { name = GFX_game_focus } }\n",
}


@pytest.fixture
def game(tmp_path: Path) -> Path:
    root = tmp_path / "game"
    for relative, text in GAME_FILES.items():
        write(root, relative, text)
    write(
        root,
        "localisation/english/game_l_english.yml",
        'l_english:\n game_ns.1.a: "Fine"\n STATE_1: "Home"\n GAM: "Gameland"\n'
        ' GAM_ADJ: "Gamish"\n game_value: "[?game_variable] left"\n',
    )
    write(root, "localisation/german/game_l_german.yml", 'l_german:\n GAM: "Spielland"\n')
    return root


def make_mod(tmp_path: Path, game: Path, **options: object) -> Mod:
    root = tmp_path / "mod"
    if not root.exists():
        write(root, "descriptor.mod", 'name = "test"\n')
        write(
            root,
            "events/mod_events.txt",
            "add_namespace = mod_ns\n"
            "country_event = { id = mod_ns.1 is_triggered_only = yes\n"
            " option = { name = mod_ns.1.a game_make_template = yes add_ideas = game_idea\n"
            " country_event = missing_ns.9 } }\n",
        )
        write(root, "common/characters/mod_characters.txt", "characters = { MOD_hero = { name = MOD_hero } }\n")
        write(root, "localisation/english/mod_l_english.yml", 'l_english:\n mod_ns.1.a: "Go"\n')
    return Mod(root, hoi4_install=game, **options)  # type: ignore[arg-type]


def findings(mod: Mod) -> list[tuple[object, ...]]:
    return sorted(
        (f.code, f.severity, f.message, str(f.file_path), f.line)
        for f in mod.validate(stage="release", advice=True)
    )


def test_findings_are_the_same_cold_warm_and_switched_off(
    store: Path, tmp_path: Path, game: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cold = findings(make_mod(tmp_path, game))
    assert list(store.glob("file_references-*.json"))

    new_process()
    read: list[str] = []
    original = catalogs.file_references

    def counted(
        path: str, relative: str, text: str, *, own: bool, characters: Collection[str] = ()
    ) -> object:
        read.append(path)
        return original(path, relative, text, own=own, characters=characters)

    monkeypatch.setattr(catalogs, "file_references", counted)
    warm = findings(make_mod(tmp_path, game))
    assert warm == cold
    # The game's references come from the store, the mod's are read.
    assert read
    assert not [path for path in read if path.startswith(str(game))]

    monkeypatch.setenv("HOI4_SDK_DISK_CACHE", "off")
    new_process()
    assert findings(make_mod(tmp_path, game)) == cold


def test_nothing_of_the_mod_itself_is_stored(store: Path, tmp_path: Path, game: Path) -> None:
    mod = make_mod(tmp_path, game)
    mod.validate(stage="release")
    mod.get_loc("GAM", language="german")
    disk_cache.flush()
    keys = stored_keys(store)
    assert keys
    assert any(key.startswith(str(game)) for key in keys)
    assert not [key for key in keys if key.startswith(str(mod.mod_root))]


def test_a_mod_inside_the_game_folder_is_not_stored(store: Path, tmp_path: Path, game: Path) -> None:
    root = game / "mod" / "inner"
    write(root, "descriptor.mod", 'name = "inner"\n')
    write(root, "events/inner.txt", "add_namespace = inner\ncountry_event = { id = inner.1 }\n")
    mod = Mod(root, hoi4_install=game)
    mod.validate(stage="release")
    disk_cache.flush()
    assert not [key for key in stored_keys(store) if key.startswith(str(root))]


def test_a_base_mod_is_stored_under_its_own_files(
    store: Path, tmp_path: Path, game: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "base"
    write(base, "descriptor.mod", 'name = "base"\n')
    write(
        base,
        "common/scripted_effects/base_effects.txt",
        'base_make_template = { division_template = { name = "Base Template" } }\n',
    )
    mod = make_mod(tmp_path, game, base_mod_paths=[base])
    first = findings(mod)
    disk_cache.flush()
    keys = stored_keys(store)
    assert any(key.startswith(str(base / "common/scripted_effects/base_effects.txt")) for key in keys)
    assert not [key for key in keys if key.startswith(str(mod.mod_root))]

    new_process()
    read: list[str] = []
    original = catalogs.file_references

    def counted(
        path: str, relative: str, text: str, *, own: bool, characters: Collection[str] = ()
    ) -> object:
        read.append(path)
        return original(path, relative, text, own=own, characters=characters)

    monkeypatch.setattr(catalogs, "file_references", counted)
    again = make_mod(tmp_path, game, base_mod_paths=[base])
    assert findings(again) == first
    assert not [path for path in read if path.startswith((str(base), str(game)))]


# Game files a warm run still reads: the tag list (read at construction) and
# the state files the OOB check builds full models of.
_READ_WHEN_WARM = ("common/country_tags/", "history/states/")


def every_catalog(mod: Mod) -> list[object]:
    """Findings and every catalog that scans the inherited files, as plain data."""

    return [
        findings(mod),
        sorted(mod._known_focus_ids()),
        sorted(mod._known_focus_tree_ids()),
        sorted(mod._known_event_ids()),
        sorted(mod._known_idea_ids()),
        sorted(mod._known_character_ids()),
        sorted(mod._known_advisor_idea_ids()),
        sorted(mod._known_organization_ids()),
        sorted(mod._known_country_tags()),
        sorted(mod._known_technology_ids()),
        sorted(mod._known_equipment_ids()),
        sorted(mod._known_country_name_pool_tags()),
        sorted(mod._known_sub_unit_types()),
        sorted(mod._known_focus_icons()),
        sorted(mod._known_sprite_textures().items()),
        sorted(mod._localisation_value_names()),
        sorted(mod._equipment_unlock_technologies().items()),
        sorted(mod._special_project_unlocks().items()),
        sorted(mod._effective_province_types().items()),
        sorted(mod._dynamic_country_tags()),
        sorted(mod._known_idea_categories()),
        sorted(mod._definitions_in("common/country_tag_aliases").tags),
        mod._shared_focuses(),
        sorted(mod._declared_event_namespaces()),
        mod._has_loc("game_value"),
        mod.get_loc("STATE_1"),
    ]


def test_a_warm_run_reads_no_other_game_file(
    store: Path, tmp_path: Path, game: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(game, "events/more_events.txt", "add_namespace = more_ns\n")
    make_mod(tmp_path, game)
    # An event in a namespace only the game declares makes the namespace scan run.
    write(tmp_path / "mod", "events/borrowed.txt", "country_event = { id = more_ns.1 }\n")
    cold = every_catalog(make_mod(tmp_path, game))
    assert "more_ns" in cold[-3]  # type: ignore[operator]
    disk_cache.flush()
    new_process()
    read: list[Path] = []
    original = pathlib.Path.read_bytes

    def counted(self: pathlib.Path) -> bytes:
        read.append(self)
        return original(self)

    monkeypatch.setattr(pathlib.Path, "read_bytes", counted)
    assert every_catalog(make_mod(tmp_path, game)) == cold
    again = sorted(
        path.relative_to(game).as_posix()
        for path in read
        if path.is_relative_to(game) and path.suffix in {".txt", ".yml", ".gfx", ".csv"}
    )
    assert again
    assert [path for path in again if not path.startswith(_READ_WHEN_WARM)] == []


# ── round trips of every stored shape ──────────────────────────────

REFERENCE_SCRIPT = """
add_namespace = trip
country_event = {
    id = trip.1
    hidden = yes
    trigger = { has_country_flag = trip_flag GER = { has_idea = trip_idea } }
    option = {
        name = trip.1.a
        hidden_effect = { set_country_flag = trip_flag }
        if = { limit = { has_country_flag = trip_flag } add_ideas = trip_idea }
        every_country = { limit = { tag = FRA } add_ideas = trip_idea }
        start_civil_war = { ideology = fascism size = 0.5 set_country_flag = civ_flag }
        5 = { transfer_state = 6 }
        country_event = { id = trip.2 days = 3 }
        create_equipment_variant = { name = "Trip" type = light_tank_equipment_1 }
    }
}
"""


@pytest.mark.parametrize(
    ("relative", "text"),
    [
        ("events/trip.txt", REFERENCE_SCRIPT),
        ("common/ideas/trip.txt", "ideas = { country = { trip_idea = { } } hidden_ideas = { h = { } } }"),
        (
            "common/units/equipment/trip.txt",
            "equipments = { tank_1 = { archetype = tank } tank_2 = { archetype = tank } }\n"
            "duplicate_archetypes = { heavy = { archetype = tank variant_name = { a = x b = y } } }",
        ),
        ("common/national_focus/trip.txt", "focus_tree = { id = trip_tree focus = { id = f1 } }"),
        ("common/technologies/trip.txt", "technologies = { t1 = { } @x = 1 }"),
        ("common/country_tags/trip.txt", "TRP = countries/Trip.txt"),
        ("common/characters/trip.txt", "characters = { c = { advisor = { idea_token = trip_adv } } }"),
        ("common/military_industrial_organization/trip.txt", "mio = { }"),
        ("common/country_tag_aliases/trip.txt", "TRA = { }"),
        ("history/countries/TRP - Trip.txt", "capital = 1 set_country_flag = trip_flag"),
        ("events/empty.txt", ""),
    ],
)
def test_file_references_round_trip(relative: str, text: str) -> None:
    found = file_references(f"/game/{relative}", relative, text, own=False)
    data = json.loads(json.dumps(_REFERENCES_ON_DISK.encode(found)))
    assert _REFERENCES_ON_DISK.decode(data) == found
    assert decode_file_references(json.loads(json.dumps(encode_file_references(found)))) == found


def test_reference_round_trip_covers_every_ref_field() -> None:
    found = file_references("/game/events/trip.txt", "events/trip.txt", REFERENCE_SCRIPT, own=False)
    refs = [ref for _, ref in found.refs()]
    assert any(ref.hidden for ref in refs)
    assert any(ref.guarded for ref in refs)
    assert any(ref.iterator for ref in refs)
    assert any(ref.scope_key for ref in refs)
    assert any(not ref.statement for ref in refs)
    assert any(ref.root >= 0 for ref in refs)


def round_trip(analysis: DiskAnalysis, value: object) -> object:
    return analysis.decode(json.loads(json.dumps(analysis.encode(value), ensure_ascii=False)))


@pytest.mark.parametrize(
    ("analysis", "value"),
    [
        (NAMES_ON_DISK, frozenset({"a", "b", "ü"})),
        (NAMES_ON_DISK, frozenset()),
        (_TEXTURES_ON_DISK, _sprite_textures(GAME_FILES["interface/game.gfx"])),
        (_TEXTURES_ON_DISK, {}),
        (_STATE_HEADERS_ON_DISK, state_index_header(GAME_FILES["history/states/1-Home.txt"])),
        (_STATE_HEADERS_ON_DISK, state_index_header("state = { id = 4 name = X }")),
        (_STATE_HEADERS_ON_DISK, state_index_header("nothing = here")),
        (LOCALISATION_ON_DISK, {"KEY": 'A "quoted"\nvalue', "Ü": "ß"}),
        (
            DiskAnalysis("regions", encode_strategic_regions, decode_strategic_regions),
            parse_strategic_regions(
                "strategic_region = { id = 3 name = R provinces = { 1 2 } weather = { } }\n"
                "strategic_region = { id = 4 provinces = { } }",
                "map/strategicregions/3.txt",
            ),
        ),
        (
            DiskAnalysis("buildings", encode_map_buildings, decode_map_buildings),
            parse_map_buildings("1;arms_factory;10.5;5.25;1e3;-0.1;0\nbroken\n2;x;1;2;3;4;5\n"),
        ),
        (
            DiskAnalysis("topology", _encode_topology, _decode_topology),
            (frozenset({1, 2, 3}), {1: {2}, 2: {1, 3}, 3: {2}, 4: set()}, frozenset({3})),
        ),
        (
            PAIRS_ON_DISK,
            _technology_unlocks(GAME_FILES["common/technologies/game_tech.txt"])
            + _special_project_unlocks(
                GAME_FILES["common/special_projects/projects/game_project.txt"]
            ),
        ),
        (PAIRS_ON_DISK, ()),
        (_PROVINCE_TYPES_ON_DISK, _province_types(GAME_FILES["map/definition.csv"])),
        (_PROVINCE_TYPES_ON_DISK, {}),
        (_COUNTRY_LOC_ON_DISK, country_loc_entries('l_english:\n GAM: "A"\n GAM_ADJ: "B"\n')),
        (NAMES_ON_DISK, localisation_keys_in('l_english:\n A:0 "x"\n b: "y"\n')),
        (
            _SHARED_FOCUSES_ON_DISK,
            _shared_focuses_in(GAME_FILES["common/national_focus/game_focus.txt"]),
        ),
        (_SHARED_FOCUSES_ON_DISK, {}),
    ],
    ids=[
        "names", "no names", "textures", "no textures", "state header", "state without owner",
        "no state", "localisation", "regions", "buildings", "topology", "unlock pairs",
        "no pairs", "province types", "no province types", "country names",
        "localisation keys", "shared focuses", "no shared focuses",
    ],
)
def test_every_stored_shape_round_trips(analysis: DiskAnalysis, value: object) -> None:
    assert round_trip(analysis, value) == value


def test_shared_focuses_keep_no_condition_apart_from_one_that_always_holds(
    store: Path, tmp_path: Path, game: Path
) -> None:
    found = _shared_focuses_in(GAME_FILES["common/national_focus/game_focus.txt"])
    expected = {
        "GAM_shared": (frozenset(), None),
        "GAM_below": (frozenset({"GAM_shared"}), "has_war = yes"),
        "GAM_joint": (frozenset(), ""),
    }
    assert found == expected
    assert list(found) == list(expected)
    assert round_trip(_SHARED_FOCUSES_ON_DISK, found) == expected

    cold = make_mod(tmp_path, game)._shared_focuses()
    disk_cache.flush()
    assert any(
        key.endswith("\0shared_focuses") and "game_focus.txt" in key for key in stored_keys(store)
    )
    new_process()
    warm = make_mod(tmp_path, game)._shared_focuses()
    assert warm == cold
    assert warm[1]["GAM_shared"] is None
    assert warm[1]["GAM_joint"] == ""


def test_country_tags_and_characters_come_from_the_store(
    store: Path, tmp_path: Path, game: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tag files behind ``_defined_country_tags`` and every character file."""

    cold = make_mod(tmp_path, game)
    tags, characters = cold._defined_country_tags(), cold._known_character_ids()
    assert "GAM" in tags
    assert {"GAM_leader", "GAM_upper", "MOD_hero"} <= characters
    disk_cache.flush()
    new_process()
    parsed: list[str] = []
    original_definitions, original_characters = catalogs.definitions_of, catalogs._character_ids

    def counted_definitions(document: object, label: str) -> object:
        parsed.append(label)
        return original_definitions(document, label)  # type: ignore[arg-type]

    def counted_characters(text: str) -> frozenset[str]:
        parsed.append(text)
        return original_characters(text)

    monkeypatch.setattr(catalogs, "definitions_of", counted_definitions)
    monkeypatch.setattr(catalogs, "_character_ids", counted_characters)
    warm = make_mod(tmp_path, game)
    assert warm._defined_country_tags() == tags
    assert warm._known_character_ids() == characters
    # Only the mod's own character file is read again.
    own = tmp_path / "mod" / "common" / "characters" / "mod_characters.txt"
    assert parsed == [own.read_text(encoding="utf-8")]


def test_localisation_entries_round_trip_through_the_store(store: Path, tmp_path: Path) -> None:
    path = write(tmp_path / "game", "localisation/english/a_l_english.yml", 'l_english:\n A: "x"\n')
    first = cached_file_result(
        "entries", path, lambda text: {"A": text.strip()}, disk=LOCALISATION_ON_DISK
    )
    disk_cache.flush()
    new_process()
    again = cached_file_result("entries", path, lambda text: {}, disk=LOCALISATION_ON_DISK)
    assert again == first
    assert localization_file_entries(path) == {"A": "x"}


# ── the state index reads only what it keeps ───────────────────────


@settings(max_examples=300)
@given(
    stem=st.text(alphabet=st.sampled_from(list("0123456789 -_\t　abcX")), max_size=12),
    state_id=st.integers(min_value=-20, max_value=200),
)
def test_state_name_from_filename_matches_the_pattern(stem: str, state_id: int) -> None:
    path = Path(f"/s/{stem}.txt")
    expected = re.sub(rf"^\s*{state_id}\s*[-_\s]*", "", path.stem).strip()
    assert _state_name_from_filename(path, state_id) == expected


DEFINITION_FILES = {
    "events/trip.txt": REFERENCE_SCRIPT,
    "common/ideas/trip.txt": (
        "ideas = { country = { trip_idea = { } } political_advisor = { adv = { } } }"
    ),
    "common/units/equipment/trip.txt": (
        "equipments = { tank_1 = { archetype = tank } tank_2 = { archetype = tank } }\n"
        "duplicate_archetypes = { heavy = { archetype = tank variant_name = { a = x b = y } } }"
    ),
    "common/national_focus/trip.txt": "focus_tree = { id = trip_tree focus = { id = f1 } }",
    "common/technologies/trip.txt": "technologies = { t1 = { } @x = 1 }",
    "common/country_tag_aliases/trip.txt": "TRA = { }",
    "common/characters/trip.txt": "characters = { c = { advisor = { idea_token = trip_adv } } }",
    "common/military_industrial_organization/organizations/trip.txt": "trip_mio = { }",
}


@pytest.mark.parametrize("relative", sorted(DEFINITION_FILES))
def test_definitions_round_trip(relative: str) -> None:
    found = definitions_of(parse(DEFINITION_FILES[relative]), relative)
    assert found != Definitions()
    assert round_trip(DEFINITIONS_ON_DISK, found) == found


def test_the_stored_shapes_cover_every_field() -> None:
    """A new field of a stored dataclass must get an encoder too."""

    assert [field.name for field in dataclasses.fields(ScriptRef)] == [
        "kind", "action", "value", "offset", "line", "column", "scope", "key",
        "certain", "statement", "scope_key", "hidden", "guarded", "iterator", "root",
    ]
    assert [field.name for field in dataclasses.fields(Definitions)] == [
        "events", "ideas", "advisor_ideas", "focus_trees", "technologies", "equipment",
        "equipment_copies", "tags", "organizations",
    ]
