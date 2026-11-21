"""Country context reports, icon suggestions and territory checks."""

from __future__ import annotations

import copy
import dataclasses
import os
from heapq import nlargest
from typing import Sequence

from ..focus import load_focus_tree
from ..idea_icons import (
    IDEA_SPRITE_PREFIX,
    normalize_idea_icon,
)
from ..ideas import read_ideas_file
from ..map_topology import (
    TerritoryComponent,
    find_country_territory_components,
    find_enclosed_foreign_components,
)
from ..paths import (
    require_country_tag,
)
from ..states import (
    read_state,
)
from ..textio import read_text
from ..types import (
    State,
)
from ._catalogs import CatalogsLayer
from ._countries import CountriesLayer
from ._states import StatesLayer

COMMON_FOCUS_ICONS: tuple[str, ...] = (
    "GFX_goal_generic_construct_civ_factory",
    "GFX_goal_generic_construct_infrastructure",
    "GFX_goal_generic_construct_mil_factory",
    "GFX_goal_generic_consumer_goods",
    "GFX_goal_generic_dangerous_deal",
    "GFX_goal_generic_demand_territory",
    "GFX_goal_generic_forceful_treaty",
    "GFX_goal_generic_intelligence_exchange",
    "GFX_goal_generic_major_war",
    "GFX_goal_generic_political_pressure",
    "GFX_goal_generic_production",
    "GFX_goal_generic_secret_weapon",
    "GFX_goal_generic_small_arms",
    "GFX_goal_generic_territory_or_war",
)


