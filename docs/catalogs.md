# HOI4 Agent SDK Catalogs

Effect, modifier, and technology catalogs exposed by the SDK. Agents should consult these instead of guessing raw Paradox keys or technology categories.

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
