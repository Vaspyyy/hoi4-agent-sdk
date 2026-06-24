from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def _ensure_nested(value: list) -> list[list[str]]:
    if not value:
        return value
    if isinstance(value[0], str):
        return [value]
    return value


@dataclass
class Focus:
    id: str
    icon: str = "GFX_goal_generic_construct_civilian"
    x: int = 0
    y: int = 0
    cost: int = 10
    prerequisites: list[list[str]] = field(default_factory=list)
    mutually_exclusive: list[list[str]] = field(default_factory=list)
    relative_position_id: str = ""
    search_filters: list[str] = field(default_factory=list)
    completion_reward: str = ""
    available: str = ""
    bypass: str = ""
    select_effect: str = ""
    complete_tooltip: str = ""
    allow_branch: str = ""
    ai_will_do: str = ""
    cancel_if_invalid: Optional[bool] = None
    continue_if_invalid: Optional[bool] = None
    available_if_capitulated: Optional[bool] = None
    will_lead_to_war_with: str = ""
    raw_block: str = ""
    touched: bool = False

    def __post_init__(self) -> None:
        self.prerequisites = _ensure_nested(self.prerequisites)
        self.mutually_exclusive = _ensure_nested(self.mutually_exclusive)


@dataclass
class FocusTree:
    id: str
    country_tag: str = ""
    focuses: list[Focus] = field(default_factory=list)
    path: Optional[Path] = None
    default: Optional[bool] = None
    continuous_focus_position: str = ""
    shared_focuses: list[str] = field(default_factory=list)


@dataclass
class Leader:
    name: str
    character_id: str = ""
    ideology: str = "liberalism"
    portrait_slug: str = ""


@dataclass
class Country:
    tag: str
    name: str = ""
    adjective: str = ""
    color: tuple[int, int, int] = (128, 128, 128)
    graphical_culture: str = "western_european_gfx"
    graphical_culture_2d: str = "western_european_2d"
    capital: int = 1
    ruling_party: str = "democratic"
    popularities: dict[str, int] = field(default_factory=dict)
    elections_allowed: bool = True
    leader: Optional[Leader] = None
    ideas: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.popularities:
            self.popularities = {
                "democratic": 100,
                "fascism": 0,
                "communism": 0,
                "neutrality": 0,
            }


@dataclass
class ValidationError:
    message: str
    severity: str = "error"
    file_path: Optional[str] = None
    focus_id: Optional[str] = None
    country_tag: Optional[str] = None
    state_id: Optional[int] = None
    event_id: Optional[str] = None
    idea_id: Optional[str] = None


@dataclass
class SaveResult:
    written_files: list[Path] = field(default_factory=list)
    dirty_sections: list[str] = field(default_factory=list)
    no_changes: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.written_files)


@dataclass
class EventOption:
    name: str = ""
    trigger: str = ""
    effect: str = ""
    ai_chance: str = ""
    raw_block: str = ""


@dataclass
class Event:
    id: str
    title: str = ""
    description: str = ""
    event_type: str = "country_event"
    picture: str = "GFX_report_event_generic"
    is_triggered_only: bool = False
    fire_only_once: Optional[bool] = None
    trigger: str = ""
    immediate: str = ""
    mean_time_to_happen: str = ""
    options: list[EventOption] = field(default_factory=list)
    path: Optional[Path] = None
    raw_block: str = ""


@dataclass
class State:
    id: int
    name: str = ""
    owner: str = ""
    cores: list[str] = field(default_factory=list)
    resources: dict[str, str | int | float] = field(default_factory=dict)
    buildings: str = ""
    local_supplies: str = ""
    manpower: str = "0"
    state_category: str = "large_city"
    victory_points: str = ""
    buildings_max_level_factor: str = "1.0"
    is_demilitarized_zone: bool = False
    provinces: list[int] = field(default_factory=list)
    history: str = ""
    path: Optional[Path] = None
    raw_text: str = ""


@dataclass
class Decision:
    id: str
    category: str = ""
    icon: str = ""
    cost: int | None = None
    days_remove: int | None = None
    fire_only_once: Optional[bool] = None
    available: str = ""
    visible: str = ""
    complete_effect: str = ""
    remove_effect: str = ""
    ai_will_do: str = ""
    path: Optional[Path] = None
    raw_block: str = ""


@dataclass
class DecisionCategory:
    id: str
    icon: str = ""
    allowed: str = ""
    visible: str = ""
    decisions: list[Decision] = field(default_factory=list)
    path: Optional[Path] = None
    raw_block: str = ""


@dataclass
class Idea:
    id: str
    icon: str = "GFX_idea_generic"
    modifier: dict[str, str | int | float | bool] = field(default_factory=dict)
    path: Optional[Path] = None
    category: str = ""
    allowed: str = ""
    research_bonus: dict[str, str | int | float | bool] = field(default_factory=dict)
    traits: list[str] = field(default_factory=list)
    ai_will_do: str = ""
    raw_block: str = ""
