from pathlib import Path

import pytest

from hoi4 import BookmarkCountry, EventOption, Focus, Mod, NotFoundError, ReadOnlyError


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


def test_country_state_and_character_reads_do_not_stage(tmp_path: Path) -> None:
    game, root = tmp_path / "game", tmp_path / "mod"
    root.mkdir()
    write(game, "common/country_tags/00.txt", 'GER = "countries/Germany.txt"\n')
    write(game, "common/countries/Germany.txt", "color = { 1 2 3 }\n")
    write(game, "history/countries/GER - Germany.txt", "capital = 1\n")
    write(game, "history/states/1.txt", 'state = { id = 1 name = "STATE_1" history = { owner = GER } }')
    write(game, "common/characters/GER.txt", "characters = { GER_leader = { name = GER_leader } }")
    mod = Mod(root, hoi4_install=game)
    before = (mod.preview(), mod.validate(stage="build"))
    country = mod.get_country("GER")
    country.capital = 99
    state = mod.get_state(1)
    state.owner = "FRA"
    character = mod.get_character("GER_leader")
    character.name = "changed"
    assert mod.get_country("GER").capital == 1
    assert mod.get_state(1).owner == "GER"
    assert mod.get_character("GER_leader").name == "GER_leader"
    assert mod.list_countries() == mod.list_states() == mod.list_characters() == []
    assert "GER" in mod.list_countries(include_vanilla=True)
    assert mod.list_states(include_vanilla=True) == [1]
    assert mod.list_characters(include_vanilla=True) == ["GER_leader"]
    assert (mod.preview(), mod.validate(stage="build")) == before
    mod.set_state_owner(1, "GER", add_core=True)
    assert mod.list_states() == [1]
    mod.update_character("GER_leader", name="new")
    assert mod.list_characters() == ["GER_leader"]


def test_inherited_oob_is_a_copy(tmp_path: Path) -> None:
    game, root = tmp_path / "game", tmp_path / "mod"
    root.mkdir()
    write(game, "history/units/GER_1936.txt", 'division_template = { name = "Inf" regiments = { infantry = { x = 0 y = 0 } } }\nunits = { }\n')
    mod = Mod(root, hoi4_install=game)
    oob = mod.get_oob("GER_1936")
    oob.templates.clear()
    assert len(mod.get_oob("GER_1936").templates) == 1
    assert mod.list_oobs() == []
    assert mod.list_oobs(include_vanilla=True) == ["GER_1936"]


def test_definition_lookup_and_list_scope(tmp_path: Path) -> None:
    game, root = tmp_path / "game", tmp_path / "mod"
    root.mkdir()
    write(game, "common/opinion_modifiers/game.txt", "opinion_modifiers = { game_value = { value = 10 } }")
    mod = Mod(root, hoi4_install=game)
    assert mod.list_definitions("opinion_modifier") == []
    assert mod.list_definitions("opinion_modifier", include_vanilla=True) == ["game_value"]
    assert mod.get_definition("opinion_modifier", "game_value").id == "game_value"
    assert mod.get_definition("opinion_modifier", "game_value", include_vanilla=False, default=None) is None


def test_localisation_and_region_scope(tmp_path: Path) -> None:
    game, root = tmp_path / "game", tmp_path / "mod"
    root.mkdir()
    write(game, "localisation/english/game_l_english.yml", 'l_english:\n test_key:0 "Game"\n')
    write(game, "map/strategicregions/1.txt", "strategic_region = { id = 1 name = X provinces = { 1 } }")
    mod = Mod(root, hoi4_install=game)
    assert mod.get_loc("test_key") == "Game"
    assert mod.get_loc("test_key", include_vanilla=False, default=None) is None
    assert mod.get_strategic_region(1).id == 1
    assert mod.get_strategic_region(1, include_vanilla=False, default=None) is None
    assert mod.list_strategic_regions() == [1]


