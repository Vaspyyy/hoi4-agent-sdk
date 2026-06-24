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
