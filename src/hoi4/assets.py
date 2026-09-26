"""Safe, optional-Pillow image helpers for HOI4 flags and portraits.

Pillow is imported lazily so the core SDK remains usable without image tooling.
No ImageMagick subprocess is required for the formats Pillow can read and write.
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any, Literal, cast

from .paths import require_country_tag, require_script_id, resolve_mod_output_path
from .script import pdx_string


FLAG_SIZES: dict[str, tuple[int, int]] = {
    "large": (82, 52),
    "medium": (41, 26),
    "small": (10, 7),
}
VANILLA_FLAG_IDEOLOGIES: tuple[str, ...] = (
    "neutrality",
    "democratic",
    "fascism",
    "communism",
)
PORTRAIT_SIZE = (156, 210)
BOOKMARK_PICTURE_SIZE = (180, 104)
DDS_LIMITATION = (
    "DDS export uses Pillow's single-level DXT encoder and does not generate mipmaps. "
    "DXT5 is the recommended portrait output. Older Pillow releases may not provide "
    "or may ignore the requested compression; upgrade Pillow or export a TGA portrait instead."
)

_ASSET_STEM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_EXPORT_FORMATS: dict[str, str] = {
    ".bmp": "BMP",
    ".dds": "DDS",
    ".jpeg": "JPEG",
    ".jpg": "JPEG",
    ".png": "PNG",
    ".tga": "TGA",
    ".webp": "WEBP",
}


class ImageBackendUnavailableError(RuntimeError):
    """Raised when an image operation needs the optional Pillow dependency."""


class UnsupportedImageFormatError(ValueError):
    """Raised when an input or requested output is not supported safely."""


class DDSExportUnsupportedError(RuntimeError):
    """Raised when the active Pillow build cannot encode the requested DDS."""


@dataclass(frozen=True)
class FlagAssetSet:
    """The three HOI4 sizes generated for one tag/ideology combination."""

    tag: str
    ideology: str | None
    large: Path
    medium: Path
    small: Path

    @property
    def paths(self) -> tuple[Path, Path, Path]:
        return self.large, self.medium, self.small


@dataclass(frozen=True)
class BookmarkPictureAsset:
    """Texture and sprite declaration created for a bookmark picture."""

    sprite_name: str
    texture: Path
    gfx: Path


def import_flag_to_mod(
    mod_root: str | Path,
    tag: str,
    src_image: str | Path,
    vanilla_override: bool = False,
    *,
    ideologies: Iterable[str | None] | None = None,
    resize_mode: Literal["stretch", "cover", "contain"] = "stretch",
    overwrite: bool = True,
) -> tuple[FlagAssetSet, ...]:
    """Import a source image as the three TGA sizes HOI4 expects.

    ``vanilla_override=True`` writes the base flag and the four vanilla ideology
    variants.  Alternatively, pass an explicit ``ideologies`` iterable where
    ``None`` means the unsuffixed base flag.
    """

    normalized_tag = require_country_tag(tag)
    if ideologies is not None and vanilla_override:
        raise ValueError("Pass either vanilla_override or ideologies, not both")
    selected_ideologies = (
        tuple(ideologies)
        if ideologies is not None
        else ((None,) + VANILLA_FLAG_IDEOLOGIES if vanilla_override else (None,))
    )
    if not selected_ideologies:
        raise ValueError("At least one flag variant is required")
    selected_ideologies = tuple(
        dict.fromkeys(_require_ideology(value) for value in selected_ideologies)
    )

    root = _require_mod_root(mod_root)
    image = _open_raster(src_image)
    sets: list[FlagAssetSet] = []
    writes: list[tuple[Path, Any, str, dict[str, object]]] = []
    for ideology in selected_ideologies:
        suffix = "" if ideology is None else f"_{ideology}"
        targets = {
            "large": resolve_mod_output_path(root, f"gfx/flags/{normalized_tag}{suffix}.tga"),
            "medium": resolve_mod_output_path(
                root, f"gfx/flags/medium/{normalized_tag}{suffix}.tga"
            ),
            "small": resolve_mod_output_path(root, f"gfx/flags/small/{normalized_tag}{suffix}.tga"),
        }
        for size_name, target in targets.items():
            resized = _resize_image(image, FLAG_SIZES[size_name], resize_mode)
            writes.append((target, resized, "TGA", {}))
        sets.append(
            FlagAssetSet(
                tag=normalized_tag,
                ideology=ideology,
                large=targets["large"],
                medium=targets["medium"],
                small=targets["small"],
            )
        )
    _write_images_atomically(writes, overwrite=overwrite)
    return tuple(sets)


def find_flag_path(
    mod_root: str | Path,
    tag: str,
    *,
    ideology: str | None = None,
    size: Literal["large", "medium", "small"] = "large",
) -> Path | None:
    """Return an existing flag path without leaving ``mod_root``."""

    normalized_tag = require_country_tag(tag)
    normalized_ideology = _require_ideology(ideology)
    if size not in FLAG_SIZES:
        raise ValueError(f"Unknown flag size {size!r}; expected one of {tuple(FLAG_SIZES)}")
    root = _require_mod_root(mod_root, create=False)
    size_dir = "" if size == "large" else f"{size}/"
    suffix = "" if normalized_ideology is None else f"_{normalized_ideology}"
    path = resolve_mod_output_path(root, f"gfx/flags/{size_dir}{normalized_tag}{suffix}.tga")
    return path if path.is_file() else None


def export_flag_from_mod(
    mod_root: str | Path,
    tag: str,
    destination: str | Path,
    *,
    ideology: str | None = None,
    size: Literal["large", "medium", "small"] = "large",
    overwrite: bool = False,
) -> Path:
    """Export a mod TGA flag to PNG, JPEG, TGA, BMP, or WebP."""

    source = find_flag_path(mod_root, tag, ideology=ideology, size=size)
    if source is None:
        raise FileNotFoundError(f"No {size} flag found for {tag}")
    target = Path(destination).expanduser().resolve(strict=False)
    output_format = _output_format(target, allow_dds=False)
    image = _open_raster(source)
    image = _prepare_for_format(image, output_format)
    _write_images_atomically([(target, image, output_format, {})], overwrite=overwrite)
    return target


def import_portrait_to_mod(
    mod_root: str | Path,
    tag: str,
    name_slug: str,
    src_image: str | Path,
    *,
    output_format: Literal["dds", "tga"] = "dds",
    size: tuple[int, int] = PORTRAIT_SIZE,
    resize_mode: Literal["stretch", "cover", "contain"] = "cover",
    dds_compression: Literal["DXT1", "DXT3", "DXT5"] = "DXT5",
    overwrite: bool = True,
) -> Path:
    """Import a leader portrait at the standard 156x210 size by default.

    DDS output is encoded directly with Pillow; ImageMagick is not used.  See
    :data:`DDS_LIMITATION` for the intentional codec/mipmap limitations.  TGA is
    available as a broadly supported lossless fallback.
    """

    normalized_tag = require_country_tag(tag)
    normalized_slug = _require_asset_stem(name_slug, label="portrait slug")
    if output_format not in {"dds", "tga"}:
        raise ValueError("Portrait output_format must be 'dds' or 'tga'")
    if size[0] <= 0 or size[1] <= 0:
        raise ValueError("Portrait dimensions must be positive")

    root = _require_mod_root(mod_root)
    image = _resize_image(_open_raster(src_image), size, resize_mode)
    target = cast(
        Path,
        resolve_mod_output_path(
            root, f"gfx/leaders/{normalized_tag}/{normalized_slug}.{output_format}"
        ),
    )
    pillow_format = output_format.upper()
    options: dict[str, object] = {}
    if pillow_format == "DDS":
        options["pixel_format"] = dds_compression
    _write_images_atomically(
        [(target, _prepare_for_format(image, pillow_format), pillow_format, options)],
        overwrite=overwrite,
    )
    return target


def find_portrait_path(
    mod_root: str | Path,
    tag: str,
    name_slug: str,
    *,
    preference: Iterable[str] = ("dds", "tga"),
) -> Path | None:
    """Find an existing portrait in a caller-defined format preference order."""

    normalized_tag = require_country_tag(tag)
    normalized_slug = _require_asset_stem(name_slug, label="portrait slug")
    root = _require_mod_root(mod_root, create=False)
    for extension in preference:
        normalized_extension = str(extension).lower().lstrip(".")
        if normalized_extension not in {"dds", "tga", "png"}:
            raise ValueError(f"Unsupported portrait preference: {extension!r}")
        candidate = cast(
            Path,
            resolve_mod_output_path(
                root,
                f"gfx/leaders/{normalized_tag}/{normalized_slug}.{normalized_extension}",
            ),
        )
        if candidate.is_file():
            return candidate
    return None


def export_portrait_from_mod(
    mod_root: str | Path,
    tag: str,
    name_slug: str,
    destination: str | Path,
    *,
    preference: Iterable[str] = ("dds", "tga"),
    dds_compression: Literal["DXT1", "DXT3", "DXT5"] = "DXT5",
    overwrite: bool = False,
) -> Path:
    """Export a portrait to a Pillow raster format, including DDS.

    DDS-to-DDS exports copy the original bytes and therefore preserve compression
    and mipmaps without requiring Pillow.  Any newly encoded DDS has the limitation
    documented by :data:`DDS_LIMITATION`.
    """

    source = find_portrait_path(mod_root, tag, name_slug, preference=preference)
    if source is None:
        raise FileNotFoundError(f"No portrait found for {tag}/{name_slug}")
    target = Path(destination).expanduser().resolve(strict=False)
    output_format = _output_format(target, allow_dds=True)
    if source.suffix.lower() == ".dds" and output_format == "DDS":
        _copy_file_atomically(source, target, overwrite=overwrite)
        return target

    image = _prepare_for_format(_open_raster(source), output_format)
    options: dict[str, object] = {}
    if output_format == "DDS":
        options["pixel_format"] = dds_compression
    _write_images_atomically([(target, image, output_format, options)], overwrite=overwrite)
    return target


def write_portrait_gfx(
    mod_root: str | Path,
    tag: str,
    portrait_slug: str,
    *,
    portrait_path: str | Path | None = None,
    sprite_name: str | None = None,
    overwrite: bool = True,
) -> Path:
    """Write a non-clobbering, per-portrait ``.gfx`` sprite declaration."""

    root = _require_mod_root(mod_root)
    normalized_tag = require_country_tag(tag)
    normalized_slug = _require_asset_stem(portrait_slug, label="portrait slug")
    if portrait_path is None:
        selected_portrait = find_portrait_path(root, normalized_tag, normalized_slug)
        if selected_portrait is None:
            raise FileNotFoundError(f"No portrait found for {normalized_tag}/{normalized_slug}")
    else:
        selected_portrait = Path(portrait_path)
        if not selected_portrait.is_absolute():
            selected_portrait = root / selected_portrait
        selected_portrait = selected_portrait.resolve(strict=False)
        if not selected_portrait.is_relative_to(root):
            raise ValueError("Portrait texture path escapes the mod root")
        if not selected_portrait.is_file():
            raise FileNotFoundError(f"Portrait texture does not exist: {selected_portrait}")

    resolved_sprite_name = require_script_id(
        sprite_name or f"GFX_portrait_{normalized_tag}_{normalized_slug}",
        label="portrait sprite name",
    )
    texture_path = selected_portrait.relative_to(root).as_posix()
    output = cast(
        Path,
        resolve_mod_output_path(root, f"interface/{normalized_tag}_{normalized_slug}_portrait.gfx"),
    )
    content = (
        "spriteTypes = {\n"
        "\tspriteType = {\n"
        f"\t\tname = {pdx_string(resolved_sprite_name)}\n"
        f"\t\ttexturefile = {pdx_string(texture_path)}\n"
        "\t}\n"
        "}\n"
    )
    _write_bytes_atomically(output, content.encode("utf-8"), overwrite=overwrite)
    return output


def import_bookmark_picture_to_mod(
    mod_root: str | Path,
    bookmark_key: str,
    src_image: str | Path,
    *,
    sprite_name: str | None = None,
    output_format: Literal["dds", "tga"] = "dds",
    resize_mode: Literal["stretch", "cover", "contain"] = "cover",
    dds_compression: Literal["DXT1", "DXT3", "DXT5"] = "DXT5",
    overwrite: bool = True,
) -> BookmarkPictureAsset:
    """Create a 180x104 bookmark texture and its ``spriteTypes`` declaration."""

    root = _require_mod_root(mod_root)
    normalized_key = _require_asset_stem(bookmark_key, label="bookmark key")
    resolved_sprite = require_script_id(
        sprite_name or f"GFX_{normalized_key}", label="bookmark sprite name"
    )
    if output_format not in {"dds", "tga"}:
        raise ValueError("Bookmark output_format must be 'dds' or 'tga'")

    texture = cast(
        Path,
        resolve_mod_output_path(
            root, f"gfx/interface/{normalized_key.lower()}.{output_format}"
        ),
    )
    gfx = cast(
        Path,
        resolve_mod_output_path(root, f"interface/{normalized_key.lower()}_bookmark.gfx"),
    )
    image = _resize_image(_open_raster(src_image), BOOKMARK_PICTURE_SIZE, resize_mode)
    pillow_format = output_format.upper()
    options: dict[str, object] = {}
    if pillow_format == "DDS":
        options["pixel_format"] = dds_compression
    texture_path = texture.relative_to(root).as_posix()
    content = (
        "spriteTypes = {\n"
        "\tspriteType = {\n"
        f"\t\tname = {pdx_string(resolved_sprite)}\n"
        f"\t\ttexturefile = {pdx_string(texture_path)}\n"
        "\t}\n"
        "}\n"
    )
    _write_asset_batch_atomically(
        [(texture, _prepare_for_format(image, pillow_format), pillow_format, options)],
        [(gfx, content.encode("utf-8"))],
        overwrite=overwrite,
    )
    return BookmarkPictureAsset(sprite_name=resolved_sprite, texture=texture, gfx=gfx)


def _require_pillow() -> tuple[Any, Any, type[Exception]]:
    try:
        Image = import_module("PIL.Image")
        ImageOps = import_module("PIL.ImageOps")
        UnidentifiedImageError = getattr(import_module("PIL"), "UnidentifiedImageError")
    except (ImportError, AttributeError) as exc:
        raise ImageBackendUnavailableError(
            "Image operations require the optional Pillow dependency. Install Pillow and retry."
        ) from exc
    return Image, ImageOps, UnidentifiedImageError


def _open_raster(source: str | Path) -> Any:
    path = Path(source).expanduser().resolve(strict=False)
    if not path.is_file():
        raise FileNotFoundError(f"Image does not exist: {path}")
    if path.suffix.lower() == ".svg":
        raise UnsupportedImageFormatError(
            "SVG input needs an explicit rasterizer; convert it to PNG/TGA before importing. "
            "The SDK does not invoke ImageMagick implicitly."
        )
    Image, ImageOps, unidentified_error = _require_pillow()
    try:
        with Image.open(path) as opened:
            opened.load()
            return ImageOps.exif_transpose(opened).convert("RGBA").copy()
    except unidentified_error as exc:
        raise UnsupportedImageFormatError(f"Pillow cannot decode image: {path}") from exc
    except OSError as exc:
        if path.suffix.lower() == ".dds":
            raise UnsupportedImageFormatError(
                f"Pillow cannot decode this DDS variant: {path}. {DDS_LIMITATION}"
            ) from exc
        raise


def _resize_image(
    image: Any,
    size: tuple[int, int],
    mode: Literal["stretch", "cover", "contain"],
) -> Any:
    Image, ImageOps, _ = _require_pillow()
    resampling = getattr(Image, "Resampling", Image)
    lanczos = resampling.LANCZOS
    if mode == "stretch":
        return image.resize(size, lanczos)
    if mode == "cover":
        return ImageOps.fit(image, size, method=lanczos)
    if mode == "contain":
        contained = ImageOps.contain(image, size, method=lanczos)
        canvas = Image.new("RGBA", size, (0, 0, 0, 0))
        offset = ((size[0] - contained.width) // 2, (size[1] - contained.height) // 2)
        # Copy RGBA pixels directly; using their alpha as a mask applies it twice.
        canvas.paste(contained, offset)
        return canvas
    raise ValueError("resize_mode must be 'stretch', 'cover', or 'contain'")


def _prepare_for_format(image: Any, output_format: str) -> Any:
    if output_format in {"JPEG", "BMP"}:
        return image.convert("RGB")
    return image.convert("RGBA")


def _write_images_atomically(
    writes: list[tuple[Path, Any, str, dict[str, object]]],
    *,
    overwrite: bool,
) -> None:
    _write_asset_batch_atomically(writes, [], overwrite=overwrite)


def _write_asset_batch_atomically(
    image_writes: list[tuple[Path, Any, str, dict[str, object]]],
    byte_writes: list[tuple[Path, bytes]],
    *,
    overwrite: bool,
) -> None:
    """Commit related image and metadata files as one rollback-capable batch."""

    targets = [target for target, _, _, _ in image_writes]
    targets.extend(target for target, _ in byte_writes)
    if len(set(targets)) != len(targets):
        raise ValueError("An atomic asset batch may write each target only once")
    if not overwrite:
        existing = next(
            (
                target
                for target in targets
                if target.exists() or target.is_symlink()
            ),
            None,
        )
        if existing is not None:
            raise FileExistsError(f"Refusing to overwrite asset: {existing}")

    temporary_paths: dict[Path, Path] = {}
    backup_paths: dict[Path, Path] = {}
    committed: list[Path] = []
    commit_succeeded = False
    try:
        for target, image, output_format, options in image_writes:
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "wb", suffix=target.suffix, dir=target.parent, delete=False
            ) as handle:
                temporary = Path(handle.name)
            try:
                image.save(temporary, format=output_format, **options)
            except (KeyError, OSError, ValueError) as exc:
                temporary.unlink(missing_ok=True)
                if output_format == "DDS":
                    raise DDSExportUnsupportedError(f"DDS export failed. {DDS_LIMITATION}") from exc
                raise UnsupportedImageFormatError(
                    f"Pillow cannot encode {output_format} output: {target}"
                ) from exc
            if output_format == "DDS" and not _is_dds(temporary):
                temporary.unlink(missing_ok=True)
                raise DDSExportUnsupportedError(
                    f"Pillow did not produce a valid DDS file. {DDS_LIMITATION}"
                )
            if output_format == "DDS":
                requested = options.get("pixel_format")
                if isinstance(requested, str) and not _dds_uses_compression(
                    temporary, requested
                ):
                    temporary.unlink(missing_ok=True)
                    raise DDSExportUnsupportedError(
                        f"Pillow did not produce the requested {requested} DDS. "
                        f"{DDS_LIMITATION}"
                    )
            temporary_paths[target] = temporary
        for target, content in byte_writes:
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "wb", suffix=target.suffix, dir=target.parent, delete=False
            ) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
                temporary_paths[target] = Path(handle.name)
        for target in temporary_paths:
            if target.is_dir() and not target.is_symlink():
                raise IsADirectoryError(f"Asset target is a directory: {target}")
            if not target.exists() and not target.is_symlink():
                continue
            with tempfile.NamedTemporaryFile(
                "wb", prefix=".hoi4-image-backup-", dir=target.parent, delete=False
            ) as handle:
                backup = Path(handle.name)
            try:
                os.replace(target, backup)
            except Exception:
                backup.unlink(missing_ok=True)
                raise
            backup_paths[target] = backup
        for target, temporary in temporary_paths.items():
            os.replace(temporary, target)
            committed.append(target)
        commit_succeeded = True
    except Exception as error:
        rollback_errors: list[OSError] = []
        for target in reversed(committed):
            if target in backup_paths:
                continue
            try:
                target.unlink(missing_ok=True)
            except OSError as rollback_error:
                rollback_errors.append(rollback_error)
        for target, backup in reversed(tuple(backup_paths.items())):
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(backup, target)
            except OSError as rollback_error:
                rollback_errors.append(rollback_error)
        if rollback_errors:
            retained = [str(path) for path in backup_paths.values() if path.exists()]
            detail = f"; retained backups: {', '.join(retained)}" if retained else ""
            raise RuntimeError(f"Asset batch rollback was incomplete{detail}") from error
        raise
    finally:
        for temporary in temporary_paths.values():
            temporary.unlink(missing_ok=True)
        if commit_succeeded:
            for backup in backup_paths.values():
                backup.unlink(missing_ok=True)


def _copy_file_atomically(source: Path, target: Path, *, overwrite: bool) -> None:
    if source.resolve(strict=False) == target.resolve(strict=False):
        return
    _write_bytes_atomically(target, source.read_bytes(), overwrite=overwrite)


def _write_bytes_atomically(target: Path, content: bytes, *, overwrite: bool) -> None:
    if target.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite file: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=target.parent, delete=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _require_mod_root(mod_root: str | Path, *, create: bool = True) -> Path:
    root = Path(mod_root).expanduser().resolve(strict=False)
    if root.exists() and not root.is_dir():
        raise NotADirectoryError(f"Mod root is not a directory: {root}")
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def _require_asset_stem(value: str, *, label: str) -> str:
    text = str(value)
    if not _ASSET_STEM_RE.fullmatch(text) or text in {".", ".."}:
        raise ValueError(
            f"Invalid {label} {value!r}; expected a filename-safe alphanumeric identifier"
        )
    return text


def _require_ideology(value: str | None) -> str | None:
    if value is None:
        return None
    return _require_asset_stem(value, label="flag ideology")


def _output_format(path: Path, *, allow_dds: bool) -> str:
    try:
        output_format = _EXPORT_FORMATS[path.suffix.lower()]
    except KeyError as exc:
        supported = ", ".join(
            extension for extension in _EXPORT_FORMATS if allow_dds or extension != ".dds"
        )
        raise UnsupportedImageFormatError(
            f"Unsupported output extension {path.suffix!r}; expected one of: {supported}"
        ) from exc
    if output_format == "DDS" and not allow_dds:
        raise UnsupportedImageFormatError("HOI4 flags use TGA, not DDS")
    return output_format


def _is_dds(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(4) == b"DDS "
    except OSError:
        return False


def _dds_uses_compression(path: Path, compression: str) -> bool:
    try:
        with path.open("rb") as handle:
            header = handle.read(88)
    except OSError:
        return False
    return len(header) >= 88 and header[84:88] == compression.encode("ascii")
