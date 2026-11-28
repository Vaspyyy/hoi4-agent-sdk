# Validation codes and severities

`Mod.validate()` returns a list of `ValidationIssue` findings. Every finding
has a stable `code` and the `severity` of that code. This page describes the
severity model, how to hide findings, and every code the SDK can produce.

## Severities

| Severity | Meaning | Returned by default |
| --- | --- | --- |
| `error` | The game rejects the content or behaves wrongly. | yes |
| `warning` | Probably a mistake: the game loads it, but the result is unlikely to be what was meant. | yes |
| `info` | Advice and style hints. The content is valid. | only with `advice=True` |

Each code has exactly one severity. The registry is
`hoi4.validation.VALIDATION_SEVERITIES` (`code -> severity`); the tables
`VALIDATION_ERROR_CODES`, `VALIDATION_WARNING_CODES` and
`VALIDATION_INFO_CODES` map the codes of one severity to a one-line
description, and `VALIDATION_CODES` holds all of them; all five are
exported from `hoi4`. Every finding has the severity of its code, also when
a check such as `validate_event()` or `validate_oob()` is called on its own.

```python
from hoi4 import Mod

mod = Mod("/path/to/my_mod", hoi4_install="/path/to/Hearts of Iron IV")
issues = mod.validate()                 # errors and warnings
issues = mod.validate(advice=True)      # also the advice
blocking = [i for i in issues if i.severity == "error"]
```

Advice never fails the release gate (`run_release_gate`) or
`mod.validate_country_package(tag)`. `mod.validate_effect(script)` checks
one snippet and returns its advice together with errors and warnings; pass
`advice=False` to leave the advice out.

## Stages

`validate(stage=...)` chooses how much is checked. Each stage runs
everything of the stage before it.

| Stage | When | What it adds |
| --- | --- | --- |
| `"build"` | While you are still building. | Syntax, references, scopes, orders of battle, states, events, ideas, focus trees. |
| `"package"` (the default) | Before you hand the mod to someone. | The completeness checks for countries the SDK created (flag, portraits, starting forces). |
| `"release"` | Before you publish. | The "is this ever used?" checks: `unfired_event`, `ungranted_idea`, `unreachable_focus`, `flag_set_never_read`, `flag_read_never_set` and `unused_localization`. |

The release checks read every file of the mod and the game, and they are
only meaningful once the content is complete: half-built content always has
events that nothing fires yet. So a flag that is read and never set is not
reported by `validate()` or `validate(stage="build")`; call
`validate(stage="release")`.

One more switch is independent of the stage: missing text. A focus
without a name is always reported (`missing_localization`). The same check
for events, ideas and countries runs with
`validate(strict_localization=True)`, because a mod in progress has many
objects whose texts are not written yet.

## What a complete country needs

Five findings are not about what the game needs, but about what the SDK asks
of a complete country: `missing_country_leader`,
`insufficient_political_advisors`, `insufficient_military_commanders`,
`missing_land_oob` and `capital_not_cored`
(`hoi4.COUNTRY_PACKAGE_REQUIREMENT_CODES`). The game loads a country without
a leader (it makes one up), without advisors, generals or an army, and with
a capital that is not a core. So these are warnings, and you can hide them
like any warning if you want a country with one general.

They still count where completeness is the question:
`mod.validate_country_package(tag).complete` is `False` while one of them is
open (`report.unmet_requirements` lists them), and the release gate fails.

## Hiding findings

`suppress=` hides warnings and advice by code. An entry is either a code,
which hides every finding with that code, or `code:ID`, which hides only the
findings about one object. `ID` is compared with the finding's focus, event,
idea, decision, country tag, state id, focus tree, ideology, dynamic modifier
or bookmark field. A finding about a flag or a script token has none of
these fields, so `code:ID` cannot pick out one flag: hide the code as a
whole, or fix the script.

```python
mod.validate(suppress=["visual_overlap"])
mod.validate(suppress=["missing_localization:TAG_secret_focus"])
mod.validate_effect("add_core_of = SCL", suppress=["country_scope_core_effect"])
```

