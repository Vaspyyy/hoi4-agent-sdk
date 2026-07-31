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

## Guard Repeated Runtime Idea Grants

A focus and a delayed event may legitimately grant the same idea when the
runtime path only fills in a missing idea:

```text
if = {
    limit = { NOT = { has_idea = TAG_security_service } }
    add_ideas = TAG_security_service
}
```

`idea_mutation_collision` recognizes this guard and does not warn. An
unguarded grant, a guard for another idea, or an event that removes/swaps the
same idea still warns because it can overwrite staged focus content.

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

## Do Not Hand-Write Character Rosters or Starting Forces

`create_country()` creates the compatibility leader, but it does not invent a
complete roster or army. Use `create_character()` with `AdvisorRole`,
`ArmyCommanderRole`, `NavyLeaderRole`, and explicit DLC
`CharacterInstance` values. Use `create_oob()` with `DivisionTemplate`,
`Battalion`, `DivisionUnit`, `Fleet`, `TaskForce`, `Ship`, `ShipEquipment`,
and `AirWing`. Keep each land, naval, or air OOB in its own file and pass the
matching `kind`; `assign=True` then uses `set_oob`, `set_naval_oob`, or
`set_air_oob`.

Repeated character instances or repeated role kinds are intentionally
ambiguous. Pass `occurrence=` and, for instance roles,
`instance_occurrence=`. The SDK raises instead of guessing and editing the
wrong DLC variant.

Never place a starting land unit from memory. OOB validation checks that its
province exists, is land, and belongs to the country at scenario start.
Naval and air validation checks unit/equipment IDs, ownership, locations, and
structure. Production and unknown blocks remain source-preserved.

Man the Guns does not load legacy ship equipment as a hull design. Create
`EquipmentVariant` records in country history, put their names in each hull's
`version_name`, and gate both variants and hull OOB with
`required_dlc=("Man the Guns",)`. Provide a separate legacy naval OOB with
`excluded_dlc=("Man the Guns",)`. Otherwise HOI4 logs "Could not find proper
equipment variant" and silently skips the ships.

## A New Tag Is Not a Complete Country

Every tag created through the SDK is package-validated during normal
`validate()` and the release gate. Before reporting success, require:

```python
report = mod.validate_country_package(tag)
assert report.complete, report.to_dict()
```

This proves structural completeness only. Check
`report.proves_dynamic_achievability` before interpreting it: the value is
false because popularity/variable threshold arithmetic and player-state
reachability are not solved. Review and live-test important runtime branches.

The report catches missing three-size flags, portrait textures or GFX,
undersized rosters, bad recruitment, and missing localization. A
scenario-start country additionally needs its land OOB, territory, and
owned/cored capital. A country released by a focus or event is classified as
`runtime` from its effect path and is not compared to the starting map or
required to have a starting OOB. A generated tag with neither starting
territory nor runtime activation evidence fails as
`missing_country_activation`. `save()` remains advisory, so agents must still
stop on errors explicitly.

Run `find_disconnected_states()` with the `map` extra for border QA. Tiny
one-province islands are ignored by the default threshold; use
`allowed_state_ids` only for deliberate islands or overseas holdings, not to
hide an accidental enclave.
Also run `find_enclosed_foreign_states()` to catch foreign states accidentally
left completely surrounded by the target country.

## Validate at the Right Stage

Use `validate(stage="build")` between pipeline scripts so a half-built country
does not need ad-hoc error filtering. Use the default `stage="package"` once
the playable package should be complete, and `stage="release"` for the final
semantic liveness pass.

With a configured game install, never ignore an undocumented script-token
warning without investigation. The warning includes installed-game usage and
a nearest documented token. Kind-qualified allowlists are for intentional
scripted extensions, not typo suppression.

The same installed documentation declares legal effect and trigger scopes.
Treat `unsupported_effect_scope` and `unsupported_trigger_scope` as evidence
that a legal token is nested under the wrong country, state, character, or
iterator scope. `validate_effect()` assumes country scope unless a different
`scope=` is supplied.

Inspect `mod.analyze_content_liveness()` before release. A set-only flag may be
deliberate historical bookkeeping, but an asymmetric flag in an otherwise
symmetrical ending set is evidence of missing content. Allowlist deliberate
patterns explicitly instead of disabling the liveness pass.
The report is a structural reference graph, not a proof that trigger thresholds
or variable arithmetic are achievable during play.

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
