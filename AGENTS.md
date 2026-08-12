# AGENTS.md — HOI4 Agent SDK Instructions

Mandatory instructions for agents using the `hoi4` Python SDK to modify Hearts of Iron IV mods.

## Setup

Requires Python 3.11 or newer.

```bash
python -m venv venv && ./venv/bin/pip install -e .
```

Mod projects should contain a `.hoi4.json` file:

```json
{
  "mod_path": "/path/to/mod",
  "hoi4_install": "/path/to/Hearts of Iron IV"
}
```

Load it with:

```python
from hoi4 import Mod
mod = Mod.from_config()
```

`hoi4_install` is optional unless vanilla fallback data is needed.

## Script Location

For one-off automation, put scripts in `/tmp/hoi4-agent-scripts/` and run them from the mod project containing `.hoi4.json`. Do not place task-specific scripts inside `/home/ransom/Projekte/hoi4-agent-sdk` or any SDK repo directory. Reusable scripts belong in the mod project's `scripts/` or `tools/` directory.

```bash
mkdir -p /tmp/hoi4-agent-scripts
cd /path/to/mod-project
/home/ransom/Projekte/hoi4-agent-sdk/venv/bin/python /tmp/hoi4-agent-scripts/task.py
```

## Required Workflow

All mutations are in memory until `save()`.

```python
from hoi4 import Mod

mod = Mod.from_config()

# Inspect existing data.
tree = mod.get_focus_tree("german_focus")

# Make changes.
# ...

# Validate.
errors = mod.validate()
for error in errors:
    print(f"[{error.severity}] {error.message}")
if any(error.severity == "error" for error in errors):
    raise RuntimeError("Validation failed")

# Review changes.
print(mod.preview())

# Save and inspect the result.
result = mod.save(require_changes=True)
print(result)
print(result.written_files)
```

Use `mod.discard()` to throw away changes and reload from disk. Use a transaction for dry runs:

```python
with mod.transaction():
    # Make temporary changes.
    print(mod.preview())
```

## Hard Rules

1. Mutate and save using the same `Mod` instance. Dirty state is process-local. Loading a fresh `Mod` and calling `save()` will not save changes made by another process.

2. Inspect the returned `SaveResult` and print it. Use `save(require_changes=True)` for agent scripts that are expected to write files.

3. `create_country(capital=...)` only writes the country's capital field. It does not transfer ownership, add cores, or modify the state file. Use `mod.set_state_owner(state_id, tag)` and `mod.add_state_core(state_id, tag)` explicitly.

4. Event and focus block fields contain the block body without outer braces: `mean_time_to_happen="days = 1"`.

5. `set_state_owner()` writes state history files. Event and focus rewards are runtime effects; use `Mod.effect_transfer_state(state_id, tag)` there, not `set_owner = ...`.

6. Never use bare country-scope core effects such as:

   ```text
   add_core_of = TAG
   ```

   Use state-scoped helpers:

   ```python
   Mod.effect_add_state_core(state_id, tag)
   Mod.effect_remove_state_core(state_id, tag)
   ```

7. Prefer SDK helpers over manually assembled Paradox script: `Mod.effect_transfer_state_with_core(...)`, `Mod.effect_transfer_state(...)`, `Mod.effect_swap_idea(...)`, `Mod.effect_upgrade_idea_chain(...)`, `Mod.effect_add_political_power(...)`, `Mod.effect_add_war_support(...)`, `Mod.effect_add_manpower(...)`, `Mod.effect_add_army_experience(...)`, `Mod.effect_add_navy_experience(...)`, `Mod.effect_add_equipment(...)`, `Mod.effect_set_technology(...)`, `Mod.effect_set_politics(...)`, `Mod.effect_create_faction(...)`, `Mod.effect_add_target_to_faction(...)`, `Mod.effect_join_faction(...)`, `Mod.effect_white_peace(...)`, `Mod.effect_spawn_revolution(...)`, `Mod.effect_convert_existing_or_spawn_revolt(...)`, `Mod.effect_load_focus_tree(...)`, `Mod.effect_spawn_civil_war_with_focus_tree(...)`, `Mod.effect_release(...)`, `Mod.effect_release_puppet(...)`, `Mod.effect_end_puppet(...)`, `Mod.effect_set_autonomy(...)`, `Mod.effect_convert_puppet_to_ally(...)`, `Mod.effect_add_civilian_factory(...)`, `Mod.effect_add_bunker(...)`, `Mod.effect_add_tech_bonus(...)`, `Mod.effect_declare_war_from(...)`, `Mod.scope_block(...)`.

