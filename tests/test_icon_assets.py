from __future__ import annotations

from pathlib import Path

import pytest

from hoi4 import Focus, Mod
from hoi4.assets import (
    DECISION_CATEGORY_PICTURE_SIZE,
    EVENT_PICTURE_SIZE,
    FOCUS_ICON_SIZE,
    IDEA_ICON_SIZE,
    NEWS_EVENT_PICTURE_SIZE,
    default_icon_gfx_path,
    import_icon_to_mod,
    render_focus_shine_sprite,
    render_sprite_type,
    upsert_sprite_type,
)

Image = pytest.importorskip("PIL.Image")


def _dxt5_supported() -> bool:
    import io

    buffer = io.BytesIO()
    try:
        Image.new("RGBA", (4, 4)).save(buffer, format="DDS", pixel_format="DXT5")
    except (KeyError, OSError, ValueError):
        return False
    return buffer.getvalue()[84:88] == b"DXT5"


# Older optional Pillow releases cannot encode DXT5; the importers then raise
# DDSExportUnsupportedError, which tests/test_assets.py covers.
requires_dxt5 = pytest.mark.skipif(not _dxt5_supported(), reason="Pillow cannot encode DXT5 DDS")


def _make_image(
    path: Path,
    size: tuple[int, int] = (300, 200),
    color: tuple[int, int, int, int] = (220, 30, 40, 255),
) -> Path:
    Image.new("RGBA", size, color).save(path, format="PNG")
    return path


def _dds_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        assert image.format == "DDS"
        return image.size


def _sprite_files(root: Path) -> set[Path]:
    return {path for path in root.rglob("*") if path.is_file()}


class TestSpriteTextPatching:
    def test_new_file_contains_one_sprite(self) -> None:
        entry = render_sprite_type("GFX_x", "gfx/interface/goals/x.dds")
        text = upsert_sprite_type("", "GFX_x", entry)
        assert text == (
            "spriteTypes = {\n"
            "\tSpriteType = {\n"
            '\t\tname = "GFX_x"\n'
            '\t\ttexturefile = "gfx/interface/goals/x.dds"\n'
            "\t}\n"
            "}\n"
        )
        assert upsert_sprite_type(text, "GFX_x", entry) == text

    def test_append_and_replace_preserve_other_bytes(self) -> None:
        original = (
            "# hand-written header\r\n"
            "spriteTypes = {\r\n"
            "    # keep this comment\r\n"
            "    spriteType = { name = GFX_keep texturefile = \"a.dds\" }  # trailing\r\n"
            "\r\n"
            "    SpriteType = {\r\n"
            "        name = \"GFX_target\"\r\n"
            "        texturefile = \"old.dds\"\r\n"
            "    }\r\n"
            "    SpriteType = { name = \"GFX_after\" texturefile = \"b.dds\" }\r\n"
            "}\r\n"
            "# footer\r\n"
        )
        replaced = upsert_sprite_type(
            original, "GFX_target", render_sprite_type("GFX_target", "new.dds")
        )
        assert replaced == original.replace('"old.dds"', '"new.dds"')

        appended = upsert_sprite_type(
            original, "GFX_new", render_sprite_type("GFX_new", "n.dds")
        )
        tail = "\r\n}\r\n# footer\r\n"
        assert appended == original[: -len(tail)] + (
            "\r\n"
            "    SpriteType = {\r\n"
            '        name = "GFX_new"\r\n'
            '        texturefile = "n.dds"\r\n'
            "    }"
        ) + tail

    def test_overwrite_false_refuses_different_entry(self) -> None:
        text = upsert_sprite_type("", "GFX_x", render_sprite_type("GFX_x", "a.dds"))
        with pytest.raises(FileExistsError, match="GFX_x"):
            upsert_sprite_type(
                text, "GFX_x", render_sprite_type("GFX_x", "b.dds"), overwrite=False
            )
        assert (
            upsert_sprite_type(
                text, "GFX_x", render_sprite_type("GFX_x", "a.dds"), overwrite=False
            )
            == text
        )

    def test_shine_template(self) -> None:
        shine = render_focus_shine_sprite("GFX_x_shine", "gfx/interface/goals/x.dds")
        assert 'name = "GFX_x_shine"' in shine
        assert 'effectFile = "gfx/FX/buttonstate.lua"' in shine
        assert shine.count("animation = {") == 2
        assert shine.count('animationmaskfile = "gfx/interface/goals/x.dds"') == 2
        assert shine.count(
            'animationtexturefile = "gfx/interface/goals/shine_overlay.dds"'
        ) == 2
        assert "animationrotation = -90.0" in shine
        assert "animationrotation = 90.0" in shine
        for line in (
            "animationlooping = no",
            "animationtime = 0.75",
            "animationdelay = 0",
            'animationblendmode = "add"',
            'animationtype = "scrolling"',
            "animationrotationoffset = { x = 0.0 y = 0.0 }",
            "animationtexturescale = { x = 1.0 y = 1.0 }",
        ):
            assert shine.count(line) == 2
        assert shine.rstrip().endswith("legacy_lazy_load = no\n}")


