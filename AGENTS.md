# AGENTS.md — HOI4 Agent SDK Reference

Complete reference for AI agents using the `hoi4` Python SDK to programmatically create and modify Hearts of Iron 4 mods.

## Installation

```bash
python -m venv venv
./venv/bin/pip install -e .
```

Requires Python >=3.11. Zero runtime dependencies.

```python
from hoi4 import Mod, Focus, FocusTree, Country, Leader, State
from hoi4 import Event, EventOption, Idea, ValidationError
from hoi4 import EFFECT_CATEGORIES, MODIFIER_CATEGORIES, TECHNOLOGY_CATEGORIES
from hoi4 import PdxNode, parse_pdx, serialize_pdx
from hoi4 import Config, find_config
```

## Configuration

The SDK uses `.hoi4.json` config files to store paths. Create one in your project root:

```json
{
  "mod_path": "/path/to/my_mod",
  "hoi4_install": "/path/to/Hearts of Iron IV"
}
```

Then use `Mod.from_config()` — it searches from the current directory upward:

```python
mod = Mod.from_config()            # searches from cwd
mod = Mod.from_config("/project")  # searches from /project
```

You can also create a config programmatically:

```python
cfg = Config(mod_path="/path/to/mod", hoi4_install="/path/to/hoi4")
cfg.save(".hoi4.json")
```

If no config file exists, use `Mod()` directly with explicit paths:

```python
mod = Mod("/path/to/my_mod", hoi4_install="/path/to/hoi4")
```

## Core Workflow

All mutations are **batched** — nothing touches disk until you call `save()`. The standard loop:

For one-off agent automation, create the Python driver script outside this SDK repository. Use `/tmp/hoi4-agent-scripts/` for temporary scripts and run them with the working directory set to the mod project/config directory containing `.hoi4.json`. If a reusable script is explicitly requested, put it in that mod project's own `scripts/` or `tools/` directory, not in `/home/ransom/Projekte/hoi4-agent-sdk`.

```bash
mkdir -p /tmp/hoi4-agent-scripts
cd /path/to/mod-project-with-hoi4-json
/home/ransom/Projekte/hoi4-agent-sdk/venv/bin/python /tmp/hoi4-agent-scripts/add_focus_branch.py
```

```python
mod = Mod("/path/to/my_mod", hoi4_install="/opt/steam/hoi4")

# Read existing data
tree = mod.get_focus_tree("german_focus")

# Mutate in memory
mod.add_focus("german_focus", Focus(id="GER_new", x=5, y=3))
mod.set_loc("GER_new", "New Focus")

# Validate before saving
errors = mod.validate()
if errors:
    for e in errors:
        print(f"[{e.severity}] {e.message}")

# Preview changes as unified diff
print(mod.preview())

# Write to disk
mod.save()

# Or throw away changes
# mod.discard()
```

**Key points:**
- `Mod()` loads country tags, focus trees, events, ideas, and localization eagerly. States are indexed but loaded lazily on first access via `get_state()`.
- All `get_*` / `list_*` methods read from the in-memory cache.
- `save()` writes only dirty sections, updates internal snapshots, and clears the dirty set.
- `discard()` clears all caches and reloads from disk.
- `preview()` returns unified diffs against the last-saved or originally-loaded content.
- `hoi4_install` is optional — needed only for vanilla fallback (states, country definitions).
- `mod.mod_root` (Path) — the mod directory path.
- `mod.hoi4_install` (Path | None) — the vanilla HOI4 install path.

## Countries

Country data spans 5 files: tag registration, definition (color/culture), history (capital/politics/leader), characters, and localization.

### Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `list_countries() -> list[str]` | Sorted tag list | All loaded country tags |
| `is_country_tag_available(tag: str) -> bool` | `bool` | False if the tag exists in the mod or vanilla install |
| `country_tag_conflicts(tag: str) -> list[str]` | `list[str]` | Returns conflict sources: `"mod"`, `"vanilla"` |
| `get_country(tag: str) -> Country` | `Country` | Cached or reads from disk. Tries mod then vanilla install. |
| `create_country(tag, name, adjective="", color=(128,128,128), capital=1, ruling_party="democratic", popularities=None, leader_name="Leader", leader_ideology="liberalism", ideas=None, overwrite=False, allow_vanilla_override=False) -> Country` | `Country` | Creates country with leader, caches, marks dirty. Raises `ValueError` for existing mod tags or vanilla tags unless explicitly allowed. |
| `update_country(tag: str, **kwargs) -> bool` | `bool` | Update any Country/Leader field. Use `leader_name`, `leader_ideology` for leader. |
| `delete_country(tag: str) -> bool` | `bool` | Remove from cache |

