from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hoi4 import project
from hoi4.parser import parse_pdx
from hoi4.project import (
    DEFAULT_MOD_DIRECTORIES,
    create_mod_structure,
    detect_launcher_mod_directory,
    detect_supported_version,
    discover_mods,
    find_mods_in_user_mod_folder,
    scan_mod_descriptors,
    scan_replace_paths,
    write_mod_descriptors,
)


class TestCreateModStructure:
    def test_creates_complete_idempotent_structure(self, tmp_path: Path) -> None:
        root = tmp_path / "my_mod"
        first = create_mod_structure(root)
        second = create_mod_structure(root)

        assert first == second
        assert {path.relative_to(root).as_posix() for path in first} == set(DEFAULT_MOD_DIRECTORIES)
        assert all(path.is_dir() for path in first)
        assert root / "common" / "decisions" in first
        assert root / "common" / "on_actions" in first
        assert root / "common" / "ideologies" in first
        assert root / "common" / "bookmarks" in first

    def test_rejects_escape_before_creating_anything(self, tmp_path: Path) -> None:
        root = tmp_path / "my_mod"
        with pytest.raises(ValueError, match="traversal"):
            create_mod_structure(root, ["common/ideas", "../outside"])
        assert not root.exists()
        assert not (tmp_path / "outside").exists()

    def test_rejects_symlink_escape(self, tmp_path: Path) -> None:
        root = tmp_path / "my_mod"
        outside = tmp_path / "outside"
        root.mkdir()
        outside.mkdir()
        (root / "linked").symlink_to(outside, target_is_directory=True)

        with pytest.raises(ValueError, match="escapes mod root"):
            create_mod_structure(root, ["linked/new"])
        assert not (outside / "new").exists()

    def test_rejects_file_where_directory_is_required(self, tmp_path: Path) -> None:
        root = tmp_path / "my_mod"
        target = root / "events"
        target.parent.mkdir(parents=True)
        target.write_text("not a directory", encoding="utf-8")
        with pytest.raises(NotADirectoryError):
            create_mod_structure(root, ["events"])


class TestSupportedVersionAndLauncherDirectory:
    def test_detects_major_minor_from_launcher_settings(self, tmp_path: Path) -> None:
        install = tmp_path / "game"
        install.mkdir()
        (install / "launcher-settings.json").write_text(
            json.dumps({"rawVersion": "1.17.3.0 (checksum)"}), encoding="utf-8"
        )
        assert detect_supported_version(install) == "1.17.*"

    def test_falls_back_to_version_file_then_safe_default(self, tmp_path: Path) -> None:
        install = tmp_path / "game"
        install.mkdir()
        (install / "version.txt").write_text("1.16.10.0\n", encoding="utf-8")
        assert detect_supported_version(install) == "1.16.*"
        assert detect_supported_version(tmp_path / "missing") == "1.*"

    def test_detects_linux_data_placeholder_without_broad_dollar_replacement(
        self, tmp_path: Path
    ) -> None:
        install = tmp_path / "game"
        install.mkdir()
        data_home = tmp_path / "data"
        expected = data_home / "Paradox Interactive" / "Hearts of Iron IV" / "mod"
        expected.mkdir(parents=True)
        (install / "launcher-settings.json").write_text(
            json.dumps(
                {"gameDataPath": ("$LINUX_DATA_HOME/Paradox Interactive/Hearts of Iron IV")}
            ),
            encoding="utf-8",
        )
        assert detect_launcher_mod_directory(install, xdg_data_home=data_home) == expected

        (install / "launcher-settings.json").write_text(
            json.dumps({"gameDataPath": "$UNTRUSTED/path"}), encoding="utf-8"
        )
        assert detect_launcher_mod_directory(install, xdg_data_home=data_home) is None


