"""
Mod facade - the primary entry point for AI agents.

Usage:
    from hoi4 import Mod

    mod = Mod("/path/to/my_mod", hoi4_install="/opt/steam/hoi4")
    tree = mod.get_focus_tree("german_focus")
    mod.add_focus("german_focus", Focus(id="GER_new", x=5, y=3))
    errors = mod.validate()
    diff = mod.preview()
    mod.save()

The class is assembled from layers, one module per content type. Each layer
class subclasses only the layers whose methods it calls, so its class header
states its dependencies and mypy rejects a call to any other layer. All shared
state is declared in ``_core``.

"Uses" below lists every layer a module calls into. Its class header names
only the ones it does not already reach through another base.

* ``_localisation``: Localisation tables: loading, lookup, edits and language mirroring.
  Uses only ``_core``.
* ``_sources``: Per-file grouping of loaded models and the effective script texts. Uses
  ``_localisation``.
* ``_catalogs``: Catalogs of known identifiers (ideas, events, technologies, equipment,
  sprites). Uses ``_sources``.
* ``_equipment``: Equipment-variant and technology history analysis. Uses ``_sources``,
  ``_catalogs``.
* ``_countries``: Country lookup, tags, politics helpers and country overrides. Uses
  only ``_core``.
* ``_characters``: Character, role and instance authoring. Uses ``_localisation``,
  ``_countries``.
* ``_country_authoring``: Creating, updating and deleting countries. Uses
  ``_localisation``, ``_countries``, ``_characters``.
* ``_oobs``: Orders of battle, equipment variants and their country assignment. Uses
  ``_catalogs``, ``_equipment``, ``_countries``.
* ``_states``: State lookup, search and history edits. Uses ``_countries``.
* ``_map``: Map editing: provinces, strategic regions, supply, railways and buildings.
  Uses ``_localisation``, ``_catalogs``, ``_states``.
* ``_events``: Event authoring. Uses only ``_core``.
* ``_on_actions``: On-action authoring. Uses only ``_core``.
* ``_decisions``: Decision and decision-category authoring. Uses ``_localisation``.
* ``_ideas``: Idea (national spirit) authoring. Uses ``_countries``.
* ``_ideologies``: Ideology authoring. Uses only ``_core``.
* ``_dynamic_modifiers``: Dynamic modifier authoring. Uses only ``_core``.
* ``_bookmarks``: Bookmark authoring. Uses only ``_core``.
* ``_context``: Country context reports, icon suggestions and territory checks. Uses
  ``_catalogs``, ``_countries``, ``_states``.
* ``_focus``: Focus tree authoring and layout. Uses ``_localisation``, ``_context``,
  ``_catalogs``, ``_countries``.
* ``_definitions``: Script imports and the keyed ``common/`` definition API. Uses only
  ``_core``.
* ``_assets``: Flag, portrait, icon and picture imports. Uses only ``_core``.
* ``_loading``: Loaders of every content type, run section by section on first use.
  Uses ``_countries``.
* ``_script_validation``: Script vocabulary, reference and localisation validation. Uses
  ``_localisation``, ``_sources``, ``_catalogs``, ``_states``.
* ``_oob_validation``: Order-of-battle validation against the effective game data. Uses
  ``_sources``, ``_catalogs``, ``_equipment``, ``_countries``, ``_states``.
* ``_country_package``: Country package completeness validation. Uses
  ``_localisation``, ``_catalogs``, ``_equipment``, ``_countries``,
  ``_characters``, ``_oobs``, ``_states``, ``_context``, ``_oob_validation``.
* ``_validation``: The staged ``validate()`` entry point and related reports. Uses
  ``_localisation``, ``_sources``, ``_catalogs``, ``_states``, ``_map``, ``_context``, ``_definitions``,
  ``_script_validation``, ``_oob_validation``, ``_country_package``.
* ``_preview``: Plain-language summaries of what pending edits change. Uses only
  ``_core``.
* ``_persistence``: Rendering pending edits to files, diffs, atomic save, discard and
  transactions. Uses ``_sources``, ``_loading``, ``_preview``.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Self

from ..config import find_config

from ._assets import AssetsLayer
from ._bookmarks import BookmarksLayer
from ._country_authoring import CountryAuthoringLayer
from ._decisions import DecisionsLayer
from ._dynamic_modifiers import DynamicModifiersLayer
from ._events import EventsLayer
from ._focus import FocusLayer
from ._ideas import IdeasLayer
from ._ideologies import IdeologiesLayer
from ._on_actions import OnActionsLayer
from ._persistence import PersistenceLayer
from ._shared import ExternalModificationError
from ._validation import ValidationLayer

__all__ = ["ExternalModificationError", "Mod"]


class Mod(
    PersistenceLayer,
    ValidationLayer,
    AssetsLayer,
    FocusLayer,
    BookmarksLayer,
    DynamicModifiersLayer,
    IdeologiesLayer,
    IdeasLayer,
    DecisionsLayer,
    OnActionsLayer,
    EventsLayer,
    CountryAuthoringLayer,
):
    """Load, inspect, edit, validate and save one HOI4 mod."""

    def __init__(
        self,
        mod_root: str | Path,
        hoi4_install: str | Path | None = None,
        *,
        strict_loading: bool = False,
        base_mod_paths: Sequence[str | Path] = (),
    ):
        super().__init__(
            mod_root,
            hoi4_install,
            strict_loading=strict_loading,
            base_mod_paths=base_mod_paths,
        )
        # Content loads section by section on first use (see ``SECTIONS`` in
        # ``_core``). Strict loading reads everything now, so a broken file
        # still fails here.
        self._capture_source_baseline()
        if strict_loading:
            self._load_all_sections()

    @classmethod
    def from_config(
        cls,
        start: str | Path | None = None,
        *,
        strict_loading: bool = False,
    ) -> Self:
        """Create a Mod instance from a .hoi4.json config file.

        Searches for .hoi4.json starting from `start` (defaults to cwd)
        and walks up to the filesystem root.
        """
        cfg = find_config(start)
        if cfg is None or cfg.mod_path is None:
            raise FileNotFoundError(
                "No .hoi4.json found (or mod_path not set). "
                'Create one with: {"mod_path": "/path/to/mod", "hoi4_install": "/path/to/hoi4"}'
            )
        return cls(
            cfg.mod_path,
            hoi4_install=cfg.hoi4_install,
            base_mod_paths=cfg.base_mod_paths,
            strict_loading=strict_loading,
        )