### Example

```python
mod.create_country("WST", "Westralia", adjective="Westralian",
                   color=(59, 130, 246), capital=345,
                   ruling_party="democratic",
                   popularities={"democratic": 60, "fascism": 20, "communism": 10, "neutrality": 10},
                   leader_name="John Curtin", leader_ideology="liberalism")

mod.update_country("WST", capital=999, leader_name="New Leader")
```

Check tag availability before inventing a new country tag:

```python
if not mod.is_country_tag_available("SIC"):
    raise ValueError(f"SIC conflicts with: {mod.country_tag_conflicts('SIC')}")
```

### Country File Layout

| File | Path |
|------|------|
| Tag registration | `common/country_tags/00_generated_tags.txt` |
| Definition | `common/countries/WST.txt` |
| History | `history/countries/WST - Westralia.txt` |
| Characters | `common/characters/WST_characters.txt` |
| Localization | `localisation/english/WST_country_l_english.yml` |

`save()` auto-generates localization keys: `WST`, `WST_DEF`, `WST_neutrality`, `WST_ADJ`, and leader name keys.

## States

State files live in `history/states/`. When accessing a state not in the mod, `get_state()` will copy it from the vanilla install if `hoi4_install` is set.

### Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `list_states() -> list[int]` | Sorted state IDs | All loaded state IDs |
| `get_state(state_id: int) -> State` | `State` | Cached or reads from mod/vanilla. Raises `KeyError`. |
| `set_state_owner(state_id: int, tag: str, add_core: bool = True) -> State` | `State` | Change owner, optionally add core |
| `set_state_properties(state_id: int, **kwargs) -> bool` | `bool` | Set any State field. List fields such as `cores` and `provinces` append unique values instead of replacing. |
| `add_state_core(state_id: int, tag: str) -> State` | `State` | Append a core without replacing existing cores |
| `remove_state_core(state_id: int, tag: str) -> State` | `State` | Remove a core |
| `batch_set_owner(state_ids: list[int], tag: str, add_core: bool = True) -> list[State]` | `list[State]` | Batch owner change |

### Example

```python
mod.set_state_owner(52, "GER")
mod.add_state_core(52, "AUT")
mod.batch_set_owner([1, 2, 3], "SOV", add_core=True)
mod.set_state_properties(52, manpower="5000000", victory_points="3620 1")
```

Loaded state files are patched through their original parsed content. Updating owner, cores, manpower, resources, buildings, or other modeled fields preserves unrelated vanilla data such as buildings, resources, local supplies, history bookmarks, resistance, and compliance blocks.

## Events

Events are grouped into files by namespace. Each event has a type, trigger, options, and optional mean_time_to_happen. Dotted event IDs infer their namespace automatically: `create_event("sic.1")` writes `add_namespace = sic` to `events/sic_events.txt`.

### Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `list_events() -> list[str]` | Sorted event IDs | All loaded events |
| `get_event(event_id: str) -> Event` | `Event` | Raises `KeyError` with available IDs if not found |
| `create_event(event_id, title="", description="", event_type="country_event", picture="GFX_report_event_generic", is_triggered_only=False, fire_only_once=None, trigger="", immediate="", mean_time_to_happen="", options=None) -> Event` | `Event` | Creates event. Default option auto-generated if none provided. Dotted IDs infer namespace from the ID prefix. |
| `update_event(event_id: str, **kwargs) -> bool` | `bool` | Update any Event field |
| `delete_event(event_id: str) -> bool` | `bool` | Remove event and its namespace mapping |
| `set_event_namespace(event_id: str, namespace: str) -> None` | `None` | Set which file this event writes to (`{namespace}_events.txt`) |
| `add_event_option(event_id: str, option: EventOption) -> bool` | `bool` | Append an option to an event |

### Example

