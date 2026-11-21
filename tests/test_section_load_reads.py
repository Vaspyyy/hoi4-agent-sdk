"""A section's first load reads each file of the mod once and parses it once."""

from __future__ import annotations

import pathlib
from collections import Counter
from pathlib import Path

import pytest

import hoi4.syntax as syntax
from hoi4 import Mod

FILES = {
    "common/characters/c.txt": "characters = { TST_hero = { name = TST_hero } }\n",
    "history/units/TST_1936.txt": (
        'division_template = { name = "Infantry" regiments = { infantry = { x = 0 y = 0 } } }\n'
    ),
    "history/states/1-Home.txt": (
        "state = { id = 1 name = STATE_1 manpower = 1 state_category = rural\n"
        " provinces = { 1 } history = { owner = TST } }\n"
    ),
    "common/national_focus/f.txt": "focus_tree = { id = tst_tree focus = { id = TST_f x = 0 y = 0 } }\n",
    "events/e.txt": "add_namespace = e\ncountry_event = { id = e.1 }\n",
    "common/on_actions/o.txt": "on_actions = { on_startup = { effect = { } } }\n",
    "common/decisions/categories/c.txt": "tst_category = { }\n",
    "common/decisions/d.txt": "tst_category = { tst_decision = { } }\n",
    "common/ideas/i.txt": "ideas = { country = { tst_idea = { } } }\n",
    "common/ideologies/i.txt": "ideologies = { tst_ideology = { types = { } } }\n",
    "common/dynamic_modifiers/m.txt": "tst_modifier = { }\n",
    "common/bookmarks/b.txt": 'bookmarks = { bookmark = { name = TST date = 1936.1.1.12 } }\n',
}


@pytest.fixture
def mod(tmp_path: Path) -> Mod:
    root = tmp_path / "mod"
    (root / "descriptor.mod").parent.mkdir(parents=True)
    (root / "descriptor.mod").write_text('name = "test"\n', encoding="utf-8")
    for relative, text in FILES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return Mod(root)


@pytest.mark.parametrize(
    "section",
    ["characters", "oobs", "states", "focus", "events", "on_actions", "decisions", "ideas",
     "ideologies", "dynamic_modifiers", "bookmarks"],
)
def test_a_first_load_reads_each_file_once(
    mod: Mod, section: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    for dependency in mod._sections.by_name[section].depends_on:
        mod._load_section(dependency)
    reads: Counter[Path] = Counter()
    original = pathlib.Path.read_bytes

    def counted(self: pathlib.Path) -> bytes:
        reads[self] += 1
        return original(self)

    monkeypatch.setattr(pathlib.Path, "read_bytes", counted)
    mod._load_section(section)
    folders = mod._sections.by_name[section].folders
    own = {
        path: count for path, count in reads.items()
        if any(path.is_relative_to(mod.mod_root / folder) for folder in folders)
    }
    assert own, f"the {section} section read none of its files"
    assert {path: count for path, count in own.items() if count > 1} == {}
    assert not mod._section_diagnostics.get(section)


def test_a_first_load_parses_an_event_file_once(mod: Mod, monkeypatch: pytest.MonkeyPatch) -> None:
    parsed: list[str] = []
    original = syntax.parse

    def counted(text: str) -> syntax.Document:
        parsed.append(text)
        return original(text)

    import hoi4.events as events

    monkeypatch.setattr(syntax, "parse", counted)
    monkeypatch.setattr(events, "parse", counted)
    assert mod.list_events() == ["e.1"]
    assert parsed.count(FILES["events/e.txt"]) == 1


def test_a_malformed_file_still_fails_to_load(tmp_path: Path) -> None:
    root = tmp_path / "mod"
    path = root / "events" / "broken.txt"
    path.parent.mkdir(parents=True)
    path.write_text("country_event = { id = e.1\n", encoding="utf-8")
    state = root / "history" / "states" / "1-Broken.txt"
    state.parent.mkdir(parents=True)
    state.write_text("state = { id = 1 provinces = { 1 }\n", encoding="utf-8")
    mod = Mod(root)
    assert mod.list_events() == []
    codes = {(diagnostic.path, diagnostic.message.split(":")[0]) for diagnostic in mod.load_diagnostics}
    assert any(found == path for found, _ in codes)
    assert any(found == state for found, _ in codes)
