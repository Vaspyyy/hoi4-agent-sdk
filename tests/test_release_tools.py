from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from hoi4.release_tools import (
    changelog_section,
    validate_release_identity,
    write_sha256s,
)


def _release_tree(root: Path, version: str = "1.2.3") -> None:
    (root / "src" / "hoi4").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "example"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (root / "src" / "hoi4" / "__init__.py").write_text(
        f'__version__ = "{version}"\n',
        encoding="utf-8",
    )
    (root / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## [{version}] - 2026-01-01\n\n### Fixed\n\n- A fix.\n",
        encoding="utf-8",
    )


def test_release_identity_and_notes_agree(tmp_path: Path) -> None:
    _release_tree(tmp_path)

    assert validate_release_identity(tmp_path, "v1.2.3") == "1.2.3"
    assert "A fix." in changelog_section(tmp_path, "1.2.3")


@pytest.mark.parametrize("tag", ["1.2.3", "v1.2", "v1.2.4"])
def test_release_identity_rejects_mismatches(tmp_path: Path, tag: str) -> None:
    _release_tree(tmp_path)

    with pytest.raises(ValueError):
        validate_release_identity(tmp_path, tag)


def test_sha256s_match_built_artifacts(tmp_path: Path) -> None:
    wheel = tmp_path / "example-1.2.3-py3-none-any.whl"
    sdist = tmp_path / "example-1.2.3.tar.gz"
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"sdist")

    output = write_sha256s(tmp_path)

    lines = output.read_text(encoding="utf-8").splitlines()
    assert f"{hashlib.sha256(wheel.read_bytes()).hexdigest()}  {wheel.name}" in lines
    assert f"{hashlib.sha256(sdist.read_bytes()).hexdigest()}  {sdist.name}" in lines
