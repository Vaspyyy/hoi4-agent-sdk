from pathlib import Path

import pytest

from hoi4 import Mod
from hoi4.config import Config, find_config
from hoi4.layers import ContentLayers
from hoi4.project import write_mod_descriptors


def put(root, relative, text):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def state(owner, ident=1):
    return f'state = {{ id = {ident} name = "STATE_{ident}" manpower = 100 state_category = rural provinces = {{ 1 }} history = {{ owner = {owner} add_core_of = {owner} }} }}'


def roots(tmp_path):
    result = [tmp_path / name for name in ("game", "base1", "base2", "top")]
    for root in result:
        root.mkdir()
    return result


def test_order_replacement_and_original_provenance(tmp_path):
    game, a, b, top = roots(tmp_path)
    put(game, "history/states/1.txt", state("GER"))
    put(game, "events/old.txt", "country_event = { id = old.1 }")
    put(a, "events/a.txt", "country_event = { id = a.1 }")
    put(b, "descriptor.mod", 'replace_path="events"')
    expected = put(b, "history/states/1.txt", state("ITA"))
    put(b, "events/b.txt", "country_event = { id = b.1 }")
    put(top, "descriptor.mod", 'replace_path="history/states"')
    put(top, "history/states/2.txt", state("FRA", 2))
    layers = ContentLayers(game, (a, b), top)
    assert layers.source("history/states/1.txt") is None
    assert layers.source("events/old.txt") is None
    assert layers.source("events/a.txt") is None
    assert layers.source("events/b.txt") == b / "events/b.txt"
    assert not (layers.root / "history/states/2.txt").exists()
    layers.close()
    assert expected.read_text() == state("ITA")


@pytest.mark.parametrize("layered", [False, True])
@pytest.mark.parametrize("replacement", ["common/scripted_triggers", "common"])
def test_content_discovery_filters_replaced_vanilla_without_hiding_writable_files(
    tmp_path, layered, replacement
):
    game, base, _, top = roots(tmp_path)
    hidden = put(game, "common/scripted_triggers/nested/hidden.txt", "hidden = yes")
    override = put(game, "common/scripted_triggers/override.txt", "old = yes")
    sibling = put(game, "common/scripted_triggers_extra/visible.txt", "visible = yes")
    root_sibling = put(game, "common_extra/visible.txt", "visible = yes")
    ordinary = put(game, "events/visible.txt", "visible = yes")
    writable = put(top, "common/scripted_triggers/override.txt", "new = yes")
    put(top, "descriptor.mod", f'replace_path="{replacement}"')
    mod = Mod(top, hoi4_install=game, base_mod_paths=[base] if layered else [])

    assert mod.content_source(hidden.relative_to(game)) is None
    assert mod.content_source(override.relative_to(game)) == writable
    assert mod.content_source(sibling.relative_to(game)) == (
        sibling if replacement == "common/scripted_triggers" else None
    )
    if not layered:
        # ContentLayers inventories only its configured runtime content roots.
        assert mod.content_source(root_sibling.relative_to(game)) == root_sibling
    assert mod.content_source(ordinary.relative_to(game)) == ordinary
    assert mod.content_files("common/scripted_triggers") == {
        "common/scripted_triggers/override.txt": writable
    }
    expected_common = {"common/scripted_triggers/override.txt": writable}
    if replacement == "common/scripted_triggers":
        expected_common["common/scripted_triggers_extra/visible.txt"] = sibling
    assert mod.content_files("common") == expected_common
    if not layered:
        assert mod.content_files("common_extra") == {"common_extra/visible.txt": root_sibling}
    assert mod.content_files("events") == {"events/visible.txt": ordinary}
    with pytest.raises(ValueError, match="Unsafe content path"):
        mod.content_source("../escape.txt")
    with pytest.raises(ValueError, match="Unsafe content path"):
        mod.content_files("../escape")


@pytest.mark.parametrize("layered", [False, True])
def test_empty_replacement_directory_and_ordinary_fallback(tmp_path, layered):
    game, base, _, top = roots(tmp_path)
    source = put(game, "common/scripted_triggers/old.txt", "old = yes")
    mod = Mod(top, hoi4_install=game, base_mod_paths=[base] if layered else [])
    assert mod.content_source("common/scripted_triggers/old.txt") == source
    assert mod.content_files("common/scripted_triggers") == {
        "common/scripted_triggers/old.txt": source
    }

    put(top, "descriptor.mod", 'replace_path="common/scripted_triggers"')
    if layered:
        mod.reload()
    assert mod.content_source("common/scripted_triggers/old.txt") is None
    assert mod.content_files("common/scripted_triggers") == {}
    assert not (top / "common/scripted_triggers").exists()


