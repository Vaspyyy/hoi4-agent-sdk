# HOI4 Agent SDK Data Models

Dataclass fields and semantics for SDK objects. Most agents should use the `Mod` facade instead of constructing low-level data directly unless a method expects one of these objects.

## Data Models

### Focus
```python
Focus(id: str,                          # Required. Unique within tree.
      icon: str = "GFX_goal_generic_construct_civilian",
      x: int = 0, y: int = 0,          # Grid position. Must be unique in tree.
      cost: int = 10,                   # Political power cost
      prerequisites: list[list[str]] = [],      # AND of OR groups
      requires: str | list[str] | list[list[str]] | None = None,
      mutually_exclusive: list[list[str]] = [], # Same structure
      relative_position_id: str = "",   # Vanilla relative layout anchor
      search_filters: list[str] = [],   # FOCUS_FILTER_* entries
      completion_reward: str = "",      # Paradox script block content
      available: str = "",              # Trigger condition
      bypass: str = "",
      select_effect: str = "",
      complete_tooltip: str = "",
      allow_branch: str = "",
      ai_will_do: str = "",             # AI weighting
      cancel_if_invalid: bool | None = None,
      continue_if_invalid: bool | None = None,
      available_if_capitulated: bool | None = None,
      will_lead_to_war_with: str = "",
)
```

`requires` is constructor-only sugar for prerequisites. `Focus(id="B", requires="A")` becomes `prerequisites=[["A"]]`; `requires=["A", "C"]` becomes one OR group.

### FocusTree
```python
FocusTree(id: str, country_tag: str = "",
          focuses: list[Focus] = [], path: Path | None = None,
          default: bool | None = None,
          continuous_focus_position: str = "",
          shared_focuses: list[str] = [])
```

### Country
```python
Country(tag: str, name: str = "", adjective: str = "",
        color: tuple[int,int,int] = (128,128,128),
        graphical_culture: str = "western_european_gfx",
        graphical_culture_2d: str = "western_european_2d",
        capital: int = 1,
        ruling_party: str = "democratic",
        popularities: dict[str,int] = {},  # Arbitrary ideology groups; auto-fills if empty
        elections_allowed: bool = True,
        leader: Leader | None = None,
        ideas: list[str] = [],
        research_slots: int | None = None,
        stability: str|int|float|None = None,
        war_support: str|int|float|None = None,
        technologies: dict[str,int] = {},
        oob: str = "",
        recruited_characters: list[str] = [])
```

### Leader
```python
Leader(name: str, character_id: str = "",
       ideology: str = "liberalism", portrait_slug: str = "")
```

When a country is loaded, `character_id` is the exact recruited leader found in
source, not a synthesized `{TAG}_leader_1` alias. `update_country()` keeps that
identity immutable and patches the matching character block.

Country popularity keys and `ruling_party` are data-driven and may reference
custom ideology groups. The empty `popularities` default expands to the four
vanilla groups; callers creating a custom-party country should provide the
matching distribution explicitly.

The history setup fields map to `set_stability`, `set_war_support`,
`set_technology`, and `oob`. Strings are accepted for exact Paradox values;
numeric stability and war-support values round-trip as numbers.

### Character and roles

```python
Character(id: str,
          country_tag: str = "",
          name: str = "",
          portraits: list[CharacterPortrait] = [],
          roles: list[CharacterRole] = [],
          instances: list[CharacterInstance] = [],
          path: Path | None = None)

CharacterPortrait(channel: Literal["civilian", "army", "navy"] = "civilian",
                  large: str = "",
                  small: str = "")

CharacterInstance(allowed: str = "",
                  name: str = "",
                  portraits: list[CharacterPortrait] = [],
                  roles: list[CharacterRole] = [])
```

Direct `roles` and repeated `instances` are both retained. An instance's
`allowed` value is the body of the HOI4 trigger block. Loaded source coordinates
are internal fidelity metadata; mutation methods select repeated instances and
roles by zero-based occurrence.

```python
CountryLeaderRole(ideology="liberalism", expire="1965.1.1.1",
                  traits=[], id=-1)

AdvisorRole(slot="political_advisor", traits=[], cost=100,
            idea_token="", ledger="", allowed="", visible="",
            available="", ai_will_do="factor = 1")

ArmyCommanderRole(kind="corps_commander", skill=1,
                  attack_skill=1, defense_skill=1,
                  planning_skill=1, logistics_skill=1,
                  traits=[], legacy_id=-1, visible="")

NavyLeaderRole(skill=1, attack_skill=1, defense_skill=1,
               maneuvering_skill=1, coordination_skill=1,
               traits=[], legacy_id=-1, visible="")
```

Service chiefs, high command, and theorists use `AdvisorRole` with the
appropriate HOI4 `slot`. One character may hold any number of roles.

### Land order of battle

