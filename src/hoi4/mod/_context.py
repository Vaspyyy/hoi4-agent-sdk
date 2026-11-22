"""Country context reports, icon suggestions and territory checks."""

from __future__ import annotations

import copy
import dataclasses
import os
from heapq import nlargest
from typing import Sequence

from ..focus import load_focus_trees
from ..idea_icons import (
    IDEA_SPRITE_PREFIX,
    normalize_idea_icon,
)
from ..ideas import read_ideas_file
from ..map_topology import (
    ProvinceOwnership,
    TerritoryComponent,
    find_country_territory_components,
    find_enclosed_foreign_components,
    province_ownership,
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

        for base in reversed(self._data_views()):
            for state_path in base.glob("history/states", "*.txt").values():
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

            for ideas_dir in ("common/ideas", "common/national_ideas"):
                names = [f"{tag}.txt"]
                if country.name:
                    names.append(f"{country.name.lower().replace(' ', '_')}.txt")
                for name in names:
                    path = base.file(f"{ideas_dir}/{name}")
                    if path is None:
                        continue
                    try:
                        ideas, _ = read_ideas_file(path)
                    except Exception:
                        continue
                    for idea in ideas:
                        context["ideas"].append(dataclasses.asdict(idea))

            for path in base.glob("common/national_focus", "*.txt").values():
                try:
                    raw_focus_text = read_text(path)
                except Exception:
                    continue
                if tag not in raw_focus_text:
                    continue  # cheap skip: the tag is not mentioned at all
                try:
                    trees = load_focus_trees(path, text=raw_focus_text)
                except Exception:
                    continue
                for tree in trees:
                    if tree.country_tag == tag:
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

        tag = self._require_known_country(tag)
        country = self.get_country(tag)
        states, ownership = self._territory_map()
        return find_country_territory_components(
            self.mod_root,
            self._base_content,
            states,
            country_tag=tag,
            capital_state_id=country.capital,
            minimum_land_provinces=minimum_land_provinces,
            allowed_state_ids=allowed_state_ids,
            ownership=ownership,
        )

    def find_enclosed_foreign_states(
        self,
        tag: str,
        *,
        minimum_land_provinces: int = 1,
        allowed_state_ids: Sequence[int] = (),
    ) -> tuple[TerritoryComponent, ...]:
        """Find foreign land components entirely enclosed by ``tag``."""

        tag = self._require_known_country(tag)
        states, ownership = self._territory_map()
        return find_enclosed_foreign_components(
            self.mod_root,
            self._base_content,
            states,
            country_tag=tag,
            minimum_land_provinces=minimum_land_provinces,
            allowed_state_ids=allowed_state_ids,
            ownership=ownership,
        )

    def _territory_map(self) -> tuple[list[State], ProvinceOwnership]:
        """The effective states and the owner of every province of the map.

        Built once per validate() run, where the country package check asks
        for every country; outside a run, on every call. Raises like
        ``find_disconnected_states()`` when the map cannot be read.
        """

        def build() -> tuple[list[State], ProvinceOwnership]:
            states = self._effective_states()
            return states, province_ownership(self.mod_root, self._base_content, states)

        return self._run_cached("territory_map", build)
