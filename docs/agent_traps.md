# Common AI Agent Mistakes

Use this page when generated content is valid Paradox script but likely wrong gameplay.

## Faction Direction

Bad:

```python
effect = "add_to_faction = BAY"
```

`add_to_faction = BAY` adds BAY to the current scope's faction. If the current scope is AUS, Bavaria joins Austria's faction.

Good:

```python
Mod.effect_add_target_to_faction("AUS", "BAY")  # AUS = { add_to_faction = BAY }
Mod.effect_join_faction("BAY", "AUS")           # Same output, named by intent.
```

Validation warning: `faction_scope_footgun` for bare or iterated-scope faction commands.

## State History vs Runtime Effects

Bad in focus/event rewards:

```text
set_owner = BAY
add_core_of = BAY
```

Good runtime effect:

```python
Mod.effect_transfer_state_with_core(52, "BAY")
```

Good pre-game history edit:

```python
mod.set_state_owner(52, "BAY")
```

For fragile vanilla state files where only owner/cores should change, queue the text-preserving patcher and save with the same `Mod` instance:

```python
mod.patch_state_history(52, owner="BAY", add_cores=["BAY"], remove_cores=["AUS"])
mod.save(require_changes=True)
```

## Idea Replacement Tooltips

Bad:

```text
remove_ideas = crisis_1
remove_ideas = crisis_2
remove_ideas = crisis_3
add_ideas = crisis_4
```

Good:

```python
Mod.effect_swap_idea("crisis_3", "crisis_4", target="ITA")
Mod.effect_upgrade_idea_chain(["crisis_1", "crisis_2", "crisis_3", "crisis_4"], target="ITA")
```

Validation warning: `bad_idea_tooltip_pattern`.

## Addable Ideas

`add_ideas = X` expects a country idea/national spirit. Advisors, theorists, and high command ideas use other categories and should not be added like spirits.

Good:

```python
mod.ensure_idea("TAG_crisis", category="country", modifier={"stability_factor": -0.05})
mod.set_loc("TAG_crisis", "Political Crisis")
mod.set_loc("TAG_crisis_desc", "The government is struggling to retain control.")
```

Validation warning: `idea_not_addable` when a loaded non-country idea is used with `add_ideas`.

## Idea Picture Stems

The `picture` value is not a complete sprite key. HOI4 prepends
`GFX_idea_` during lookup.

Bad:

```text
picture = GFX_idea_generic_political_support
```

That silently attempts to resolve
`GFX_idea_GFX_idea_generic_political_support` and falls back to the question
mark without writing an engine error.

Good:

```python
mod.create_idea(
    "TAG_crisis",
    icon="generic_political_support",
    modifier={"stability_factor": -0.05},
)
```

The matching `interface/*.gfx` declaration remains
`GFX_idea_generic_political_support`. The compatibility `icon=` argument also
accepts the prefixed spelling, but normalizes it before writing.
`invalid_idea_picture_prefix` rejects the broken on-disk form, while
`unknown_idea_icon` checks the correctly resolved full sprite key.

Never write `desc = TAG_crisis_desc` inside the idea block. Current HOI4 derives
that key from the idea ID and rejects the assignment. Likewise, generated
characters declare `country_leader`, `advisor`, or commander blocks directly;
there is no top-level `roles = { ... }` field.

## Decision Category Files

Category metadata and decisions are separate:

- `common/decisions/categories/*.txt`: category `icon`, `allowed`, `visible`
- `common/decisions/*.txt`: the category block containing its decisions

Use `create_decision_category()` and `create_decision()` rather than combining
both shapes manually. Validation rejects combined pre-0.4.2 output.

## Revolts Need a Playable Baseline

Bad:

```text
BAY = { transfer_state = 52 }
BAY = { declare_war_on = { type = annex_everything target = AUS } }
```

This can create a country with no units, equipment, techs, or manpower.

Good:

```python
Mod.effect_spawn_revolution(
    "BAY",
    [52],
    overlord="AUS",
    manpower=15000,
    equipment={"infantry_equipment_0": 500},
    technologies={"infantry_weapons": 1},
    division_template=Mod.effect_division_template("Militia", "infantry = { x = 0 y = 0 }"),
    units=["Militia"],
)
```

The helper warns if called without any playable baseline.

## Civil-War Focus Tree Assignment

Focus tree selectors such as `original_tag = AUS` are not enough for many dynamic civil-war countries. Load the rebel tree at runtime.

Bad:

```python
Mod.effect_start_civil_war("communism", size=0.4, capital=4)
```

Good:

```python
Mod.effect_spawn_civil_war_with_focus_tree(
    "communism",
    "FB_AUS_focus",
    size=0.4,
    capital=4,
)
```

The helper places `load_focus_tree` inside `start_civil_war`, whose inner
effects run in the spawned country's scope. Dynamic tags such as `D01` are
assigned by the game and must not be guessed. Validation warns with
`civil_war_focus_tree_missing` when no tree load is present, reports unknown
trees as `unknown_focus_tree_reference`, and reports invalid capital state IDs
as `civil_war_capital_ref`.

## Existing or Puppet Revolt Tags

Do not assume a tag is unreleased. Vanilla paths may release a tag as a puppet or transfer some of its states before your content runs.

Good:

```python
Mod.effect_convert_existing_or_spawn_revolt(
    "SLV",
    [102, 103],
    overlord="YUG",
    manpower=5000,
    equipment={"infantry_equipment_0": 300},
)
```

For non-war conversions:

```python
Mod.effect_convert_puppet_to_ally("SLV", "YUG", faction_leader="AUS")
```

Validation warning: `revolt_state_already_owned` when a transfer effect gives a state to the country that already owns it in loaded history.

## Resistance Is Not Generic Unrest

Occupation resistance is not a universal revolt meter. If a state is a core of its current owner, `add_resistance` usually does not model nationalist unrest the way an event chain expects.

Bad:

```text
102 = { add_resistance = 20 }
```

Better: use country/state flags, variables, decisions, and events to represent unrest, then fire a revolt event when thresholds are met.

Validation warning: `resistance_on_core_state` for state-scoped `add_resistance` on owner-core states.

## Lore Events Need Gameplay Options

Flavor popups are fine, but triggered event options with no effects often feel broken in agent-generated content.

Good:

```python
EventOption(name="slv.1.a", effect="set_country_flag = SLV_cells_organized")
```

Validation warning: `event_option_no_effect`.

## Focus Layout

After adding generated branches:

```python
branch = mod.auto_layout_branch("TAG_focus", focuses, anchor_focus_id="TAG_anchor")
for focus in branch:
    mod.upsert_focus("TAG_focus", focus)
mod.place_continuous_focus_below_tree("TAG_focus", padding=400)
mod.assert_no_visual_overlap("TAG_focus")
```

Validation warning: `visual_overlap` if normal focuses collide or continuous focuses are too high.

## Localization

Before final save:

```python
errors = mod.validate(validate_icons=True, strict_localization=True)
```

Strict localization checks events, options, ideas, countries, and leaders. Focus name/description localization is always checked.

Validation warning: `missing_localization`.