```python
Battalion(unit_type: str, x: int, y: int)

DivisionTemplate(name: str,
                 battalions: list[Battalion] = [],
                 support: list[Battalion] = [],
                 division_names_group: str = "",
                 priority: int | None = None)

DivisionUnit(division_template: str,
             location: int,
             name: str = "",
             name_order: int | None = None,
             start_experience_factor: float | None = None,
             start_equipment_factor: float | None = None)

ShipEquipment(equipment_type: str,
              amount: int = 1,
              owner: str = "",
              creator: str = "",
              version_name: str = "")

Ship(name: str,
     definition: str,
     equipment: list[ShipEquipment] = [],
     pride_of_the_fleet: bool = False)

TaskForce(name: str,
          location: int,
          ships: list[Ship] = [])

Fleet(name: str,
      naval_base: int,
      task_forces: list[TaskForce] = [])

AirWing(location: int,
        equipment_type: str,
        amount: int,
        owner: str = "",
        creator: str = "",
        version_name: str = "")

OOBReference(name: str,
             kind: Literal["land", "naval", "air", "mixed"],
             required_dlc: tuple[str, ...] = (),
             excluded_dlc: tuple[str, ...] = ())

EquipmentVariant(name: str,
                 equipment_type: str,
                 name_group: str = "",
                 parent_version: int = 0,
                 upgrades: dict[str, int] = {},
                 modules: dict[str, str] = {},
                 required_dlc: tuple[str, ...] = (),
                 excluded_dlc: tuple[str, ...] = ())

OrderOfBattle(name: str,
              country_tag: str = "",
              templates: list[DivisionTemplate] = [],
              divisions: list[DivisionUnit] = [],
              fleets: list[Fleet] = [],
              air_wings: list[AirWing] = [],
              kind: Literal["land", "naval", "air", "mixed"] = "land",
              required_dlc: tuple[str, ...] = (),
              excluded_dlc: tuple[str, ...] = (),
              path: Path | None = None)
```

Land templates/divisions, naval fleets/task forces/ships, and air wings are
modeled for authoring in separate, correctly assigned files. `OOBReference`
records the engine assignment kind and DLC conditions. `EquipmentVariant`
models country-history `create_equipment_variant` definitions needed by Man
the Guns hulls. Production and unknown blocks remain in `raw_text`; comments,
ordering, and untouched modeled blocks remain source-preserved.

### ContentLivenessReport and GameScriptVocabulary

`ContentLivenessReport` exposes reachable/unreachable focuses, fired/unfired
events, granted/ungranted ideas, flag reads/writes and asymmetries, used/unused
localization, and structured warning findings. `GameScriptVocabulary` maps
effect, trigger, and modifier names to immutable `ScriptTokenInfo` records with
supported scopes, categories, installed-game usage counts, and usage domains.
Effect and trigger scopes come from the shipped generated documentation;
modifier applicability is expressed through `categories`. Semantic validation
tracks the current scope through explicit tag, state, character, and iterator
blocks before comparing a token with `supported_scopes`.
`ContentLivenessReport.analysis_scope` is `"structural"`, and
`proves_dynamic_achievability` is false: the graph does not solve popularity or
variable arithmetic.

### CountryPackageReport and TerritoryComponent

```python
CountryPackageReport(tag: str,
                     findings: tuple[ValidationError, ...] = (),
                     lifecycle: str = "starting",
                     advisor_count: int = 0,
                     commander_count: int = 0,
                     character_count: int = 0,
                     owned_state_count: int = 0,
                     runtime_state_ids: tuple[int, ...] = (),
                     activation_sources: tuple[str, ...] = (),
                     analysis_scope: str = "structural",
                     proves_dynamic_achievability: bool = False)

TerritoryComponent(state_ids: tuple[int, ...],
                   province_ids: tuple[int, ...],
                   land_province_count: int,
                   contains_capital: bool = False)
```

`CountryPackageReport.lifecycle` distinguishes scenario-start countries from
runtime-released countries and unresolved generated tags.
`runtime_state_ids` and `activation_sources` expose the evidence used for
automatic classification. `.errors`, `.warnings`, `.complete`, and `.to_dict()`
are derived views. `.complete` and `.structurally_complete` are intentionally
structural and do not prove a runtime threshold chain can fire.
`TerritoryComponent` values returned by `find_disconnected_states()` or
`find_enclosed_foreign_states()` represent disconnected owned land or enclosed
foreign land after threshold and allowlist filtering.

### State
```python
State(id: int, name: str = "", owner: str = "",
      cores: list[str] = [],
      resources: dict[str, str|int|float] = {},
      buildings: str = "",
      local_supplies: str = "",
      manpower: str = "0",
      state_category: str = "large_city",
      victory_points: str = "",
      buildings_max_level_factor: str = "1.0",
      is_demilitarized_zone: bool = False,
      provinces: list[int] = [],
      history: str = "",
      path: Path | None = None)
```

`State.impassable` is an optional boolean (default `False`); set it by keyword
to mark a state impassable without changing existing positional arguments.

### Decision
```python
Decision(id: str, category: str = "",
         icon: str = "",
         cost: int | None = None,
         days_remove: int | None = None,
         fire_only_once: bool | None = None,
         available: str = "",
         visible: str = "",
         complete_effect: str = "",
         remove_effect: str = "",
         ai_will_do: str = "",
         path: Path | None = None)
```

