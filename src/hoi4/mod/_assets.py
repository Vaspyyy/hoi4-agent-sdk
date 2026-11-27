"""Flag, portrait, icon and picture imports."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Literal
from collections.abc import Sequence

from ..assets import FlagAssetSet, IconAsset
from ..paths import (
    require_country_tag,
    require_script_id,
    resolve_mod_output_path,
)
from ..script import (
    pdx_string,
)
from ._core import CoreLayer


class AssetsLayer(CoreLayer):
    """Flag, portrait, icon and picture imports."""

    # ── Transactional image assets ─────────────────────────────

    def _stage_asset_files(
        self,
        files: dict[Path, bytes],
        *,
        overwrite: bool,
    ) -> None:
        """Queue binary assets for preview/save with transaction rollback."""

        normalized: dict[Path, bytes] = {}
        for raw_path, content in files.items():
            path = resolve_mod_output_path(self.mod_root, raw_path)
            if path.is_dir() and not path.is_symlink():
                raise IsADirectoryError(f"Asset target is a directory: {path}")
            if not overwrite and (
                path.exists() or path in self._pending_asset_writes
            ):
                raise FileExistsError(f"Refusing to overwrite asset: {path}")
            normalized[path] = content
        for path, content in normalized.items():
            self._pending_asset_baselines.setdefault(
                path,
                path.read_bytes() if path.is_file() else None,
            )
            self._pending_asset_writes[path] = content
        if normalized:
            self._dirty.add("assets")

    def import_flag_to_mod(
        self,
        tag: str,
        src_image: str | Path,
        *,
        overwrite_vanilla: bool = False,
        ideologies: Sequence[str | None] | None = None,
        resize_mode: Literal["stretch", "cover", "contain"] = "stretch",
        overwrite: bool = True,
    ) -> tuple[FlagAssetSet, ...]:
        """Stage all three HOI4 flag sizes for transactional save.

        Raises:
            FileNotFoundError: ``src_image`` does not exist.
            FileExistsError: A target is staged or on disk and ``overwrite`` is false.
            UnsupportedImageFormatError: The image cannot be read.
            ImageBackendUnavailableError: Pillow is not installed.
            ValueError: The tag or an ideology is not valid, or both
                ``overwrite_vanilla`` and ``ideologies`` are given.
        """

        from ..assets import import_flag_to_mod

        with tempfile.TemporaryDirectory(prefix="hoi4-sdk-flag-") as temporary:
            temporary_root = Path(temporary)
            generated = import_flag_to_mod(
                temporary_root,
                tag,
                src_image,
                overwrite_vanilla=overwrite_vanilla,
                ideologies=ideologies,
                resize_mode=resize_mode,
                overwrite=True,
            )
            staged: dict[Path, bytes] = {}
            results: list[FlagAssetSet] = []
            for asset_set in generated:
                targets: dict[str, Path] = {}
                for size_name in ("large", "medium", "small"):
                    source = getattr(asset_set, size_name)
                    target = resolve_mod_output_path(
                        self.mod_root,
                        source.relative_to(temporary_root),
                    )
                    staged[target] = source.read_bytes()
                    targets[size_name] = target
                results.append(
                    FlagAssetSet(
                        tag=asset_set.tag,
                        ideology=asset_set.ideology,
                        large=targets["large"],
                        medium=targets["medium"],
                        small=targets["small"],
                    )
                )
        self._stage_asset_files(staged, overwrite=overwrite)
        return tuple(results)

    def import_portrait_to_mod(
        self,
        tag: str,
        name_slug: str,
        src_image: str | Path,
        *,
        output_format: Literal["dds", "tga"] = "dds",
        size: tuple[int, int] = (156, 210),
        resize_mode: Literal["stretch", "cover", "contain"] = "cover",
        dds_compression: Literal["DXT1", "DXT3", "DXT5"] = "DXT5",
        overwrite: bool = True,
        crop: tuple[int, int, int, int] | None = None,
    ) -> Path:
        """Stage one converted portrait for transactional save.

        ``crop`` is a ``(left, upper, right, lower)`` box in pixels of the
        source image, cut out before the resize.

        Raises:
            FileNotFoundError: ``src_image`` does not exist.
            FileExistsError: A target is staged or on disk and ``overwrite`` is false.
            UnsupportedImageFormatError: The image cannot be read.
            ImageBackendUnavailableError: Pillow is not installed.
            ValueError: The tag, slug, format or size is not valid.
        """

        from ..assets import import_portrait_to_mod

        with tempfile.TemporaryDirectory(prefix="hoi4-sdk-portrait-") as temporary:
            temporary_root = Path(temporary)
            generated = import_portrait_to_mod(
                temporary_root,
                tag,
                name_slug,
                src_image,
                output_format=output_format,
                size=size,
                resize_mode=resize_mode,
                dds_compression=dds_compression,
                overwrite=True,
                crop=crop,
            )
            target = resolve_mod_output_path(
                self.mod_root,
                generated.relative_to(temporary_root),
            )
            content = generated.read_bytes()
        self._stage_asset_files({target: content}, overwrite=overwrite)
        return target

    def write_portrait_gfx(
        self,
        tag: str,
        portrait_slug: str,
        *,
        portrait_path: str | Path | None = None,
        sprite_name: str | None = None,
        overwrite: bool = True,
    ) -> Path:
        """Stage a portrait sprite declaration for transactional save.

        Writes ``interface/<TAG>_<slug>_portrait.gfx`` with one sprite
        pointing at the portrait texture. Staged until ``save()``.

        Args:
            portrait_path: The texture, inside the mod. Without it,
                ``gfx/leaders/<TAG>/<slug>`` with a ``.dds``, ``.tga`` or
                ``.png`` ending, on disk or staged.
            sprite_name: Defaults to ``GFX_portrait_<TAG>_<slug>``.
            overwrite: Replace an existing sprite file.

        Returns:
            The path of the staged ``.gfx`` file.

        Raises:
            FileNotFoundError: No portrait texture is found.
            FileExistsError: The sprite file exists and ``overwrite`` is false.
            ValueError: The tag, slug or sprite name is not valid, or
                ``portrait_path`` is outside the mod.
        """

        from ..assets import _require_asset_stem

        normalized_tag = require_country_tag(tag)
        normalized_slug = _require_asset_stem(
            portrait_slug,
            label="portrait slug",
        )
        if portrait_path is None:
            selected = next(
                (
                    path
                    for extension in ("dds", "tga", "png")
                    if (
                        path := resolve_mod_output_path(
                            self.mod_root,
                            f"gfx/leaders/{normalized_tag}/{normalized_slug}.{extension}",
                        )
                    ).is_file()
                    or path in self._pending_asset_writes
                ),
                None,
            )
            if selected is None:
                raise FileNotFoundError(
                    f"No portrait found for {normalized_tag}/{normalized_slug}"
                )
        else:
            selected = Path(portrait_path)
            if not selected.is_absolute():
                selected = self.mod_root / selected
            selected = selected.resolve(strict=False)
            if not selected.is_relative_to(self.mod_root):
                raise ValueError("Portrait texture path escapes the mod root")
            if not selected.is_file() and selected not in self._pending_asset_writes:
                raise FileNotFoundError(
                    f"Portrait texture does not exist: {selected}"
                )
        resolved_sprite = require_script_id(
            sprite_name or f"GFX_portrait_{normalized_tag}_{normalized_slug}",
            label="portrait sprite name",
        )
        texture = selected.relative_to(self.mod_root).as_posix()
        output = resolve_mod_output_path(
            self.mod_root,
            f"interface/{normalized_tag}_{normalized_slug}_portrait.gfx",
        )
        content = (
            "spriteTypes = {\n"
            "\tspriteType = {\n"
            f"\t\tname = {pdx_string(resolved_sprite)}\n"
            f"\t\ttexturefile = {pdx_string(texture)}\n"
            "\t}\n"
            "}\n"
        ).encode()
        self._stage_asset_files({output: content}, overwrite=overwrite)
        return output

    def import_focus_icon(
        self,
        name: str,
        src_image: str | Path,
        *,
        size: tuple[int, int] = (100, 88),
        resize_mode: Literal["stretch", "cover", "contain"] = "contain",
        shine: bool = True,
        gfx_file: str | Path | None = None,
        dds_compression: Literal["DXT1", "DXT3", "DXT5"] = "DXT5",
        overwrite: bool = False,
    ) -> str:
        """Stage a focus icon and return its ``GFX_<name>`` sprite for ``Focus.icon``.

        Writes ``gfx/interface/goals/<name>.dds``, merges the sprite into
        ``interface/<mod>_goals.gfx`` (or ``gfx_file``), and with ``shine=True``
        adds the standard ``GFX_<name>_shine`` animation to the sibling
        ``*_shine.gfx`` file. A leading ``GFX_`` in ``name`` is ignored.

        Raises:
            FileNotFoundError: ``src_image`` does not exist.
            FileExistsError: A target is staged or on disk and ``overwrite`` is false.
            UnsupportedImageFormatError: The image cannot be read.
            ImageBackendUnavailableError: Pillow is not installed.
            ValueError: The name, size or sprite file is not valid.
        """

        return self._stage_icon_asset(
            "focus",
            name,
            src_image,
            size=size,
            resize_mode=resize_mode,
            shine=shine,
            gfx_file=gfx_file,
            dds_compression=dds_compression,
            overwrite=overwrite,
        ).sprite_name

    def import_idea_icon(
        self,
        name: str,
        src_image: str | Path,
        *,
        size: tuple[int, int] = (65, 67),
        resize_mode: Literal["stretch", "cover", "contain"] = "contain",
        gfx_file: str | Path | None = None,
        dds_compression: Literal["DXT1", "DXT3", "DXT5"] = "DXT5",
        overwrite: bool = False,
    ) -> str:
        """Stage an idea picture and return its ``GFX_idea_<name>`` sprite.

        ``Idea.icon`` accepts either the returned sprite or the bare ``name``;
        both serialize as ``picture = <name>``.

        Raises:
            FileNotFoundError: ``src_image`` does not exist.
            FileExistsError: A target is staged or on disk and ``overwrite`` is false.
            UnsupportedImageFormatError: The image cannot be read.
            ImageBackendUnavailableError: Pillow is not installed.
            ValueError: The name, size or sprite file is not valid.
        """

        return self._stage_icon_asset(
            "idea",
            name,
            src_image,
            size=size,
            resize_mode=resize_mode,
            gfx_file=gfx_file,
            dds_compression=dds_compression,
            overwrite=overwrite,
        ).sprite_name

    def import_decision_icon(
        self,
        name: str,
        src_image: str | Path,
        *,
        size: tuple[int, int] = (33, 32),
        resize_mode: Literal["stretch", "cover", "contain"] = "contain",
        gfx_file: str | Path | None = None,
        dds_compression: Literal["DXT1", "DXT3", "DXT5"] = "DXT5",
        overwrite: bool = False,
    ) -> str:
        """Stage a decision icon and return its ``GFX_decision_<name>`` sprite.

        ``Decision.icon`` may use either the returned sprite or the bare name.

        Raises:
            FileNotFoundError: ``src_image`` does not exist.
            FileExistsError: A target is staged or on disk and ``overwrite`` is false.
            UnsupportedImageFormatError: The image cannot be read.
            ImageBackendUnavailableError: Pillow is not installed.
            ValueError: The name, size or sprite file is not valid.
        """

        return self._stage_icon_asset(
            "decision",
            name,
            src_image,
            size=size,
            resize_mode=resize_mode,
            gfx_file=gfx_file,
            dds_compression=dds_compression,
            overwrite=overwrite,
        ).sprite_name

    def import_decision_category_icon(
        self,
        name: str,
        src_image: str | Path,
        *,
        size: tuple[int, int] = (52, 40),
        resize_mode: Literal["stretch", "cover", "contain"] = "contain",
        gfx_file: str | Path | None = None,
        dds_compression: Literal["DXT1", "DXT3", "DXT5"] = "DXT5",
        overwrite: bool = False,
    ) -> str:
        """Stage a ``GFX_decision_category_<name>`` icon for ``DecisionCategory.icon``.

        Raises:
            FileNotFoundError: ``src_image`` does not exist.
            FileExistsError: A target is staged or on disk and ``overwrite`` is false.
            UnsupportedImageFormatError: The image cannot be read.
            ImageBackendUnavailableError: Pillow is not installed.
            ValueError: The name, size or sprite file is not valid.
        """

        return self._stage_icon_asset(
            "decision_category",
            name,
            src_image,
            size=size,
            resize_mode=resize_mode,
            gfx_file=gfx_file,
            dds_compression=dds_compression,
            overwrite=overwrite,
        ).sprite_name

    def import_decision_category_picture(
        self,
        name: str,
        src_image: str | Path,
        *,
        size: tuple[int, int] = (114, 101),
        resize_mode: Literal["stretch", "cover", "contain"] = "cover",
        gfx_file: str | Path | None = None,
        dds_compression: Literal["DXT1", "DXT3", "DXT5"] = "DXT5",
        overwrite: bool = False,
    ) -> str:
        """Stage a category banner and return its ``GFX_decision_cat_picture_<name>``.

        The sprite is referenced by a category definition's ``picture`` field.

        Raises:
            FileNotFoundError: ``src_image`` does not exist.
            FileExistsError: A target is staged or on disk and ``overwrite`` is false.
            UnsupportedImageFormatError: The image cannot be read.
            ImageBackendUnavailableError: Pillow is not installed.
            ValueError: The name, size or sprite file is not valid.
        """

        return self._stage_icon_asset(
            "decision_category_picture",
            name,
            src_image,
            size=size,
            resize_mode=resize_mode,
            gfx_file=gfx_file,
            dds_compression=dds_compression,
            overwrite=overwrite,
        ).sprite_name

    def import_event_picture(
        self,
        name: str,
        src_image: str | Path,
        *,
        news: bool = False,
        size: tuple[int, int] | None = None,
        resize_mode: Literal["stretch", "cover", "contain"] = "cover",
        gfx_file: str | Path | None = None,
        dds_compression: Literal["DXT1", "DXT3", "DXT5"] = "DXT5",
        overwrite: bool = False,
    ) -> str:
        """Stage an event picture and return the sprite for ``Event.picture``.

        Report events use a 210x176 ``GFX_report_event_<name>`` picture; news
        events (``news=True``) use 397x153 ``GFX_news_event_<name>``.

        Raises:
            FileNotFoundError: ``src_image`` does not exist.
            FileExistsError: A target is staged or on disk and ``overwrite`` is false.
            UnsupportedImageFormatError: The image cannot be read.
            ImageBackendUnavailableError: Pillow is not installed.
            ValueError: The name, size or sprite file is not valid.
        """

        return self._stage_icon_asset(
            "news_event" if news else "event",
            name,
            src_image,
            size=size,
            resize_mode=resize_mode,
            gfx_file=gfx_file,
            dds_compression=dds_compression,
            overwrite=overwrite,
        ).sprite_name

    def _stage_icon_asset(
        self,
        kind: str,
        name: str,
        src_image: str | Path,
        *,
        size: tuple[int, int] | None,
        resize_mode: Literal["stretch", "cover", "contain"],
        shine: bool = False,
        gfx_file: str | Path | None,
        dds_compression: Literal["DXT1", "DXT3", "DXT5"],
        overwrite: bool,
    ) -> IconAsset:
        """Plan an icon against staged and on-disk files, then stage the changes."""

        from ..assets import plan_icon_asset

        def read(path: Path) -> bytes | None:
            pending = self._pending_asset_writes.get(path)
            if pending is not None:
                return pending
            return path.read_bytes() if path.is_file() else None

        interface_dir = self.mod_root / "interface"
        declared = {
            path
            for path in self._pending_asset_writes
            if path.suffix.lower() == ".gfx"
        }
        if interface_dir.is_dir():
            declared.update(
                path.resolve(strict=False)
                for path in interface_dir.rglob("*.gfx")
                if path.is_file()
            )
        asset, files = plan_icon_asset(
            self.mod_root,
            kind,
            name,
            src_image,
            read=read,
            size=size,
            resize_mode=resize_mode,
            shine=shine,
            gfx_file=gfx_file,
            dds_compression=dds_compression,
            overwrite=overwrite,
            declared_gfx=sorted(declared),
        )
        if files:
            self._stage_asset_files(files, overwrite=True)
        return asset