```python
evt = mod.create_event("my_mod.1", title="my_mod.1.t", description="my_mod.1.d",
                       is_triggered_only=True)
mod.add_event_option("my_mod.1", EventOption(
    name="my_mod.1.a",
    effect="add_political_power = 100"
))
# Optional; already inferred for "my_mod.1".
mod.set_event_namespace("my_mod.1", "my_mod")
```

## Decisions

Decisions live in `common/decisions/*.txt` and are grouped by category.

### Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `list_decision_categories() -> list[str]` | Sorted category IDs | Loaded decision categories |
| `list_decisions() -> list[str]` | Sorted decision IDs | Loaded decisions |
| `get_decision(decision_id: str) -> Decision` | `Decision` | Get a decision |
| `get_decision_category(category_id: str) -> DecisionCategory` | `DecisionCategory` | Get a category |
| `create_decision_category(category_id, icon="", allowed="", visible="", path=None) -> DecisionCategory` | `DecisionCategory` | Create or route a category |
| `create_decision(category_id, decision_id, icon="", cost=None, days_remove=None, fire_only_once=None, available="", visible="", complete_effect="", remove_effect="", ai_will_do="", path=None) -> Decision` | `Decision` | Create a decision in a category |
| `update_decision(decision_id: str, **kwargs) -> bool` | `bool` | Update a decision |
| `delete_decision(decision_id: str) -> bool` | `bool` | Remove a decision |

### Example

```python
mod.create_decision(
    "lux_industrial_policy",
    "LUX_subsidize_arbed",
    cost=25,
    days_remove=30,
    available="has_war = no",
    complete_effect=Mod.effect_add_civilian_factory(8, 1),
)
mod.set_loc("LUX_subsidize_arbed", "Subsidize ARBED")
mod.set_loc("LUX_subsidize_arbed_desc", "Support the domestic steel industry.")
```

### Event Types

Valid `event_type` values: `"country_event"`, `"state_event"`, `"news_event"`.

### Event File Layout

Events without an explicit `path` write to `events/{namespace}_events.txt`. Events with no namespace write to `events/mod_events.txt`.

## Ideas

Ideas (national spirits, advisors, etc.) store typed modifier dicts. Idea files are loaded from both `common/national_ideas/` and `common/ideas/`. Both `country_ideas = { }` and `ideas = { }` container formats are supported.

Without an explicit `path`, new ideas write to `common/national_ideas/mod_ideas.txt`. To write to a country's existing idea file (e.g., `common/ideas/luxembourg.txt`), pass the `path` parameter.

### Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `list_ideas() -> list[str]` | Sorted idea IDs | All loaded ideas |
| `get_idea(idea_id: str) -> Idea` | `Idea` | Raises `KeyError` if not found |
| `create_idea(idea_id, icon="GFX_idea_generic", modifier=None, path=None) -> Idea` | `Idea` | Creates idea. `modifier` is `dict[str, str|int|float|bool]`. Optional `path` sets target file. |
| `update_idea(idea_id: str, **kwargs) -> bool` | `bool` | Update fields. `modifier` kwarg **merges** into existing dict. |
| `delete_idea(idea_id: str) -> bool` | `bool` | Remove from cache |

### Example

```python
mod.create_idea("strong_economy", modifier={
    "industrial_capacity_factory": 0.10,
    "consumer_goods_factor": -0.05,
    "research_time_factor": -0.03,
})
mod.update_idea("strong_economy", modifier={"political_power_gain": 0.25})

# Write to a country-specific ideas file
mod.create_idea("lux_steel", modifier={"industrial_capacity_factory": 0.05},
                path="common/ideas/luxembourg.txt")
```

Use `MODIFIER_CATEGORIES` to discover available modifier keys (see Catalogs section).

## Focus Trees

Focus trees are collections of focuses. Each focus has a position (x, y), cost, prerequisites, and completion reward.

### Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `list_focus_trees() -> list[str]` | Sorted tree IDs | All loaded tree IDs |
| `get_focus_tree(tree_id: str) -> FocusTree` | `FocusTree` | Raises `KeyError` if not found |
| `create_focus_tree(tree_id: str, country_tag: str) -> FocusTree` | `FocusTree` | Creates empty tree linked to tag |
| `update_focus_tree(tree_id: str, **kwargs) -> bool` | `bool` | Update tree-level properties like `continuous_focus_position`, `default`, or `shared_focuses` |
| `delete_focus_tree(tree_id: str) -> bool` | `bool` | Remove tree |
| `add_focus(tree_id: str, focus: Focus) -> None` | `None` | Append focus to tree |
| `remove_focus(tree_id: str, focus_id: str) -> bool` | `bool` | Remove focus by ID |
| `get_focus(tree_id: str, focus_id: str) -> Focus \| None` | `Focus \| None` | Find focus in tree |
| `update_focus(tree_id: str, focus_id: str, **kwargs) -> bool` | `bool` | Update any Focus field |
| `insert_focus_after(tree_id, anchor_focus_id, focus, add_prerequisite=True, relative_position=True) -> None` | `None` | Insert a focus after an existing focus and optionally wire prerequisite/relative positioning |
| `insert_branch(tree_id, anchor_focus_id, focuses, chain_prerequisites=True) -> None` | `None` | Insert a vertical branch after an anchor focus |
| `append_to_focus_reward(tree_id, focus_id, effect) -> bool` | `bool` | Append an effect to an existing focus reward |
| `set_focus_loc(focus_id, name, description, file_path=None) -> None` | `None` | Set both focus name and description localization |
| `create_industrial_branch(tree_id, tag, anchor_focus_id=None, state_id=None, grounded=True) -> list[Focus]` | `list[Focus]` | Generate a small grounded industrial branch with localization |

### Prerequisites

Prerequisites are `list[list[str]]` — outer list is AND, inner lists are OR groups:

```python
Focus(
    id="GER_anschluss",
    x=10, y=5, cost=10,
    prerequisites=[["GER_rhineland"]],            # requires Rhineland
    mutually_exclusive=[["GER_other_option"]],     # mutually exclusive with
    relative_position_id="GER_rhineland",
    search_filters=["FOCUS_FILTER_INDUSTRY"],
    completion_reward="transfer_state = 52",
    available="has_war = no",
    bypass="has_full_control_of_state = 52",
    ai_will_do="factor = 1",
)
```

Multiple OR prerequisites: `prerequisites=[["GER_a", "GER_b"], ["GER_c"]]` means "(A OR B) AND C".

### Example

```python
tree = mod.create_focus_tree("west_focus", "WST")
mod.add_focus("west_focus", Focus(id="WST_independence", x=5, y=0, cost=10))
mod.add_focus("west_focus", Focus(
    id="WST_fortify", x=5, y=1, cost=10,
    prerequisites=[["WST_independence"]],
    completion_reward="add_building_construction = { type = bunker level = 3 province = 3620 instant_build = yes }",
))
```

Focus tree files write to `common/national_focus/{TAG}_focus.txt`.

## Localization

Localization uses HOI4 YML format (`l_english:` header, ` KEY:0 "value"` entries). All keys and values are stored in a flat dict.

### Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `get_loc(key: str) -> str \| None` | `str \| None` | Get localized string |
| `set_loc(key: str, value: str, file_path=None) -> None` | `None` | Set entry. Routes to `file_path` or `default_loc_file`. |
| `delete_loc(key: str) -> bool` | `bool` | Remove entry |
| `search_loc(query: str) -> dict[str, str]` | `dict` | Case-insensitive substring search in keys and values |
| `all_loc() -> dict[str, str]` | `dict` | Full copy of all entries |

### Key Conventions

- Pass keys **without** `:0` suffix — the serializer adds it automatically
- Focus names: `"FOCUS_ID"`
- Event titles: `"EVENT_ID.t"`
- Country names: `"TAG"`, `"TAG_DEF"`, `"TAG_ADJ"`, `"TAG_fascism"`, etc.
- Leader names: `"CHARACTER_ID"`
- When reading existing YML files, keys come back **with** `:0` included (e.g., `"GER_anschluss:0"`) — both forms work for `get_loc()`/`set_loc()`

### Example

```python
mod.set_loc("WST_independence", "Declare Independence")
mod.set_loc("WST_independence_desc", "The time has come to stand alone.")
mod.search_loc("independence")
```

Default file: `localisation/english/mod_l_english.yml`.

## Validation

`mod.validate()` runs all validators and returns `list[ValidationError]`. Does **not** raise — returns empty list if all valid.

### What Gets Checked

