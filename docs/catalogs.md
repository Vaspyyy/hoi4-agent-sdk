# HOI4 Agent SDK Catalogs

Effect, modifier, and technology catalogs exposed by the SDK. Agents should consult these instead of guessing raw Paradox keys or technology categories.

## Effects & Modifiers Catalogs

The SDK ships static catalogs for discovering available effects and modifiers.

### EFFECT_CATEGORIES

```python
from hoi4 import EFFECT_CATEGORIES

# Structure: list[tuple[str, list[tuple[str, str]]]]
# (category_name, [(display_name, paradox_script), ...])

for cat_name, entries in EFFECT_CATEGORIES:
    for display_name, script in entries:
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
from hoi4 import effects

effects.add_state_core(115, "SCL")      # 115 = { add_core_of = SCL }
effects.remove_state_core(115, "SCL")   # 115 = { remove_core_of = SCL }
```

Use runtime state transfer effects in events, decisions, and focus rewards:

```python
effects.transfer_state(115, "SCL")      # SCL = { transfer_state = 115 }
effects.transfer_state_with_core(115, "SCL")
```

Do not use `set_owner = 115` in event/focus script. `set_owner` belongs in `history/states` files; it is not a runtime effect.

### TECHNOLOGY_CATEGORIES

```python
from hoi4 import TECHNOLOGY_CATEGORIES, effects

print("infantry_weapons" in TECHNOLOGY_CATEGORIES)  # True
print("infantry" in TECHNOLOGY_CATEGORIES)          # False; this is not a tech bonus category

effects.add_tech_bonus("rifle_bonus", category="infantry_weapons", uses=1, bonus=0.5)
effects.set_technology("infantry_weapons", 1, popup=False)
```

Common valid `add_tech_bonus` categories include `industry`,
`infantry_weapons`, `artillery`, `armor`, `electronics`, `land_doctrine`,
`air_doctrine`, `naval_doctrine`, `special_forces_doctrine`, and their
`cat_*` doctrine branches. The exported catalog tracks the 133 unique
categories present in HOI4 1.19.

### MODIFIER_CATEGORIES

```python
from hoi4 import MODIFIER_CATEGORIES

# Structure: list[tuple[str, list[tuple[str, str, str]]]]
# (category_name, [(display_name, modifier_key, example_value), ...])

for cat_name, modifiers in MODIFIER_CATEGORIES:
    for display_name, key, value in modifiers:
        print(f"{key} = {value}  # {display_name}")
```

**Categories:** Economy, Politics, Army, Navy, Air, Intelligence, Manpower & Occupation, Research: 61 entries in all.

Example entries:
```python
("Industrial Capacity Factory", "industrial_capacity_factory", "0.05")
("Political Power Gain", "political_power_gain", "0.25")
("Army Attack Factor", "army_attack_factor", "0.05")
("Research Speed Factor", "research_speed_factor", "0.05")
```

`research_time_factor` is not a HOI4 modifier. Use positive
`research_speed_factor` values for faster research.

The catalog is checked against the exact headings in the installed game's
generated modifier documentation by `scripts/audit_hoi4_install.py`.
