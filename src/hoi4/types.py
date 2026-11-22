from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from pathlib import Path
from typing import cast

from .idea_icons import DEFAULT_IDEA_ICON


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
    """One national focus of a focus tree.

    ``x`` and ``y`` are grid cells, relative to ``relative_position_id``
    when it is set. ``prerequisites`` is a list of groups: the focus needs
    one focus of every group (``[["A", "B"], ["C"]]`` reads "A or B, and
    C"); ``requires=`` accepts a flat list or a single id as a short form.
    Script fields (``completion_reward``, ``available`` and the like) hold
    block bodies without the outer braces. ``raw_block`` and ``touched``
    are bookkeeping for loaded focuses.
    """

    id: str
    icon: str = "GFX_goal_generic_construct_civilian"
    x: int = 0
    y: int = 0
    # Days divided by seven, as the game reads it; fractional costs such as
    # ``7.2`` are kept, whole numbers stay ``int``.
    cost: float = 10
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
    cancel_if_invalid: bool | None = None
    continue_if_invalid: bool | None = None
    available_if_capitulated: bool | None = None
    will_lead_to_war_with: str = ""
    raw_block: str = ""
    touched: bool = False
    # Bodies of the ``icon = { ... }`` blocks (conditional icons), in source
    # order. ``icon`` is the plain ``icon = GFX_...`` entry; a focus with only
    # icon blocks reports the first icon named in them.
    icon_blocks: list[str] = field(default_factory=list)
    # The block key: ``focus`` inside a ``focus_tree``; a top-level block can
    # also be a ``shared_focus`` or a ``joint_focus``.
    kind: str = "focus"

    def __post_init__(self, requires) -> None:
        if requires:
            self.prerequisites.extend(_ensure_nested(requires))
        self.prerequisites = _ensure_nested(self.prerequisites)
        self.mutually_exclusive = _ensure_nested(self.mutually_exclusive)

    def icon_names(self) -> list[str]:
        """Return every icon sprite this focus can show, the plain ``icon`` first.

        Reads both block forms the game accepts:
        ``icon = { trigger = { ... } value = GFX_a }`` and
        ``icon = { GFX_a = { <trigger> } GFX_b = yes }``.
        """

        from .syntax import Assignment, parse

        names = [self.icon] if self.icon else []
        for body in self.icon_blocks:
            for item in parse(body).root.items:
                if type(item) is not Assignment or item.name == "trigger":
                    continue
                name = item.scalar if item.name == "value" else item.name
                if name and name not in names:
                    names.append(name)
        return names


@dataclass
class FocusTree:
    """A focus tree (or the top-level focuses of one file, see ``kind``) and its focuses."""

    id: str
    country_tag: str = ""
    focuses: list[Focus] = field(default_factory=list)
    path: Path | None = None
    default: bool | None = None
    continuous_focus_position: str = ""
    shared_focuses: list[str] = field(default_factory=list)
    raw_block: str = ""
    touched: bool = False
    # ``focus_tree`` for a real tree. ``file`` is the container for the
    # top-level ``focus``, ``shared_focus`` and ``joint_focus`` blocks of one
    # file: its ``id`` is the file name without extension, and the game sees no
    # tree with that ID. A file can hold real trees and one container.
    kind: str = "focus_tree"


@dataclass
class Leader:
    """The country leader as ``create_country()`` and ``update_country(leader_...=)`` set it.

    ``character_id`` is the leader's character; ``ideology`` is a
    sub-ideology such as ``"liberalism"``.
    """

    name: str
    character_id: str = ""
    ideology: str = "liberalism"
    portrait_slug: str = ""


@dataclass
class Country:
    """A country: its tag file entry, ``common/countries`` definition and starting history.

    ``popularities`` are percentages by ideology group; ``technologies``
    are the starting technologies. ``stability`` and ``war_support`` keep
    the spelling of the file. The ``raw_*``, ``*_path`` and
    ``touched_fields`` fields are bookkeeping for loaded countries.
    """

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
    leader: Leader | None = None
    ideas: list[str] = field(default_factory=list)
    research_slots: int | None = None
    stability: str | int | float | None = None
    war_support: str | int | float | None = None
    technologies: dict[str, int] = field(default_factory=dict)
    oob: str = ""
    recruited_characters: list[str] = field(default_factory=list)
    raw_definition: str = ""
    raw_history: str = ""
    raw_character: str = ""
    definition_path: Path | None = None
    history_path: Path | None = None
    character_path: Path | None = None
    touched_fields: set[str] = field(default_factory=set)
    # UI colour from ``common/countries/colors.txt``; ``None`` means "same as color".
    color_ui: tuple[int, int, int] | None = None

    def __post_init__(self):
        if not self.popularities:
            self.popularities = {
                "democratic": 100,
                "fascism": 0,
                "communism": 0,
                "neutrality": 0,
            }


