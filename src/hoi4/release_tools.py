"""Deterministic helpers used by the gated GitHub release workflow."""

from __future__ import annotations

import hashlib
import re
import tomllib
from pathlib import Path

_TAG_PATTERN = re.compile(r"^v(\d+\.\d+\.\d+)$")
_RUNTIME_VERSION_PATTERN = re.compile(r'^\s*__version__\s*=\s*"([^"]+)"\s*$', re.MULTILINE)


def project_version(root: str | Path) -> str:
    """Return the package version declared by ``pyproject.toml``."""

    path = Path(root) / "pyproject.toml"
    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    value = payload.get("project", {}).get("version")
    if not isinstance(value, str) or not value:
        raise ValueError(f"Missing project.version in {path}")
    return value


def runtime_fallback_version(root: str | Path) -> str:
    """Return the source-tree fallback used by ``hoi4.__version__``."""

    path = Path(root) / "src" / "hoi4" / "__init__.py"
    match = _RUNTIME_VERSION_PATTERN.search(path.read_text(encoding="utf-8"))
    if match is None:
        raise ValueError(f"Missing literal __version__ fallback in {path}")
    return match.group(1)


def changelog_section(root: str | Path, version: str) -> str:
    """Extract a release version's changelog body."""

    path = Path(root) / "CHANGELOG.md"
    text = path.read_text(encoding="utf-8")
    match = re.search(
        rf"^## \[{re.escape(version)}\](?:\s+-[^\r\n]+)?\s*$"
        rf"(?P<body>.*?)(?=^## \[|\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise ValueError(f"CHANGELOG.md has no section for {version}")
    body = match.group("body").strip()
    if not body:
        raise ValueError(f"CHANGELOG.md section for {version} is empty")
    return body


def validate_release_identity(root: str | Path, tag: str) -> str:
    """Require tag, project metadata, runtime fallback, and changelog to agree."""

    match = _TAG_PATTERN.fullmatch(tag)
    if match is None:
        raise ValueError(f"Release tag must match vMAJOR.MINOR.PATCH: {tag}")
    tagged_version = match.group(1)
    declared = project_version(root)
    runtime = runtime_fallback_version(root)
    if declared != tagged_version:
        raise ValueError(
            f"Tag version {tagged_version} does not match pyproject.toml {declared}"
        )
    if runtime != tagged_version:
        raise ValueError(
            f"Tag version {tagged_version} does not match runtime __version__ {runtime}"
        )
    changelog_section(root, tagged_version)
    return tagged_version


def write_sha256s(dist_dir: str | Path) -> Path:
    """Write GNU-compatible SHA-256 lines for wheel and sdist artifacts."""

    directory = Path(dist_dir)
    artifacts = sorted(
        path
        for path in directory.iterdir()
        if path.is_file()
        and path.name != "SHA256SUMS"
        and (path.suffix == ".whl" or path.name.endswith(".tar.gz"))
    )
    if not artifacts:
        raise ValueError(f"No wheel or sdist artifacts found in {directory}")
    lines = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}"
        for path in artifacts
    ]
    output = directory / "SHA256SUMS"
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output