class TestModIconImports:
    @requires_dxt5
    def test_focus_icon_with_shine_is_staged_until_save(self, tmp_path: Path) -> None:
        source = _make_image(tmp_path / "icon.png")
        root = tmp_path / "my_mod"
        mod = Mod(root)

        sprite = mod.import_focus_icon("focus_TST_industry", source)

        assert sprite == "GFX_focus_TST_industry"
        texture = root / "gfx/interface/goals/focus_TST_industry.dds"
        gfx = root / "interface/my_mod_goals.gfx"
        shine = root / "interface/my_mod_goals_shine.gfx"
        assert not texture.exists()
        preview = mod.preview()
        assert "gfx/interface/goals/focus_TST_industry.dds" in preview
        assert "interface/my_mod_goals_shine.gfx" in preview

        saved = mod.save(require_changes=True)
        assert {texture.resolve(), gfx.resolve(), shine.resolve()} <= set(
            saved.written_files
        )
        assert _dds_size(texture) == FOCUS_ICON_SIZE
        assert texture.read_bytes()[84:88] == b"DXT5"
        assert gfx.read_text(encoding="utf-8") == (
            "spriteTypes = {\n"
            "\tSpriteType = {\n"
            '\t\tname = "GFX_focus_TST_industry"\n'
            '\t\ttexturefile = "gfx/interface/goals/focus_TST_industry.dds"\n'
            "\t}\n"
            "}\n"
        )
        shine_text = shine.read_text(encoding="utf-8")
        assert 'name = "GFX_focus_TST_industry_shine"' in shine_text
        assert (
            'animationmaskfile = "gfx/interface/goals/focus_TST_industry.dds"'
            in shine_text
        )

    @requires_dxt5
    def test_reimporting_identical_icon_is_a_noop(self, tmp_path: Path) -> None:
        source = _make_image(tmp_path / "icon.png")
        root = tmp_path / "mod"
        mod = Mod(root)
        mod.import_focus_icon("focus_x", source)
        mod.save(require_changes=True)
        before = {path: path.read_bytes() for path in _sprite_files(root)}

        reopened = Mod(root)
        assert reopened.import_focus_icon("focus_x", source) == "GFX_focus_x"
        assert reopened.preview() == ""
        with pytest.warns(RuntimeWarning, match="no dirty changes"):
            assert reopened.save().no_changes

        # Staged twice in one session is also a no-op for the second call.
        fresh = Mod(tmp_path / "other")
        fresh.import_idea_icon("my_spirit", source)
        staged = dict(fresh._pending_asset_writes)
        fresh.import_idea_icon("my_spirit", source)
        assert fresh._pending_asset_writes == staged
        assert {path: path.read_bytes() for path in _sprite_files(root)} == before

    @requires_dxt5
    def test_overwrite_semantics(self, tmp_path: Path) -> None:
        red = _make_image(tmp_path / "red.png")
        blue = _make_image(tmp_path / "blue.png", color=(10, 20, 220, 255))
        root = tmp_path / "mod"
        mod = Mod(root)
        mod.import_decision_icon("my_decision", red)
        mod.save(require_changes=True)
        texture = root / "gfx/interface/decisions/my_decision.dds"
        original = texture.read_bytes()

        mod = Mod(root)
        with pytest.raises(FileExistsError, match="my_decision.dds"):
            mod.import_decision_icon("my_decision", blue)
        assert mod.preview() == ""
        assert mod.import_decision_icon("my_decision", blue, overwrite=True) == (
            "GFX_decision_my_decision"
        )
        mod.save(require_changes=True)
        assert texture.read_bytes() != original

    @requires_dxt5
    def test_existing_gfx_file_is_preserved_byte_for_byte(self, tmp_path: Path) -> None:
        source = _make_image(tmp_path / "icon.png")
        root = tmp_path / "mod"
        gfx = root / "interface/mod_ideas.gfx"
        gfx.parent.mkdir(parents=True)
        original = (
            "# Ideas maintained by hand\n"
            "spriteTypes = {\n"
            "\t# existing national spirit\n"
            "\tspriteType = {\n"
            "\t\tname = \"GFX_idea_existing\"   # keep spacing\n"
            "\t\ttexturefile = \"gfx/interface/ideas/existing.dds\"\n"
            "\t}\n"
            "}\n"
        )
        gfx.write_bytes(original.encode("utf-8"))
        mod = Mod(root)

        assert mod.import_idea_icon("new_spirit", source) == "GFX_idea_new_spirit"
        mod.save(require_changes=True)

        text = gfx.read_text(encoding="utf-8")
        head = original[: -len("}\n")]
        assert text.startswith(head.rstrip("\n"))
        assert text == head + (
            "\tSpriteType = {\n"
            '\t\tname = "GFX_idea_new_spirit"\n'
            '\t\ttexturefile = "gfx/interface/ideas/new_spirit.dds"\n'
            "\t}\n"
            "}\n"
        )
        assert _dds_size(root / "gfx/interface/ideas/new_spirit.dds") == IDEA_ICON_SIZE

    @requires_dxt5
    def test_replacing_one_sprite_keeps_neighbours(self, tmp_path: Path) -> None:
        red = _make_image(tmp_path / "red.png")
        root = tmp_path / "mod"
        gfx = root / "interface/custom.gfx"
        gfx.parent.mkdir(parents=True)
        original = (
            "spriteTypes = {\n"
            "    SpriteType = { name = \"GFX_first\" texturefile = \"one.dds\" } # a\n"
            "    SpriteType = {\n"
            "        name = \"GFX_report_event_riot\"\n"
            "        texturefile = \"gfx/event_pictures/old_riot.dds\"\n"
            "    }\n"
            "    SpriteType = { name = \"GFX_last\" texturefile = \"two.dds\" } # b\n"
            "}\n"
        )
        gfx.write_text(original, encoding="utf-8")
        mod = Mod(root)

        sprite = mod.import_event_picture(
            "riot", red, gfx_file="interface/custom.gfx", overwrite=True
        )
        mod.save(require_changes=True)

        assert sprite == "GFX_report_event_riot"
        lines = gfx.read_text(encoding="utf-8").splitlines(keepends=True)
        original_lines = original.splitlines(keepends=True)
        assert lines[:2] == original_lines[:2]
        assert lines[-2:] == original_lines[-2:]
        assert "".join(lines[2:-2]) == (
            "    SpriteType = {\n"
            '        name = "GFX_report_event_riot"\n'
            '        texturefile = "gfx/event_pictures/riot.dds"\n'
            "    }\n"
        )

    @requires_dxt5
    def test_duplicate_sprite_in_another_file_is_rejected(self, tmp_path: Path) -> None:
        source = _make_image(tmp_path / "icon.png")
        root = tmp_path / "mod"
        other = root / "interface/legacy.gfx"
        other.parent.mkdir(parents=True)
        other.write_text(
            'spriteTypes = { SpriteType = { name = "GFX_idea_dupe" texturefile = "x.dds" } }\n',
            encoding="utf-8",
        )
        mod = Mod(root)
        with pytest.raises(ValueError, match="legacy.gfx"):
            mod.import_idea_icon("dupe", source)
        assert mod.preview() == ""

        mod.import_idea_icon("dupe", source, gfx_file="interface/legacy.gfx", overwrite=True)
        mod.save(require_changes=True)
        assert 'texturefile = "gfx/interface/ideas/dupe.dds"' in other.read_text(
            encoding="utf-8"
        )

    @requires_dxt5
    def test_decision_and_category_assets(self, tmp_path: Path) -> None:
        source = _make_image(tmp_path / "icon.png")
        root = tmp_path / "mod"
        mod = Mod(root)

        decision = mod.import_decision_icon("GFX_decision_rally", source, size=(48, 40))
        category_icon = mod.import_decision_category_icon("my_category", source)
        picture = mod.import_decision_category_picture("my_category", source)
        mod.save(require_changes=True)

        assert decision == "GFX_decision_rally"
        assert category_icon == "GFX_decision_category_my_category"
        assert picture == "GFX_decision_cat_picture_my_category"
        assert _dds_size(root / "gfx/interface/decisions/rally.dds") == (48, 40)
        assert _dds_size(
            root / "gfx/interface/decisions/category_pictures/my_category.dds"
        ) == DECISION_CATEGORY_PICTURE_SIZE
        assert (root / "gfx/interface/decisions/categories/my_category.dds").is_file()
        text = (root / "interface/mod_decisions.gfx").read_text(encoding="utf-8")
        for sprite in (decision, category_icon, picture):
            assert text.count(f'name = "{sprite}"') == 1

    @requires_dxt5
    def test_event_pictures_report_and_news(self, tmp_path: Path) -> None:
        source = _make_image(tmp_path / "picture.png")
        root = tmp_path / "mod"
        mod = Mod(root)

        report = mod.import_event_picture("strike", source)
        news = mod.import_event_picture("headline", source, news=True)
        mod.save(require_changes=True)

        assert report == "GFX_report_event_strike"
        assert news == "GFX_news_event_headline"
        assert _dds_size(root / "gfx/event_pictures/strike.dds") == EVENT_PICTURE_SIZE
        assert _dds_size(root / "gfx/event_pictures/headline.dds") == (
            NEWS_EVENT_PICTURE_SIZE
        )
        text = (root / "interface/mod_eventpictures.gfx").read_text(encoding="utf-8")
        assert 'texturefile = "gfx/event_pictures/headline.dds"' in text
        assert 'name = "GFX_report_event_strike"' in text

    @requires_dxt5
    def test_transaction_rollback_discards_staged_files(self, tmp_path: Path) -> None:
        source = _make_image(tmp_path / "icon.png")
        root = tmp_path / "mod"
        mod = Mod(root)

        with mod.transaction():
            mod.import_focus_icon("focus_temp", source)
            mod.import_event_picture("temp", source)
            assert "focus_temp.dds" in mod.preview()
            assert "GFX_focus_temp" in mod.suggest_focus_icons("focus_temp")

        assert mod.preview() == ""
        assert "GFX_focus_temp" not in mod.suggest_focus_icons("focus_temp", count=50)
        assert not any(root.rglob("*.dds"))
        assert not any(root.rglob("*.gfx"))

        with pytest.raises(RuntimeError, match="boom"):
            with mod.transaction():
                mod.import_idea_icon("temp", source)
                raise RuntimeError("boom")
        assert mod.preview() == ""

    @requires_dxt5
    def test_validate_accepts_staged_focus_and_idea_icons(self, tmp_path: Path) -> None:
        source = _make_image(tmp_path / "icon.png")
        root = tmp_path / "mod"
        mod = Mod(root)
        focus_icon = mod.import_focus_icon("focus_TST_custom", source)
        idea_icon = mod.import_idea_icon("TST_custom_spirit", source)
        mod.create_focus_tree("tst_focus", "TST")
        mod.add_focus("tst_focus", Focus(id="TST_custom", icon=focus_icon))
        mod.add_focus("tst_focus", Focus(id="TST_missing", icon="GFX_nope", x=2))
        mod.create_idea("TST_spirit", icon=idea_icon)

        def icon_codes() -> list[tuple[str, str]]:
            return [
                (error.code or "", error.message)
                for error in mod.validate(validate_icons=True)
                if error.code in {"unknown_focus_icon", "unknown_idea_icon"}
            ]

        staged = icon_codes()
        assert [code for code, _ in staged] == ["unknown_focus_icon"]
        assert "GFX_nope" in staged[0][1]
        assert mod.suggest_idea_icon("TST custom spirit") == "TST_custom_spirit"

        mod.save(require_changes=True)
        reopened = Mod(root)
        codes = [
            error.code
            for error in reopened.validate(validate_icons=True)
            if error.code in {"unknown_focus_icon", "unknown_idea_icon"}
        ]
        assert codes == ["unknown_focus_icon"]

    def test_rejects_unsafe_names_and_paths(self, tmp_path: Path) -> None:
        source = _make_image(tmp_path / "icon.png")
        mod = Mod(tmp_path / "mod")
        with pytest.raises(ValueError, match="focus icon name"):
            mod.import_focus_icon("../escape", source)
        with pytest.raises(ValueError, match="escapes mod root"):
            mod.import_idea_icon("x", source, gfx_file="../outside.gfx")
        with pytest.raises(ValueError, match="under interface/"):
            mod.import_idea_icon("x", source, gfx_file="common/x.gfx")
        with pytest.raises(ValueError, match=".gfx extension"):
            mod.import_idea_icon("x", source, gfx_file="interface/x.txt")
        with pytest.raises(ValueError, match="positive"):
            mod.import_idea_icon("x", source, size=(0, 10))
        assert mod.preview() == ""