def test_effective_states_include_inherited_without_prior_lookup(tmp_path: Path) -> None:
    game, root = tmp_path / "game", tmp_path / "mod"
    root.mkdir()
    write(game, "history/states/1.txt", 'state = { id = 1 name = "STATE_1" history = { owner = GER } }')
    mod = Mod(root, hoi4_install=game)
    assert [state.id for state in mod._effective_states()] == [1]
    assert mod.list_states() == []


def test_localisation_mod_only_excludes_base_layers(tmp_path: Path) -> None:
    game, base, root = tmp_path / "game", tmp_path / "base", tmp_path / "mod"
    root.mkdir()
    write(game, "localisation/english/game_l_english.yml", 'l_english:\n game_key:0 "Game"\n')
    write(base, "localisation/english/base_l_english.yml", 'l_english:\n base_key:0 "Base"\n')
    mod = Mod(root, hoi4_install=game, base_mod_paths=[base])
    assert mod.get_loc("base_key") == "Base"
    assert mod.get_loc("game_key") == "Game"
    assert mod.get_loc("base_key", include_vanilla=False, default=None) is None
    assert mod.get_loc("game_key", include_vanilla=False, default=None) is None
    mod.set_loc("base_key", "Changed")
    assert mod.get_loc("base_key", include_vanilla=False) == "Changed"


@pytest.mark.parametrize("delete", [False, True])
def test_edit_inherited_character_preserves_other_file_entries(tmp_path: Path, delete: bool) -> None:
    game, root = tmp_path / "game", tmp_path / "mod"
    root.mkdir()
    source = "characters = { GER_one = { name = ONE } GER_two = { name = TWO } }\n"
    write(game, "common/characters/GER.txt", source)
    mod = Mod(root, hoi4_install=game)
    if delete:
        mod.delete_character("GER_one", remove_recruitment=False)
    else:
        mod.update_character("GER_one", name="NEW")
    result = mod.save(require_changes=True)
    assert result.written_files
    assert (game / "common/characters/GER.txt").read_text() == source
    reloaded = Mod(root, hoi4_install=game)
    assert reloaded.get_character("GER_two", include_vanilla=False).name == "TWO"
    if delete:
        assert reloaded.get_character("GER_one", include_vanilla=False, default=None) is None
    else:
        assert reloaded.get_character("GER_one", include_vanilla=False).name == "NEW"


def test_delete_and_update_inherited_characters_preserves_file(tmp_path: Path) -> None:
    game, root = tmp_path / "game", tmp_path / "mod"
    root.mkdir()
    source = "characters = { GER_one = { name = ONE } GER_two = { name = TWO } GER_three = { name = THREE } }\n"
    write(game, "common/characters/GER.txt", source)
    mod = Mod(root, hoi4_install=game)
    mod.delete_character("GER_one", remove_recruitment=False)
    mod.update_character("GER_two", name="UPDATED")
    result = mod.save(require_changes=True)
    assert result.written_files
    reloaded = Mod(root, hoi4_install=game)
    assert reloaded.list_characters() == ["GER_three", "GER_two"]
    assert reloaded.get_character("GER_two", include_vanilla=False).name == "UPDATED"
    assert reloaded.get_character("GER_three", include_vanilla=False).name == "THREE"
    assert (game / "common/characters/GER.txt").read_text() == source