def test_unlayered_discovery_rejects_unsafe_descriptor_replacement(tmp_path):
    game, _, _, top = roots(tmp_path)
    put(game, "events/old.txt", "old = yes")
    put(top, "events/local.txt", "local = yes")
    put(top, "descriptor.mod", 'replace_path="../events"')
    mod = Mod(top, hoi4_install=game)
    with pytest.raises(ValueError, match="Unsafe content path"):
        mod.content_source("events/local.txt")
    with pytest.raises(ValueError, match="Unsafe content path"):
        mod.content_files("events")


@pytest.mark.parametrize("layered", [False, True])
def test_replaced_vanilla_script_ids_do_not_block_public_authoring(tmp_path, layered):
    game, base, _, top = roots(tmp_path)
    put(game, "common/scripted_triggers/vanilla.txt", "example = { always = no }")
    put(top, "descriptor.mod", 'replace_path="common/scripted_triggers"')
    source = put(tmp_path, "import.txt", "example = { always = yes }")
    mod = Mod(top, hoi4_install=game, base_mod_paths=[base] if layered else [])
    mod.create_scripted_trigger("example", "always = yes")
    with pytest.raises(ValueError, match="already queued"):
        mod.create_scripted_trigger("example", "always = no", path="common/scripted_triggers/other.txt")
    with pytest.raises(ValueError, match="conflict"):
        mod.import_script_file(source, "common/scripted_triggers/import.txt")
    result = mod.save(require_changes=True)
    assert result.written_files

    # A separate import is allowed when the only matching ID is suppressed vanilla.
    import_top = tmp_path / "import_top"
    put(import_top, "descriptor.mod", 'replace_path="common/scripted_triggers"')
    importing = Mod(import_top, hoi4_install=game, base_mod_paths=[base] if layered else [])
    importing.import_script_file(source, "common/scripted_triggers/import.txt")
    assert importing.save(require_changes=True).written_files

    # Without replacement the vanilla collision is active for both APIs.
    active = Mod(tmp_path / "active", hoi4_install=game, base_mod_paths=[base] if layered else [])
    with pytest.raises(ValueError, match="already exists"):
        active.create_scripted_trigger("example", "always = yes")
    with pytest.raises(ValueError, match="conflict"):
        active.import_script_file(source, "common/scripted_triggers/import.txt")


def test_state_fallback_save_and_reload_do_not_write_bases(tmp_path):
    game, a, b, top = roots(tmp_path)
    put(game, "history/states/1.txt", state("GER"))
    source = put(b, "history/states/1.txt", state("ITA"))
    put(b, "localisation/english/states_l_english.yml", 'l_english:\n STATE_1:0 "Latium"\n')
    mod = Mod(top, hoi4_install=game, base_mod_paths=[a, b])
    assert mod.hoi4_install == game
    assert mod.get_state(1).owner == "ITA"
    assert mod.find_state("Latium")[0]["id"] == 1
    assert mod.content_source("history/states/1.txt") == source
    mod.set_state_owner(1, "ROM")
    result = mod.save(require_changes=True)
    assert result.written_files
    assert all(Path(path).is_relative_to(top) for path in result.written_files)
    assert source.read_text() == state("ITA")
    assert (game / "history/states/1.txt").read_text() == state("GER")
    mod.reload()
    assert mod.get_state(1).owner == "ROM"
    (top / "history/states/1.txt").unlink()
    source.write_text(state("FRA"))
    mod.reload()
    assert mod.get_state(1).owner == "FRA"


def test_private_snapshot_and_asset_precedence(tmp_path):
    game, a, b, top = roots(tmp_path)
    put(game, "gfx/a.dds", "old")
    original = put(a, "gfx/a.dds", "new")
    layers = ContentLayers(game, [a, b], top)
    assert layers.source("gfx/a.dds") == original
    (layers.root / "gfx/a.dds").write_text("changed snapshot")
    assert original.read_text() == "new"
    path = layers.root
    layers.close()
    assert not path.exists()


