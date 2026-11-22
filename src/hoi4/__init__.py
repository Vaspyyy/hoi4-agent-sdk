"""HOI4 Agent SDK: a Python SDK for AI agents to mod Hearts of Iron IV.

Usage (the quick start of ``README.md``)::

    from hoi4 import Focus, Mod

    mod = Mod("/path/to/my_mod", hoi4_install="/path/to/Hearts of Iron IV")

    with mod.transaction():
        mod.create_focus_tree("ger_extra_focus", "GER")
        mod.add_focus(
            "ger_extra_focus",
            Focus(
                id="GER_expand_industry",
                x=3,
                y=2,
                completion_reward="add_political_power = 50",
            ),
        )
        mod.set_focus_loc("GER_expand_industry", "Expand Industry", "More factories.")
        print(mod.preview_summary())
        print(mod.preview())
        issues = mod.validate()
        for issue in issues:
            print(f"[{issue.severity}] {issue.message}")
        if any(issue.severity == "error" for issue in issues):
            raise RuntimeError("Validation failed")

    # This dry run discards its changes on exit. After reviewing the output,
    # rerun the block with mod.transaction(save=True) to apply and save the edits.

A focus tree the game ships is not visible to ``get_focus_tree()`` until its
file is copied into the mod with ``mod.load_inherited_content(path)``.
"""

import importlib.metadata as _metadata

from .assets import (
    ICON_KINDS,
    BookmarkPictureAsset,
    DDSExportUnsupportedError,
    FlagAssetSet,
    IconAsset,
    ImageBackendUnavailableError,
    UnsupportedImageFormatError,
    export_flag_from_mod,
    export_portrait_from_mod,
    find_flag_path,
    find_portrait_path,
    import_bookmark_picture_to_mod,
    import_icon_to_mod,
    render_focus_shine_sprite,
    render_sprite_type,
    upsert_sprite_type,
)
from .bookmarks import Bookmark, BookmarkCountry
from .characters import (
    AdvisorRole,
    ArmyCommanderRole,
    Character,
    CharacterInstance,
    CharacterPortrait,
    CountryLeaderRole,
    NavyLeaderRole,
)
from .commons_images import (
    CommonsImage,
    CommonsImageClient,
    CommonsImageError,
    DownloadedCommonsImage,
)
from .config import Config, find_config
from .content_graph import ContentLivenessReport
from .country_package import CountryPackageReport
from .definitions import DEFINITION_KINDS, DefinitionKind, ScriptDefinition
from .dynamic_modifiers import DynamicModifier
from .errors import (
    AlreadyExistsError,
    Hoi4Error,
    LoadError,
    NotFoundError,
    ReadOnlyError,
    SaveRefusedError,
)
from .game_log import (
    GameLogEntry,
    GameLogReport,
    format_game_log_report,
    parse_hoi4_error_log,
)
from .gemini_images import (
    DEFAULT_GEMINI_IMAGE_MODEL,
    DEFAULT_GEMINI_IMAGE_SIZE,
    GeminiAuthenticationError,
    GeminiBackendUnavailableError,
    GeminiImageError,
    GeminiImageGenerationError,
    GeminiImageGenerator,
    GeminiImageResponseError,
    GeminiImageResult,
)
from .ideologies import Ideology, SubIdeology
from .map_editing import MapBuilding, MapEditReport, Railway, StrategicRegion
from .map_render import MapRenderCancelled, PoliticalMapResult, render_political_map
from .map_topology import TerritoryComponent
from .mod import ExternalModificationError, Mod
from .oob import (
    AirWing,
    Battalion,
    DivisionTemplate,
    DivisionUnit,
    EquipmentVariant,
    Fleet,
    OOBKind,
    OOBReference,
    OrderOfBattle,
    Ship,
    ShipEquipment,
    TaskForce,
)
from .parser import ParseError, PdxNode, parse_pdx, serialize_pdx
from .progress import OperationCancelled, ProgressEvent
from .project import (
    DiscoveredMod,
    ModDescriptorFiles,
    ModDiscoveryIssue,
    ModDiscoveryResult,
    create_mod_structure,
    detect_launcher_mod_directory,
    discover_mods,
    scan_mod_descriptors,
    write_mod_descriptors,
)
from . import effects
from .script import (
    normalize_block_body,
    pdx_string,
    pdx_value,
    validate_script_syntax,
)
from .script_vocabulary import (
    GameScriptVocabulary,
    ScriptSource,
    ScriptTokenInfo,
    load_game_script_vocabulary,
)
from .types import (
    Country,
    Decision,
    DecisionCategory,
    Event,
    EventOption,
    EventText,
    Focus,
    FocusTree,
    Idea,
    Leader,
    LoadDiagnostic,
    OnAction,
    SaveResult,
    State,
    ValidationIssue,
)
from .validation import (
    COUNTRY_PACKAGE_REQUIREMENT_CODES,
    VALIDATION_CODES,
    VALIDATION_ERROR_CODES,
    VALIDATION_INFO_CODES,
    VALIDATION_SEVERITIES,
    VALIDATION_WARNING_CODES,
)
from .validation_stages import ValidationStage

