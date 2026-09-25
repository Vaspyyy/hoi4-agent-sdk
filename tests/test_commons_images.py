"""Offline Commons transport, provenance, and raster safety regressions."""

from dataclasses import replace
import io
import json
from urllib.error import HTTPError
from unittest.mock import Mock

import pytest

from hoi4.commons_images import CommonsImageClient, CommonsImageError, _Redirects


def page(index=1, mime="image/png"):
    return {
        "title": "File:Example.png",
        "pageid": index,
        "index": index,
        "imageinfo": [
            {
                "url": "https://upload.wikimedia.org/example.png",
                "thumburl": "https://thumb.wikimedia.org/example.png",
                "descriptionurl": "https://commons.wikimedia.org/wiki/File:Example.png",
                "mime": mime,
                "width": 800,
                "height": 600,
                "extmetadata": {
                    "Artist": {"value": '<a href="https://example.org">A &amp; B</a>'},
                    "LicenseShortName": {"value": "CC BY-SA 4.0"},
                    "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"},
                    "AttributionRequired": {"value": "true"},
                },
            }
        ],
    }


def fixture_image(monkeypatch):
    client = CommonsImageClient()
    monkeypatch.setattr(
        client, "_request", lambda *a, **k: json.dumps({"query": {"pages": [page()]}}).encode()
    )
    return client, client.get_image("Example.png")


def png():
    Image = pytest.importorskip("PIL.Image")
    data = io.BytesIO()
    Image.new("RGB", (4, 3), "red").save(data, format="PNG")
    return data.getvalue()


def test_metadata_ranking_svg_and_query(monkeypatch):
    client = CommonsImageClient()
    request = Mock(
        return_value=json.dumps({"query": {"pages": [page(2), page(1, "image/svg+xml")]}}).encode()
    )
    monkeypatch.setattr(client, "_request", request)
    images = client.search("a historical flag", limit=2)
    assert [x.page_id for x in images] == [1, 2]
    assert images[0].artist == "A & B"
    assert "https://example.org" in images[0].raw_metadata
    assert images[0].download_url.startswith("https://thumb.wikimedia.org")
    assert images[0].attribution_required
    assert "gsrnamespace=6" in request.call_args.args[0]
    assert "gsrsearch=a+historical+flag" in request.call_args.args[0]


def test_download_provenance_and_cache(monkeypatch, tmp_path):
    client, image = fixture_image(monkeypatch)
    request = Mock(return_value=png())
    monkeypatch.setattr(client, "_request", request)
    downloaded = client.download(image, tmp_path)
    record = json.loads(downloaded.metadata_path.read_text())
    assert record["source"]["license_name"] == "CC BY-SA 4.0"
    assert "https://example.org" in record["extmetadata"]["Artist"]["value"]
    assert record["sha256"] == downloaded.sha256
    assert record["retrieved_at"].endswith("+00:00")
    assert client.download(image, tmp_path) == downloaded
    assert request.call_count == 1
    downloaded.path.write_bytes(b"tampered")
    with pytest.raises(CommonsImageError, match="checksum"):
        client.download(image, tmp_path)
    assert request.call_count == 1


