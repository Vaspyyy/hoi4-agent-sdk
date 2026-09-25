"""Downloaded sources work with the existing transactional Mod asset pipeline."""

import io
import json
from pathlib import Path

import pytest

from hoi4 import CommonsImageClient, Mod


@pytest.mark.parametrize("kind", ["flag", "portrait"])
def test_downloaded_assets_import_transactionally(tmp_path: Path, monkeypatch, kind: str) -> None:
    image_backend = pytest.importorskip("PIL.Image")
    payload = io.BytesIO()
    image_backend.new("RGB", (160, 210), "blue").save(payload, format="PNG")
    response = {
        "query": {
            "pages": [
                {
                    "title": "File:Test.svg",
                    "pageid": 42,
                    "imageinfo": [
                        {
                            "url": "https://upload.wikimedia.org/test.svg",
                            "thumburl": "https://thumb.wikimedia.org/test.svg.png",
                            "descriptionurl": "https://commons.wikimedia.org/wiki/File:Test.svg",
                            "mime": "image/svg+xml",
                            "width": 160,
                            "height": 210,
                            "extmetadata": {"LicenseShortName": {"value": "Public domain"}},
                        }
                    ],
                }
            ]
        }
    }
    client = CommonsImageClient()
    monkeypatch.setattr(
        client,
        "_request",
        lambda url, *, media: payload.getvalue() if media else json.dumps(response).encode(),
    )
    source = client.download(client.get_image("File:Test.svg"), tmp_path / "assets/sources")
    mod = Mod(tmp_path)

    def stage() -> list[tuple[Path, tuple[int, int]]]:
        if kind == "flag":
            flags = mod.import_flag_to_mod("TST", source.path)[0]
            return [(flags.large, (82, 52)), (flags.medium, (41, 26)), (flags.small, (10, 7))]
        portrait = mod.import_portrait_to_mod("TST", "leader", source.path)
        mod.write_portrait_gfx("TST", "leader", portrait_path=portrait)
        return [(portrait, (156, 210))]

    with mod.transaction():
        targets = stage()
        assert "Binary asset create" in mod.preview()
        assert all(not path.exists() for path, _ in targets)
    assert mod.preview() == ""
    assert source.path.is_file() and source.metadata_path.is_file()

    targets = stage()
    result = mod.save(require_changes=True)
    for path, size in targets:
        assert path in result.written_files
        with image_backend.open(path) as image:
            assert image.size == size
    if kind == "portrait":
        assert any(path.suffix == ".gfx" for path in result.written_files)
    assert source.path.read_bytes() == payload.getvalue()
    assert json.loads(source.metadata_path.read_text())["source"]["license_name"] == "Public domain"