8. Search rather than guess:

   ```python
   mod.find_state("Sicily")
   mod.suggest_tag("Sicily")
   mod.is_country_tag_available("SIC")
   ```

   `find_state()` resolves `STATE_<id>` localization before stale filenames.
   Inspect each result's `display_name`, `file_name`, and `matched` fields.

9. Focus prerequisites use `list[list[str]]`:

   ```python
   prerequisites=[["FOCUS_A", "FOCUS_B"], ["FOCUS_C"]]
   ```

   This means `(FOCUS_A OR FOCUS_B) AND FOCUS_C`.

10. `validate()` is advisory and does not block `save()`. Explicitly stop when errors are present. Use `validate(stage="build")` between multi-script pipeline steps, the default `stage="package"` for complete-country enforcement, and `validate(stage="release", validate_icons=True, strict_localization=True)` before final saves. Release-stage validation also audits focus/event/idea/flag/localization liveness. Known false-positive warnings can be hidden with stable codes from `VALIDATION_WARNING_CODES`, for example `mod.validate(suppress_warnings=["country_scope_core_effect"])`.

11. Use idempotent/patch-style helpers when editing existing content: `ensure_idea()`, `ensure_event()`, `ensure_focus_tree()`, `upsert_focus()`, `insert_focus_after()`, `insert_branch()`, `append_to_focus_reward()`, `set_focuses_mutually_exclusive()`.

12. Use `get_country_context(tag)` before generating country-specific content. Pass `copy_states=True` before directly modifying vanilla states.

13. Use `create_event(..., overwrite=True)` only when replacing an existing event. Use `update_event()` or `update_event_option()` for targeted edits.

14. Register startup hooks with `create_on_action()`, not manual `common/on_actions` file edits. Use `Mod.effect_schedule_country_event(...)` for delayed event firing.

15. New ideas default to current HOI4 `common/ideas/...` files and `ideas = { country = { ... } }`. Use `create_idea(..., category="political_advisor")` or another explicit category for non-spirit ideas. National spirits and other ideas use `picture = <bare_stem>`, never `icon =` and never `picture = GFX_idea_<stem>`. HOI4 prepends `GFX_idea_` itself, so `picture = generic_political_support` resolves the sprite declaration `GFX_idea_generic_political_support` in `interface/*.gfx`. The compatibility `icon=` API argument accepts either spelling but normalizes to the bare stem. Validate both the assignment shape and sprite resolution before treating the idea as renderable.

    Idea descriptions are localization-only: set `{idea_id}_desc` with
    `set_loc()`. Never write `desc =` inside an idea. Character roles are direct
    `country_leader`, `advisor`, or commander blocks, never `roles = { ... }`.
    Decision-category metadata belongs in `common/decisions/categories`, while
    decision definitions belong in `common/decisions`.

16. `create_focus_tree()`, `create_decision_category()`, `create_decision()`, and `create_idea()` also raise on existing IDs unless `overwrite=True`.

17. Use `Focus(..., requires="FOCUS_ID")` for a single prerequisite instead of manually writing `prerequisites=[["FOCUS_ID"]]`.

18. For generated focus trees, run `auto_layout_branch()`, `place_continuous_focus_below_tree()`, and `assert_no_visual_overlap()` before saving.

19. Use `preview_summary()` alongside `preview()` in long scripts so the agent log contains a compact semantic change list.

20. Use `patch_state_history()` for owner/core-only changes to fragile vanilla states when preserving exact unrelated text matters.

21. Avoid bare `add_to_faction = TAG`. Use `effect_add_target_to_faction(leader, target)` or `effect_join_faction(actor, leader)` so faction direction is explicit.

22. State serialization preserves unmodeled vanilla content. Do not rewrite entire state files manually unless specifically required.

23. For civil-war countries that need custom focus trees, use `Mod.effect_spawn_civil_war_with_focus_tree(...)`. It nests the tree load inside `start_civil_war`, where HOI4 scopes it to the spawned country. Do not guess dynamic `D01`-style rebel tags.

24. For revolt chains where the tag might already exist or be a puppet, use `Mod.effect_convert_existing_or_spawn_revolt(...)` or explicit puppet helpers. Do not assume the target tag is unreleased.

25. Do not use `add_resistance` as generic unrest on states that are cores of their current owner. Use flags, variables, decisions, and events for unrest/revolt progression.

26. Triggered lore events should have at least one gameplay effect in each option, even if it is only a flag. Use `create_decision_chain()` for staged flag/event/cleanup decision chains and `create_recovery_decision()` for live-save repair hooks.

27. Use native `create_dynamic_modifier()` and
    `effect_add_dynamic_modifier()`. `dynamic_country_ideas` is not a HOI4
    construct and validation rejects it.

