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

For fragile vanilla state files where only owner/cores should change, use the immediate text-preserving patcher:

```python
mod.patch_state_history(52, owner="BAY", add_cores=["BAY"], remove_cores=["AUS"])
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
```

Validation warning: `idea_not_addable` when a loaded non-country idea is used with `add_ideas`.

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
