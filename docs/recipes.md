# HOI4 Agent SDK Recipes

Copyable workflows for common modding tasks. Read only the recipe relevant to the current task.

## Common Recipes

### Source a Flag Without Paid Generation

Search first and inspect the returned source page for the intended country,
date, design, creator, and reuse terms. This example selects a known file to
demonstrate the API; choose a historically appropriate file for the actual mod.

```python
from hoi4 import CommonsImageClient, Mod

mod = Mod.from_config()
client = CommonsImageClient()
for image in client.search("Flag of France", limit=5):
    print(image.title, image.source_url, image.license_name, image.artist)

selected = client.get_image("File:Flag of France.svg")
download = client.download(selected, mod.mod_root / "assets" / "sources")
print(download.path, download.metadata_path)

# Inspect the source and a 10x7 flag preview before importing.
mod.import_flag_to_mod("FRA", download.path)
issues = mod.validate(stage="build")
for issue in issues:
    print(f"[{issue.severity}] {issue.message}")
if any(issue.severity == "error" for issue in issues):
    raise RuntimeError("Validation failed")
print(mod.preview_summary())
print(mod.preview())
result = mod.save(require_changes=True)
print(result)
print(result.written_files)
```

For a portrait, search for the person's full name with a date or role, check
their identity, and download the selected `File:` title the same way. Inspect
the face/crop at 156x210 (and 65x67 for an advisor's small portrait), then use
`mod.import_portrait_to_mod(tag, slug, download.path)` and
`mod.write_portrait_gfx(tag, slug, portrait_path=texture)` with the returned
texture. Import a separate small portrait when its character role needs one.
Retain the JSON source record and publish credits/license notices as required
by the source's terms. Neither workflow needs a Gemini key. Downloaded source
files survive `Mod` dry runs; only the imported game assets are transactional.

### Create a Complete New Country with Focus Tree

Prepare the referenced local flag and portrait files first. They can be
reviewed internet/local sources or explicitly requested generated artwork;
the `assets/candidates/` paths below are illustrative prepared inputs.

```python
from pathlib import Path

from hoi4 import (
    AdvisorRole, AirWing, ArmyCommanderRole, Battalion, Character,
    CharacterPortrait, DivisionTemplate, DivisionUnit, EquipmentVariant,
    Fleet, Focus, Mod, Ship, ShipEquipment, TaskForce,
)

root = Path("/path/to/my_mod")
candidate_dir = root / "assets" / "candidates"
mod = Mod(root, hoi4_install="/path/to/Hearts of Iron IV")

mod.create_country("ZAR", "Zarland", adjective="Zarlandian",
                   color=(200, 50, 50), capital=100,
                   leader_name="General Zar", leader_ideology="despotism",
                   technologies={
                       "early_ship_hull_light": 1,
                       "iw_small_airframe": 1,
                   })
mod.set_country_name_pool(
    "ZAR",
    male_names=("Aleks", "Boris"),
    female_names=("Ana", "Mira"),
    surnames=("Zoric", "Petrov"),
)
mod.set_state_owner(100, "ZAR")  # owner + core

mod.import_flag_to_mod("ZAR", candidate_dir / "ZAR_flag.png")

def add_portrait(slug: str, *, size: tuple[int, int] = (156, 210)) -> str:
    texture = mod.import_portrait_to_mod(
        "ZAR", slug,
        candidate_dir / f"ZAR_{slug}.png",
        size=size,
    )
    mod.write_portrait_gfx("ZAR", slug, portrait_path=texture)
    return f"GFX_portrait_ZAR_{slug}"

add_portrait("leader_1")  # create_country() already references this sprite
for index in (1, 2):
    mod.create_character(
        "ZAR",
        Character(
            id=f"ZAR_advisor_{index}",
            name=f"Advisor {index}",
            portraits=[CharacterPortrait(
                large=add_portrait(f"advisor_{index}"),
                small=add_portrait(
                    f"advisor_{index}_small", size=(65, 67)
                ),
            )],
            roles=[AdvisorRole(
                slot="political_advisor",
                traits=["silent_workhorse"],
            )],
        ),
    )
    mod.create_character(
        "ZAR",
        Character(
            id=f"ZAR_commander_{index}",
            name=f"Commander {index}",
            portraits=[CharacterPortrait(
                channel="army",
                large=add_portrait(f"commander_{index}"),
            )],
            roles=[ArmyCommanderRole(
                skill=2,
                attack_skill=2,
                defense_skill=2,
                planning_skill=2,
                logistics_skill=2,
            )],
        ),
    )

mod.create_oob(
    "ZAR_1936",
    "ZAR",
    kind="land",
    templates=[DivisionTemplate(
        "Zar Infantry",
        battalions=[Battalion("infantry", 0, 0)],
    )],
    divisions=[DivisionUnit(
        "Zar Infantry",
        location=1234,  # land province in owned state 100
        name="1st Zar Division",
        start_equipment_factor=1.0,
    )],
    assign=True,
)

mod.create_equipment_variant("ZAR", EquipmentVariant(
    name="Independence Class",
    equipment_type="ship_hull_light_1",
    modules={
        "fixed_ship_battery_slot": "ship_light_battery_1",
        "fixed_ship_engine_slot": "light_ship_engine_1",
    },
    required_dlc=("Man the Guns",),
))
mod.create_oob(
    "ZAR_1936_naval_mtg",
    "ZAR",
    kind="naval",
    required_dlc=("Man the Guns",),
    fleets=[Fleet(
        name="Zar Navy",
        naval_base=1234,
        task_forces=[TaskForce(
            name="Zar Squadron",
            location=1234,
            ships=[Ship(
                name="ZNS Independence",
                definition="destroyer",
                equipment=[ShipEquipment(
                    "ship_hull_light_1",
                    owner="ZAR",
                    version_name="Independence Class",
                )],
            )],
        )],
    )],
    assign=True,
)
mod.create_oob(
    "ZAR_1936_naval_legacy",
    "ZAR",
    kind="naval",
    excluded_dlc=("Man the Guns",),
    fleets=[Fleet(
        name="Zar Navy",
        naval_base=1234,
        task_forces=[TaskForce(
            name="Zar Squadron",
            location=1234,
            ships=[Ship(
                name="ZNS Independence",
                definition="destroyer",
                equipment=[ShipEquipment("destroyer_1", owner="ZAR")],
            )],
        )],
    )],
    assign=True,
)
mod.create_equipment_variant("ZAR", EquipmentVariant(
    name="Zar Fighter",
    equipment_type="small_plane_airframe_0",
    modules={
        "fixed_main_weapon_slot": "light_mg_2x",
        "engine_type_slot": "engine_1_1x",
    },
    required_dlc=("By Blood Alone",),
))
mod.create_oob(
    "ZAR_1936_air_bba",
    "ZAR",
    kind="air",
    required_dlc=("By Blood Alone",),
    air_wings=[AirWing(
        location=100,  # state ID containing the starting air base
        equipment_type="small_plane_airframe_0",
        amount=24,
        owner="ZAR",
        version_name="Zar Fighter",
    )],
    assign=True,
)
mod.create_oob(
    "ZAR_1936_air_legacy",
    "ZAR",
    kind="air",
    excluded_dlc=("By Blood Alone",),
    air_wings=[AirWing(
        location=100,
        equipment_type="fighter_equipment_0",
        amount=24,
        owner="ZAR",
    )],
    assign=True,
)

tree = mod.create_focus_tree("zar_focus", "ZAR")
mod.add_focus("zar_focus", Focus(id="ZAR_militarize", x=5, y=0, cost=10,
    completion_reward="army_experience = 25"))
mod.add_focus("zar_focus", Focus(id="ZAR_conquer", x=5, y=1, cost=10,
    prerequisites=[["ZAR_militarize"]],
    completion_reward="create_wargoal = { type = annex_everything target = NEI }"))

package = mod.validate_country_package("ZAR")
assert package.complete, package.to_dict()
errors = mod.validate(
    stage="release",
    validate_icons=True,
    strict_localization=True,
)
assert not any(e.severity == "error" for e in errors), errors
mod.save()
```

Runtime events cannot execute `recruit_character`. Recruit the character in
country history and put the later unlock condition on its role availability.
A broad country request is not complete until
`validate_country_package(tag).complete` is true. That result is structural;
review and live-test runtime popularity/variable thresholds separately.

If the tag does not exist at scenario start, do not fabricate starting
ownership or an OOB. Put the territory/core/release setup in the actual focus,
event, decision, or on-action effect. The default `lifecycle="auto"` report
then returns `lifecycle == "runtime"` and exposes the detected state IDs and
source effects. A generated tag with no starting territory and no such path
fails as `missing_country_activation`.

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

mod.create_idea("great_depression", icon="generic_production_bonus", modifier={
    "consumer_goods_factor": 0.30,
    "political_power_gain": -0.50,
    "industrial_capacity_factory": -0.25,
})
mod.set_loc("great_depression", "The Great Depression")
mod.set_loc("great_depression_desc", "Economic collapse grips the nation.")

mod.save()
```

Idea `icon=` values are bare `picture` stems. HOI4 resolves
`generic_production_bonus` as the interface sprite
`GFX_idea_generic_production_bonus`; do not put the prefix in the saved
`picture` assignment.

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
        army_experience = 50
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

Use `get_country_context("LUX")` before generating content. It returns country basics, owned/core states, matching ideas/advisors/designers, localization, and matching focus trees from the mod and vanilla install. By default it is read-only. `get_state()` and validation also read vanilla fallback states without copying them. Use `get_country_context("LUX", copy_states=True)` or `ensure_country_states_in_mod("LUX")` to queue state overrides, review them with `preview()`, and persist them with `save()` before direct state-file work.

Effect builders:

```python
Mod.effect_add_civilian_factory(8, 1)
Mod.effect_add_military_factory(8, 1)
Mod.effect_add_infrastructure(8, 1)
Mod.effect_add_bunker(8, 3)
Mod.effect_add_state_building(8, "bunker", level=3, province=1234)
Mod.effect_transfer_state_with_core(8, "LUX")
Mod.effect_transfer_state(8, "LUX")
Mod.effect_add_state_core(8, "LUX")
Mod.effect_remove_state_core(8, "GER")
Mod.effect_add_political_power(100)
Mod.effect_add_war_support(0.1)
Mod.effect_add_stability(0.05)
Mod.effect_add_manpower(15000)
Mod.effect_add_equipment("infantry_equipment_0", 1000, producer="GER")
Mod.effect_set_technology("infantry_weapons", 1, popup=False)
Mod.effect_add_industry_bonus("LUX_industry_bonus", uses=1, bonus=0.5)
Mod.effect_add_tech_bonus("LUX_rifle_bonus", category="infantry_weapons", uses=1, bonus=0.5)
Mod.effect_add_timed_idea("LUX_recovery_spirit", days=365)
Mod.effect_create_wargoal("GER")
Mod.effect_declare_war("GER")
Mod.effect_declare_war_from("LUX", "GER")
Mod.effect_start_civil_war("fascism", size=0.4, capital=8)
Mod.effect_load_focus_tree("TAG_focus")
Mod.effect_spawn_civil_war_with_focus_tree("communism", "TAG_rebel_focus", capital=8)
Mod.effect_release("SLV")
Mod.effect_release_puppet("SLV")
Mod.effect_end_puppet("SLV", "YUG")
Mod.effect_set_autonomy("SLV", "autonomy_free")
Mod.effect_convert_puppet_to_ally("SLV", "YUG", faction_leader="AUS")
Mod.effect_convert_existing_or_spawn_revolt("SLV", [102], overlord="YUG", manpower=5000)
Mod.scope_block("LUX", Mod.effect_declare_war("GER"))
Mod.effect_block("declare_war_on", {"type": "annex_everything", "target": "GER"})
mod.validate_effect("LUX = { declare_war_on = { target = GER } }")
```

`set_state_owner()` modifies state history files before game start. In event, decision, and focus effects, use `transfer_state`, preferably through `Mod.effect_transfer_state(...)`; `set_owner = ...` is not a runtime effect.

### Small Country Focus Tree Template

Small countries usually play better with short setup focuses, visible branch choices, and concrete rewards. Avoid a single long 70-day line.

```python
tree = mod.ensure_focus_tree("tag_focus", "TAG")

root = Focus(id="TAG_assess_the_state", x=5, y=0, cost=5,
             icon="GFX_goal_generic_political_pressure",
             completion_reward=Mod.effect_add_political_power(50))
industry = Focus(id="TAG_expand_workshops", x=3, y=1, cost=5,
                 requires="TAG_assess_the_state",
                 icon="GFX_goal_generic_construct_civ_factory",
                 completion_reward=Mod.effect_add_civilian_factory(123, 1))
army = Focus(id="TAG_arm_the_militia", x=7, y=1, cost=5,
             requires="TAG_assess_the_state",
             icon="GFX_goal_generic_small_arms",
             completion_reward="\n".join([
                 Mod.effect_add_army_experience(15),
                 Mod.effect_add_equipment("infantry_equipment_0", 500),
             ]))
foreign_a = Focus(id="TAG_seek_neighbor_support", x=4, y=2, cost=10,
                  requires="TAG_expand_workshops",
                  completion_reward=Mod.effect_add_target_to_faction("ALLY", "TAG"))
foreign_b = Focus(id="TAG_stay_independent", x=6, y=2, cost=10,
                  requires="TAG_arm_the_militia",
                  completion_reward=Mod.effect_add_war_support(0.05))

for focus in [root, industry, army, foreign_a, foreign_b]:
    mod.upsert_focus("tag_focus", focus)

mod.set_focuses_mutually_exclusive("tag_focus", "TAG_seek_neighbor_support", "TAG_stay_independent")
mod.place_continuous_focus_below_tree("tag_focus", padding=400)
mod.assert_no_visual_overlap("tag_focus")
```

Use varied icons, 35-day setup focuses (`cost=5`), at least one meaningful branch choice, and rewards that change gameplay. Always add localization for every focus.

### Civil-War Rebels With Custom Focus Trees

Dynamic civil-war countries often do not match normal focus tree selectors. Pair the civil war effect with a runtime tree load:

```python
reward = Mod.effect_spawn_civil_war_with_focus_tree(
    "communism",
    "FB_AUS_focus",
    size=0.4,
    capital=4,
)
mod.append_to_focus_reward("AUS_focus", "AUS_arm_the_cells", reward)
```

The helper nests `load_focus_tree` inside `start_civil_war`. HOI4 executes
those inner effects in the newly spawned civil-war country's scope, so no
guessed `D01`-style dynamic tag is needed.

If a revolt tag may already exist, use the existing-or-spawn helper instead of assuming the tag is unreleased:

```python
effect = Mod.effect_convert_existing_or_spawn_revolt(
    "SLV",
    [102, 103],
    overlord="YUG",
    manpower=5000,
    equipment={"infantry_equipment_0": 300},
)
mod.create_event("slv.10", is_triggered_only=True,
                 options=[EventOption(name="slv.10.a", effect=effect)])
```

### Decision Chains and Save Recovery

Use `create_decision_chain()` for staged revolt/campaign decisions instead of hand-writing flags:

```python
mod.create_decision_chain("slv_revolt", [
    {"id": "SLV_organize_cells", "complete_effect": "add_political_power = 25", "event": "slv.1"},
    {"id": "SLV_launch_revolt",
     "complete_effect": Mod.effect_convert_existing_or_spawn_revolt("SLV", [102], overlord="YUG", manpower=5000)},
], final_event="slv.99")
```

For live-save migrations, create a visible repair decision or a hidden one triggered by a scripted effect:

```python
mod.create_recovery_decision(
    "slv_revolt",
    "SLV_repair_focus_tree",
    effect=Mod.effect_load_focus_tree("SLV_focus"),
    hidden=True,
    loc_name="Repair Slovenia Revolt",
)
```