class TestDescriptors:
    def test_writes_launcher_and_inner_descriptors_with_safe_metadata(self, tmp_path: Path) -> None:
        root = tmp_path / "project" / "mod"
        launcher_dir = tmp_path / "launcher" / "mod"
        (root / "events").mkdir(parents=True)
        (root / "events" / "custom.txt").write_text("# event", encoding="utf-8")

        result = write_mod_descriptors(
            root,
            launcher_dir,
            'My "Quoted" Mod',
            supported_version="1.17.*",
            tags=["Alternative History", "Events", "Events"],
            replace_paths=["history\\states", "events", "events"],
            remote_file_id=12345,
        )

        assert result.launcher == launcher_dir / "My_Quoted_Mod.mod"
        assert result.descriptor == root / "descriptor.mod"
        assert result.replace_paths == ("history/states", "events")
        inner = parse_pdx(result.descriptor.read_text(encoding="utf-8"))
        launcher = parse_pdx(result.launcher.read_text(encoding="utf-8"))
        assert inner.get_value("name") == 'My "Quoted" Mod'
        assert inner.get_value("path") == ""
        assert launcher.get_value("path") == root.resolve().as_posix()
        assert inner.get_value("supported_version") == "1.17.*"
        assert inner.get_value("remote_file_id") == "12345"
        assert len(inner.find_all("replace_path")) == 2

    def test_replace_paths_are_opt_in_even_when_content_exists(self, tmp_path: Path) -> None:
        root = tmp_path / "mod"
        (root / "events").mkdir(parents=True)
        (root / "events" / "custom.txt").write_text("# event", encoding="utf-8")
        launcher_dir = tmp_path / "launcher"

        safe = write_mod_descriptors(root, launcher_dir, "Safe Mod")
        assert "replace_path" not in safe.descriptor.read_text(encoding="utf-8")
        assert safe.replace_paths == ()

        total_conversion = write_mod_descriptors(
            root,
            launcher_dir,
            "Total Conversion",
            auto_detect_replace_paths=True,
        )
        assert total_conversion.replace_paths == ("events",)
        assert 'replace_path="events"' in total_conversion.descriptor.read_text(encoding="utf-8")

    def test_replace_path_scan_ignores_empty_scaffold_directories(self, tmp_path: Path) -> None:
        root = tmp_path / "mod"
        (root / "events").mkdir(parents=True)
        (root / "history" / "states").mkdir(parents=True)
        (root / "history" / "states" / "1-Test.txt").write_text("state = {}")
        assert scan_replace_paths(root) == ("history/states",)

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"supported_version": '1.17.*" bad=yes'}, "supported version"),
            ({"replace_paths": ["../history/states"]}, "traversal"),
            ({"replace_paths": ["/history/states"]}, "relative game path"),
            ({"launcher_filename": "../evil.mod"}, "path components"),
        ],
    )
    def test_rejects_descriptor_injection_and_path_escape(
        self, tmp_path: Path, kwargs: dict[str, Any], message: str
    ) -> None:
        with pytest.raises(ValueError, match=message):
            write_mod_descriptors(tmp_path / "mod", tmp_path / "launcher", "Test", **kwargs)
        assert not (tmp_path / "evil.mod").exists()

    def test_detects_supported_version_when_not_explicit(self, tmp_path: Path) -> None:
        install = tmp_path / "game"
        install.mkdir()
        (install / "launcher-settings.json").write_text(
            json.dumps({"rawVersion": "1.18.2.0"}), encoding="utf-8"
        )
        result = write_mod_descriptors(
            tmp_path / "project", tmp_path / "launcher", "Versioned", hoi4_install=install
        )
        assert result.supported_version == "1.18.*"
        assert 'supported_version="1.18.*"' in result.descriptor.read_text(encoding="utf-8")

    def test_late_commit_failure_restores_both_existing_descriptors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "project"
        launcher_dir = tmp_path / "launcher"
        root.mkdir()
        launcher_dir.mkdir()
        descriptor = root / "descriptor.mod"
        launcher = launcher_dir / "My_Mod.mod"
        descriptor.write_text("old inner descriptor\n", encoding="utf-8")
        launcher.write_text("old launcher descriptor\n", encoding="utf-8")
        originals = {
            descriptor: descriptor.read_bytes(),
            launcher: launcher.read_bytes(),
        }
        real_replace = project.os.replace
        failed = False

        def fail_launcher_commit(source_path: object, destination_path: object) -> None:
            nonlocal failed
            if Path(destination_path) == launcher and not failed:
                failed = True
                raise OSError("injected descriptor commit failure")
            real_replace(source_path, destination_path)

        monkeypatch.setattr(project.os, "replace", fail_launcher_commit)

        with pytest.raises(OSError, match="injected descriptor commit failure"):
            write_mod_descriptors(root, launcher_dir, "My Mod", supported_version="1.17.*")

        assert failed
        assert descriptor.read_bytes() == originals[descriptor]
        assert launcher.read_bytes() == originals[launcher]
        assert {path for path in root.iterdir() if path.is_file()} == {descriptor}
        assert {path for path in launcher_dir.iterdir() if path.is_file()} == {launcher}