from .effects_catalog import EFFECT_CATEGORIES, TECHNOLOGY_CATEGORIES
from .modifiers_catalog import MODIFIER_CATEGORIES

try:
    __version__ = _metadata.version("hoi4-agent-sdk")
except _metadata.PackageNotFoundError:  # pragma: no cover - source tree without installation metadata
    __version__ = "0.6.5"

__all__ = [
    "Mod",
    "effects",
    "Hoi4Error",
    "NotFoundError",
    "AlreadyExistsError",
    "ReadOnlyError",
    "SaveRefusedError",
    "LoadError",
    "ExternalModificationError",
    "__version__",
    "Config",
    "find_config",
    "Country",
    "CountryPackageReport",
    "ContentLivenessReport",
    "Character",
    "CharacterInstance",
    "CharacterPortrait",
    "CountryLeaderRole",
    "AdvisorRole",
    "ArmyCommanderRole",
    "NavyLeaderRole",
    "OrderOfBattle",
    "OOBKind",
    "OOBReference",
    "EquipmentVariant",
    "DivisionTemplate",
    "Battalion",
    "DivisionUnit",
    "Fleet",
    "TaskForce",
    "Ship",
    "ShipEquipment",
    "AirWing",
    "Decision",
    "DecisionCategory",
    "Event",
    "EventOption",
    "EventText",
    "Focus",
    "FocusTree",
    "Idea",
    "Leader",
    "LoadDiagnostic",
    "OnAction",
    "SaveResult",
    "State",
    "ValidationIssue",
    "Bookmark",
    "BookmarkCountry",
    "DynamicModifier",
    "DEFINITION_KINDS",
    "DefinitionKind",
    "ScriptDefinition",
    "CommonsImage",
    "CommonsImageClient",
    "CommonsImageError",
    "DownloadedCommonsImage",
    "DEFAULT_GEMINI_IMAGE_MODEL",
    "DEFAULT_GEMINI_IMAGE_SIZE",
    "GeminiAuthenticationError",
    "GeminiBackendUnavailableError",
    "GeminiImageError",
    "GeminiImageGenerationError",
    "GeminiImageGenerator",
    "GeminiImageResponseError",
    "GeminiImageResult",
    "GameLogEntry",
    "GameLogReport",
    "format_game_log_report",
    "parse_hoi4_error_log",
    "Ideology",
    "SubIdeology",
    "ProgressEvent",
    "OperationCancelled",
    "BookmarkPictureAsset",
    "DDSExportUnsupportedError",
    "FlagAssetSet",
    "ImageBackendUnavailableError",
    "UnsupportedImageFormatError",
    "export_flag_from_mod",
    "export_portrait_from_mod",
    "find_flag_path",
    "find_portrait_path",
    "import_bookmark_picture_to_mod",
    "ICON_KINDS",
    "IconAsset",
    "import_icon_to_mod",
    "render_focus_shine_sprite",
    "render_sprite_type",
    "upsert_sprite_type",
    "MapRenderCancelled",
    "PoliticalMapResult",
    "TerritoryComponent",
    "MapBuilding",
    "MapEditReport",
    "Railway",
    "StrategicRegion",
    "render_political_map",
    "ModDescriptorFiles",
    "DiscoveredMod",
    "ModDiscoveryIssue",
    "ModDiscoveryResult",
    "create_mod_structure",
    "detect_launcher_mod_directory",
    "discover_mods",
    "scan_mod_descriptors",
    "write_mod_descriptors",
    "PdxNode",
    "ParseError",
    "parse_pdx",
    "serialize_pdx",
    "normalize_block_body",
    "pdx_value",
    "pdx_string",
    "validate_script_syntax",
    "COUNTRY_PACKAGE_REQUIREMENT_CODES",
    "VALIDATION_CODES",
    "VALIDATION_ERROR_CODES",
    "VALIDATION_INFO_CODES",
    "VALIDATION_SEVERITIES",
    "VALIDATION_WARNING_CODES",
    "ValidationStage",
    "GameScriptVocabulary",
    "ScriptSource",
    "ScriptTokenInfo",
    "load_game_script_vocabulary",
    "EFFECT_CATEGORIES",
    "TECHNOLOGY_CATEGORIES",
    "MODIFIER_CATEGORIES",
]
