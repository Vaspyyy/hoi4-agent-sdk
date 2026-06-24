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

from .mod import Mod
from .config import Config, find_config
from .parser import PdxNode, parse_pdx, serialize_pdx
from .script import effect_block, normalize_block_body, pdx_value, scope_block, validate_script_syntax
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

__all__ = [
    "Mod",
    "Config",
    "find_config",
    "Country",
    "Decision",
    "DecisionCategory",
    "Event",
    "EventOption",
    "Focus",
    "FocusTree",
    "Idea",
    "Leader",
    "SaveResult",
    "State",
    "ValidationError",
    "PdxNode",
    "parse_pdx",
    "serialize_pdx",
    "effect_block",
    "normalize_block_body",
    "pdx_value",
    "scope_block",
    "validate_script_syntax",
    "VALIDATION_CODES",
    "VALIDATION_WARNING_CODES",
    "EFFECT_CATEGORIES",
    "TECHNOLOGY_CATEGORIES",
    "MODIFIER_CATEGORIES",
]