# The same layered lookup checks run for each supported kind.
_MATRIX = {
    "event": ("events", "country_event = {{ id = {id} title = {marker} option = {{ name = choice }} }}", "get_event", "list_events"),
    "focus_tree": ("common/national_focus", "focus_tree = {{ id = {id} focus = {{ id = {id}_focus x = 0 y = 0 }} }}", "get_focus_tree", "list_focus_trees"),
    "focus": ("common/national_focus", "focus_tree = {{ id = {id} focus = {{ id = {id}_focus x = 0 y = 0 }} }}", "get_focus", "list_focus_trees"),
    "idea": ("common/ideas", "ideas = {{ country = {{ {id} = {{ picture = {marker} }} }} }}", "get_idea", "list_ideas"),
    "decision": ("common/decisions", "matrix_category = {{ {id} = {{ cost = 1 }} }}", "get_decision", "list_decisions"),
    "decision_category": ("common/decisions/categories", "{id} = {{ icon = {marker} }}", "get_decision_category", "list_decision_categories"),
    "bookmark": ("common/bookmarks", "bookmarks = {{ bookmark = {{ name = {id} desc = {marker} date = 1936.1.1.12 GER = {{ history = old }} }} }}", "get_bookmark", "list_bookmarks"),
    "dynamic_modifier": ("common/dynamic_modifiers", "{id} = {{ icon = {marker} }}", "get_dynamic_modifier", "list_dynamic_modifiers"),
    "on_action": ("common/on_actions", "on_actions = {{ {id} = {{ effect = {{ log = {marker} }} }} }}", "get_on_action", "list_on_actions"),
    "on_action_occurrences": ("common/on_actions", "on_actions = {{ {id} = {{ effect = {{ log = {marker} }} }} }}", "get_on_action_occurrences", "list_on_actions"),
}


@pytest.fixture(params=sorted(_MATRIX), ids=lambda kind: kind)
def layered_content(request, tmp_path: Path):
    kind = request.param
    folder, template, getter, lister = _MATRIX[kind]
    game, base, root = tmp_path / "game", tmp_path / "base", tmp_path / "mod"
    root.mkdir()
    ids = {"game": "game.1" if kind == "event" else "game_id", "base": "base.1" if kind == "event" else "base_id", "both": "both.1" if kind == "event" else "both_id", "mod": "mod.1" if kind == "event" else "mod_id"}
    def put(layer, filename, key, marker):
        write(layer, f"{folder}/{filename}.txt", template.format(id=ids[key], marker=marker))
    put(game, "game", "game", "game")
    put(game, "game_both", "both", "game")
    put(base, "base", "base", "base")
    put(base, "base_both", "both", "base")
    put(root, "mod", "mod", "mod")
    mod = Mod(root, hoi4_install=game, base_mod_paths=[base])
    def get(key, **kwargs):
        args = (ids[key], ids[key] + "_focus") if kind == "focus" else (ids[key],)
        return getattr(mod, getter)(*args, **kwargs)
    return kind, mod, game, base, root, ids, get, getattr(mod, lister), put


def test_matrix_game_only(layered_content) -> None:
    kind, mod, game, base, root, ids, get, listing, put = layered_content
    obj = mod.get_focus_tree(ids["game"]) if kind == "focus" else get("game")
    if kind == "on_action_occurrences":
        obj = obj[0]
    assert (obj.definition_path if kind == "decision_category" else obj.path).is_relative_to(game)
    assert get("game") is not None


def test_matrix_base_only(layered_content) -> None:
    kind, mod, game, base, root, ids, get, listing, put = layered_content
    obj = mod.get_focus_tree(ids["base"]) if kind == "focus" else get("base")
    if kind == "on_action_occurrences":
        obj = obj[0]
    assert (obj.definition_path if kind == "decision_category" else obj.path).is_relative_to(base)
    assert get("base") is not None


def test_matrix_base_precedence(layered_content) -> None:
    kind, mod, game, base, root, ids, get, listing, put = layered_content
    obj = mod.get_focus_tree(ids["both"]) if kind == "focus" else get("both")
    if kind == "on_action_occurrences":
        obj = obj[0]
    assert (obj.definition_path if kind == "decision_category" else obj.path).is_relative_to(base)
    assert get("both") is not None


def test_matrix_mod_precedence(layered_content) -> None:
    kind, mod, game, base, root, ids, get, listing, put = layered_content
    put(root, "mod_both", "both", "mod")
    new = Mod(root, hoi4_install=game, base_mod_paths=[base])
    args = (ids["both"], ids["both"] + "_focus") if kind == "focus" else (ids["both"],)
    found = getattr(new, _MATRIX[kind][2])(*args)
    origin = new.get_focus_tree(ids["both"]) if kind == "focus" else found
    if kind == "on_action_occurrences":
        origin = origin[0]
    assert (origin.definition_path if kind == "decision_category" else origin.path).is_relative_to(root)