@dataclass
class ValidationIssue:
    """One finding of ``Mod.validate()``: a record, not an exception.

    ``severity`` is ``"error"``, ``"warning"`` or ``"info"`` and ``code`` a
    registered validation code (see ``docs/validation.md``).
    """

    message: str
    severity: str = "error"
    code: str = ""
    file_path: str | None = None
    focus_id: str | None = None
    country_tag: str | None = None
    state_id: int | None = None
    event_id: str | None = None
    idea_id: str | None = None
    decision_id: str | None = None
    focus_tree_id: str | None = None
    ideology_id: str | None = None
    dynamic_modifier_id: str | None = None
    bookmark_name: str | None = None
    related_file_path: str | None = None
    line: int | None = None
    column: int | None = None


@dataclass
class SaveResult:
    """What ``Mod.save()`` did: the written files, the sections that had edits, and a message."""

    written_files: list[Path] = field(default_factory=list)
    dirty_sections: list[str] = field(default_factory=list)
    no_changes: bool = False
    message: str = ""

    @property
    def changed(self) -> bool:
        """Whether any file was written."""
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
    related_path: Path | None = None
    identifier: str | None = None


@dataclass
class EventText:
    """One conditional localisation key of an event title, description or option name.

    ``title = { text = KEY trigger = { ... } }`` reads as
    ``EventText(text="KEY", trigger="...")``. The game shows the first entry
    whose trigger holds.
    """

    text: str
    trigger: str = ""


@dataclass
class EventOption:
    """One option of an event: its name key, trigger, effect and AI chance.

    ``trigger``, ``effect`` and ``ai_chance`` hold block bodies without the
    outer braces.
    """

    name: str = ""
    trigger: str = ""
    effect: str = ""
    ai_chance: str = ""
    raw_block: str = ""
    touched: bool = False
    # ``name = { text = ... trigger = { ... } }`` entries, in source order.
    conditional_names: list[EventText] = field(default_factory=list)
    # Exact interior retained independently of the option's current list position.
    _source_body: str | None = field(default=None, repr=False, compare=False)


@dataclass
class Event:
    """One event.

    ``title`` and ``description`` hold the unconditional key: a plain
    ``title = KEY`` or a ``title = { text = KEY }`` without a trigger.
    ``conditional_titles`` and ``conditional_descriptions`` hold every
    ``title``/``desc`` block that has a trigger, in source order.
    """

    id: str
    title: str = ""
    description: str = ""
    event_type: str = "country_event"
    picture: str = "GFX_report_event_generic"
    is_triggered_only: bool = False
    fire_only_once: bool | None = None
    trigger: str = ""
    immediate: str = ""
    mean_time_to_happen: str = ""
    options: list[EventOption] = field(default_factory=list)
    path: Path | None = None
    raw_block: str = ""
    touched: bool = False
    touched_fields: set[str] = field(default_factory=set)
    conditional_titles: list[EventText] = field(default_factory=list)
    conditional_descriptions: list[EventText] = field(default_factory=list)


@dataclass
class OnAction:
    """One occurrence of an on-action hook (``on_startup`` and the like) in one file.

    ``events`` are fired every time; ``random_events`` is the flattened
    weight/event list; ``effect`` is a block body.
    """

    id: str
    effect: str = ""
    events: list[str] = field(default_factory=list)
    random_events: list[str] = field(default_factory=list)
    path: Path | None = None
    raw_block: str = ""
    touched: bool = False
    # On-action hooks are compositional in HOI4: the same hook may occur more
    # than once, including multiple times in one file.  These internal source
    # coordinates let the serializer patch or remove the exact occurrence
    # without collapsing its siblings.
    source_path: Path | None = None
    source_occurrence: int = -1