class TestStandaloneIconImport:
    @requires_dxt5
    def test_writes_immediately_and_is_idempotent(self, tmp_path: Path) -> None:
        source = _make_image(tmp_path / "icon.png")
        root = tmp_path / "Fancy Mod!"

        asset = import_icon_to_mod(root, "focus", "focus_fancy", source)

        assert default_icon_gfx_path(root, "focus") == "interface/fancy_mod_goals.gfx"
        assert asset.sprite_name == "GFX_focus_fancy"
        assert asset.shine_sprite_name == "GFX_focus_fancy_shine"
        assert asset.gfx == (root / "interface/fancy_mod_goals.gfx").resolve()
        assert asset.shine_gfx == (root / "interface/fancy_mod_goals_shine.gfx").resolve()
        assert _dds_size(asset.texture) == FOCUS_ICON_SIZE
        before = {path: path.stat().st_mtime_ns for path in _sprite_files(root)}
        contents = {path: path.read_bytes() for path in _sprite_files(root)}

        again = import_icon_to_mod(root, "focus", "focus_fancy", source)
        assert again == asset
        assert {path: path.stat().st_mtime_ns for path in _sprite_files(root)} == before
        assert {path: path.read_bytes() for path in _sprite_files(root)} == contents

    @requires_dxt5
    def test_focus_without_shine_and_unknown_kind(self, tmp_path: Path) -> None:
        source = _make_image(tmp_path / "icon.png")
        root = tmp_path / "mod"
        asset = import_icon_to_mod(root, "focus", "plain", source, shine=False)
        assert asset.shine_gfx is None
        assert not (root / "interface/mod_goals_shine.gfx").exists()
        with pytest.raises(ValueError, match="Unknown icon kind"):
            import_icon_to_mod(root, "flag", "plain", source)
        with pytest.raises(ValueError, match="Only focus icons"):
            import_icon_to_mod(root, "idea", "plain", source, shine=True)