| Entity | Checks |
|--------|--------|
| **Country** | Tag format `^[A-Z0-9]{3}$`, has name, popularities sum to 100, valid ruling party, valid RGB color, leader ideology/ruling party mismatch |
| **State** | Has ID, has owner, owner is known tag, cores are known tags |
| **Event** | Has ID, has title, has description, has options, valid event_type, namespace matches dotted ID, unsafe bare core effects, invalid tech categories, triggered-only MTTH contradiction, war declarations in immediate |
| **Idea** | Has ID, has modifiers |
| **Focus tree** | No duplicate IDs, no duplicate (x,y) positions, prerequisite references exist, mutually_exclusive references exist, unsafe bare core effects, invalid tech categories |
| **Cross-cut** | Every focus has a localization entry (warning) |

### ValidationError Fields

```python
@dataclass
class ValidationError:
    message: str            # Human-readable description
    severity: str = "error" # "error" or "warning"
    file_path: str | None   # Source file if known
    focus_id: str | None    # Focus ID if relevant
    country_tag: str | None # Country tag if relevant
    state_id: int | None    # State ID if relevant
    event_id: str | None    # Event ID if relevant
    idea_id: str | None     # Idea ID if relevant
```

**Errors** mean the mod will crash or behave incorrectly. **Warnings** mean potential issues (missing localization, etc.).

## Preview & Diff

`mod.preview()` returns a unified diff string comparing in-memory state against the last-saved or originally-loaded file contents. Returns empty string if nothing is dirty.

```python
diff = mod.preview()
if diff:
    print(diff)
    # Output like:
    # --- a/common/national_focus/GER_focus.txt
    # +++ b/common/national_focus/GER_focus.txt
    # @@ -10,3 +10,8 @@
    #  }
    # +    focus = {
    # +        id = GER_new
    # +        x = 5
    # +        y = 3
    # +        cost = 10
```

Only dirty sections produce diffs. After `save()`, `preview()` returns empty until further changes.

## Data Models

### Focus
```python
Focus(id: str,                          # Required. Unique within tree.
      icon: str = "GFX_goal_generic_construct_civilian",
      x: int = 0, y: int = 0,          # Grid position. Must be unique in tree.
      cost: int = 10,                   # Political power cost
      prerequisites: list[list[str]] = [],      # AND of OR groups
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
        popularities: dict[str,int] = {},  # Auto-fills if empty
        elections_allowed: bool = True,
        leader: Leader | None = None,
        ideas: list[str] = [])
```

### Leader
```python
Leader(name: str, character_id: str = "",
       ideology: str = "liberalism", portrait_slug: str = "")
```

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
                 path: Path | None = None)
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

### Idea
```python
Idea(id: str, icon: str = "GFX_idea_generic",
     modifier: dict[str, str|int|float|bool] = {},
     path: Path | None = None,
     category: str = "",                # e.g. industrial_concern, political_advisor
     allowed: str = "",
     research_bonus: dict[str, str|int|float|bool] = {},
     traits: list[str] = [],
     ai_will_do: str = "")
```

## Effects & Modifiers Catalogs

The SDK ships static catalogs for discovering available effects and modifiers.

### EFFECT_CATEGORIES

```python
from hoi4 import EFFECT_CATEGORIES

# Structure: list[tuple[str, list[tuple[str, str]]]]
# (category_name, [(display_name, paradox_script), ...])

for cat_name, effects in EFFECT_CATEGORIES:
    for display_name, script in effects:
        print(f"{display_name}: {script}")
```

**Categories:** Political, Ideology, Economy, Military, Diplomacy, Territory, War Goals, Technology, Custom.

Example entries:
```python
("Political Power (+100)", "add_political_power = 100")
("Civilian Factory (+1)", "add_building_construction = { type = industrial_complex level = 1 instant_build = yes }")
("Add Core to State", "123 = { add_core_of = TAG }")
("Create Faction", 'create_faction = "My Faction"')
```

Do not use bare `add_core_of = TAG` or `remove_core_of = TAG` inside country-scope event effects or focus rewards. Scope them to a state:

```python
Mod.effect_add_state_core(115, "SIC")      # 115 = { add_core_of = SIC }
Mod.effect_remove_state_core(115, "SIC")   # 115 = { remove_core_of = SIC }
```