def test_config_and_dependencies(tmp_path):
    Config(mod_path="top", hoi4_install="game", base_mod_paths=["a", "b"]).save(
        tmp_path / ".hoi4.json"
    )
    cfg = find_config(tmp_path)
    assert cfg.base_mod_paths == (tmp_path / "a", tmp_path / "b")
    result = write_mod_descriptors(
        tmp_path / "top",
        tmp_path / "mods",
        "Roma",
        supported_version="1.17.*",
        dependencies=["Magna Europa", "Magna Europa"],
    )
    assert result.dependencies == ("Magna Europa",)
    for path in (result.descriptor, result.launcher):
        assert 'dependencies={\n\t"Magna Europa"\n}' in path.read_text()


@pytest.mark.parametrize("path", ["../history", "/history", ".", "C:/history"])
def test_reject_unsafe_replacement(tmp_path, path):
    game, a, b, top = roots(tmp_path)
    put(a, "descriptor.mod", f'replace_path="{path}"')
    with pytest.raises(ValueError):
        ContentLayers(game, [a, b], top)


def test_inherited_scripts_edit_siblings_and_sources_preserved(tmp_path):
    game, a, b, top = roots(tmp_path)
    event_source = put(
        a,
        "events/ancient.txt",
        "add_namespace = ancient\ncountry_event = { id = ancient.1 title = ancient.1.t is_triggered_only = yes option = { name = ancient.1.a add_stability = 0.01 } }\ncountry_event = { id = ancient.2 title = ancient.2.t is_triggered_only = yes option = { name = ancient.2.a add_stability = 0.01 } }\n",
    )
    focus_source = put(
        b,
        "common/national_focus/ancient.txt",
        "focus_tree = { id = ancient_tree focus = { id = ancient_focus x = 0 y = 0 cost = 5 } }",
    )
    idea_source = put(
        b,
        "common/ideas/ancient.txt",
        "ideas = { country = { ancient_idea = { picture = generic modifier = { stability_factor = 0.01 } } } }",
    )
    before = {p: p.read_bytes() for p in (event_source, focus_source, idea_source)}
    mod = Mod(top, hoi4_install=game, base_mod_paths=[a, b])
    for relative in (
        "events/ancient.txt",
        "common/national_focus/ancient.txt",
        "common/ideas/ancient.txt",
    ):
        mod.load_inherited_content(relative)
    assert not mod.preview()
    assert mod.get_idea("ancient_idea").path == top / "common/ideas/ancient.txt"
    assert mod.update_event("ancient.1", title="new_title")
    assert mod.update_focus("ancient_tree", "ancient_focus", cost=3)
    result = mod.save(require_changes=True)
    assert result.written_files
    assert "ancient.2" in (top / "events/ancient.txt").read_text()
    assert "new_title" in (top / "events/ancient.txt").read_text()
    assert all(p.read_bytes() == content for p, content in before.items())


def test_sprite_and_texture_validation_use_effective_layers(tmp_path):
    game, a, b, top = roots(tmp_path)
    put(
        game,
        "interface/a.gfx",
        'spriteTypes = { spriteType = { name = "GFX_layer" texturefile = "gfx/old.dds" } }',
    )
    put(game, "gfx/old.dds", "old")
    put(
        a,
        "interface/a.gfx",
        'spriteTypes = { spriteType = { name = "GFX_layer" texturefile = "gfx/new.dds" } }',
    )
    put(a, "gfx/new.dds", "new")
    put(top, "descriptor.mod", 'replace_path="gfx"')
    mod = Mod(top, hoi4_install=game, base_mod_paths=[a, b])
    assert mod._known_sprite_textures()["GFX_layer"] == "gfx/new.dds"
    assert not mod._texture_exists("gfx/old.dds")
    assert not mod._texture_exists("gfx/new.dds")
    put(top, "gfx/new.dds", "local")
    assert mod._texture_exists("gfx/new.dds")


def test_base_only_has_no_fake_game_vocabulary(tmp_path):
    _, a, b, top = roots(tmp_path)
    put(a, "history/states/1.txt", state("ITA"))
    mod = Mod(top, base_mod_paths=[a, b])
    assert mod.hoi4_install is None
    assert mod.get_state(1).owner == "ITA"
    assert isinstance(mod.validate_effect("add_stability = 0.01"), list)
    with pytest.raises(ValueError, match="configured HOI4"):
        mod.game_script_vocabulary()