def test_matrix_mod_only_and_default(layered_content) -> None:
    kind, mod, game, base, root, ids, get, listing, put = layered_content
    with pytest.raises(NotFoundError):
        get("game", include_vanilla=False)
    assert get("mod", include_vanilla=False) is not None
    missing_args = ("missing", "missing_focus") if kind == "focus" else ("missing",)
    sentinel = object()
    assert getattr(mod, _MATRIX[kind][2])(*missing_args, default=sentinel) is sentinel


def test_matrix_lists_are_sorted_and_mod_only(layered_content) -> None:
    kind, mod, game, base, root, ids, get, listing, put = layered_content
    assert listing() == [ids["mod"]]
    assert listing(include_vanilla=True) == sorted(ids.values())


def test_matrix_lookup_does_not_stage(layered_content) -> None:
    kind, mod, game, base, root, ids, get, listing, put = layered_content
    before = (listing(), mod.preview(), mod.validate())
    files = sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file())
    get("game")
    assert (listing(), mod.preview(), mod.validate()) == before
    with pytest.warns(RuntimeWarning):
        result = mod.save(require_changes=False)
    assert not result.written_files
    assert sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file()) == files


def test_matrix_inherited_object_is_a_copy(layered_content) -> None:
    kind, mod, game, base, root, ids, get, listing, put = layered_content
    obj = get("game")
    if kind == "on_action_occurrences":
        obj = obj[0]
    field = "name" if kind == "bookmark" else "id"
    old = getattr(obj, field)
    setattr(obj, field, "changed")
    second = get("game")
    if kind == "on_action_occurrences":
        second = second[0]
    assert getattr(second, field) == old


@pytest.mark.parametrize("edit", ["update", "delete", "add_option", "update_option", "namespace"])
@pytest.mark.parametrize("layered_content", ["event"], indirect=True)
def test_matrix_event_edit_requires_staged_file(layered_content, edit: str) -> None:
    kind, mod, game, base, root, ids, get, listing, put = layered_content
    if kind != "event":
        pytest.skip("event edit applies to events")
    operations = {
        "update": lambda: mod.update_event(ids["game"], title="changed"),
        "delete": lambda: mod.delete_event(ids["game"]),
        "add_option": lambda: mod.add_event_option(ids["game"], EventOption(name="second")),
        "update_option": lambda: mod.update_event_option(ids["game"], "choice", name="changed"),
        "namespace": lambda: mod.set_event_namespace(ids["game"], "new_namespace"),
    }
    before = mod.preview()
    with pytest.raises(ReadOnlyError) as error:
        operations[edit]()
    assert 'mod.load_inherited_content("events/game.txt")' in str(error.value)
    assert mod.preview() == before
    mod.load_inherited_content("events/game.txt")
    operations[edit]()
    assert mod.save(require_changes=True).written_files
    assert (root / "events/game.txt").exists()


@pytest.mark.parametrize("action", ["discard", "save", "reload"])
@pytest.mark.parametrize("layered_content", ["event"], indirect=True)
def test_matrix_event_cache_clears(layered_content, action: str) -> None:
    kind, mod, game, base, root, ids, get, listing, put = layered_content
    if kind != "event":
        pytest.skip("event cache uses event title")
    assert get("game").title == "game"
    put(game, "game", "game", "changed")
    if action == "save":
        with pytest.warns(RuntimeWarning):
            mod.save(require_changes=False)
    else:
        getattr(mod, action)()
    assert get("game").title == "changed"