class TestDiscovery:
    def test_create_then_discover_trusts_only_explicit_project_parent(
        self, tmp_path: Path
    ) -> None:
        project_parent = tmp_path / "projects"
        mod_path = project_parent / "my_mod"
        launcher_dir = tmp_path / "launcher"
        create_mod_structure(mod_path)
        write_mod_descriptors(
            mod_path,
            launcher_dir,
            "My Mod",
            supported_version="1.17.*",
        )

        rejected = scan_mod_descriptors(launcher_dir)
        assert rejected.mods == ()
        assert "outside the allowed roots" in rejected.issues[0].message

        accepted = scan_mod_descriptors(
            launcher_dir,
            allowed_roots=[project_parent],
        )
        assert [mod.mod_path for mod in accepted.mods] == [mod_path.resolve()]

    def test_discovers_relative_mod_and_compatibility_tuple(self, tmp_path: Path) -> None:
        mod_path = tmp_path / "my_mod"
        mod_path.mkdir()
        descriptor = tmp_path / "my_mod.mod"
        descriptor.write_text(
            'name="My Mod"\nsupported_version="1.17.*"\npath="my_mod"\n',
            encoding="utf-8",
        )

        mods = discover_mods(tmp_path)
        assert len(mods) == 1
        assert mods[0].name == "My Mod"
        assert mods[0].mod_path == mod_path.resolve()
        assert mods[0].supported_version == "1.17.*"
        assert find_mods_in_user_mod_folder(tmp_path) == [("my_mod.mod", mod_path.resolve())]

    def test_rejects_relative_escape_and_symlink_escape(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / f"{tmp_path.name}_outside"
        outside.mkdir()
        try:
            (tmp_path / "traversal.mod").write_text(f'path="../{outside.name}"\n', encoding="utf-8")
            (tmp_path / "linked").symlink_to(outside, target_is_directory=True)
            (tmp_path / "symlink.mod").write_text('path="linked"\n', encoding="utf-8")

            result = scan_mod_descriptors(tmp_path)
            assert result.mods == ()
            assert len(result.issues) == 2
            assert any("traversal" in issue.message for issue in result.issues)
            assert any("escapes" in issue.message for issue in result.issues)
        finally:
            outside.rmdir()

    def test_absolute_external_path_requires_explicit_trust(self, tmp_path: Path) -> None:
        launcher_dir = tmp_path / "launcher"
        launcher_dir.mkdir()
        external_root = tmp_path / "projects"
        mod_path = external_root / "mod"
        mod_path.mkdir(parents=True)
        (launcher_dir / "external.mod").write_text(f'path="{mod_path}"\n', encoding="utf-8")

        rejected = scan_mod_descriptors(launcher_dir)
        assert rejected.mods == ()
        assert "outside the allowed roots" in rejected.issues[0].message

        accepted = discover_mods(launcher_dir, allowed_roots=[external_root])
        assert [mod.mod_path for mod in accepted] == [mod_path.resolve()]

    def test_malformed_missing_and_oversized_descriptors_are_diagnostic(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "broken.mod").write_text("}", encoding="utf-8")
        (tmp_path / "missing.mod").write_text('name="No path"\n', encoding="utf-8")
        (tmp_path / "large.mod").write_text('path="x"\n' + "#" * 100, encoding="utf-8")

        result = scan_mod_descriptors(tmp_path, max_descriptor_bytes=32)
        assert result.mods == ()
        assert len(result.issues) == 3
        assert any("no path" in issue.message for issue in result.issues)
        assert any("size limit" in issue.message for issue in result.issues)
        assert any("closing brace" in issue.message for issue in result.issues)

    def test_descriptor_symlink_is_rejected(self, tmp_path: Path) -> None:
        external = tmp_path / "outside.mod.txt"
        external.write_text('path="mod"\n', encoding="utf-8")
        (tmp_path / "linked.mod").symlink_to(external)
        result = scan_mod_descriptors(tmp_path, require_existing=False)
        assert result.mods == ()
        assert result.issues[0].message == "descriptor is a symlink"
