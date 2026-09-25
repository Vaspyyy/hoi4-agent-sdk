"""Free Wikimedia Commons image discovery with bounded, attributed downloads.

Candidates remain external assets until explicitly imported through :class:`hoi4.Mod`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
from importlib import import_module
import io
import json
from pathlib import Path
import time
import warnings
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

_API = "https://commons.wikimedia.org/w/api.php"
_MEDIA_HOSTS = frozenset({"upload.wikimedia.org", "thumb.wikimedia.org"})
_RASTER = {
    "PNG": ".png",
    "JPEG": ".jpg",
    "WEBP": ".webp",
    "TIFF": ".tiff",
    "GIF": ".gif",
    "BMP": ".bmp",
}


class CommonsImageError(RuntimeError):
    """Commons discovery, transfer, validation, or provenance failure."""


@dataclass(frozen=True)
class CommonsImage:
    title: str
    page_id: int
    source_url: str
    original_url: str
    download_url: str
    mime_type: str
    width: int
    height: int
    artist: str
    credit: str
    license_name: str
    license_url: str
    usage_terms: str
    attribution_required: bool
    description: str
    raw_metadata: str = "{}"  # Immutable JSON retaining original HTML and attribution links.


@dataclass(frozen=True)
class DownloadedCommonsImage:
    path: Path
    metadata_path: Path
    sha256: str
    source: CommonsImage


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _plain(value: str) -> str:
    parser = _Text()
    parser.feed(value)
    return " ".join(" ".join(parser.parts).split())


def _check_url(url: str, hosts: frozenset[str]) -> None:
    try:
        parsed = urlsplit(url)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname in hosts
            and parsed.port in (None, 443)
            and not parsed.username
            and not parsed.password
        )
    except ValueError as exc:
        raise CommonsImageError("Invalid Wikimedia URL") from exc
    if not valid:
        raise CommonsImageError(f"Refusing non-allowlisted Wikimedia URL: {url}")


class _Redirects(HTTPRedirectHandler):
    def __init__(self, hosts: frozenset[str]) -> None:
        self.hosts = hosts
        super().__init__()

    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> Any:
        _check_url(newurl, self.hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class CommonsImageClient:
    """Read public Commons metadata and retain verified raster candidates locally.

    ``width``/``height`` and ``mime_type`` describe the original Commons file;
    ``download_url`` may select its raster thumbnail, including for SVG originals.
    Missing attribution requirements conservatively default to True.
    """

    def __init__(
        self,
        user_agent: str = (
            "hoi4-agent-sdk/CommonsImageClient (https://github.com/Vaspyyy/hoi4-agent-sdk)"
        ),
        timeout: float = 30,
        max_download_bytes: int = 20_000_000,
    ) -> None:
        if not user_agent.strip() or timeout <= 0 or max_download_bytes <= 0:
            raise ValueError("Require a descriptive user_agent and positive timeout/byte limit")
        self.user_agent = user_agent
        self.timeout = timeout
        self.max_download_bytes = max_download_bytes

    def _request(self, url: str, *, media: bool) -> bytes:
        hosts = _MEDIA_HOSTS if media else frozenset({"commons.wikimedia.org"})
        _check_url(url, hosts)
        limit = self.max_download_bytes if media else 5_000_000
        start = time.monotonic()
        try:
            opener = build_opener(_Redirects(hosts))
            with opener.open(
                Request(url, headers={"User-Agent": self.user_agent}), timeout=self.timeout
            ) as response:
                _check_url(response.geturl(), hosts)
                content_length = response.headers.get("Content-Length")
                if content_length is not None and int(content_length) > limit:
                    raise CommonsImageError(f"Response exceeds {limit} byte limit")
                chunks: list[bytes] = []
                count = 0
                while True:
                    if time.monotonic() - start > self.timeout:
                        raise CommonsImageError("Wikimedia transfer exceeded time limit")
                    chunk = response.read(min(65536, limit + 1 - count))
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > limit:
                        raise CommonsImageError(f"Response exceeds {limit} byte limit")
                    chunks.append(chunk)
                return b"".join(chunks)
        except HTTPError as exc:
            hint = " (rate limited; retry later)" if exc.code == 429 else ""
            raise CommonsImageError(f"Wikimedia HTTP {exc.code}{hint}") from exc
        except (URLError, OSError, ValueError) as exc:
            raise CommonsImageError(f"Wikimedia request failed: {exc}") from exc

    def _query(self, params: dict[str, Any], thumbnail_width: int) -> list[CommonsImage]:
        if not 1 <= thumbnail_width <= 4096:
            raise ValueError("thumbnail_width must be between 1 and 4096")
        params.update(
            action="query",
            prop="imageinfo",
            iiprop="url|size|mime|extmetadata",
            iiurlwidth=thumbnail_width,
            format="json",
            formatversion=2,
        )
        try:
            data = json.loads(self._request(_API + "?" + urlencode(params), media=False))
            if "error" in data:
                error = data["error"]
                raise CommonsImageError(f"Commons API {error.get('code')}: {error.get('info')}")
            pages = data.get("query", {}).get("pages", [])
            images = []
            for page in sorted(pages, key=lambda p: p.get("index", 0)):
                if not page.get("imageinfo"):
                    continue
                info = page["imageinfo"][0]
                meta = info.get("extmetadata", {})

                def field(key: str) -> str:
                    return _plain(str(meta.get(key, {}).get("value", "")))

                original = info["url"]
                download = info.get("thumburl", original)
                _check_url(original, _MEDIA_HOSTS)
                _check_url(download, _MEDIA_HOSTS)
                source_url = info["descriptionurl"]
                _check_url(source_url, frozenset({"commons.wikimedia.org"}))
                mime = info["mime"]
                if mime == "image/svg+xml" and download == original:
                    continue  # SVG must have a server-rendered raster thumbnail.
                if mime not in {
                    "image/svg+xml",
                    "image/png",
                    "image/jpeg",
                    "image/webp",
                    "image/tiff",
                    "image/gif",
                    "image/bmp",
                    "image/x-ms-bmp",
                }:
                    continue
                images.append(
                    CommonsImage(
                        title=page["title"],
                        page_id=int(page["pageid"]),
                        source_url=source_url,
                        original_url=original,
                        download_url=download,
                        mime_type=mime,
                        width=int(info["width"]),
                        height=int(info["height"]),
                        artist=field("Artist"),
                        credit=field("Credit"),
                        license_name=field("LicenseShortName"),
                        license_url=field("LicenseUrl"),
                        usage_terms=field("UsageTerms"),
                        attribution_required=field("AttributionRequired").lower() != "false",
                        description=field("ImageDescription"),
                        raw_metadata=json.dumps(meta, ensure_ascii=False, sort_keys=True),
                    )
                )
            return images
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise CommonsImageError(f"Malformed Commons API response: {exc}") from exc

    def search(
        self, query: str, *, limit: int = 5, thumbnail_width: int = 1024
    ) -> list[CommonsImage]:
        if not query.strip() or not 1 <= limit <= 50:
            raise ValueError("Require a nonempty query and limit between 1 and 50")
        return self._query(
            dict(generator="search", gsrnamespace=6, gsrsearch=query, gsrlimit=limit),
            thumbnail_width,
        )

    def get_image(self, title: str, *, thumbnail_width: int = 1024) -> CommonsImage:
        if not title.strip() or "|" in title:
            raise ValueError("Provide exactly one Commons file title")
        title = title if title.lower().startswith("file:") else "File:" + title
        images = self._query({"titles": title}, thumbnail_width)
        if not images:
            raise CommonsImageError(f"No supported raster image found for {title}")
        return images[0]

    def download(self, image: CommonsImage, directory: str | Path) -> DownloadedCommonsImage:
        """Download once, verify with Pillow, and retain JSON provenance beside it.

        Existing incomplete or conflicting cache records fail closed. No mod files
        are changed. Install the existing ``assets`` extra to enable raster checks.
        """
        _check_url(image.download_url, _MEDIA_HOSTS)
        root = Path(directory)
        identity = sha256(image.download_url.encode()).hexdigest()[:20]
        stem = f"commons-{image.page_id}-{identity}"
        sidecar = root / f"{stem}.json"
        source = asdict(image)
        if sidecar.is_symlink():
            raise CommonsImageError("Refusing symlinked Commons provenance")
        if sidecar.exists():
            try:
                record = json.loads(sidecar.read_text(encoding="utf-8"))
                name = record["file"]
                if (
                    name != Path(name).name
                    or name not in {stem + ext for ext in _RASTER.values()}
                    or record["source"] != source
                ):
                    raise CommonsImageError(
                        "Cached Commons provenance does not match requested source"
                    )
                path = root / name
                if path.is_symlink() or path.stat().st_size > self.max_download_bytes:
                    raise CommonsImageError("Unsafe or oversized cached Commons file")
                digest = sha256(path.read_bytes()).hexdigest()
                if digest != record["sha256"]:
                    raise CommonsImageError("Cached Commons file checksum mismatch")
                return DownloadedCommonsImage(path, sidecar, digest, image)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise CommonsImageError(f"Invalid Commons cache: {exc}") from exc
        try:
            Image = import_module("PIL.Image")
        except ImportError as exc:
            raise CommonsImageError(
                "Raster validation requires Pillow (the SDK assets extra)"
            ) from exc
        payload = self._request(image.download_url, media=True)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(payload)) as raster:
                    extension = _RASTER.get(raster.format or "")
                    if extension is None:
                        raise CommonsImageError("Downloaded file is not a supported raster image")
                    dimensions = raster.size
                    raster.verify()
                with Image.open(io.BytesIO(payload)) as raster:
                    raster.load()
        except (
            OSError,
            ValueError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ) as exc:
            raise CommonsImageError(f"Invalid raster image: {exc}") from exc
        digest = sha256(payload).hexdigest()
        path = root / (stem + extension)
        try:
            raw_metadata = json.loads(image.raw_metadata)
        except (ValueError, TypeError) as exc:
            raise CommonsImageError("Invalid source raw_metadata JSON") from exc
        record = {
            "schema_version": 1,
            "file": path.name,
            "sha256": digest,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "downloaded_width": dimensions[0],
            "downloaded_height": dimensions[1],
            "extmetadata": raw_metadata,
        }
        written: list[Path] = []
        try:
            root.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as output:
                written.append(path)
                output.write(payload)
            with sidecar.open("x", encoding="utf-8") as output:
                written.append(sidecar)
                json.dump(record, output, ensure_ascii=False, indent=2)
        except (OSError, ValueError) as exc:
            for item in reversed(written):
                item.unlink(missing_ok=True)
            raise CommonsImageError(f"Could not retain Commons candidate: {exc}") from exc
        return DownloadedCommonsImage(path, sidecar, digest, image)