@pytest.mark.parametrize("layered_content", ["focus_tree", "focus"], indirect=True)
@pytest.mark.parametrize("edit", ["update_tree", "delete_tree", "add", "remove", "update"])
def test_matrix_focus_edit_requires_staged_file(layered_content, edit: str) -> None:
    kind, mod, game, base, root, ids, get, listing, put = layered_content
    focus_id = ids["game"] + "_focus"
    operations = {
        "update_tree": lambda: mod.update_focus_tree(ids["game"], default=True),
        "delete_tree": lambda: mod.delete_focus_tree(ids["game"]),
        "add": lambda: mod.add_focus(ids["game"], Focus(id="new_focus", x=1, y=1)),
        "remove": lambda: mod.remove_focus(ids["game"], focus_id),
        "update": lambda: mod.update_focus(ids["game"], focus_id, x=2),
    }
    before = mod.preview()
    with pytest.raises(ReadOnlyError) as error:
        operations[edit]()
    assert 'mod.load_inherited_content("common/national_focus/game.txt")' in str(error.value)
    assert mod.preview() == before
    mod.load_inherited_content("common/national_focus/game.txt")
    operations[edit]()
    assert mod.save(require_changes=True).written_files
    assert (root / "common/national_focus/game.txt").exists()


@pytest.mark.parametrize("layered_content", ["focus_tree", "focus"], indirect=True)
@pytest.mark.parametrize("action", ["discard", "save", "reload"])
def test_matrix_focus_cache_clears(layered_content, action: str) -> None:
    kind, mod, game, base, root, ids, get, listing, put = layered_content
    assert get("game") is not None
    path = game / "common/national_focus/game.txt"
    path.write_text(f"focus_tree = {{ id = {ids['game']} focus = {{ id = {ids['game']}_focus x = 7 y = 0 }} }}")
    if action == "save":
        with pytest.warns(RuntimeWarning):
            mod.save(require_changes=False)
    else:
        getattr(mod, action)()
    focus = get("game") if kind == "focus" else get("game").focuses[0]
    assert focus.x == 7


@pytest.mark.parametrize("layered_content", ["idea"], indirect=True)
@pytest.mark.parametrize("edit", ["update", "delete"])
def test_matrix_idea_edit_requires_staged_file(layered_content, edit: str) -> None:
    kind, mod, game, base, root, ids, get, listing, put = layered_content
    operations = {
        "update": lambda: mod.update_idea(ids["game"], icon="changed"),
        "delete": lambda: mod.delete_idea(ids["game"]),
    }
    before = mod.preview()
    with pytest.raises(ReadOnlyError) as error:
        operations[edit]()
    assert 'mod.load_inherited_content("common/ideas/game.txt")' in str(error.value)
    assert mod.preview() == before
    mod.load_inherited_content("common/ideas/game.txt")
    operations[edit]()
    assert mod.save(require_changes=True).written_files
    assert (root / "common/ideas/game.txt").exists()


@pytest.mark.parametrize("layered_content", ["idea"], indirect=True)
@pytest.mark.parametrize("action", ["discard", "save", "reload"])
def test_matrix_idea_cache_clears(layered_content, action: str) -> None:
    kind, mod, game, base, root, ids, get, listing, put = layered_content
    assert get("game").icon == "game"
    put(game, "game", "game", "changed")
    if action == "save":
        with pytest.warns(RuntimeWarning):
            mod.save(require_changes=False)
    else:
        getattr(mod, action)()
    assert get("game").icon == "changed"


@pytest.mark.parametrize("layered_content", ["decision"], indirect=True)
@pytest.mark.parametrize("edit", ["update", "delete"])
def test_matrix_decision_edit_requires_staged_file(layered_content, edit: str) -> None:
    kind, mod, game, base, root, ids, get, listing, put = layered_content
    operations = {
        "update": lambda: mod.update_decision(ids["game"], cost=2),
        "delete": lambda: mod.delete_decision(ids["game"]),
    }
    before = mod.preview()
    with pytest.raises(ReadOnlyError) as error:
        operations[edit]()
    assert 'mod.load_inherited_content("common/decisions/game.txt")' in str(error.value)
    assert mod.preview() == before
    mod.load_inherited_content("common/decisions/game.txt")
    operations[edit]()
    assert mod.save(require_changes=True).written_files
    assert (root / "common/decisions/game.txt").exists()


