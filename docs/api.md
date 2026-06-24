# HOI4 Agent SDK API Reference

Complete public API reference for agents and developers. For mandatory agent workflow rules, read `../AGENTS.md` first.

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

## Countries

Country data spans 5 files: tag registration, definition (color/culture), history (capital/politics/leader), characters, and localization.

### Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `list_countries() -> list[str]` | Sorted tag list | All loaded country tags |
| `is_country_tag_available(tag: str) -> bool` | `bool` | False if the tag exists in the mod or vanilla install |
| `country_tag_conflicts(tag: str) -> list[str]` | `list[str]` | Returns conflict details such as `"mod: TAG (Name)"` or `"vanilla: TAG (Name)"` |
| `suggest_tag(name: str) -> str` | `str` | Pick an available 3-character tag from a country/place name |
| `suggest_tags(name: str, count=5) -> list[str]` | `list[str]` | Return available tag candidates |
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
tag = mod.suggest_tag("Sicily")
```

`create_country()` never changes state ownership, cores, controller, or capital state files. The `capital` argument only writes `capital = <state_id>` in `history/countries/{TAG} - {Name}.txt`.

`set_state_owner()` and `batch_set_owner()` patch state history files. In event options, event immediate blocks, decisions, and focus rewards, use the runtime effect helper instead:

```python
Mod.effect_transfer_state(115, "SCL")  # SCL = { transfer_state = 115 }
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
| `state_index(include_vanilla=True) -> list[dict]` | `list[dict]` | Cached state metadata with `id`, `name`, `display_name`, `owner`, `path`, `source` |
| `get_state_name_map(include_vanilla=True) -> dict[int, str]` | `dict[int,str]` | State ID to display name |
| `find_state(query: str, include_vanilla=True, limit=10) -> list[dict]` | `list[dict]` | Search by ID, filename name, or state loc key |
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
print(mod.find_state("Sicily")[0]["id"])
```

Loaded state files are patched through their original parsed content. Updating owner, cores, manpower, resources, buildings, or other modeled fields preserves unrelated vanilla data such as buildings, resources, local supplies, history bookmarks, resistance, and compliance blocks.
## Events

Events are grouped into files by namespace. Each event has a type, trigger, options, and optional mean_time_to_happen. Dotted event IDs infer their namespace automatically: `create_event("sic.1")` writes `add_namespace = sic` to `events/sic_events.txt`.

Block fields are body strings. The SDK adds the outer braces:

```python
mod.create_event("sic.1", mean_time_to_happen="days = 1")      # correct
mod.create_event("sic.2", mean_time_to_happen="{ days = 1 }")  # normalized to days = 1
```

### Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `list_events() -> list[str]` | Sorted event IDs | All loaded events |
| `get_event(event_id: str) -> Event` | `Event` | Raises `KeyError` with available IDs if not found |
| `create_event(event_id, title="", description="", event_type="country_event", picture="GFX_report_event_generic", is_triggered_only=False, fire_only_once=None, trigger="", immediate="", mean_time_to_happen="", options=None, overwrite=False) -> Event` | `Event` | Creates event. Raises if the event exists unless `overwrite=True`. Default option auto-generated if none provided. Dotted IDs infer namespace from the ID prefix. |
| `update_event(event_id: str, **kwargs) -> bool` | `bool` | Update any Event field |
| `update_event_option(event_id: str, option: int \| str, **kwargs) -> bool` | `bool` | Update one option by zero-based index or option name. Supports `name`, `effect`, `trigger`, and `ai_chance`. |
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
mod.update_event_option("my_mod.1", "my_mod.1.a",
                        effect=Mod.effect_transfer_state(115, "SCL"))
# Optional; already inferred for "my_mod.1".
mod.set_event_namespace("my_mod.1", "my_mod")
```

To replace an existing event loaded from disk, be explicit:

```python
mod.create_event("my_mod.1", options=[...], overwrite=True)
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
| `set_focuses_mutually_exclusive(tree_id, focus_a_id, focus_b_id) -> bool` | `bool` | Add reciprocal simple mutual exclusion groups |
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

For two mutually exclusive focuses, use one reciprocal single-focus group on each focus:

```python
mod.set_focuses_mutually_exclusive("west_focus", "WST_path_a", "WST_path_b")
# Equivalent raw fields:
# WST_path_a.mutually_exclusive == [["WST_path_b"]]
# WST_path_b.mutually_exclusive == [["WST_path_a"]]
```

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

## Effect Builders

Prefer these helpers when generating event effects, decision effects, or focus rewards. They avoid Python f-string brace escaping and encode common HOI4 scoping rules.

| Helper | Output shape |
|--------|--------------|
| `Mod.effect_transfer_state(state_id, tag)` | `TAG = { transfer_state = 115 }` |
| `Mod.effect_add_state_core(state_id, tag)` | `115 = { add_core_of = TAG }` |
| `Mod.effect_remove_state_core(state_id, tag)` | `115 = { remove_core_of = TAG }` |
| `Mod.effect_add_civilian_factory(state_id, level=1)` | State-scoped `industrial_complex` construction |
| `Mod.effect_add_military_factory(state_id, level=1)` | State-scoped `arms_factory` construction |
| `Mod.effect_add_infrastructure(state_id, level=1)` | State-scoped infrastructure construction |
| `Mod.effect_add_bunker(state_id, level=1, province=None)` | State/province-scoped bunker construction |
| `Mod.effect_add_equipment(equipment_type, amount, producer=None, variant_name=None)` | `add_equipment_to_stockpile = { type = ... amount = ... }` |
| `Mod.effect_set_technology(technology, level=1, popup=None)` | `set_technology = { tech = 1 }` |
| `Mod.effect_set_technologies({technology: level, ...})` | Multi-entry `set_technology` block |
| `Mod.effect_add_tech_bonus(name, category, uses=1, bonus=0.5)` | Validated `add_tech_bonus` block |
| `Mod.effect_create_wargoal(target, wargoal_type="annex_everything")` | `create_wargoal = { type = ... target = TAG }` |
| `Mod.effect_declare_war(target, wargoal_type="annex_everything")` | Current-scope `declare_war_on` block |
| `Mod.effect_declare_war_from(attacker, target, wargoal_type="annex_everything")` | Attacker-scoped war declaration |
| `Mod.scope_block(scope, *effects)` | Generic scoped effect block |
| `Mod.effect_block(name, fields)` | Generic `effect = { key = value }` block |

Examples:

```python
reward = "\n".join([
    Mod.effect_transfer_state(115, "SCL"),
    Mod.effect_add_state_core(115, "SCL"),
    Mod.effect_add_equipment("infantry_equipment_0", 1000, producer="GER"),
    Mod.effect_set_technology("infantry_weapons", 1, popup=False),
])
mod.validate_effect(reward)
```
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
    code: str = ""          # Stable suppression/matching code when available
    file_path: str | None   # Source file if known
    focus_id: str | None    # Focus ID if relevant
    country_tag: str | None # Country tag if relevant
    state_id: int | None    # State ID if relevant
    event_id: str | None    # Event ID if relevant
    idea_id: str | None     # Idea ID if relevant
```

**Errors** mean the mod will crash or behave incorrectly. **Warnings** mean potential issues (missing localization, etc.).

Suppress known false-positive warnings by stable code:

```python
errors = mod.validate(suppress_warnings=["country_scope_core_effect"])
errors = mod.validate_effect("add_core_of = SCL", suppress_warnings=["country_scope_core_effect"])
```

Useful script warning codes include `country_scope_core_effect`, `history_set_owner_in_effect`, `unknown_tech_bonus_category`, `missing_effect_target`, `missing_wargoal_type`, `unknown_country_scope`, and `script_syntax`.

The `history_set_owner_in_effect` warning catches a common HOI4 boundary mistake: `set_owner` is a state history directive, not a runtime event/focus effect. Use `transfer_state`, preferably through `Mod.effect_transfer_state(...)`.
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

`mod.save()` returns `SaveResult`:

```python
result = mod.save()
print(result)               # "Saved N file(s)" or "No changes written: ..."
print(result.written_files)
print(result.no_changes)
```

If there is no dirty in-memory state, `save()` emits a warning, returns `no_changes=True`, writes no files, and sets a loud `message`.
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
