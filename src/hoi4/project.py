"""Project layout and Paradox launcher descriptor services.

The functions in this module are deliberately independent from :class:`hoi4.Mod`.
They are suitable for a desktop UI that needs to create or discover projects before
loading the content model.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .parser import ParseError, parse_pdx
from .paths import resolve_mod_output_path, safe_file_stem
from .script import pdx_string


DEFAULT_SUPPORTED_VERSION = "1.*"

# Covers every directory currently authored by the SDK or the Studio.  Empty
# directories are harmless; importantly, their presence is not treated as an
# instruction to replace the corresponding vanilla directory.
DEFAULT_MOD_DIRECTORIES: tuple[str, ...] = (
    "common/bookmarks",
    "common/characters",
    "common/countries",
    "common/country_tags",
    "common/decisions",
    "common/defines",
    "common/dynamic_modifiers",
    "common/ideas",
    "common/ideologies",
    "common/national_focus",
    "common/on_actions",
    "events",
    "gfx/flags",
    "gfx/flags/medium",
    "gfx/flags/small",
    "gfx/interface",
    "gfx/leaders",
    "history/countries",
    "history/states",
    "history/units",
    "interface",
    "localisation/english",
)

# These are useful for total conversions, but dangerous for ordinary mods.  They
# are suggestions only and are never emitted unless the caller explicitly passes
# them or opts into auto detection.
KNOWN_REPLACE_PATHS: tuple[str, ...] = (
    "common/ai_strategy",
    "common/factions",
    "common/factions/goals",
    "common/factions/rules",
    "common/factions/rules/groups",
    "common/factions/templates",
    "common/on_actions",
    "events",
    "history/states",
    "history/units",
    "map/strategicregions",
)

_TAG_PATHS: tuple[tuple[str, str], ...] = (
    ("common/decisions", "Decisions"),
    ("common/ideas", "Ideas"),
    ("common/national_focus", "National Focuses"),
    ("common/technologies", "Technologies"),
    ("events", "Events"),
    ("gfx", "Graphics"),
    ("history/units", "Military"),
    ("map", "Map"),
    ("music", "Sound"),
    ("tutorial", "Tutorial"),
)

_SUPPORTED_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)*(?:\.\*)?$")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class ModDescriptorFiles:
    """Paths and resolved metadata written for one launcher descriptor pair."""

    launcher: Path
    descriptor: Path
    supported_version: str
    replace_paths: tuple[str, ...]
    dependencies: tuple[str, ...] = ()


@dataclass(frozen=True)
class DiscoveredMod:
    """One editable directory-backed mod discovered from a launcher descriptor."""

    descriptor_path: Path
    mod_path: Path
    name: str
    supported_version: str | None = None


@dataclass(frozen=True)
class ModDiscoveryIssue:
    """A descriptor skipped during safe discovery."""

    descriptor_path: Path
    message: str


@dataclass(frozen=True)
class ModDiscoveryResult:
    """Safe discovery results together with non-fatal descriptor diagnostics."""

    mods: tuple[DiscoveredMod, ...]
    issues: tuple[ModDiscoveryIssue, ...]


def create_mod_structure(
    mod_root: str | Path,
    directories: Iterable[str | Path] = DEFAULT_MOD_DIRECTORIES,
) -> tuple[Path, ...]:
    """Create an idempotent HOI4 mod directory layout beneath ``mod_root``.

    All requested paths are validated before anything is created.  Absolute
    paths, traversal components, and symlink escapes are rejected.
    """

    root = Path(mod_root).expanduser().resolve(strict=False)
    if root.exists() and not root.is_dir():
        raise NotADirectoryError(f"Mod root is not a directory: {root}")

    targets: list[Path] = []
    for directory in directories:
        relative = _normalize_content_path(directory, label="mod directory")
        target = resolve_mod_output_path(root, relative)
        if target == root:
            raise ValueError("A mod directory must name a child of the mod root")
        if target.exists() and not target.is_dir():
            raise NotADirectoryError(f"Mod structure path is not a directory: {target}")
        targets.append(target)

    root.mkdir(parents=True, exist_ok=True)
    for target in targets:
        target.mkdir(parents=True, exist_ok=True)
    return tuple(targets)


def detect_supported_version(
    hoi4_install: str | Path | None,
    *,
    fallback: str = DEFAULT_SUPPORTED_VERSION,
) -> str:
    """Return a launcher wildcard version such as ``1.17.*``.

    ``launcher-settings.json`` is preferred.  A plain ``version.txt`` is also
    understood for installations that do not expose ``rawVersion``.
    """

    fallback = _validate_supported_version(fallback)
    if hoi4_install is None:
        return fallback
    install = Path(hoi4_install).expanduser()

    launcher_settings = install / "launcher-settings.json"
    try:
        data = json.loads(launcher_settings.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict):
            detected = _major_minor_wildcard(data.get("rawVersion"))
            if detected is not None:
                return detected
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass

    version_file = install / "version.txt"
    try:
        detected = _major_minor_wildcard(version_file.read_text(encoding="utf-8-sig"))
        if detected is not None:
            return detected
    except (OSError, UnicodeError):
        pass
    return fallback


def detect_launcher_mod_directory(
    hoi4_install: str | Path,
    *,
    home: str | Path | None = None,
    xdg_data_home: str | Path | None = None,
    require_existing: bool = True,
) -> Path | None:
    """Read ``gameDataPath`` and return the launcher's ``mod`` directory.

    Only the launcher's documented home/data placeholders are expanded.  Other
    unresolved variables are rejected rather than accidentally creating a path
    containing ``$``.
    """

    install = Path(hoi4_install).expanduser().resolve(strict=False)
    settings_path = install / "launcher-settings.json"
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("gameDataPath"), str):
        return None

    home_path = Path(home).expanduser() if home is not None else Path.home()
    data_home = (
        Path(xdg_data_home).expanduser()
        if xdg_data_home is not None
        else Path(os.environ.get("XDG_DATA_HOME", home_path / ".local" / "share"))
    )
    raw = data["gameDataPath"]
    replacements = {
        "${LINUX_DATA_HOME}": str(data_home),
        "$LINUX_DATA_HOME": str(data_home),
        "${XDG_DATA_HOME}": str(data_home),
        "$XDG_DATA_HOME": str(data_home),
        "${HOME}": str(home_path),
        "$HOME": str(home_path),
    }
    for token, value in replacements.items():
        raw = raw.replace(token, value)
    if "$" in raw or "\x00" in raw:
        return None

    game_data = Path(raw).expanduser()
    if not game_data.is_absolute():
        game_data = install / game_data
    result = (game_data / "mod").resolve(strict=False)
    if require_existing and not result.is_dir():
        return None
    return result


def scan_mod_tags(mod_root: str | Path) -> tuple[str, ...]:
    """Infer harmless launcher category tags from files actually present."""

    root = Path(mod_root).expanduser().resolve(strict=False)
    tags = [tag for relative, tag in _TAG_PATHS if _directory_has_files(root, relative)]
    return tuple(tags)


def scan_replace_paths(
    mod_root: str | Path,
    candidates: Iterable[str | Path] = KNOWN_REPLACE_PATHS,
) -> tuple[str, ...]:
    """Suggest replace paths that contain at least one real file.

    This function never treats an empty scaffold directory as replacement intent.
    Callers still need to opt into using the returned values because ``replace_path``
    suppresses all matching vanilla content.
    """

    root = Path(mod_root).expanduser().resolve(strict=False)
    found: list[str] = []
    for candidate in candidates:
        relative = _normalize_content_path(candidate, label="replace_path")
        if _directory_has_files(root, relative):
            found.append(relative)
    return tuple(dict.fromkeys(found))


def write_mod_descriptors(
    mod_root: str | Path,
    user_mods_dir: str | Path,
    name: str,
    *,
    supported_version: str | None = None,
    hoi4_install: str | Path | None = None,
    tags: Iterable[str] | None = None,
    replace_paths: Iterable[str | Path] = (),
    dependencies: Iterable[str] = (),
    auto_detect_replace_paths: bool = False,
    launcher_filename: str | None = None,
    picture: str | None = "thumbnail.png",
    version: str | None = "1.0",
    user_dir: str | None = None,
    remote_file_id: str | int | None = None,
) -> ModDescriptorFiles:
    """Write ``descriptor.mod`` and its launcher ``.mod`` companion.

    The launcher file receives the absolute ``path`` field; the in-project file
    does not.  Replace paths are empty by default and every supplied value is
    normalized and traversal-checked.  Set ``auto_detect_replace_paths=True``
    only for a deliberate total-conversion style project.
    """

    root = Path(mod_root).expanduser().resolve(strict=False)
    launcher_root = Path(user_mods_dir).expanduser().resolve(strict=False)
    if root.exists() and not root.is_dir():
        raise NotADirectoryError(f"Mod root is not a directory: {root}")
    if launcher_root.exists() and not launcher_root.is_dir():
        raise NotADirectoryError(f"Launcher mod path is not a directory: {launcher_root}")

    display_name = _validate_descriptor_scalar(name, label="mod name")
    selected_version = (
        detect_supported_version(hoi4_install)
        if supported_version is None
        else _validate_supported_version(supported_version)
    )
    if isinstance(dependencies, (str, bytes)):
        raise ValueError("dependencies must be a sequence of mod names")
    selected_dependencies = _normalize_scalars(dependencies, "dependency")
    selected_tags = scan_mod_tags(root) if tags is None else _normalize_scalars(tags, "tag")

    normalized_replace_paths = [
        _normalize_content_path(path, label="replace_path") for path in replace_paths
    ]
    if auto_detect_replace_paths:
        normalized_replace_paths.extend(scan_replace_paths(root))
    selected_replace_paths = tuple(dict.fromkeys(normalized_replace_paths))

    if launcher_filename is None:
        launcher_filename = f"{safe_file_stem(display_name, fallback='mod')}.mod"
    launcher_filename = _validate_launcher_filename(launcher_filename)
    launcher_path = resolve_mod_output_path(launcher_root, launcher_filename)
    descriptor_path = resolve_mod_output_path(root, "descriptor.mod")
    if launcher_path == descriptor_path:
        raise ValueError("Launcher descriptor and in-project descriptor resolve to one file")

    common_lines = [f"name={pdx_string(display_name)}"]
    if selected_dependencies:
        common_lines.append("dependencies={")
        common_lines.extend(f"\t{pdx_string(name)}" for name in selected_dependencies)
        common_lines.append("}")
    if picture is not None:
        common_lines.append(
            f"picture={pdx_string(_validate_descriptor_scalar(picture, label='picture'))}"
        )
    if version is not None:
        common_lines.append(
            f"version={pdx_string(_validate_descriptor_scalar(version, label='version'))}"
        )
    if user_dir is not None:
        common_lines.append(
            f"user_dir={pdx_string(_validate_descriptor_scalar(user_dir, label='user_dir'))}"
        )
    common_lines.extend(f"replace_path={pdx_string(path)}" for path in selected_replace_paths)
    if selected_tags:
        common_lines.append("tags={")
        common_lines.extend(f"\t{pdx_string(tag)}" for tag in selected_tags)
        common_lines.append("}")
    else:
        common_lines.append("tags={}")
    common_lines.append(f"supported_version={pdx_string(selected_version)}")
    if remote_file_id is not None:
        common_lines.append(
            "remote_file_id="
            + pdx_string(_validate_descriptor_scalar(remote_file_id, label="remote_file_id"))
        )

    inner_content = "\n".join(common_lines) + "\n"
    launcher_content = inner_content + f"path={pdx_string(root.as_posix())}\n"
    _atomic_write_texts(
        {
            descriptor_path: inner_content,
            launcher_path: launcher_content,
        }
    )
    return ModDescriptorFiles(
        launcher=launcher_path,
        descriptor=descriptor_path,
        supported_version=selected_version,
        replace_paths=selected_replace_paths,
        dependencies=selected_dependencies,
    )


def generate_mod_descriptor(
    mod_root: str | Path,
    user_mods_dir: str | Path,
    mod_name: str,
    tags: Iterable[str] | None = None,
    replace_paths: Iterable[str | Path] | None = None,
    hoi4_install: str | Path | None = None,
    *,
    supported_version: str | None = None,
    dependencies: Iterable[str] = (),
) -> Path:
    """Studio-compatible wrapper returning the launcher descriptor path.

    Unlike the legacy Studio helper, ``replace_paths=None`` is conservative and
    writes no replacements.  Call :func:`scan_replace_paths` explicitly when a
    total conversion really intends to suppress vanilla directories.
    """

    result = write_mod_descriptors(
        mod_root,
        user_mods_dir,
        mod_name,
        tags=tags,
        replace_paths=() if replace_paths is None else replace_paths,
        hoi4_install=hoi4_install,
        supported_version=supported_version,
        user_dir=mod_name,
        dependencies=dependencies,
    )
    return result.launcher


def scan_mod_descriptors(
    user_mods_dir: str | Path,
    *,
    allowed_roots: Iterable[str | Path] = (),
    allow_external_absolute: bool = False,
    require_existing: bool = True,
    max_descriptor_bytes: int = 1024 * 1024,
) -> ModDiscoveryResult:
    """Discover editable mods without trusting descriptor paths.

    Relative targets must stay beneath ``user_mods_dir`` and may not contain
    ``..``.  Absolute targets are accepted only beneath ``user_mods_dir`` or an
    explicitly supplied allowed root, unless ``allow_external_absolute`` is set.
    Symlink descriptors and symlink escapes are rejected.
    """

    root = Path(user_mods_dir).expanduser().resolve(strict=False)
    if not root.is_dir():
        return ModDiscoveryResult((), ())
    if max_descriptor_bytes <= 0:
        raise ValueError("max_descriptor_bytes must be positive")

    permitted_roots = (root,) + tuple(
        Path(path).expanduser().resolve(strict=False) for path in allowed_roots
    )
    mods: list[DiscoveredMod] = []
    issues: list[ModDiscoveryIssue] = []
    for candidate in sorted(root.glob("*.mod"), key=lambda path: path.name.casefold()):
        display_path = candidate.absolute()
        if candidate.is_symlink():
            issues.append(ModDiscoveryIssue(display_path, "descriptor is a symlink"))
            continue
        resolved_descriptor = candidate.resolve(strict=False)
        if not resolved_descriptor.is_relative_to(root):
            issues.append(ModDiscoveryIssue(display_path, "descriptor escapes the mods directory"))
            continue
        try:
            if candidate.stat().st_size > max_descriptor_bytes:
                raise ValueError("descriptor exceeds the configured size limit")
            parsed = parse_pdx(candidate.read_text(encoding="utf-8-sig"))
            raw_path = parsed.get_value("path")
            if not raw_path:
                raise ValueError("descriptor has no path field")
            mod_path, was_absolute = _resolve_discovered_path(candidate.parent, raw_path)
            if not was_absolute and not mod_path.is_relative_to(root):
                raise ValueError("relative mod path escapes the mods directory")
            if (
                was_absolute
                and not allow_external_absolute
                and not any(mod_path.is_relative_to(base) for base in permitted_roots)
            ):
                raise ValueError("absolute mod path is outside the allowed roots")
            if require_existing and not mod_path.is_dir():
                raise ValueError("mod path is not an existing directory")
            mods.append(
                DiscoveredMod(
                    descriptor_path=resolved_descriptor,
                    mod_path=mod_path,
                    name=parsed.get_value("name", candidate.stem),
                    supported_version=parsed.get_value("supported_version") or None,
                )
            )
        except (OSError, UnicodeError, ParseError, ValueError) as exc:
            issues.append(ModDiscoveryIssue(display_path, str(exc)))
    return ModDiscoveryResult(tuple(mods), tuple(issues))


def discover_mods(
    user_mods_dir: str | Path,
    *,
    allowed_roots: Iterable[str | Path] = (),
    allow_external_absolute: bool = False,
    require_existing: bool = True,
) -> list[DiscoveredMod]:
    """Return only the valid records from :func:`scan_mod_descriptors`."""

    return list(
        scan_mod_descriptors(
            user_mods_dir,
            allowed_roots=allowed_roots,
            allow_external_absolute=allow_external_absolute,
            require_existing=require_existing,
        ).mods
    )


def find_mods_in_user_mod_folder(
    user_mods_dir: str | Path,
    *,
    allowed_roots: Iterable[str | Path] = (),
    allow_external_absolute: bool = False,
) -> list[tuple[str, Path]]:
    """Studio-compatible tuple view of :func:`discover_mods`."""

    return [
        (mod.descriptor_path.name, mod.mod_path)
        for mod in discover_mods(
            user_mods_dir,
            allowed_roots=allowed_roots,
            allow_external_absolute=allow_external_absolute,
        )
    ]


def _normalize_content_path(value: str | Path, *, label: str) -> str:
    raw = str(value)
    if not raw or raw != raw.strip() or any(char in raw for char in "\r\n\x00"):
        raise ValueError(f"Invalid {label}: {value!r}")
    normalized_separators = raw.replace("\\", "/")
    if normalized_separators.startswith("/") or _WINDOWS_DRIVE_RE.match(normalized_separators):
        raise ValueError(f"Invalid {label}; expected a relative game path: {value!r}")
    parts = normalized_separators.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(
            f"Invalid {label}; traversal and empty components are not allowed: {value!r}"
        )
    if any(any(ord(char) < 32 for char in part) for part in parts):
        raise ValueError(f"Invalid {label}; control characters are not allowed: {value!r}")
    return PurePosixPath(*parts).as_posix()


def _validate_descriptor_scalar(value: object, *, label: str) -> str:
    text = str(value)
    if not text or text != text.strip() or any(char in text for char in "\r\n\x00"):
        raise ValueError(f"Invalid {label}: {value!r}")
    return text


def _normalize_scalars(values: Iterable[object], label: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_validate_descriptor_scalar(value, label=label) for value in values))


def _validate_supported_version(value: str) -> str:
    text = _validate_descriptor_scalar(value, label="supported version")
    if not _SUPPORTED_VERSION_RE.fullmatch(text):
        raise ValueError(
            f"Invalid supported version {value!r}; expected a numeric version such as '1.17.*'"
        )
    return text


def _major_minor_wildcard(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    match = re.search(r"(?<![0-9])([0-9]+)\.([0-9]+)(?![0-9])", value)
    if match is None:
        return None
    return f"{match.group(1)}.{match.group(2)}.*"


def _validate_launcher_filename(value: str) -> str:
    if not value.lower().endswith(".mod"):
        raise ValueError("Launcher descriptor filename must end in .mod")
    if Path(value).name != value or "/" in value or "\\" in value:
        raise ValueError("Launcher descriptor filename may not contain path components")
    safe = safe_file_stem(value, fallback="mod.mod")
    if safe != value or value in {".", ".."}:
        raise ValueError(f"Invalid launcher descriptor filename: {value!r}")
    return value


def _directory_has_files(root: Path, relative: str) -> bool:
    try:
        directory = resolve_mod_output_path(root, relative)
    except ValueError:
        return False
    if not directory.is_dir():
        return False
    for path in directory.rglob("*"):
        try:
            if path.is_file() and path.resolve(strict=False).is_relative_to(root):
                return True
        except OSError:
            continue
    return False


def _resolve_discovered_path(descriptor_parent: Path, raw_path: str) -> tuple[Path, bool]:
    if not raw_path or any(char in raw_path for char in "\r\n\x00"):
        raise ValueError("descriptor contains an invalid path field")
    normalized = raw_path.replace("\\", "/")
    path = Path(normalized).expanduser()
    was_absolute = path.is_absolute() or _WINDOWS_DRIVE_RE.match(normalized) is not None
    if not was_absolute and ".." in PurePosixPath(normalized).parts:
        raise ValueError("relative mod path contains traversal components")
    if _WINDOWS_DRIVE_RE.match(normalized) and not path.is_absolute():
        # A Windows absolute path is not meaningful/safe on a POSIX host.
        raise ValueError("Windows absolute mod path is not valid on this platform")
    candidate = path if was_absolute else descriptor_parent / path
    return candidate.resolve(strict=False), was_absolute


def _atomic_write_texts(files: dict[Path, str]) -> None:
    temporary_paths: dict[Path, Path] = {}
    backup_paths: dict[Path, Path] = {}
    committed: list[Path] = []
    commit_succeeded = False
    try:
        for target, content in files.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("wb", dir=target.parent, delete=False) as handle:
                handle.write(content.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
                temporary_paths[target] = Path(handle.name)
        for target in temporary_paths:
            if target.is_dir() and not target.is_symlink():
                raise IsADirectoryError(f"Text target is a directory: {target}")
            if not target.exists() and not target.is_symlink():
                continue
            with tempfile.NamedTemporaryFile(
                "wb", prefix=".hoi4-text-backup-", dir=target.parent, delete=False
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
            raise RuntimeError(f"Text batch rollback was incomplete{detail}") from error
        raise
    finally:
        for temporary in temporary_paths.values():
            temporary.unlink(missing_ok=True)
        if commit_succeeded:
            for backup in backup_paths.values():
                backup.unlink(missing_ok=True)