@pytest.mark.parametrize("layered_content", ["decision"], indirect=True)
@pytest.mark.parametrize("action", ["discard", "save", "reload"])
def test_matrix_decision_cache_clears(layered_content, action: str) -> None:
    kind, mod, game, base, root, ids, get, listing, put = layered_content
    assert get("game").cost == 1
    path = game / "common/decisions/game.txt"
    path.write_text(f"matrix_category = {{ {ids['game']} = {{ cost = 7 }} }}")
    if action == "save":
        with pytest.warns(RuntimeWarning):
            mod.save(require_changes=False)
    else:
        getattr(mod, action)()
    assert get("game").cost == 7


@pytest.mark.parametrize("layered_content", ["decision_category"], indirect=True)
def test_matrix_category_edit_requires_staged_file(layered_content) -> None:
    kind, mod, game, base, root, ids, get, listing, put = layered_content
    before = mod.preview()
    with pytest.raises(ReadOnlyError) as error:
        mod.update_decision_category(ids["game"], icon="changed")
    assert 'mod.load_inherited_content("common/decisions/categories/game.txt")' in str(error.value)
    assert mod.preview() == before
    mod.load_inherited_content("common/decisions/categories/game.txt")
    mod.update_decision_category(ids["game"], icon="changed")
    assert mod.save(require_changes=True).written_files
    assert (root / "common/decisions/categories/game.txt").exists()


@pytest.mark.parametrize("layered_content", ["decision_category"], indirect=True)
@pytest.mark.parametrize("action", ["discard", "save", "reload"])
def test_matrix_category_cache_clears(layered_content, action: str) -> None:
    kind, mod, game, base, root, ids, get, listing, put = layered_content
    assert get("game").icon == "game"
    put(game, "game", "game", "changed")
    if action == "save":
        with pytest.warns(RuntimeWarning):
            mod.save(require_changes=False)
    else:
        getattr(mod, action)()
    assert get("game").icon == "changed"


def test_inherited_category_combines_decision_files(tmp_path: Path) -> None:
    game, root = tmp_path / "game", tmp_path / "mod"
    root.mkdir()
    write(game, "common/decisions/categories/c.txt", "joined = { icon = category_icon }")
    write(game, "common/decisions/a.txt", "joined = { first_decision = { cost = 1 } }")
    write(game, "common/decisions/b.txt", "joined = { second_decision = { cost = 2 } }")
    category = Mod(root, hoi4_install=game).get_decision_category("joined")
    assert category.icon == "category_icon"
    assert [decision.id for decision in category.decisions] == ["first_decision", "second_decision"]


@pytest.mark.parametrize("layered_content", ["bookmark"], indirect=True)
@pytest.mark.parametrize("edit", ["update", "delete", "add_country", "update_country", "delete_country"])
def test_matrix_bookmark_edit_requires_staged_file(layered_content, edit: str) -> None:
    kind, mod, game, base, root, ids, get, listing, put = layered_content
    operations = {
        "update": lambda: mod.update_bookmark(ids["game"], description="changed"),
        "delete": lambda: mod.delete_bookmark(ids["game"]),
        "add_country": lambda: mod.add_bookmark_country(ids["game"], BookmarkCountry(tag="FRA")),
        "update_country": lambda: mod.update_bookmark_country(ids["game"], "GER", history="changed"),
        "delete_country": lambda: mod.delete_bookmark_country(ids["game"], "GER"),
    }
    before = mod.preview()
    with pytest.raises(ReadOnlyError) as error:
        operations[edit]()
    assert 'mod.load_inherited_content("common/bookmarks/game.txt")' in str(error.value)
    assert mod.preview() == before
    mod.load_inherited_content("common/bookmarks/game.txt")
    operations[edit]()
    assert mod.save(require_changes=True).written_files
    assert (root / "common/bookmarks/game.txt").exists()