### TECHNOLOGY_CATEGORIES

```python
from hoi4 import TECHNOLOGY_CATEGORIES

print("infantry_weapons" in TECHNOLOGY_CATEGORIES)  # True
print("infantry" in TECHNOLOGY_CATEGORIES)          # False; this is not a tech bonus category

Mod.effect_add_tech_bonus("rifle_bonus", category="infantry_weapons", uses=1, bonus=0.5)
```

Common valid `add_tech_bonus` categories include `industry`, `infantry_weapons`, `artillery`, `armor`, `electronics`, and `land_doctrine`.

### MODIFIER_CATEGORIES

```python
from hoi4 import MODIFIER_CATEGORIES

# Structure: list[tuple[str, list[tuple[str, str, str]]]]
# (category_name, [(display_name, modifier_key, example_value), ...])

for cat_name, modifiers in MODIFIER_CATEGORIES:
    for display_name, key, value in modifiers:
        print(f"{key} = {value}  # {display_name}")
```

**Categories:** Economy, Politics, Army, Navy, Air, Intelligence, Manpower & Occupation, Research — 65 total entries.

Example entries:
```python
("Industrial Capacity Factory", "industrial_capacity_factory", "0.05")
("Political Power Gain", "political_power_gain", "0.25")
("Army Attack Factor", "army_attack_factor", "0.05")
```

## File Path Conventions

All paths relative to `mod_root`:

```
mod_root/
  common/
    country_tags/
      00_generated_tags.txt          # Tag registration (appended)
    countries/
      {TAG}.txt                      # Country definitions
    characters/
      {TAG}_characters.txt           # Leader/character definitions
    national_focus/
      {TAG}_focus.txt                # Focus trees
    national_ideas/
      mod_ideas.txt                  # Ideas (default container)
    ideas/
      {TAG}.txt                      # Country-specific ideas (alternate container)
  history/
    countries/
      "{TAG} - {Name}.txt"           # Country history
    states/
      "{ID} - {Name}.txt"            # State definitions
  events/
      {namespace}_events.txt         # Event files
  localisation/
    english/
      mod_l_english.yml              # Default localization target
      {TAG}_country_l_english.yml    # Country-specific localization
```

## Common Recipes

### Create a New Country with Focus Tree

```python
mod = Mod("/path/to/my_mod")

mod.create_country("ZAR", "Zarland", adjective="Zarlandian",
                   color=(200, 50, 50), capital=100,
                   leader_name="General Zar", leader_ideology="despotism")
mod.set_loc("ZAR", "Zarland")
mod.set_loc("ZAR_DEF", "Zarland")
mod.set_loc("ZAR_ADJ", "Zarlandian")

tree = mod.create_focus_tree("zar_focus", "ZAR")
mod.add_focus("zar_focus", Focus(id="ZAR_militarize", x=5, y=0, cost=10,
    completion_reward="add_army_experience = 25"))
mod.add_focus("zar_focus", Focus(id="ZAR_conquer", x=5, y=1, cost=10,
    prerequisites=[["ZAR_militarize"]],
    completion_reward="create_wargoal = { type = annex_everything target = NEI }"))

errors = mod.validate()
assert all(e.severity == "warning" for e in errors), f"Errors: {errors}"
mod.save()
```

### Modify Vanilla State Ownership

```python
mod = Mod("/path/to/my_mod", hoi4_install="/opt/steam/hoi4")

mod.set_state_owner(52, "GER")
mod.set_state_properties(52, cores=["GER", "AUT"])

print(mod.preview())
mod.save()
```

### Add National Spirit with Modifiers

```python
mod = Mod("/path/to/my_mod")

mod.create_idea("great_depression", modifier={
    "consumer_goods_factor": 0.30,
    "political_power_gain": -0.50,
    "industrial_capacity_factory": -0.25,
})
mod.set_loc("great_depression", "The Great Depression")
mod.set_loc("great_depression_desc", "Economic collapse grips the nation.")

mod.save()
```

### Create Event Chain

