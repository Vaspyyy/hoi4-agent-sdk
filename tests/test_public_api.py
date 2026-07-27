from __future__ import annotations


def test_root_api_exposes_project_and_asset_workflows_without_optional_dependencies() -> None:
    import hoi4

    expected = {
        "create_mod_structure",
        "detect_launcher_mod_directory",
        "DynamicModifier",
        "export_flag_from_mod",
        "export_portrait_from_mod",
        "ExternalModificationError",
        "find_flag_path",
        "find_portrait_path",
        "GeminiImageGenerator",
        "GeminiImageResult",
        "MapRenderCancelled",
        "scan_mod_descriptors",
        "write_portrait_gfx",
    }

    assert expected <= set(hoi4.__all__)
    assert all(callable(getattr(hoi4, name)) for name in expected)
