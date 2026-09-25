from hoi4 import Mod
import hoi4.mod as module


def test_runtime_exact_body_cache_keeps_evidence_and_reacts_to_edits(tmp_path, monkeypatch):
    mod = Mod(tmp_path)
    original = "AAA = { transfer_state = 1 } 1 = { add_core_of = AAA } release = BBB"
    mod.create_event("test.1", immediate=original)
    mod.create_event("test.2", immediate=original)
    for event_id in ("test.1", "test.2"):
        mod.get_event(event_id).path = tmp_path / "events/test_events.txt"
    actual_parse = module.parse_pdx
    parses = []
    displays = []
    actual_display = mod._display_path

    def parse(body):
        parses.append(body)
        return actual_parse(body)

    def display(path):
        displays.append(path)
        return actual_display(path)

    monkeypatch.setattr(module, "parse_pdx", parse)
    monkeypatch.setattr(mod, "_display_path", display)
    first = mod._country_runtime_activation_evidence("AAA")
    assert first[:3] == ({1}, {1}, set())
    assert len(first[3]) == 1  # Identical repeated evidence retains source semantics.
    assert len(displays) == 1  # Two scripts in one file resolve that path once.
    assert mod._country_runtime_activation_evidence("BBB")[:3] == (set(), set(), {"release"})
    assert mod._country_runtime_activation_evidence("AAA") == first
    assert parses == [original]
    changed = "AAA = { transfer_state = 3 }"
    mod.update_event("test.1", immediate=changed)
    assert mod._country_runtime_activation_evidence("AAA")[0] == {1, 3}
    assert parses == [original, changed]
    with mod.transaction():
        mod.update_event("test.1", immediate="AAA = {")
        assert mod._country_runtime_activation_evidence("AAA")[0] == {1}
        assert mod._country_runtime_activation_evidence("AAA")[0] == {1}
    assert parses.count("AAA = {") == 1
    assert mod._country_runtime_activation_evidence("AAA")[0] == {1, 3}
    mod.discard()
    mod.create_event("test.1", immediate=original)
    assert mod._country_runtime_activation_evidence("AAA")[0] == {1}
    assert parses.count(original) == 2


def test_sprite_exact_text_cache_refreshes_pending_and_disk_definitions(tmp_path, monkeypatch):
    interface = tmp_path / "interface"
    interface.mkdir()
    disk = interface / "disk.gfx"
    disk.write_text('spriteTypes = { spriteType = { name = GFX_DISK texturefile = "gfx/a.dds" } }')
    for name in ("a", "b"):
        texture = tmp_path / f"gfx/{name}.dds"
        texture.parent.mkdir(exist_ok=True)
        texture.write_bytes(b"test texture source")
    mod = Mod(tmp_path)
    mod.write_portrait_gfx("ABC", "leader", portrait_path="gfx/a.dds", sprite_name="GFX_PENDING")
    actual = module.iter_assignment_blocks
    parses = []

    def blocks(text, key):
        if key == "spriteType":
            parses.append(text)
        return actual(text, key)

    monkeypatch.setattr(module, "iter_assignment_blocks", blocks)
    initial = mod._known_sprite_textures()
    assert initial == {"GFX_DISK": "gfx/a.dds", "GFX_PENDING": "gfx/a.dds"}
    assert mod._known_sprite_textures() == initial
    assert len(parses) == 2
    with mod.transaction():
        mod.write_portrait_gfx(
            "ABC", "leader", portrait_path="gfx/b.dds", sprite_name="GFX_PENDING"
        )
        assert mod._known_sprite_textures()["GFX_PENDING"] == "gfx/b.dds"
    assert mod._known_sprite_textures()["GFX_PENDING"] == "gfx/a.dds"
    assert len(parses) == 3
    disk.write_text('spriteTypes = { spriteType = { name = GFX_DISK texturefile = "gfx/b.dds" } }')
    assert mod._known_sprite_textures()["GFX_DISK"] == "gfx/b.dds"
    assert len(parses) == 4
    mod.reload()
    assert mod._known_sprite_textures() == {"GFX_DISK": "gfx/b.dds"}
    assert len(parses) == 5


def test_declared_laws_are_country_assignable_but_advisor_categories_are_not(tmp_path):
    idea_file = tmp_path / "common/ideas/laws.txt"
    idea_file.parent.mkdir(parents=True)
    idea_file.write_text("""ideas = {
      mobilization_laws = { law = yes limited_conscription = { modifier = { conscription = 0.025 } } }
      economy = { law = yes civilian_economy = { modifier = { stability_factor = 0.1 } } }
      trade_laws = { law = yes export_focus = { modifier = { research_speed_factor = 0.1 } } }
      custom_laws = { law = yes custom_law = { cost = 150 } }
      political_advisor = { advisor = { cost = 150 } }
      undeclared = { not_a_law = { allowed = { law = yes } cost = 150 } }
    }""")
    mod = Mod(tmp_path)
    mod.create_country(
        "ABC",
        "Test",
        ideas=[
            "limited_conscription",
            "civilian_economy",
            "export_focus",
            "custom_law",
            "advisor",
            "not_a_law",
        ],
    )
    warnings = [
        issue
        for issue in mod.validate(stage="build")
        if issue.code == "assigned_idea_not_country_category"
    ]
    assert {issue.idea_id for issue in warnings} == {"advisor", "not_a_law"}