- Errors cannot be suppressed: an error code in `suppress=` raises `ValueError`.
- An unknown code raises `ValueError`, so a typo does not silently hide
  nothing. Suppressing by message text is not supported.
- One mistake gives one finding: a core effect in a country scope is
  `country_scope_core_effect`, not also `unsupported_effect_scope`.

## How the checks read the game

The SDK inherits the files of the configured game install and base mods, and
the content checks read them the way the game does:

- Localisation keys are looked up in the mod (with unsaved edits), the base
  mods and the game. A key counts as present when a line of a
  `localisation/english` file starts with `KEY:` and a quote, like the game
  reads it, so text with inner quotes (`KEY: "The "Plan" X"`) counts too. A
  focus is named by its `text = KEY` when it has one; a country by `TAG` or
  an ideology variant such as `TAG_fascism`; an idea by its `name = KEY` or
  its id.
- An event namespace may be declared by an `add_namespace` in any event file
  of the game, a base mod or the mod, compared without regard to case. A
  file may declare several. Wherever an event id is looked up (references,
  `unfired_event`), its namespace (the text before the last dot) is compared
  without regard to case too, as the game does: `PER_SSB_events.8` fires
  `PER_ssb_events.8`. The number behind the dot is compared as it is. A
  namespace may hold dots itself: `MUN_slovakia.news.1` needs
  `add_namespace = MUN_slovakia.news`, and `add_namespace = MUN_slovakia`
  does not cover it. The HOI4 wiki (Event modding) says so
  ("`add_namespace = my_event.subtopic` can be used for events of the type
  of `id = my_event.subtopic.1`. The numeric ID will be taken to be
  everything after the last dot"), and every event of the game follows it.
  `create_event("my_mod.news.1")` declares `my_mod.news` on its own.
- The token check, the reference check and the liveness checks all read the
  mod's own script files as `save()` will write them, split the same way
  (`hoi4._script_corpus`) and walked by the same scope walker
  (`hoi4.script_scopes`). Findings carry file lines. A definition made with
  `create_definition()` (a building, an ideology...) counts before `save()`.
- A block holds statements of its own kind: `OR`, `NOT`, `AND`,
  `hidden_trigger` and `any_of` are triggers, `hidden_effect`, `random_list`
  and the loops are effects (as the game's documentation lists them), and a
  scripted trigger is not an effect. A statement like `GER = 5` does nothing
  among effects and is reported; among triggers it compares scopes.
- A localisation key of the mod is used (not `unused_localization`) when
  the game or a base mod defines the same key (the mod overrides a text the
  game already shows, as in `localisation/english/replace`), when something
  the SDK models shows it, or when it (or the name before `_desc`) is a key
  or value anywhere in the mod's own script files or interface `.gui` files.
- A focus search filter is known when a sprite named `GFX_<filter>` is
  declared in an `interface/*.gfx` file of the game, a base mod or the mod.
- Two focuses may share a position only when the script gives the game a
  way to keep one of them off it. The game draws every focus of a tree,
  both sides of a mutually exclusive choice included, so exclusivity is no
  reason. `allow_branch` can hide a focus, by the rule of the HOI4 wiki
  (National focus modding): "By default, a focus will also be disallowed if
  either of the parent focuses (as set by prerequisites) is disallowed.
  However, a focus containing allow_branch within its definition will only
  check its own allowed status, still showing up if it has disallowed
  parents." So a focus with `allow_branch` needs its own condition and
  nothing else (an empty block and `always = yes` hide nothing), and a
  focus without one needs every condition that one of its prerequisite
  focuses needs, whichever `prerequisite` block names them. That holds
  also for a prerequisite that is a shared focus of another file
  (`shared_focus = ID` in the tree): what hangs below it is hidden with
  it. A pair may
  share a position in two cases. One: an `offset` block moves one of them
  and not the other (a focus moves while the trigger of its own `offset`
  block holds, or of one on a focus it is placed relative to; an offset of
  0 0 moves nothing), and `allow_branch` can hide at least one of them.
  Two: each of them needs an `allow_branch` condition that the other does
  not need. A condition is its text without comments and spacing, with
  names in lower case and no quotes around a single word, so two focuses
  that write the same `allow_branch` are behind one condition and appear
  together. The SDK does not work out whether two different
  conditions can hold at the same time, or whether the trigger of an
  `offset` block holds exactly when the other focus appears.
- Hidden events (`hidden = yes`) need no title, description or option.
- Tag aliases from `common/country_tag_aliases` count as country tags, and
  tags declared after `dynamic_tags = yes` are named at runtime.

What the checks read out of the game's files (and base mods) is kept on disk,
so the first validation of a new process does not parse the game again. The
cache lives in `~/.cache/hoi4-sdk/file-results/`; `HOI4_SDK_CACHE_DIR` moves
it and `HOI4_SDK_DISK_CACHE=off` turns it off. Findings are the same with or
without it. See [internals/disk_cache.md](internals/disk_cache.md).

## All codes

### Errors

| Code | Severity | Meaning |
| --- | --- | --- |
| `bookmark_randomize_weather_missing` | error | A bookmark is missing its obligatory randomize_weather effect. |
| `capital_not_owned` | error | A country's capital state is not owned by that country. |
| `capital_ref` | error | A country history capital points to a missing state. |
| `country_colors_parse` | error | A country colors table changed after loading and could not be parsed during validation. |
| `duplicate_bookmark_name` | error | A bookmark name is defined more than once. |
| `duplicate_character_id` | error | A character ID is defined more than once. |
| `duplicate_country_tag` | error | A country tag is declared in more than one mod file. |
| `duplicate_decision_category_id` | error | A decision category repeats within one file. |
| `duplicate_decision_id` | error | A decision ID is defined more than once. |
| `duplicate_definition_id` | error | A scripted effect, trigger or other definition is defined twice. |
| `duplicate_dynamic_modifier_id` | error | A dynamic modifier ID is defined more than once. |
| `duplicate_event_id` | error | An event ID is defined more than once. |
| `duplicate_focus_id` | error | A focus ID is defined more than once in one tree. |
| `duplicate_focus_tree_id` | error | A focus tree ID is defined more than once. |
| `duplicate_idea_id` | error | An idea ID is defined more than once. |
| `duplicate_identifier` | error | A loaded object id is defined more than once. |
| `duplicate_ideology_id` | error | An ideology ID is defined more than once. |
| `duplicate_oob_fleet` | error | An OOB repeats a fleet name. |
| `duplicate_oob_ship_equipment` | error | A ship repeats an equipment type. |
| `duplicate_oob_task_force` | error | A fleet repeats a task-force name. |
| `duplicate_oob_template` | error | An OOB repeats a division-template name. |
| `duplicate_state_id` | error | A state ID is defined in more than one state file. |
| `duplicate_strategic_region_id` | error | A strategic region id is defined more than once. |
| `duplicate_subideology_id` | error | A subtype is repeated within an ideology. |
| `empty_land_oob` | error | An assigned land OOB has no template or no starting division. |
| `equipment_variant_chassis_not_unlocked` | error | Country history creates an equipment variant before its chassis technology. |
| `focus_cycle` | error | A focus tree contains a prerequisite cycle. |
| `game_log_error` | error | HOI4 reported an engine error for a file owned by this mod. |
| `invalid_bookmark_available` | error | A bookmark availability block is malformed. |
| `invalid_bookmark_country_tag` | error | A bookmark country tag is invalid. |
| `invalid_bookmark_date` | error | A bookmark date is invalid. |
| `invalid_bookmark_effect` | error | A bookmark effect block is malformed. |
| `invalid_bookmark_required_dlc` | error | A bookmark required DLC list is invalid. |
| `invalid_character_roles_key` | error | Characters declare roles through role blocks, not a top-level roles assignment. |
| `invalid_country_color` | error | A country color is malformed or has a channel outside 0..255. |
| `invalid_country_tag` | error | A country tag is not three letters or digits. |
| `invalid_decision_category_layout` | error | Decision-category metadata belongs in common/decisions/categories. |
| `invalid_dynamic_modifier_id` | error | A dynamic modifier ID is invalid. |
| `invalid_dynamic_modifier_script` | error | A dynamic modifier contains malformed script. |
| `invalid_event_type` | error | An event block type is not one HOI4 knows. |
| `invalid_focus_kind` | error | A focus block kind (focus, shared_focus, joint_focus) is not allowed where it is. |
| `invalid_idea_desc_key` | error | Idea descriptions use the fixed <idea_id>_desc localization key, not a desc assignment. |
| `invalid_ideology_ai_behavior` | error | An ideology AI behavior is unknown. |
| `invalid_ideology_color` | error | An ideology color channel is outside 0..255. |
| `invalid_ideology_id` | error | An ideology ID is not valid Paradox Script syntax. |
| `invalid_oob_air_equipment` | error | An air wing has invalid equipment. |
| `invalid_oob_air_location` | error | An air wing has an invalid location. |
| `invalid_oob_battalion` | error | A division template contains an empty battalion type. |
| `invalid_oob_factor` | error | An OOB experience or equipment factor is outside 0..1. |
| `invalid_oob_grid_position` | error | A battalion grid position is outside the supported range. |
| `invalid_oob_location` | error | A starting division has an invalid province location. |
| `invalid_oob_naval_base` | error | A fleet has an invalid naval-base location. |
| `invalid_oob_naval_location` | error | A naval OOB entry has an invalid location. |
| `invalid_oob_ship_definition` | error | A ship has no unit definition. |
| `invalid_oob_ship_equipment` | error | A ship has invalid equipment. |
| `invalid_railway` | error | A railway has an invalid level, province or connection. |
| `invalid_ruling_party` | error | A country's ruling party is not a known ideology group. |
| `invalid_set_politics_field` | error | The set_politics effect contains a field unsupported by the installed game grammar. |
| `invalid_subideology_id` | error | An ideology subtype ID is invalid. |
| `invalid_supply_node` | error | A supply node is not on a known land province. |
| `legacy_naval_oob_without_dlc_fallback` | error | Legacy naval equipment is active when Man the Guns may be enabled. |
| `load_failure` | error | A discovered file could not be loaded by its structured reader. |
| `localization_bom` | error | A localization file is missing the UTF-8 byte-order mark HOI4 expects. |
| `localization_encoding` | error | A localization file is not valid UTF-8. |
| `localization_header` | error | A localization file has no valid l_<language>: header. |
| `localization_language_mismatch` | error | A localization file's l_<language>: header does not match its language folder. |
| `malformed_map_building` | error | A buildings.txt line is malformed. |
| `malformed_railway` | error | A railway line is malformed. |
| `map_building_unknown_province` | error | A buildings.txt line names a province that does not exist. |
| `map_building_unknown_state` | error | A buildings.txt line names a state that does not exist. |
| `missing_bba_air_variant_name` | error | An airframe-based air wing has no equipment variant name. |
| `missing_bba_equipment_variant` | error | An airframe-based air wing refers to no compatible country-history variant. |
| `missing_character_localization` | error | A visible character is missing name localization. |
| `missing_character_portrait` | error | A visible character has no portrait reference. |
| `missing_character_portrait_gfx` | error | A character portrait has no interface sprite declaration. |
| `missing_character_portrait_texture` | error | A character portrait sprite points to a missing texture. |
| `missing_character_small_portrait` | error | An advisor character has no small portrait. |
| `missing_country_activation` | error | A generated country has neither scenario-start territory nor a runtime release/setup path. |
| `missing_country_flag` | error | An SDK-created country is missing one or more required flag sizes. |
| `missing_country_name_pool` | error | A country that needs generated names has no common/names entry. |
| `missing_effect_target` | error | War goal or war declaration block is missing target = TAG. |
| `missing_event_id` | error | An event has no id. |
| `missing_event_options` | error | A visible event has no option, so its window cannot be closed. |
| `missing_focus_id` | error | A focus has no id. |
| `missing_focus_mutually_exclusive` | error | A focus mutually_exclusive entry names a focus that does not exist. |
| `missing_focus_prerequisite` | error | A focus prerequisite names a focus that does not exist. |
| `missing_idea_id` | error | An idea has no id. |
| `missing_mtg_equipment_variant` | error | A hull-based ship refers to no compatible country-history variant. |
| `missing_mtg_ship_variant_name` | error | A hull-based ship has no equipment variant name. |
| `missing_oob_reference` | error | Country history refers to an OOB file that does not exist. |
| `missing_oob_ship_equipment` | error | A ship has no equipment. |
| `missing_state_id` | error | A state has no id. |
| `mtg_naval_oob_uses_legacy_equipment` | error | A Man the Guns naval OOB uses legacy equipment types. |
| `no_owned_territory` | error | A complete country owns no states. |
| `non_land_state_province` | error | A state lists a sea province. |
| `oob_fleet_name` | error | A fleet has no name. |
| `oob_kind_mismatch` | error | An OOB assignment kind does not match its file content. |
| `oob_load_error` | error | An order of battle referenced by a country could not be read. |
| `oob_task_force_name` | error | A task force has no name. |
| `oob_template_name` | error | A division template has no name. |
| `oob_water_location` | error | A starting land division is placed in a water province. |
| `province_in_multiple_states` | error | A land province belongs to more than one state. |
| `province_in_multiple_strategic_regions` | error | A province belongs to more than one strategic region. |
| `runtime_capital_not_assigned` | error | A runtime-created country does not receive its declared capital. |
| `runtime_capital_not_cored` | error | A runtime-created country receives its capital without a matching core. |
| `runtime_recruit_character` | error | recruit_character runs outside country history, where the game ignores it. |
| `script_syntax` | error | Raw Paradox script has mismatched braces or quotes. |
| `stale_game_log` | error | The supplied HOI4 error log predates the audited mod files. |
| `state_buildings_outside_history` | error | State buildings must be declared inside the history block. |
| `state_load_error` | error | A state file could not be read during validation. |
| `strategic_region_parse_error` | error | A strategic region file could not be read. |
| `tag_definition` | error | A country tag points to a missing country definition file. |
| `undeclared_event_namespace` | error | A dotted event id uses a namespace that no add_namespace in the game, a base mod or the mod declares. |
| `undefined_recruited_character` | error | Country history recruits a character that is not defined. |
| `ungated_bba_air_oob` | error | An airframe-based air OOB is not gated behind By Blood Alone. |
| `ungated_mtg_naval_oob` | error | A hull-based naval OOB is not gated behind Man the Guns. |
| `unknown_idea_category` | error | An idea's category is not a category, slot or character_slot declared in common/idea_tags of the game, a base mod or the mod. |
| `unknown_oob_air_location` | error | An air OOB entry references an unknown state or province. |
| `unknown_oob_equipment` | error | A naval or air OOB entry uses unknown equipment. |
| `unknown_oob_equipment_owner` | error | A naval or air equipment entry uses an unknown country tag. |
| `unknown_oob_naval_location` | error | A naval OOB entry references an unknown province. |
| `unknown_oob_province` | error | A starting division references an unknown province. |
| `unknown_oob_ship_definition` | error | A ship uses an unknown unit definition. |
| `unknown_oob_template` | error | A starting division refers to an undefined template. |
| `unknown_oob_unit_type` | error | A division template uses an unknown sub-unit type. |
| `unknown_state_province` | error | A state lists a province that does not exist. |
| `unknown_strategic_region_province` | error | A strategic region lists a province that does not exist. |
| `unrecruited_character` | error | A character created for immediate recruitment is not recruited. |
| `unsupported_dynamic_country_ideas` | error | dynamic_country_ideas is not a supported HOI4 construct. |

### Warnings

| Code | Severity | Meaning |
| --- | --- | --- |
| `bookmark_default_country_missing` | warning | The default country has no bookmark entry. |
| `capital_not_cored` | warning | A country's capital state is not cored by that country. |
| `character_ref` | warning | A recruited character is not defined in mod or vanilla data. |
| `civil_war_capital_ref` | warning | A civil-war capital is not a known state ID. |
| `country_colors_shadow_vanilla` | warning | A mod colors.txt shadows vanilla country color entries that it does not repeat. |
| `country_popularity_sum` | warning | A country's party popularities do not add up to 100. |
| `country_scope_core_effect` | warning | Core add/remove effect appears outside an explicit state scope. |
| `disconnected_country_territory` | warning | Owned territory is significantly disconnected from the capital. |
| `duplicate_bookmark_country_variant` | warning | A bookmark repeats an indistinguishable country variant. |
| `duplicate_oob_grid_position` | warning | A division template repeats a battalion grid position. |
| `duplicate_oob_ship` | warning | An OOB repeats a ship name. |
| `empty_strategic_region` | warning | A strategic region has no provinces. |
| `enclosed_foreign_territory` | warning | A foreign land component is completely enclosed by the target country. |
| `event_conditional_text_missing` | warning | A conditional event title, description or option name has no text key. |
| `event_text_key_clash` | warning | An event's title or description uses the same localisation key as one of its options. |
| `flag_read_never_set` | warning | A scripted flag is read but never written by the mod or the game. Release stage only. |
| `flag_set_never_read` | warning | A scripted flag is written but never read. Release stage only. |
| `history_set_owner_in_effect` | warning | State history owner directive appears in runtime effect script. |
| `insufficient_military_commanders` | warning | A complete country needs at least two recruited army commanders. |
| `insufficient_political_advisors` | warning | A complete country needs at least two recruited political advisors. |
| `invalid_idea_icon_key` | warning | Idea definitions must use a bare picture stem, not an icon assignment. |
| `missing_country_leader` | warning | A country has no recruited character with a country-leader role. |
| `missing_country_name` | warning | No name localisation exists for a country, in any ideology variant. |
| `missing_event_description` | warning | A visible event has no description. |
| `missing_event_title` | warning | A visible event has no title. |
| `missing_land_oob` | warning | A complete country has no unambiguous land order of battle. |
| `missing_localization` | warning | Shown text (a focus, event, idea, country or leader name) has a localization key that exists nowhere: not in the mod, a base mod or the game. |
| `missing_state_owner` | warning | A state has no owner. |
| `missing_translation` | warning | English localization keys are missing from another language the mod ships. |
| `missing_wargoal_type` | warning | War goal or war declaration block is missing type = <wargoal_type>. |
| `mission_with_cost` | warning | A mission (days_mission_timeout) also declares a cost. |
| `mixed_oob_kinds` | warning | One OOB file mixes land, naval, or air content instead of using the engine's separate assignment effects. |
| `oob_ship_name` | warning | A ship has neither a name nor an ordered_name. |
| `province_missing_state` | warning | A land province belongs to no state. |
| `province_missing_strategic_region` | warning | A province belongs to no strategic region. |
| `unfired_event` | warning | A triggered-only event is fired by nothing in the mod or the game. Release stage only. |
| `ungranted_idea` | warning | A country idea is granted by nothing in the mod or the game. Release stage only. |
| `unknown_assigned_idea` | warning | Country history assigns an idea ID not loaded by the SDK. |
| `unknown_character_trait` | warning | A character uses a trait no trait file defines. |
| `unknown_country_scope` | warning | Script scopes into a tag that no country tag, tag alias or dynamic tag of the game, a base mod or the mod declares. |
| `unknown_decision_icon` | warning | Decision or decision category icon did not resolve to a sprite (the game's own decisions have about 20 such names). |
| `unknown_effect_token` | warning | Effect token is absent from installed-game documentation. |
| `unknown_equipment_reference` | warning | Effect script references an equipment ID not found in configured data. |
| `unknown_event_reference` | warning | Effect script references an event ID not loaded from the mod or configured HOI4 install. |
| `unknown_focus_icon` | warning | Focus icon GFX key was not found in interface files. |
| `unknown_focus_search_filter` | warning | A focus search filter has no GFX_<filter> sprite in the game, a base mod or the mod. |
| `unknown_focus_tree_reference` | warning | Effect script references a focus tree ID not loaded by the SDK. |
| `unknown_idea_icon` | warning | Idea picture stem did not resolve to a GFX_idea_ sprite. |
| `unknown_idea_reference` | warning | Effect script references an idea ID not loaded from the mod or configured HOI4 install. |
| `unknown_modifier_token` | warning | Modifier token is absent from installed-game documentation. |
| `unknown_state_core` | warning | A state core is not a known country tag. |
| `unknown_state_owner` | warning | A state owner is not a known country tag. |
| `unknown_state_reference` | warning | Focus effect script scopes into a state id that does not exist. |
| `unknown_tech_bonus_category` | warning | add_tech_bonus category is not in the technology category catalog. |
| `unknown_technology_reference` | warning | Effect script references a technology ID not found in configured data. |
| `unknown_trigger_token` | warning | Trigger token is absent from installed-game documentation. |
| `unloaded_missing_oob_reference` | warning | A country that owns no state at the start refers to an OOB file that does not exist. |
| `unreachable_focus` | warning | A focus cannot be reached from any root focus. Release stage only. |
| `unresolved_focus_position` | warning | A focus position has a missing or cyclic local anchor. |
| `unsupported_effect_scope` | warning | Effect token is used outside its documented scope. |
| `unsupported_trigger_scope` | warning | Trigger token is used outside its documented scope. |
| `unused_localization` | warning | A localization key is not referenced by modeled content. Release stage only. |
| `visual_overlap` | warning | Two focuses share a position and the game can show both (mutually exclusive focuses are both drawn; only allow_branch or an offset block keeps one away). |

### Advice

| Code | Severity | Meaning |
| --- | --- | --- |
| `bad_idea_tooltip_pattern` | info | An effect removes several ideas and adds one; swap_ideas usually gives a cleaner tooltip. |
| `civil_war_focus_tree_missing` | info | Civil war script starts a revolt without loading a focus tree. |
| `civil_war_scope_footgun` | info | Civil war effects need careful target/capital scope. |
| `continuous_focus_position_high` | info | The continuous focus panel starts above the bottom row of the focus tree. |
| `event_option_no_effect` | info | An event option only closes the event. That is normal for flavour events. |
| `faction_scope_footgun` | info | Faction effect direction depends on current scope. |
| `idea_mutation_collision` | info | Focuses and delayed/runtime events mutate the same idea IDs. |
| `idea_not_addable` | info | An effect adds an idea of an advisor category with add_ideas. Advisors are hired through their character, not added as ideas. |
| `idea_without_effect` | info | An idea has no modifier, bonus, rule or effect block. |
| `invalid_idea_picture_prefix` | info | An idea picture repeats the GFX_idea_ prefix. The game accepts it; the bare stem is the convention. |
| `leader_party_mismatch` | info | No recruited leader of the country leads its ruling party; the game shows a generated one. |
| `missing_optional_localization` | info | An optional localization key (an idea description, a country's _DEF name) is missing; the game falls back to other text. |
| `mission_activation_without_timeout` | info | A decision without days_mission_timeout has an activation trigger, which the game ignores. |
| `oob_air_location_not_owned` | info | An air OOB entry is outside its country's territory. |
| `oob_location_not_owned` | info | A starting division is placed outside its country's territory. |
| `oob_naval_location_not_owned` | info | A naval OOB entry is outside its country's territory. |
| `resistance_on_core_state` | info | Resistance effects target a state that is already a core of its owner. |
| `revolt_state_already_owned` | info | Revolt script transfers a state already owned by the target country. |
| `triggered_only_with_mtth` | info | A triggered-only event also has mean_time_to_happen, which the game ignores. |
| `unseen_effect_token` | info | A documented effect has zero installed-game uses. |
| `unseen_modifier_token` | info | A documented modifier has zero installed-game uses. |
| `unseen_trigger_token` | info | A documented trigger has zero installed-game uses. |
| `war_declaration_in_immediate` | info | An event declares war in immediate, before the player picks an option. |
