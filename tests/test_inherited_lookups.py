from pathlib import Path

import pytest

from hoi4 import EventOption, Focus, Mod, NotFoundError, ReadOnlyError


def write(root: Path, path: str, text: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


@pytest.fixture
def mod(tmp_path: Path) -> Mod:
    game = tmp_path / "game"
    root = tmp_path / "mod"
    root.mkdir()
    write(game, "events/game.txt", "add_namespace = test\ncountry_event = { id = test.1 title = original option = { name = a } }\n")
    write(game, "common/national_focus/game.txt", "focus_tree = { id = tree focus = { id = one x = 0 y = 0 } }\n")
    return Mod(root, hoi4_install=game)


def test_inherited_event_and_focus_are_read_only_copies(mod: Mod) -> None:
    before = (mod.list_events(), mod.list_focus_trees(), mod.preview(), mod.validate())
    event = mod.get_event("test.1")
    tree = mod.get_focus_tree("tree")
    event.title = "changed"
    tree.focuses[0].x = 99
    assert mod.get_event("test.1").title == "original"
    assert mod.get_focus("tree", "one").x == 0
    assert mod.list_events(include_vanilla=True) == ["test.1"]
    assert mod.list_focus_trees(include_vanilla=True) == ["tree"]
    assert mod.get_event("missing", default=None) is None
    assert mod.get_focus("missing", "one", default=None) is None
    with pytest.raises(NotFoundError):
        mod.get_event("test.1", include_vanilla=False)
    with pytest.raises(NotFoundError):
        mod.get_focus_tree("tree", include_vanilla=False)
    assert (mod.list_events(), mod.list_focus_trees(), mod.preview(), mod.validate()) == before
    assert not mod.save(require_changes=False).written_files


@pytest.mark.parametrize("operation,path", [
    (lambda m: m.update_event("test.1", title="new"), "events/game.txt"),
    (lambda m: m.delete_event("test.1"), "events/game.txt"),
    (lambda m: m.add_event_option("test.1", EventOption(name="b")), "events/game.txt"),
    (lambda m: m.update_focus("tree", "one", x=1), "common/national_focus/game.txt"),
    (lambda m: m.add_focus("tree", Focus(id="two", x=1, y=1)), "common/national_focus/game.txt"),
    (lambda m: m.remove_focus("tree", "one"), "common/national_focus/game.txt"),
    (lambda m: m.update_focus_tree("tree", default=True), "common/national_focus/game.txt"),
    (lambda m: m.delete_focus_tree("tree"), "common/national_focus/game.txt"),
])
def test_inherited_edits_require_staging(mod: Mod, operation, path: str) -> None:
    with pytest.raises(ReadOnlyError) as error:
        operation(mod)
    assert f'mod.load_inherited_content("{path}")' in str(error.value)
    mod.load_inherited_content(path)
    operation(mod)


@pytest.mark.parametrize("action", ["discard", "save", "reload"])
def test_inherited_cache_dropped(mod: Mod, action: str) -> None:
    mod.get_event("test.1")
    assert mod._inherited_files
    getattr(mod, action)()
    assert not mod._inherited_files


def test_mod_definitions_win(tmp_path: Path) -> None:
    game, root = tmp_path / "game", tmp_path / "mod"
    write(game, "events/game.txt", "country_event = { id = test.1 title = game }\n")
    write(root, "events/mod.txt", "country_event = { id = test.1 title = mod }\n")
    write(game, "common/national_focus/game.txt", "focus_tree = { id = tree focus = { id = one x = 1 y = 0 } }\n")
    write(root, "common/national_focus/mod.txt", "focus_tree = { id = tree focus = { id = one x = 2 y = 0 } }\n")
    mod = Mod(root, hoi4_install=game)
    assert mod.get_event("test.1").title == "mod"
    assert mod.get_focus("tree", "one").x == 2
    assert not mod._inherited_index


def test_inherited_idea_lookup_and_edit(tmp_path: Path) -> None:
    game, root = tmp_path / "game", tmp_path / "mod"
    root.mkdir()
    write(game, "common/ideas/game.txt", "ideas = { country = { test_spirit = { picture = generic } } }")
    mod = Mod(root, hoi4_install=game)
    before = (mod.list_ideas(), mod.preview(), mod.validate())
    idea = mod.get_idea("test_spirit")
    idea.icon = "changed"
    assert mod.get_idea("test_spirit").icon == "generic"
    assert mod.list_ideas(include_vanilla=True) == ["test_spirit"]
    assert mod.get_idea("test_spirit", include_vanilla=False, default=None) is None
    assert (mod.list_ideas(), mod.preview(), mod.validate()) == before
    with pytest.raises(ReadOnlyError, match="load_inherited_content"):
        mod.update_idea("test_spirit", icon="other")
    mod.load_inherited_content("common/ideas/game.txt")
    mod.update_idea("test_spirit", icon="other")


def test_inherited_dynamic_modifier(tmp_path: Path) -> None:
    game, root = tmp_path / "game", tmp_path / "mod"
    root.mkdir()
    write(game, "common/dynamic_modifiers/game.txt", "test_modifier = { icon = generic }\n")
    mod = Mod(root, hoi4_install=game)
    assert mod.get_dynamic_modifier("test_modifier").id == "test_modifier"
    assert mod.list_dynamic_modifiers() == []
    assert mod.list_dynamic_modifiers(include_vanilla=True) == ["test_modifier"]
    with pytest.raises(ReadOnlyError, match="load_inherited_content"):
        mod.delete_dynamic_modifier("test_modifier")
    mod.load_inherited_content("common/dynamic_modifiers/game.txt")
    mod.delete_dynamic_modifier("test_modifier")


def test_inherited_decision_and_category(tmp_path: Path) -> None:
    game, root = tmp_path / "game", tmp_path / "mod"
    root.mkdir()
    write(game, "common/decisions/categories/game.txt", "test_category = { icon = generic }\n")
    write(game, "common/decisions/game.txt", "test_category = { test_decision = { cost = 10 } }\n")
    mod = Mod(root, hoi4_install=game)
    assert mod.get_decision("test_decision").id == "test_decision"
    assert mod.get_decision_category("test_category").id == "test_category"
    assert mod.list_decisions() == []
    assert mod.list_decisions(include_vanilla=True) == ["test_decision"]
    assert mod.list_decision_categories(include_vanilla=True) == ["test_category"]
    with pytest.raises(ReadOnlyError, match="load_inherited_content"):
        mod.delete_decision("test_decision")
    mod.load_inherited_content("common/decisions/game.txt")
    mod.delete_decision("test_decision")


def test_inherited_bookmark(tmp_path: Path) -> None:
    game, root = tmp_path / "game", tmp_path / "mod"
    root.mkdir()
    write(game, "common/bookmarks/game.txt", 'bookmarks = { bookmark = { name = "START" date = 1936.1.1.12 } }')
    mod = Mod(root, hoi4_install=game)
    assert mod.get_bookmark("START").name == "START"
    assert mod.list_bookmarks() == []
    assert mod.list_bookmarks(include_vanilla=True) == ["START"]
    with pytest.raises(ReadOnlyError, match="load_inherited_content"):
        mod.delete_bookmark("START")
    mod.load_inherited_content("common/bookmarks/game.txt")
    mod.delete_bookmark("START")


def test_inherited_on_actions_compose(tmp_path: Path) -> None:
    game, root = tmp_path / "game", tmp_path / "mod"
    root.mkdir()
    write(game, "common/on_actions/game.txt", "on_actions = { on_startup = { events = { game.1 } } }\n")
    mod = Mod(root, hoi4_install=game)
    assert len(mod.get_on_action_occurrences("on_startup")) == 1
    assert mod.list_on_actions() == []
    assert mod.list_on_actions(include_vanilla=True) == ["on_startup"]
    with pytest.raises(ReadOnlyError, match="create_on_action"):
        mod.update_on_action("on_startup", effect="log = test")
    mod.create_on_action("on_startup", effect="log = test")
    assert len(mod.get_on_action_occurrences("on_startup")) == 2