28. A new mod-only country keeps its color in
    `common/countries/{TAG}.txt`; it must not create a partial global
    `common/countries/colors.txt`. Vanilla color overrides require a configured
    HOI4 install so the complete table can be retained.

29. With `hoi4_install` configured, keep static vocabulary warnings enabled.
    An undocumented effect, trigger, or modifier with zero installed-game uses
    is evidence of a typo; inspect its suggested high-frequency replacement.
    Allowlist only intentional extensions with kind-qualified entries such as
    `effect:my_scripted_effect`.

30. After a real game launch, run `scripts/parse_hoi4_log.py` or
    `Mod.validate_game_log()` against the target mod. Do not treat a static
    release gate as proof that HOI4 accepted every generated token. The parser
    attributes quoted and unquoted `file:` paths in any mod-owned directory,
    including `taskforce.cpp` equipment-variant failures.

31. Never hand-write a character roster or starting OOB when the public
    models can express it. Use `Character` plus explicit role/instance mutation
    methods, and `create_oob()` with templates, battalions, divisions,
    fleets/task forces/ships, and air wings.
    Repeated roles or DLC instances require an occurrence selector; do not
    guess. Before reporting any SDK-created country complete, require
    `mod.validate_country_package(tag).complete`.

    Recruit characters only through country history. HOI4 rejects
    `recruit_character` in events, focuses, decisions, on-actions, and
    scripted effects; gate a later role unlock with the role's
    `available`/`visible` trigger instead.
    Every advisor needs a civilian `small` portrait sprite, and an air-capable
    new country needs a `set_country_name_pool()` entry for generated names.

32. Treat `unsupported_effect_scope` and `unsupported_trigger_scope` as real
    domain warnings. The installed documentation declares where tokens are
    legal, and validation follows explicit country, state, character, and
    iterator blocks. `validate_effect()` assumes country scope; pass the
    fragment's actual `scope=` or `None` when only explicit nested scopes are
    known.

33. Do not suppress `idea_mutation_collision` merely because a focus and event
    mention the same spirit. Guard a runtime fallback with
    `if = { limit = { NOT = { has_idea = X } } add_ideas = X }`; validation
    recognizes that exact safety invariant while preserving warnings for
    unguarded, mismatched, removal, and swap mutations.

34. Keep land, naval, and air OOBs in separate files. Pass `kind="land"`,
    `kind="naval"`, or `kind="air"`; the SDK assigns them through `set_oob`,
    `set_naval_oob`, or `set_air_oob`. For Man the Guns, define hull variants
    with `create_equipment_variant()`, require `version_name` on every hull,
    gate the hull OOB with `required_dlc=("Man the Guns",)`, and provide a
    legacy naval OOB with `excluded_dlc=("Man the Guns",)`. Never load legacy
    ship equipment while Man the Guns may be active: the engine skips every
    unresolved ship.

    Apply the same split for By Blood Alone airframes: gate the airframe OOB
    and variants with `required_dlc=("By Blood Alone",)` and provide a legacy
    air OOB with that DLC excluded.
    Grant the hull/airframe's enabling chassis technology before creating its
    modular variant, in the same DLC branch or a broader one that covers it.
    A later grant or a grant in the mutually exclusive fallback branch does
    not count. Nested `AND`, `OR`, and `NOT` DLC gates retain their Boolean
    meaning. Mixed non-DLC predicates remain correlated within their own
    `if`/`else` chain but are not assumed identical across separate condition
    checks. `update_country(..., technologies=...)` places the grant before
    both existing and subsequently created variants.
    `allow_without_tech=yes` does not create the chassis and the engine will
    reject the variant.

35. Treat `.complete` and `ContentLivenessReport.clean` as structural results,
    not proofs of dynamic achievability. Review popularity/variable threshold
    arithmetic and mutually dependent trigger chains, then live-test important
    runtime releases and endings before calling them playable.

## Country Visual Completeness

A broad request to create, release, restore, or make a country independent
implicitly includes a complete player-facing country package even when the user
does not separately ask for graphics. Do not stop at a tag, state transfer, and
generic portrait. The default package includes:

- one original flag, imported at all three HOI4 sizes;
- one head-of-state portrait;
- portraits for every newly created visible character, with a coherent roster
  containing at least two political advisors and two military commanders;
- for a country that exists at scenario start, an assigned land OOB with at
  least one valid template and starting division, with every division placed
  in an existing owned land province;
- for a country released later, an explicit focus/event/decision/on-action
  activation path that grants territory and correctly establishes its capital;
