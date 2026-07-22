from __future__ import annotations

from pathlib import Path

import pytest

from hoi4 import assets
from hoi4.assets import (
    DDSExportUnsupportedError,
    BOOKMARK_PICTURE_SIZE,
    FLAG_SIZES,
    ImageBackendUnavailableError,
    UnsupportedImageFormatError,
    export_flag_from_mod,
    export_portrait_from_mod,
    find_flag_path,
    find_portrait_path,
    import_flag_to_mod,
    import_bookmark_picture_to_mod,
    import_portrait_to_mod,
    write_portrait_gfx,
)

Image = pytest.importorskip("PIL.Image")


def _make_image(path: Path, size: tuple[int, int] = (320, 240)) -> Path:
    Image.new("RGBA", size, (220, 30, 40, 255)).save(path, format="PNG")
    return path


class TestFlags:
    def test_imports_all_three_tga_sizes(self, tmp_path: Path) -> None:
        source = _make_image(tmp_path / "flag.png")
        result = import_flag_to_mod(tmp_path / "mod", "abc", source)

        assert len(result) == 1
        assert result[0].tag == "ABC"
        for size_name, path in zip(("large", "medium", "small"), result[0].paths):
            assert path.is_file()
            with Image.open(path) as image:
                assert image.size == FLAG_SIZES[size_name]
                assert image.format == "TGA"

    def test_vanilla_override_and_custom_variants(self, tmp_path: Path) -> None:
        source = _make_image(tmp_path / "flag.png")
        root = tmp_path / "mod"
        variants = import_flag_to_mod(root, "GER", source, vanilla_override=True)
        assert [variant.ideology for variant in variants] == [
            None,
            "neutrality",
            "democratic",
            "fascism",
            "communism",
        ]
        assert all(path.is_file() for variant in variants for path in variant.paths)

        custom_root = tmp_path / "custom"
        custom = import_flag_to_mod(
            custom_root, "TST", source, ideologies=[None, "social_democracy", None]
        )
        assert [variant.ideology for variant in custom] == [None, "social_democracy"]
        assert find_flag_path(custom_root, "TST", ideology="social_democracy") is not None

    def test_exports_flag_to_png_and_preserves_dimensions(self, tmp_path: Path) -> None:
        source = _make_image(tmp_path / "flag.png")
        root = tmp_path / "mod"
        import_flag_to_mod(root, "TST", source)
        destination = tmp_path / "exported.png"
        result = export_flag_from_mod(root, "TST", destination)
        assert result == destination
        with Image.open(destination) as image:
            assert image.size == (82, 52)
            assert image.format == "PNG"

        with pytest.raises(FileExistsError):
            export_flag_from_mod(root, "TST", destination)

    def test_rejects_invalid_tag_and_dds_flag_export(self, tmp_path: Path) -> None:
        source = _make_image(tmp_path / "flag.png")
        with pytest.raises(ValueError, match="Invalid country tag"):
            import_flag_to_mod(tmp_path / "mod", "../", source)

        root = tmp_path / "valid"
        import_flag_to_mod(root, "TST", source)
        with pytest.raises(UnsupportedImageFormatError, match="flags use TGA"):
            export_flag_from_mod(root, "TST", tmp_path / "flag.dds")

    def test_rejects_output_symlink_escape(self, tmp_path: Path) -> None:
        source = _make_image(tmp_path / "flag.png")
        root = tmp_path / "mod"
        outside = tmp_path / "outside"
        root.mkdir()
        outside.mkdir()
        (root / "gfx").symlink_to(outside, target_is_directory=True)
        with pytest.raises(ValueError, match="escapes mod root"):
            import_flag_to_mod(root, "TST", source)
        assert list(outside.rglob("*")) == []

    def test_batch_commit_failure_restores_every_existing_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = _make_image(tmp_path / "flag.png")
        root = tmp_path / "mod"
        targets = (
            root / "gfx/flags/TST.tga",
            root / "gfx/flags/medium/TST.tga",
            root / "gfx/flags/small/TST.tga",
        )
        originals = {
            target: f"old-{index}".encode()
            for index, target in enumerate(targets, start=1)
        }
        for target, content in originals.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

        real_replace = assets.os.replace
        failed = False

        def fail_second_commit(source_path: object, destination_path: object) -> None:
            nonlocal failed
            if Path(destination_path) == targets[1] and not failed:
                failed = True
                raise OSError("injected image commit failure")
            real_replace(source_path, destination_path)

        monkeypatch.setattr(assets.os, "replace", fail_second_commit)

        with pytest.raises(OSError, match="injected image commit failure"):
            import_flag_to_mod(root, "TST", source, overwrite=True)

        assert failed
        assert {target: target.read_bytes() for target in targets} == originals
        assert {path for path in root.rglob("*") if path.is_file()} == set(targets)


