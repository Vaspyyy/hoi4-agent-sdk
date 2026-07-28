"""
HOI4 Agent SDK - Python SDK for AI agents to mod Hearts of Iron 4.

Usage:
    from hoi4 import Mod, Focus, Country

    mod = Mod("/path/to/my_mod", hoi4_install="/opt/steam/hoi4")
    mod.create_country("WST", "Westralia", color=(59, 130, 246))
    tree = mod.get_focus_tree("german_focus")
    mod.add_focus("german_focus", Focus(id="GER_new_focus", x=5, y=3))
    mod.save()
"""

from importlib.metadata import PackageNotFoundError, version

from .assets import (
    BookmarkPictureAsset,
    DDSExportUnsupportedError,
    FlagAssetSet,
    ImageBackendUnavailableError,
    UnsupportedImageFormatError,
    export_flag_from_mod,
    export_portrait_from_mod,
    find_flag_path,
    find_portrait_path,
    import_bookmark_picture_to_mod,
    import_flag_to_mod,
    import_portrait_to_mod,
    write_portrait_gfx,
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
from .country_package import CountryPackageReport
from .mod import ExternalModificationError, Mod
from .config import Config, find_config
from .dynamic_modifiers import DynamicModifier
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
from .game_log import (
    GameLogEntry,
    GameLogReport,
    format_game_log_report,
    parse_hoi4_error_log,
)
from .ideologies import Ideology, SubIdeology
from .map_render import MapRenderCancelled, PoliticalMapResult, render_political_map
from .map_topology import TerritoryComponent
from .oob import Battalion, DivisionTemplate, DivisionUnit, OrderOfBattle
from .parser import ParseError, PdxNode, parse_pdx, serialize_pdx
from .progress import OperationCancelled, ProgressEvent
from .project import (
    DiscoveredMod,
    ModDiscoveryIssue,
    ModDiscoveryResult,
    ModDescriptorFiles,
    create_mod_structure,
    detect_launcher_mod_directory,
    discover_mods,
    scan_mod_descriptors,
    write_mod_descriptors,
)
from .script import (
    effect_block,
    normalize_block_body,
    pdx_string,
    pdx_value,
    scope_block,
    validate_script_syntax,
)
from .validation import VALIDATION_CODES, VALIDATION_WARNING_CODES
from .types import (
    Country,
    Decision,
    DecisionCategory,
    Event,
    EventOption,
    Focus,
    FocusTree,
    Idea,
    Leader,
    LoadDiagnostic,
    OnAction,
    SaveResult,
    State,
    ValidationError,
)

try:
    from .effects_catalog import EFFECT_CATEGORIES, TECHNOLOGY_CATEGORIES
    from .modifiers_catalog import MODIFIER_CATEGORIES
except ImportError:
    EFFECT_CATEGORIES = []
    TECHNOLOGY_CATEGORIES = ()
    MODIFIER_CATEGORIES = []

try:
    __version__ = version("hoi4-agent-sdk")
except PackageNotFoundError:  # pragma: no cover - source tree without installation metadata
    __version__ = "0.5.0"

__all__ = [
    "Mod",
    "ExternalModificationError",
    "__version__",
    "Config",
    "find_config",
    "Country",
    "CountryPackageReport",
    "Character",
    "CharacterInstance",
    "CharacterPortrait",
    "CountryLeaderRole",
    "AdvisorRole",
    "ArmyCommanderRole",
    "NavyLeaderRole",
    "OrderOfBattle",
    "DivisionTemplate",
    "Battalion",
    "DivisionUnit",
    "Decision",
    "DecisionCategory",
    "Event",
    "EventOption",
    "Focus",
    "FocusTree",
    "Idea",
    "Leader",
    "LoadDiagnostic",
    "OnAction",
    "SaveResult",
    "State",
    "ValidationError",
    "Bookmark",
    "BookmarkCountry",
    "DynamicModifier",
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
    "import_flag_to_mod",
    "import_portrait_to_mod",
    "write_portrait_gfx",
    "MapRenderCancelled",
    "PoliticalMapResult",
    "TerritoryComponent",
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
    "effect_block",
    "normalize_block_body",
    "pdx_value",
    "pdx_string",
    "scope_block",
    "validate_script_syntax",
    "VALIDATION_CODES",
    "VALIDATION_WARNING_CODES",
    "EFFECT_CATEGORIES",
    "TECHNOLOGY_CATEGORIES",
    "MODIFIER_CATEGORIES",
]