- appropriate service chiefs, high-command officers, theorists, field marshals,
  or admirals when the country's history and gameplay scope warrant them; and
- matching character definitions, roles, recruitment/history entries,
  localization, portrait DDS files, and sprite declarations.

Build the roster through `create_character()` and the role models. Recruit the
character in country history and gate event-driven role availability; never
emit runtime `recruit_character`. Build starting land forces through
`create_oob(..., assign=True)`. Do not assign a
starting OOB or starting ownership merely to satisfy validation for a tag that
does not exist at scenario start; `validate_country_package()` classifies
evidence-backed focus/event releases as `runtime`. Run
`find_disconnected_states()` and `find_enclosed_foreign_states()` when the
`map` extra is available, and allowlist only deliberate islands or overseas
components. `CountryPackageReport.complete` is the final structural package
check, not merely a clean preview or successful save; it does not prove that a
runtime threshold chain is achievable.

Prefer real people who plausibly held or could have held each position at the
scenario date. Research the historical fit instead of inventing a famous person
at random. If a fictional person is necessary, make that clear. Without a
requested art direction, use the built-in HOI4-compatible flag and historical
portrait styles.

Before promising this package, check only whether `GEMINI_API_KEY` or
`GOOGLE_API_KEY` is present; never print its value. If neither is available,
tell the user that proper custom flags and character portraits require a
billing-enabled Gemini API key from <https://aistudio.google.com/>, explain
which environment variable to set, and continue any safe non-visual work that
can be completed. Do not silently omit the graphics or describe the country as
complete. A broad country-creation request counts as authorization to generate
and import the package once a key is available. Before billable calls, state the
planned asset count and remember that each asset may require up to three
candidates.

## Gemini Image Candidate Workflow

Gemini image generation is optional and billable. Install `.[gemini]`, keep the
credential only in `GEMINI_API_KEY` or `GOOGLE_API_KEY`, and never write it to
`.hoi4.json`, scripts, logs, prompts, or mod files.

For an AI-generated flag or portrait:

1. Resolve the mod project directory containing `.hoi4.json` and generate each
   PNG candidate under its durable `assets/candidates/` directory with
   `GeminiImageGenerator`. Use a unique filename for every billable attempt.
   Generated candidates are paid, non-reproducible build inputs and must
   survive reboots; never place them in an operating-system temporary
   directory. Supply reference-image paths only when the user explicitly
   provided or authorized those exact files; never discover or upload game or
   mod assets automatically.
2. Inspect the full PNG and an exact-size preview: 10x7 for flags and 156x210
   for portraits.
3. Reject and regenerate with concrete corrective prompt language when anatomy,
   composition, symbols, colors, crop margins, or tiny-size readability fail.
   For flags, specify an exhaustive layout: exact bands, exact symbol counts,
   allowed color roles, forbidden additions, and minimum relative symbol size.
   Treat persistent gradients or shading as a failed flat-vexillology candidate.
   For historical portraits, use positive descriptions of the person's year,
   role, age, expression, and period clothing. Do not name extremist or violent
   imagery merely to say it should be absent; that can trigger safety filtering.
4. Stop after three billable candidates. If none passes, show the best
   candidate, retain the generated PNGs in `assets/candidates/`, and leave the
   mod unchanged.
5. Keep the first passing PNG in `assets/candidates/` before importing it.
   Import that durable source with `mod.import_flag_to_mod()`. Import a passing
   portrait with `mod.import_portrait_to_mod()` and then
   `mod.write_portrait_gfx()` so the files participate in preview,
   transactions, and atomic save.
6. When the user's original request explicitly authorized asset creation,
   import immediately after review. A broad country-creation request covered by
   the visual-completeness policy is such authorization. Otherwise show the
   candidate and wait for approval before touching the mod.

If no style is requested, keep the generator's built-in HOI4 flag/portrait
presets. A supplied style replaces only the aesthetic; preserve the no-text,
safe-crop, and small-size-readability constraints. One generator call is one
billable attempt: do not add retries or silently switch models. The separate
`/tmp/hoi4-agent-scripts/` convention remains correct for disposable one-off
automation and must not be used for generated art.

## Documentation

Read only the documentation relevant to the current task:

- Public methods and signatures: `docs/api.md`
- Examples and common workflows: `docs/recipes.md`
- Dataclass fields: `docs/models.md`
- Effect, modifier, and technology catalogs: `docs/catalogs.md`
- Common AI agent mistakes: `docs/agent_traps.md`
- Low-level parser internals: `docs/internals/parser.md`

Prefer the `Mod` facade. Use the low-level parser only when the public API cannot represent the required modification.