def test_inherited_localization_is_read_and_overridden_safely(tmp_path):
    game, a, b, top = roots(tmp_path)
    source = put(
        a,
        "localisation/english/ancient_l_english.yml",
        'l_english:\n ancient_focus:0 "Old title"\n sibling:0 "Keep me"\n',
    )
    original = source.read_bytes()
    mod = Mod(top, hoi4_install=game, base_mod_paths=[a, b])
    assert mod.get_loc("ancient_focus") == "Old title"
    mod.set_loc("ancient_focus", "New title")
    result = mod.save(require_changes=True)
    assert result.written_files
    assert source.read_bytes() == original
    output = (top / source.relative_to(a)).read_text(encoding="utf-8-sig")
    assert "New title" in output and "Keep me" in output


@pytest.mark.parametrize("existing_override", [False, True])
def test_delete_last_inherited_localization_preserves_empty_override(tmp_path, existing_override):
    game, a, b, top = roots(tmp_path)
    relative = "localisation/english/nested/ancient_l_english.yml"
    game_source = put(game, relative, 'l_english:\n ancient:0 "Game"\n')
    source = put(a, relative, '# Base comment\nl_english:\n ancient:0 "Base"\n')
    target = top / relative
    comment = "Base comment"
    if existing_override:
        put(top, relative, '# Override comment\nl_english:\n ancient:0 "Override"\n')
        comment = "Override comment"
    originals = {p: p.read_bytes() for p in (game_source, source)}
    mod = Mod(top, hoi4_install=game, base_mod_paths=[a, b])
    assert mod.delete_loc("ancient")
    assert mod.get_loc("ancient") is None
    assert mod.preview()
    result = mod.save(require_changes=True)
    print(result)
    assert target in result.written_files
    assert target.read_text(encoding="utf-8-sig") == f"# {comment}\nl_english:\n"
    assert mod.get_loc("ancient") is None
    mod.reload()
    assert mod.get_loc("ancient") is None
    assert not mod.preview()
    assert all(p.read_bytes() == original for p, original in originals.items())
    # The retained header/comment baseline also survives adding a new entry.
    mod.set_loc("new_key", "New", file_path=target)
    result = mod.save(require_changes=True)
    print(result)
    assert target in result.written_files
    assert comment in target.read_text(encoding="utf-8-sig")
    assert mod.get_loc("new_key") == "New"
    assert mod.get_loc("ancient") is None


@pytest.mark.parametrize("mode", ["standalone", "top_only", "top_replace", "base_replace"])
def test_delete_last_localization_without_fallback_removes_file(tmp_path, mode):
    _, a, b, top = roots(tmp_path)
    relative = "localisation/english/ancient_l_english.yml"
    if mode in {"top_replace", "base_replace"}:
        put(a, relative, 'l_english:\n ancient:0 "Base"\n')
        put(top if mode == "top_replace" else b, "descriptor.mod",
            'replace_path="localisation"')
    target = put(top, relative, 'l_english:\n ancient:0 "Top"\n')
    mod = Mod(top, base_mod_paths=[] if mode == "standalone" else [a, b])
    assert mod.delete_loc("ancient")
    result = mod.save(require_changes=True)
    print(result)
    assert not target.exists()
    mod.reload()
    assert mod.get_loc("ancient") is None
    assert not mod.preview()


def test_explicit_descendant_replacement_expansion(tmp_path):
    from hoi4.layers import expand_replace_paths

    base = tmp_path / "base"
    (base / "common/decisions/categories").mkdir(parents=True)
    (base / "common/decisions/nested/inner").mkdir(parents=True)
    assert expand_replace_paths(
        ["common/decisions", "common/missing", "common/decisions"], [base]
    ) == (
        "common/decisions",
        "common/decisions/categories",
        "common/decisions/nested",
        "common/decisions/nested/inner",
        "common/missing",
    )
    with pytest.raises(ValueError):
        expand_replace_paths(["../outside"], [base])
    (base / "common/decisions/escape").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes"):
        expand_replace_paths(["common/decisions"], [base])