```python
mod = Mod("/path/to/my_mod")

mod.create_event("rebellion.1",
    title="rebellion.1.t", description="rebellion.1.d",
    is_triggered_only=True,
    options=[
        EventOption(name="rebellion.1.a", effect="add_political_power = -50"),
        EventOption(name="rebellion.1.b", effect="civilwar = { ideology = fascism }"),
    ])
mod.set_event_namespace("rebellion.1", "rebellion")

mod.create_event("rebellion.2",
    title="rebellion.2.t", description="rebellion.2.d",
    is_triggered_only=True,
    trigger="has_civil_war = yes",
    options=[
        EventOption(name="rebellion.2.a", effect="add_stability = 0.10"),
    ])
mod.set_event_namespace("rebellion.2", "rebellion")

for key, val in [
    ("rebellion.1.t", "Rebellion!"),
    ("rebellion.1.d", "Unrest has boiled over into open revolt."),
    ("rebellion.1.a", "Suppress them"),
    ("rebellion.1.b", "Let the civil war begin"),
    ("rebellion.2.t", "Aftermath"),
    ("rebellion.2.d", "The fighting has died down."),
    ("rebellion.2.a", "Rebuild"),
]:
    mod.set_loc(key, val)

mod.save()
```

### Batch State Transfer Between Countries

```python
mod = Mod("/path/to/my_mod", hoi4_install="/opt/steam/hoi4")

states_to_transfer = [1, 2, 3, 4, 5, 6]
mod.batch_set_owner(states_to_transfer, "SOV", add_core=True)

print(mod.preview())
mod.save()
```

### Read Existing Mod Data and Add Focus

```python
mod = Mod("/path/to/my_mod")

tree = mod.get_focus_tree("german_focus")
existing = mod.get_focus("german_focus", "GER_anschluss")

mod.add_focus("german_focus", Focus(
    id="GER_super_focus",
    x=existing.x, y=existing.y + 1,
    cost=15,
    prerequisites=[["GER_anschluss"]],
    completion_reward="""
        add_political_power = 200
        add_army_experience = 50
        transfer_state = 52
    """,
))
mod.set_loc("GER_super_focus", "Total German Domination")

errors = mod.validate()
print(mod.preview())
mod.save()
```

### Agent-Safe Branch Editing

Prefer patch-style helpers over manual tree rewrites when modifying existing vanilla-style focus trees:

```python
branch = [
    Focus(id="LUX_modernize_transport_links", x=0, y=1,
          icon="GFX_goal_generic_construct_infrastructure",
          completion_reward=Mod.effect_add_infrastructure(8, 1)),
    Focus(id="LUX_support_domestic_steel", x=0, y=1,
          icon="GFX_goal_generic_production",
          completion_reward="\n".join([
              Mod.effect_add_civilian_factory(8, 1),
              Mod.effect_add_industry_bonus("LUX_steel_bonus", uses=1),
          ])),
]

mod.insert_branch("lux_focus", "LUX_existing_industry_anchor", branch)
for focus in branch:
    mod.set_focus_loc(focus.id, "Readable Name", "Readable description.")
```

For a quick grounded industrial branch:

```python
mod.create_industrial_branch("lux_focus", "LUX", anchor_focus_id="LUX_existing_anchor")
```

Use `get_country_context("LUX")` before generating content. It returns country basics, owned/core states, matching ideas/advisors/designers, localization, and matching focus trees from the mod and vanilla install. By default it is read-only. Use `get_country_context("LUX", copy_states=True)` or `ensure_country_states_in_mod("LUX")` before generating effects that will directly modify vanilla states.

Effect builders:

```python
Mod.effect_add_civilian_factory(8, 1)
Mod.effect_add_military_factory(8, 1)
Mod.effect_add_infrastructure(8, 1)
Mod.effect_add_state_core(8, "LUX")
Mod.effect_remove_state_core(8, "GER")
Mod.effect_add_industry_bonus("LUX_industry_bonus", uses=1, bonus=0.5)
Mod.effect_add_tech_bonus("LUX_rifle_bonus", category="infantry_weapons", uses=1, bonus=0.5)
Mod.effect_add_timed_idea("LUX_recovery_spirit", days=365)
Mod.effect_create_wargoal("GER")
Mod.effect_declare_war("GER")
Mod.effect_start_civil_war("fascism", size=0.4, capital=8)
```

## Gotchas & Important Notes

1. **Batched writes.** Nothing touches disk until `save()`. If the process crashes between mutations, no partial state is written. Call `save()` explicitly.