@dataclass
class State:
    """A state's history file: owner, cores, provinces, resources and buildings.

    ``name`` is the localisation key (``STATE_<id>``). ``buildings`` and
    ``history`` hold block bodies; ``raw_text`` is the whole file as read.
    """

    id: int
    name: str = ""
    owner: str = ""
    cores: list[str] = field(default_factory=list)
    resources: dict[str, str | int | float] = field(default_factory=dict)
    buildings: str = ""
    # Numeric properties may be given as numbers or as their script spelling.
    # Loaded states keep the spelling of the file.
    local_supplies: str | int | float = ""
    manpower: str | int | float = "0"
    state_category: str = "large_city"
    victory_points: str = ""
    buildings_max_level_factor: str | int | float = "1.0"
    is_demilitarized_zone: bool = False
    provinces: list[int] = field(default_factory=list)
    history: str = ""
    path: Path | None = None
    raw_text: str = ""
    source_path: Path | None = None

    impassable: bool = False
    # Set when a caller passes ``victory_points`` explicitly: the points are
    # then written one province/value pair per block even when the value equals
    # what the file says. Other edits leave the file's spelling alone.
    _victory_points_requested: bool = field(default=False, repr=False, compare=False)

@dataclass
class Decision:
    """One decision, mission or targeted decision of a decision category.

    Script fields hold block bodies without the outer braces. See
    ``Mod.create_decision()`` for which fields belong to missions and
    targeted decisions.
    """

    id: str
    category: str = ""
    icon: str = ""
    cost: int | None = None
    days_remove: int | None = None
    fire_only_once: bool | None = None
    available: str = ""
    visible: str = ""
    complete_effect: str = ""
    remove_effect: str = ""
    ai_will_do: str = ""
    path: Path | None = None
    raw_block: str = ""
    touched: bool = False
    # Missions (timed decisions that fire on their own).
    days_mission_timeout: int | None = None
    activation: str = ""
    timeout_effect: str = ""
    cancel_trigger: str = ""
    cancel_effect: str = ""
    is_good: bool | None = None
    selectable_mission: bool | None = None
    # Targeted decisions (one instance per country or state target).
    target_trigger: str = ""
    targets: list[str] = field(default_factory=list)
    target_array: str = ""
    target_root_trigger: str = ""
    state_target: bool | None = None
    targets_dynamic: bool | None = None
    target_non_existing: bool | None = None
    on_map_mode: str = ""
    # General decision fields.
    allowed: str = ""
    days_re_enable: int | None = None
    war_with_on_remove: str = ""
    custom_cost_trigger: str = ""
    custom_cost_text: str = ""
    cancel_if_not_visible: bool | None = None
    modifier: str = ""
    remove_trigger: str = ""
    highlight_states: str = ""
    priority: int | None = None


@dataclass
class DecisionCategory:
    """A decision category: its definition (icon, ``allowed``, ``visible``) and its decisions."""

    id: str
    icon: str = ""
    allowed: str = ""
    visible: str = ""
    decisions: list[Decision] = field(default_factory=list)
    # Category presentation belongs in common/decisions/categories while
    # decision content belongs in common/decisions. ``path`` is the
    # decision-content file new decisions go to: the last file, in load
    # order, that holds a block of the category.
    path: Path | None = None
    definition_path: Path | None = None
    raw_block: str = ""
    definition_raw_block: str = ""
    touched: bool = False
    # Every common/decisions file that holds a block of this category, in
    # load order. The game merges them all into one category.
    paths: list[Path] = field(default_factory=list)


@dataclass
class Idea:
    """An idea: a national spirit, law, advisor slot entry or other idea category member.

    ``icon`` is the bare picture name (the game adds ``GFX_idea_``);
    ``modifier`` holds the modifier values by name.
    """

    id: str
    # Compatibility name retained for the public API. HOI4's ``picture`` field
    # stores a bare stem; the engine resolves it as ``GFX_idea_<stem>``.
    icon: str = DEFAULT_IDEA_ICON
    modifier: dict[str, str | int | float | bool] = field(default_factory=dict)
    path: Path | None = None
    category: str = ""
    allowed: str = ""
    research_bonus: dict[str, str | int | float | bool] = field(default_factory=dict)
    traits: list[str] = field(default_factory=list)
    ai_will_do: str = ""
    # Compatibility-only view of unsupported idea-level ``desc`` scalars read
    # from older SDK output. Current HOI4 uses ``{idea_id}_desc`` localization.
    desc: str = ""
    removal_cost: str | int | float | None = None
    # The idea's own body as read from its file (without the braces).
    raw_block: str = ""
    # The category's own settings (``designer = yes``, ``law = yes``...), not
    # its sibling ideas. Ideas of one category share the same string; it is
    # used only to recreate the category when the idea moves to a new file.
    category_raw_block: str = ""
    touched: bool = False
    touched_fields: set[str] = field(default_factory=set)
    modifier_merge: bool = field(default=False, repr=False, compare=False)
