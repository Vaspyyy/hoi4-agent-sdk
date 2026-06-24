# HOI4 Agent SDK Recipes

Copyable workflows for common modding tasks. Read only the recipe relevant to the current task.

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
Mod.effect_add_bunker(8, 3)
Mod.effect_add_state_building(8, "bunker", level=3, province=1234)
Mod.effect_add_state_core(8, "LUX")
Mod.effect_remove_state_core(8, "GER")
Mod.effect_add_industry_bonus("LUX_industry_bonus", uses=1, bonus=0.5)
Mod.effect_add_tech_bonus("LUX_rifle_bonus", category="infantry_weapons", uses=1, bonus=0.5)
Mod.effect_add_timed_idea("LUX_recovery_spirit", days=365)
Mod.effect_create_wargoal("GER")
Mod.effect_declare_war("GER")
Mod.effect_declare_war_from("LUX", "GER")
Mod.effect_start_civil_war("fascism", size=0.4, capital=8)
Mod.scope_block("LUX", Mod.effect_declare_war("GER"))
Mod.effect_block("declare_war_on", {"type": "annex_everything", "target": "GER"})
mod.validate_effect("LUX = { declare_war_on = { target = GER } }")
```