2. **Eager loading.** `Mod()` reads all data on construction. If the mod directory is large, this takes a moment. Use `discard()` to re-read from disk if external changes occur.

3. **`update_country` leader fields.** Use `leader_name="X"` and `leader_ideology="X"` as kwargs — they forward to the nested Leader object. Don't pass a `Leader` object directly.

4. **`update_idea` modifier merge.** Passing `modifier={...}` to `update_idea` **merges** into the existing modifier dict, not replaces. To clear modifiers, pass `modifier={}`.

5. **Focus prerequisites are `list[list[str]]`.** The outer list is AND, inner lists are OR: `[[A, B], [C]]` means "(A OR B) AND C". A single prereq is `[["FOCUS_ID"]]`.

6. **State vanilla fallback.** `get_state()` copies the vanilla state file into the mod directory if it doesn't exist there. This requires `hoi4_install` to be set.

7. **Event namespaces control file grouping.** Events with the same namespace write to the same file. Dotted event IDs infer their namespace: `sic.1` writes `add_namespace = sic` in `events/sic_events.txt`. Use `set_event_namespace()` only to override.

8. **Localization key format.** Pass keys without `:0` — the serializer adds it. When reading existing YML files, keys come back with `:0` included. Both forms work for lookups.

9. **State edits preserve unknown vanilla data.** Loaded states retain original raw content. `serialize_state()` patches modeled fields and keeps unrelated blocks like buildings, resources, local supplies, history bookmarks, resistance, and compliance.

10. **`manpower`, `victory_points`, `buildings`, and `history` are strings.** State fields that could be complex Paradox expressions are stored as raw strings.

11. **Validation is advisory.** `validate()` returns errors but does not prevent `save()`. Check `severity == "error"` for critical issues.

12. **`preview()` only shows dirty sections.** If a module isn't in the dirty set (no mutations made), it won't appear in the diff. After `save()`, `preview()` returns empty.

13. **Country localization auto-sync.** `save()` calls `_sync_country_loc()` which auto-generates localization entries for the country name, adjective, and leader. These also appear in the localization diff.

14. **Country leaders use characters, not `set_country_leader`.** Generated country history uses `recruit_character = TAG_leader_1`; the character file supplies `roles = { country_leader }` and the `country_leader = { ... }` block. Do not add `set_country_leader` to history files.

15. **Country tags are guarded.** `create_country()` raises if the tag already exists in the mod or vanilla install. Use `is_country_tag_available()` and pick a free tag. Pass `overwrite=True` or `allow_vanilla_override=True` only for deliberate replacement work.

16. **Core effects must be state-scoped.** In country-scope effects, bare `add_core_of = TAG` or `remove_core_of = TAG` applies across all owned states. Use `Mod.effect_add_state_core(state_id, tag)` or `state_id = { add_core_of = TAG }`.

17. **Random events need namespace and firing rules.** `create_event("sic.1")` now infers `sic`, but validation still catches missing/mismatched namespaces in loaded files. Do not combine `is_triggered_only = yes` with `mean_time_to_happen` if you expect random firing.

## Low-Level Parser

For direct file manipulation outside the `Mod` facade:

```python
from hoi4 import parse_pdx, serialize_pdx, PdxNode

node = parse_pdx('capital = 1\nset_politics = { ruling_party = democratic }')

node.get_value("capital")          # "1"
node.get_int("capital")            # 1
block = node.get_block("set_politics")
block.get_value("ruling_party")    # "democratic"

node.set_value("capital", "999")
print(serialize_pdx(node))
```

### PdxNode Methods

| Method | Description |
|--------|-------------|
| `is_block() -> bool` | Has children (not a scalar) |
| `is_assignment() -> bool` | Has scalar value |
| `find(key) -> PdxNode \| None` | First child by key |
| `find_all(key) -> list[PdxNode]` | All children by key |
| `get_value(key, default="") -> str` | Scalar value of child |
| `get_int(key, default=0) -> int` | Integer value of child |
| `get_float(key, default=0.0) -> float` | Float value of child |
| `get_block(key) -> PdxNode \| None` | Block child by key |
| `set_value(key, value) -> None` | Update or add child |
| `remove(key) -> None` | Remove all children with key |
| `add_child(node) -> None` | Append child node |
