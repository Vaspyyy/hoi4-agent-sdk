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
        "Character",
        "CharacterInstance",
        "CharacterPortrait",
        "CountryLeaderRole",
        "AdvisorRole",
        "ArmyCommanderRole",
        "NavyLeaderRole",
        "OrderOfBattle",
        "OOBReference",
        "EquipmentVariant",
        "DivisionTemplate",
        "DivisionUnit",
        "Battalion",
        "Fleet",
        "TaskForce",
        "Ship",
        "ShipEquipment",
        "AirWing",
        "CountryPackageReport",
        "ContentLivenessReport",
        "GameScriptVocabulary",
        "ScriptTokenInfo",
        "ScriptSource",
        "load_game_script_vocabulary",
        "TerritoryComponent",
        "scan_mod_descriptors",
    }

    assert expected <= set(hoi4.__all__)
    # The immediate-write asset functions live in hoi4.assets only; Mod has
    # staged methods of the same names.
    for name in ("import_flag_to_mod", "import_portrait_to_mod", "write_portrait_gfx"):
        assert name not in hoi4.__all__
        assert not hasattr(hoi4, name)
    assert not hasattr(hoi4, "version")
    assert not hasattr(hoi4, "PackageNotFoundError")
    assert all(callable(getattr(hoi4, name)) for name in expected)
    assert "ValidationStage" in hoi4.__all__