class ContextLayer(StatesLayer, CountriesLayer, CatalogsLayer):
    """Country context reports, icon suggestions and territory checks."""

    def get_country_context(self, tag: str, *, copy_states: bool = False) -> dict:
        tag = require_country_tag(tag)
        if not copy_states and not self._dirty and tag in self._country_context_cache:
            return copy.deepcopy(self._country_context_cache[tag])
        country = self.get_country(tag)
        context: dict = {
            "tag": tag,
            "name": country.name,
            "adjective": country.adjective,
            "capital": country.capital,
            "ruling_party": country.ruling_party,
            "popularities": dict(country.popularities),
            "leader": dataclasses.asdict(country.leader) if country.leader else None,
            "states": [],
            "ideas": [],
            "localization": {},
            "focus_trees": [],
        }

        for base in [self._base_content_root, self.mod_root]:
            if base is None:
                continue
            states_dir = base / "history" / "states"
            if states_dir.exists():
                for state_path in sorted(states_dir.glob("*.txt")):
                    try:
                        state_text = read_text(state_path)
                    except Exception:
                        continue
                    if (
                        f"owner = {tag}" not in state_text
                        and f"add_core_of = {tag}" not in state_text
                    ):
                        continue
                    try:
                        state = read_state(state_path, text=state_text)
                    except Exception:
                        continue
                    if state.owner == tag or tag in state.cores:
                        if copy_states:
                            try:
                                state = self.get_state(state.id)
                                if self._materialize_state_override(state):
                                    self._dirty.add("states")
                                    self._dirty_states.add(state.id)
                            except KeyError:
                                pass
                        context["states"].append(dataclasses.asdict(state))

            for ideas_dir in [base / "common" / "ideas", base / "common" / "national_ideas"]:
                if not ideas_dir.exists():
                    continue
                candidates = [ideas_dir / f"{tag}.txt"]
                if country.name:
                    candidates.append(ideas_dir / f"{country.name.lower().replace(' ', '_')}.txt")
                for path in candidates:
                    if not path.exists():
                        continue
                    try:
                        ideas, _ = read_ideas_file(path)
                    except Exception:
                        continue
                    for idea in ideas:
                        context["ideas"].append(dataclasses.asdict(idea))

            focus_dir = base / "common" / "national_focus"
            if focus_dir.exists():
                for path in sorted(focus_dir.glob("*.txt")):
                    try:
                        raw_focus_text = read_text(path)
                    except Exception:
                        continue
                    focus_header = raw_focus_text.split("focus = {", 1)[0]
                    if (
                        f"tag = {tag}" not in focus_header
                        and f"original_tag = {tag}" not in focus_header
                    ):
                        continue
                    try:
                        tree = load_focus_tree(path, text=raw_focus_text)
                    except Exception:
                        continue
                    if tree and tree.country_tag == tag:
                        context["focus_trees"].append(
                            {
                                "id": tree.id,
                                "path": str(path),
                                "focus_ids": [focus.id for focus in tree.focuses],
                            }
                        )

        for key, value in self._loc_entries.items():
            if key == tag or key.startswith(f"{tag}_"):
                context["localization"][key] = value

        # Deduplicate repeated vanilla/mod fallback entries by stable IDs.
        context["states"] = list({state["id"]: state for state in context["states"]}.values())
        context["ideas"] = list({idea["id"]: idea for idea in context["ideas"]}.values())
        if not copy_states and not self._dirty:
            self._country_context_cache[tag] = copy.deepcopy(context)
        return context

    def ensure_country_states_in_mod(self, tag: str) -> list[State]:
        context = self.get_country_context(tag, copy_states=True)
        return [self.get_state(state["id"]) for state in context["states"]]

    def suggest_focus_icons(self, query: str, count: int = 5) -> list[str]:
        icons = sorted(self._known_focus_icons())
        if not icons:
            icons = sorted(COMMON_FOCUS_ICONS)
        return self._rank_icons(query, icons, count)

    def suggest_idea_icons(self, query: str, count: int = 5) -> list[str]:
        icons = sorted(
            icon.removeprefix(IDEA_SPRITE_PREFIX)
            for icon in self._known_focus_icons()
            if icon.startswith(IDEA_SPRITE_PREFIX)
        )
        return self._rank_icons(normalize_idea_icon(query), icons, count)

    @staticmethod
    def _rank_icons(query: str, icons: Sequence[str], count: int) -> list[str]:
        needle = query.lower().replace(" ", "_")

        needle_parts = {part for part in needle.split("_") if part}

        def score(icon: str) -> tuple[float, str]:
            low = icon.lower()
            low_parts = set(low.split("_"))
            substring = 2.0 if needle and needle in low else 0.0
            token_overlap = len(needle_parts & low_parts) / max(1, len(needle_parts))
            prefix = (
                len(os.path.commonprefix((needle, low))) / max(1, len(needle)) if needle else 0.0
            )
            return (substring + token_overlap + prefix, icon)

        return [icon for _, icon in nlargest(count, (score(icon) for icon in icons))]

    def suggest_focus_icon(self, query: str) -> str:
        suggestions = self.suggest_focus_icons(query, count=1)
        if not suggestions:
            raise ValueError(
                "No focus icons available from mod, vanilla install, or built-in fallback list"
            )
        return suggestions[0]

    def suggest_idea_icon(self, query: str) -> str:
        suggestions = self.suggest_idea_icons(query, count=1)
        if not suggestions:
            raise ValueError(
                "No idea icons available from mod or configured vanilla install"
            )
        return suggestions[0]

    # ── Complete country packages and geography ─────────────────

    def find_disconnected_states(
        self,
        tag: str,
        *,
        minimum_land_provinces: int = 2,
        allowed_state_ids: Sequence[int] = (),
    ) -> tuple[TerritoryComponent, ...]:
        """Find significant owned territory disconnected from the capital.

        This method imports NumPy and Pillow lazily and therefore requires the
        optional ``map`` extra only when it is called.
        """

        tag = require_country_tag(tag)
        if not self._is_known_country_tag(tag):
            raise KeyError(f"Country '{tag}' is not defined")
        country = self.get_country(tag)
        return find_country_territory_components(
            self.mod_root,
            self._base_content_root,
            self._effective_states(),
            country_tag=tag,
            capital_state_id=country.capital,
            minimum_land_provinces=minimum_land_provinces,
            allowed_state_ids=allowed_state_ids,
        )

    def find_enclosed_foreign_states(
        self,
        tag: str,
        *,
        minimum_land_provinces: int = 1,
        allowed_state_ids: Sequence[int] = (),
    ) -> tuple[TerritoryComponent, ...]:
        """Find foreign land components entirely enclosed by ``tag``."""

        tag = require_country_tag(tag)
        if not self._is_known_country_tag(tag):
            raise KeyError(f"Country '{tag}' is not defined")
        return find_enclosed_foreign_components(
            self.mod_root,
            self._base_content_root,
            self._effective_states(),
            country_tag=tag,
            minimum_land_provinces=minimum_land_provinces,
            allowed_state_ids=allowed_state_ids,
        )