class TestPortraits:
    def test_imports_tga_portrait_at_standard_size_and_exports_png(self, tmp_path: Path) -> None:
        source = _make_image(tmp_path / "portrait.png", (500, 300))
        root = tmp_path / "mod"
        portrait = import_portrait_to_mod(root, "TST", "leader_1", source, output_format="tga")
        assert portrait == root / "gfx" / "leaders" / "TST" / "leader_1.tga"
        with Image.open(portrait) as image:
            assert image.size == (156, 210)
            assert image.format == "TGA"

        exported = export_portrait_from_mod(
            root, "TST", "leader_1", tmp_path / "portrait_export.png"
        )
        with Image.open(exported) as image:
            assert image.size == (156, 210)
            assert image.format == "PNG"

    def test_encodes_dxt5_dds_with_pillow(self, tmp_path: Path) -> None:
        source = _make_image(tmp_path / "portrait.png", (156, 210))
        try:
            portrait = import_portrait_to_mod(tmp_path / "mod", "TST", "leader", source)
        except DDSExportUnsupportedError as exc:  # pragma: no cover - old optional Pillow
            pytest.skip(str(exc))

        data = portrait.read_bytes()
        assert data[:4] == b"DDS "
        assert data[84:88] == b"DXT5"
        with Image.open(portrait) as image:
            assert image.size == (156, 210)

    def test_dds_copy_export_preserves_bytes_without_decoding(self, tmp_path: Path) -> None:
        root = tmp_path / "mod"
        source = root / "gfx" / "leaders" / "TST" / "leader.dds"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"DDS " + b"opaque codec payload")

        destination = tmp_path / "copy.dds"
        result = export_portrait_from_mod(root, "TST", "leader", destination)
        assert result.read_bytes() == source.read_bytes()

    def test_writes_one_sprite_file_per_portrait_without_clobbering(self, tmp_path: Path) -> None:
        source = _make_image(tmp_path / "portrait.png")
        root = tmp_path / "mod"
        first = import_portrait_to_mod(root, "TST", "leader_1", source, output_format="tga")
        second = import_portrait_to_mod(root, "TST", "leader_2", source, output_format="tga")
        first_gfx = write_portrait_gfx(root, "TST", "leader_1")
        second_gfx = write_portrait_gfx(root, "TST", "leader_2")

        assert first_gfx != second_gfx
        assert first.is_file() and second.is_file()
        assert 'name = "GFX_portrait_TST_leader_1"' in first_gfx.read_text(encoding="utf-8")
        assert 'texturefile = "gfx/leaders/TST/leader_2.tga"' in second_gfx.read_text(
            encoding="utf-8"
        )

    def test_gfx_rejects_missing_and_external_texture(self, tmp_path: Path) -> None:
        root = tmp_path / "mod"
        root.mkdir()
        with pytest.raises(FileNotFoundError, match="No portrait"):
            write_portrait_gfx(root, "TST", "leader")

        external = _make_image(tmp_path / "external.png")
        with pytest.raises(ValueError, match="escapes"):
            write_portrait_gfx(root, "TST", "leader", portrait_path=external)

    def test_invalid_slug_cannot_escape_asset_directory(self, tmp_path: Path) -> None:
        source = _make_image(tmp_path / "portrait.png")
        with pytest.raises(ValueError, match="portrait slug"):
            import_portrait_to_mod(tmp_path / "mod", "TST", "../leader", source)
        assert find_portrait_path(tmp_path / "mod", "TST", "leader") is None

    def test_invalid_staged_dds_does_not_destroy_existing_portrait(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = _make_image(tmp_path / "portrait.png", (156, 210))
        root = tmp_path / "mod"
        target = root / "gfx/leaders/TST/leader.dds"
        target.parent.mkdir(parents=True)
        original = b"DDS " + b"pre-existing portrait payload"
        target.write_bytes(original)
        monkeypatch.setattr(assets, "_is_dds", lambda _path: False)

        with pytest.raises(DDSExportUnsupportedError, match="valid DDS"):
            import_portrait_to_mod(root, "TST", "leader", source, overwrite=True)

        assert target.read_bytes() == original
        assert {path for path in root.rglob("*") if path.is_file()} == {target}


class TestBookmarkPictures:
    def test_imports_texture_and_writes_matching_sprite(self, tmp_path: Path) -> None:
        source = _make_image(tmp_path / "bookmark.png")

        asset = import_bookmark_picture_to_mod(
            tmp_path / "mod", "my_start", source, output_format="tga"
        )

        assert asset.sprite_name == "GFX_my_start"
        with Image.open(asset.texture) as image:
            assert image.size == BOOKMARK_PICTURE_SIZE
        gfx = asset.gfx.read_text(encoding="utf-8")
        assert 'name = "GFX_my_start"' in gfx
        assert 'texturefile = "gfx/interface/my_start.tga"' in gfx

    def test_gfx_commit_failure_restores_existing_texture_and_gfx(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "mod"
        first_source = _make_image(tmp_path / "first.png")
        asset = import_bookmark_picture_to_mod(
            root, "my_start", first_source, output_format="tga"
        )
        originals = {
            asset.texture: asset.texture.read_bytes(),
            asset.gfx: asset.gfx.read_bytes(),
        }
        second_source = tmp_path / "second.png"
        Image.new("RGBA", (320, 240), (20, 180, 80, 255)).save(second_source, format="PNG")

        real_replace = assets.os.replace
        failed = False

        def fail_gfx_commit(source_path: object, destination_path: object) -> None:
            nonlocal failed
            if Path(destination_path) == asset.gfx and not failed:
                failed = True
                raise OSError("injected bookmark gfx commit failure")
            real_replace(source_path, destination_path)

        monkeypatch.setattr(assets.os, "replace", fail_gfx_commit)

        with pytest.raises(OSError, match="injected bookmark gfx commit failure"):
            import_bookmark_picture_to_mod(
                root, "my_start", second_source, output_format="tga", overwrite=True
            )

        assert failed
        assert {path: path.read_bytes() for path in originals} == originals
        assert {path for path in root.rglob("*") if path.is_file()} == set(originals)


class TestBackendAndFormats:
    def test_missing_pillow_has_actionable_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "input.png"
        source.write_bytes(b"not opened before backend lookup")

        def missing_import(name: str):
            if name.startswith("PIL"):
                raise ImportError("not installed")
            raise AssertionError(name)

        monkeypatch.setattr(assets, "import_module", missing_import)
        with pytest.raises(ImageBackendUnavailableError, match="optional Pillow"):
            import_flag_to_mod(tmp_path / "mod", "TST", source)

    def test_svg_is_explicitly_unsupported_without_implicit_imagemagick(
        self, tmp_path: Path
    ) -> None:
        source = tmp_path / "flag.svg"
        source.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
        with pytest.raises(UnsupportedImageFormatError, match="does not invoke ImageMagick"):
            import_flag_to_mod(tmp_path / "mod", "TST", source)