### DecisionCategory
```python
DecisionCategory(id: str, icon: str = "",
                 allowed: str = "",
                 visible: str = "",
                 decisions: list[Decision] = [],
                 path: Path | None = None,             # decision content
                 definition_path: Path | None = None)  # category metadata
```

### Event
```python
Event(id: str, title: str = "", description: str = "",
      event_type: str = "country_event",
      picture: str = "GFX_report_event_generic",
      is_triggered_only: bool = False,
      fire_only_once: bool | None = None,
      trigger: str = "", immediate: str = "",
      mean_time_to_happen: str = "",
      options: list[EventOption] = [], path: Path | None = None)
```

### EventOption
```python
EventOption(name: str = "", trigger: str = "", effect: str = "",
            ai_chance: str = "")
```

Valid event types are `country_event`, `state_event`, `news_event`,
`unit_leader_event`, and `operative_leader_event`.

### OnAction
```python
OnAction(id: str,
         effect: str = "",
         events: list[str] = [],
         random_events: list[str] = [],
         path: Path | None = None)
```

An ID is a compositional HOI4 hook, not a globally unique object ID. Use
`Mod.get_on_action_occurrences(id)` when the hook is extended in multiple source blocks;
`source_path` and `source_occurrence` on loaded models are internal fidelity metadata.

### Idea
```python
Idea(id: str, icon: str = "generic_political_support",
     modifier: dict[str, str|int|float|bool] = {},
     path: Path | None = None,
     category: str = "",                # e.g. industrial_concern, political_advisor
     allowed: str = "",
     research_bonus: dict[str, str|int|float|bool] = {},
     traits: list[str] = [],
     ai_will_do: str = "",
     desc: str = "",                    # legacy unsupported source compatibility
     removal_cost: str|int|float|None = None)
```

At the facade level, a supplied `modifier` replaces the mapping by default;
`modifier={}` removes the block. Pass `merge_modifier=True` to `update_idea()`
or `ensure_idea()` for key-by-key merging.
Idea descriptions use `{idea_id}_desc` localization and are not serialized as a
field inside the idea definition.
`icon` is retained as the compatibility field name, but its value is a bare
HOI4 `picture` stem. For example, `generic_political_support` resolves the
interface sprite `GFX_idea_generic_political_support`. Prefixed API input is
accepted and normalized; serialized `picture` values never include
`GFX_idea_`.

### Ideology and SubIdeology

```python
SubIdeology(name: str, can_be_randomly_selected: bool = True)

Ideology(id: str,
         color: tuple[int, ...] = (128, 128, 128),
         types: list[SubIdeology] = [],
         rules: dict[str, str] = {},
         modifiers: dict[str, str] = {},
         hidden_modifiers: dict[str, str] = {},
         faction_modifiers: dict[str, str] = {},
         dynamic_faction_names: list[str] = [],
         ai_behavior: str = "",
         ai_ideology_wanted_units_factor: str | int | float | None = "1.0",
         ai_give_core_state_control_threshold: str | int | float | None = "0",
         war_impact_on_world_tension: str | int | float | None = "0.25",
         faction_impact_on_world_tension: str | int | float | None = "0.1",
         can_host_government_in_exile: bool = False,
         can_collaborate: bool = False,
         effects: list[str] = [])
```

The four numeric scalar fields accept strings to preserve exact Paradox source
formatting, as well as `int` and `float` values. Set one to `None` to omit it
from a new definition or remove its existing assignment during an update.
The loader retains the source color's channel count so validation can report a
malformed non-RGB value instead of silently truncating it; newly created
ideologies accept an RGB tuple.

### DynamicModifier

```python
DynamicModifier(id: str,
                icon: str = "",
                enable: str = "",
                remove_trigger: str = "",
                attacker_modifier: bool|None = None,
                modifier: dict[str, str|int|float|bool] = {},
                path: Path|None = None)
```

Dynamic modifiers are top-level entries in `common/dynamic_modifiers/*.txt`.
`modifier` contains their direct modifier assignments; there is no surrounding
`modifier = { }` block. `dynamic_country_ideas` and grouped dynamic ideas are
not HOI4 constructs.

### Bookmark and BookmarkCountry

```python
BookmarkCountry(tag: str, history: str = "", ideology: str = "",
                available: str = "", required_dlc: list[str] = [],
                ideas: list[str] = [], focuses: list[str] = [])

Bookmark(name: str, description: str = "", date: str = "1936.1.1.12",
         picture: str = "GFX_select_date_1936", default_country: str = "",
         default: bool | None = None, filters: str = "",
         effect: str = "randomize_weather = 22345",
         countries: list[BookmarkCountry] = [])
```

Bookmark countries are ordered rather than keyed by tag because vanilla may
repeat a country for DLC variants. The bookmark mutation methods use a
zero-based `occurrence` argument to target one repeated tag. New bookmarks use
`randomize_weather = 22345`; replacing `effect` must preserve a top-level
`randomize_weather` assignment.

Loaded source-backed models retain raw text and, where needed, field-touch
metadata internally. Callers should mutate them through `Mod.update_*` methods
so serializers know which source spans to patch.