@pytest.mark.parametrize("layered_content", ["bookmark"], indirect=True)
@pytest.mark.parametrize("action", ["discard", "save", "reload"])
def test_matrix_bookmark_cache_clears(layered_content, action: str) -> None:
    kind, mod, game, base, root, ids, get, listing, put = layered_content
    assert get("game").description == "game"
    put(game, "game", "game", "changed")
    if action == "save":
        with pytest.warns(RuntimeWarning):
            mod.save(require_changes=False)
    else:
        getattr(mod, action)()
    assert get("game").description == "changed"


@pytest.mark.parametrize("layered_content", ["dynamic_modifier"], indirect=True)
@pytest.mark.parametrize("edit", ["update", "delete"])
def test_matrix_modifier_edit_requires_staged_file(layered_content, edit: str) -> None:
    kind, mod, game, base, root, ids, get, listing, put = layered_content
    operations = {
        "update": lambda: mod.update_dynamic_modifier(ids["game"], icon="changed"),
        "delete": lambda: mod.delete_dynamic_modifier(ids["game"]),
    }
    before = mod.preview()
    with pytest.raises(ReadOnlyError) as error:
        operations[edit]()
    assert 'mod.load_inherited_content("common/dynamic_modifiers/game.txt")' in str(error.value)
    assert mod.preview() == before
    mod.load_inherited_content("common/dynamic_modifiers/game.txt")
    operations[edit]()
    assert mod.save(require_changes=True).written_files
    assert (root / "common/dynamic_modifiers/game.txt").exists()


@pytest.mark.parametrize("layered_content", ["dynamic_modifier"], indirect=True)
@pytest.mark.parametrize("action", ["discard", "save", "reload"])
def test_matrix_modifier_cache_clears(layered_content, action: str) -> None:
    kind, mod, game, base, root, ids, get, listing, put = layered_content
    assert get("game").icon == "game"
    put(game, "game", "game", "changed")
    if action == "save":
        with pytest.warns(RuntimeWarning):
            mod.save(require_changes=False)
    else:
        getattr(mod, action)()
    assert get("game").icon == "changed"


@pytest.mark.parametrize("layered_content", ["on_action"], indirect=True)
@pytest.mark.parametrize("edit", ["update", "delete"])
def test_matrix_on_action_edit_requires_own_hook(layered_content, edit: str) -> None:
    kind, mod, game, base, root, ids, get, listing, put = layered_content
    operations = {
        "update": lambda: mod.update_on_action(ids["game"], effect="log = changed"),
        "delete": lambda: mod.delete_on_action(ids["game"]),
    }
    before = mod.preview()
    with pytest.raises(ReadOnlyError) as error:
        operations[edit]()
    assert "common/on_actions/game.txt" in str(error.value)
    assert "create_on_action()" in str(error.value)
    assert mod.preview() == before
    mod.load_inherited_content("common/on_actions/game.txt")
    operations[edit]()
    assert mod.save(require_changes=True).written_files
    assert (root / "common/on_actions/game.txt").exists()


@pytest.mark.parametrize("layered_content", ["on_action"], indirect=True)
@pytest.mark.parametrize("action", ["discard", "save", "reload"])
def test_matrix_on_action_cache_clears(layered_content, action: str) -> None:
    kind, mod, game, base, root, ids, get, listing, put = layered_content
    assert "game" in get("game").effect
    put(game, "game", "game", "changed")
    if action == "save":
        with pytest.warns(RuntimeWarning):
            mod.save(require_changes=False)
    else:
        getattr(mod, action)()
    assert "changed" in get("game").effect


@pytest.mark.parametrize("layered_content", ["on_action"], indirect=True)
def test_inherited_on_action_occurrences_compose_layers(layered_content) -> None:
    kind, mod, game, base, root, ids, get, listing, put = layered_content
    occurrences = mod.get_on_action_occurrences(ids["both"])
    assert len(occurrences) == 2
    assert occurrences[0].path.is_relative_to(base)
    assert occurrences[1].path.is_relative_to(game)
    assert mod.get_on_action_occurrences(ids["both"], include_vanilla=False, default=None) is None