@pytest.mark.parametrize("payload", [b"<svg></svg>", b"<html>oops</html>", b"garbage"])
def test_invalid_download_leaves_no_files(monkeypatch, tmp_path, payload):
    pytest.importorskip("PIL.Image")
    client, image = fixture_image(monkeypatch)
    monkeypatch.setattr(client, "_request", Mock(return_value=payload))
    with pytest.raises(CommonsImageError, match="raster"):
        client.download(image, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_refuse_overwrite_and_source_mismatch(monkeypatch, tmp_path):
    client, image = fixture_image(monkeypatch)
    monkeypatch.setattr(client, "_request", Mock(return_value=png()))
    downloaded = client.download(image, tmp_path)
    with pytest.raises(CommonsImageError, match="provenance"):
        client.download(replace(image, artist="different"), tmp_path)
    downloaded.metadata_path.unlink()
    with pytest.raises(CommonsImageError, match="retain"):
        client.download(image, tmp_path)
    assert downloaded.path.read_bytes() == png()


class Response(io.BytesIO):
    def __init__(self, payload, headers=None):
        super().__init__(payload)
        self.headers = headers or {}

    def geturl(self):
        return "https://upload.wikimedia.org/example.png"


@pytest.mark.parametrize("headers", [{}, {"Content-Length": "30"}])
def test_transfer_byte_bound(monkeypatch, headers):
    client = CommonsImageClient(max_download_bytes=10)
    opener = Mock()
    opener.open.return_value = Response(b"x" * 30, headers)
    monkeypatch.setattr("hoi4.commons_images.build_opener", Mock(return_value=opener))
    with pytest.raises(CommonsImageError, match="byte limit"):
        client._request("https://upload.wikimedia.org/example.png", media=True)


def test_http_error_and_api_error(monkeypatch):
    client = CommonsImageClient()
    opener = Mock()
    opener.open.side_effect = HTTPError("url", 429, "rate", {}, None)
    monkeypatch.setattr("hoi4.commons_images.build_opener", Mock(return_value=opener))
    with pytest.raises(CommonsImageError, match="429.*rate limited"):
        client._request("https://upload.wikimedia.org/example.png", media=True)
    monkeypatch.setattr(
        client, "_request", Mock(return_value=b'{"error":{"code":"maxlag","info":"busy"}}')
    )
    with pytest.raises(CommonsImageError, match="maxlag.*busy"):
        client.search("flag")


@pytest.mark.parametrize(
    "url",
    [
        "http://upload.wikimedia.org/a",
        "https://127.0.0.1/a",
        "https://upload.wikimedia.org.evil.org/a",
        "https://upload.wikimedia.org:444/a",
        "https://user@upload.wikimedia.org/a",
    ],
)
def test_redirect_and_initial_url_allowlist(url):
    with pytest.raises(CommonsImageError, match="allowlisted"):
        _Redirects(frozenset({"upload.wikimedia.org"})).redirect_request(
            None, None, 302, "", {}, url
        )
    with pytest.raises(CommonsImageError, match="allowlisted"):
        CommonsImageClient()._request(url, media=True)


def test_svg_without_thumbnail_and_missing_page(monkeypatch):
    client = CommonsImageClient()
    svg = page(mime="image/svg+xml")
    del svg["imageinfo"][0]["thumburl"]
    monkeypatch.setattr(
        client, "_request", Mock(return_value=json.dumps({"query": {"pages": [svg]}}).encode())
    )
    assert client.search("flag") == []
    with pytest.raises(CommonsImageError, match="No supported"):
        client.get_image("File:missing.svg")


def test_transfer_deadline(monkeypatch):
    client = CommonsImageClient(timeout=1)
    opener = Mock()
    opener.open.return_value = Response(b"x")
    monkeypatch.setattr("hoi4.commons_images.build_opener", Mock(return_value=opener))
    monkeypatch.setattr("hoi4.commons_images.time.monotonic", Mock(side_effect=[0, 2]))
    with pytest.raises(CommonsImageError, match="time limit"):
        client._request("https://upload.wikimedia.org/example.png", media=True)


def test_sidecar_failure_removes_candidate(monkeypatch, tmp_path):
    client, image = fixture_image(monkeypatch)
    monkeypatch.setattr(client, "_request", Mock(return_value=png()))
    from pathlib import Path

    original_open = Path.open

    def fail_metadata(path, *args, **kwargs):
        if path.suffix == ".json" and args and args[0] == "x":
            raise OSError("disk full")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_metadata)
    with pytest.raises(CommonsImageError, match="disk full"):
        client.download(image, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_cache_cannot_escape_directory(monkeypatch, tmp_path):
    client, image = fixture_image(monkeypatch)
    monkeypatch.setattr(client, "_request", Mock(return_value=png()))
    downloaded = client.download(image, tmp_path)
    record = json.loads(downloaded.metadata_path.read_text())
    assert (record["downloaded_width"], record["downloaded_height"]) == (4, 3)
    record["file"] = "../outside.png"
    downloaded.metadata_path.write_text(json.dumps(record))
    with pytest.raises(CommonsImageError, match="provenance"):
        client.download(image, tmp_path)


def test_search_is_dependency_free_and_download_explains_missing_pillow(monkeypatch, tmp_path):
    dependency = Mock(side_effect=ImportError("Pillow intentionally unavailable"))
    monkeypatch.setattr("hoi4.commons_images.import_module", dependency)
    client, image = fixture_image(monkeypatch)
    assert image.title == "File:Example.png"
    assert len(client.search("example")) == 1
    dependency.assert_not_called()
    with pytest.raises(CommonsImageError, match="requires Pillow.*assets extra"):
        client.download(image, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_public_import_does_not_load_pillow():
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from hoi4 import CommonsImageClient; "
            "assert not any(k == 'PIL' or k.startswith('PIL.') for k in sys.modules); "
            "CommonsImageClient()",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
