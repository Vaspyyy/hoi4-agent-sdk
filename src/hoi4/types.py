from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from pathlib import Path
from typing import Optional, cast


def _ensure_nested(value: str | list[str] | list[list[str]] | None) -> list[list[str]]:
    if not value:
        return []
    if isinstance(value, str):
        return [[value]]
    if isinstance(value[0], str):
        return [list(cast(list[str], value))]
    return [list(group) for group in cast(list[list[str]], value)]


@dataclass
class Focus:
    id: str
    icon: str = "GFX_goal_generic_construct_civilian"
    x: int = 0
    y: int = 0
    cost: int = 10
    prerequisites: list[list[str]] = field(default_factory=list)
    requires: InitVar[str | list[str] | list[list[str]] | None] = None
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

    def __post_init__(self, requires) -> None:
        if requires:
            self.prerequisites.extend(_ensure_nested(requires))
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
    raw_block: str = ""
    touched: bool = False


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
    research_slots: int | None = None
    raw_definition: str = ""
    raw_history: str = ""
    raw_character: str = ""
    definition_path: Optional[Path] = None
    history_path: Optional[Path] = None
    character_path: Optional[Path] = None
    touched_fields: set[str] = field(default_factory=set)

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
    code: str = ""
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
    message: str = ""

    @property
    def changed(self) -> bool:
        return bool(self.written_files)

    def __str__(self) -> str:
        if self.message:
            return self.message
        if self.no_changes:
            return "No changes written"
        return f"Saved {len(self.written_files)} file(s)"


@dataclass(frozen=True)
class LoadDiagnostic:
    """A mod file that could not be loaded without hiding the failure."""

    section: str
    path: Path
    error_type: str
    message: str


@dataclass
class EventOption:
    name: str = ""
    trigger: str = ""
    effect: str = ""
    ai_chance: str = ""
    raw_block: str = ""
    touched: bool = False


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
    touched: bool = False


@dataclass
class OnAction:
    id: str
    effect: str = ""
    events: list[str] = field(default_factory=list)
    random_events: list[str] = field(default_factory=list)
    path: Optional[Path] = None
    raw_block: str = ""
    touched: bool = False


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
    source_path: Optional[Path] = None


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
    touched: bool = False


@dataclass
class DecisionCategory:
    id: str
    icon: str = ""
    allowed: str = ""
    visible: str = ""
    decisions: list[Decision] = field(default_factory=list)
    path: Optional[Path] = None
    raw_block: str = ""
    touched: bool = False


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
    category_raw_block: str = ""
    touched: bool = False
