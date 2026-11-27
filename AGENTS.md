# AGENTS.md: HOI4 Agent SDK Instructions

Mandatory instructions for agents using the `hoi4` Python SDK to modify Hearts of Iron IV mods.

## Setup

Requires Python 3.11 or newer. The package is not on PyPI; install it from a
checkout of this repository:

```bash
python -m venv venv && ./venv/bin/pip install -e .
```

Mod projects should contain a `.hoi4.json` file:

```json
{
  "mod_path": "/path/to/my_mod",
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

For one-off automation, put scripts in `/tmp/hoi4-agent-scripts/` and run them from the mod project containing `.hoi4.json`. Do not place task-specific scripts inside the SDK checkout or any other SDK directory. Reusable scripts belong in the mod project's `scripts/` or `tools/` directory.

Run them with the Python of the environment you installed the SDK into
(`$HOI4_SDK_PYTHON` below stands for its path, for example
`<sdk checkout>/venv/bin/python`):

```bash
mkdir -p /tmp/hoi4-agent-scripts
cd /path/to/mod-project
"$HOI4_SDK_PYTHON" /tmp/hoi4-agent-scripts/task.py
```

## Required Workflow

All mutations are in memory until `save()`.

```python
from hoi4 import Focus, Mod

mod = Mod.from_config()

# Stage the game file before editing its focus tree.
mod.load_inherited_content("common/national_focus/germany.txt")
anchor = mod.get_focus("german_focus", "GER_anschluss")

# Make changes.
mod.add_focus("german_focus", Focus(
    id="GER_new_focus", x=anchor.x, y=anchor.y + 1, requires="GER_anschluss",
))
mod.set_focus_loc("GER_new_focus", "New Focus", "What it does.")

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

Use `mod.discard()` to throw away every unsaved change; it also picks up files
changed on disk. Use a transaction for dry runs:

```python
with mod.transaction():
    mod.set_loc("GER_new_focus", "A Trial Name")  # thrown away on exit
    print(mod.preview())
```

Lookups and changes raise instead of returning `None` or `False`:

- `get_x(id)` raises `NotFoundError` (a `KeyError`) for a missing id; its
  message names close matches. Pass `default=None` to get `None` instead.
- `create_x(id, ...)` raises `AlreadyExistsError` (a `ValueError`) for an id
  that exists. Use `ensure_x()` when a script may run twice, or
  `overwrite=True` to replace on purpose.
- `update_x()`, `delete_x()` and `remove_x()` raise `NotFoundError` for a
  missing target. Deleting game content raises `ReadOnlyError`: override it
  instead.

All four classes are importable from `hoi4` (`Hoi4Error` is their base). See
[Errors](docs/api.md#errors).

## Hard Rules

1. Mutate and save using the same `Mod` instance. Dirty state is process-local. Loading a fresh `Mod` and calling `save()` will not save changes made by another process.

2. Inspect the returned `SaveResult` and print it. Use `save(require_changes=True)` for agent scripts that are expected to write files.

3. `create_country(capital=...)` only writes the country's capital field. It does not transfer ownership, add cores, or modify the state file. Use `mod.set_state_owner(state_id, tag)` and `mod.add_state_core(state_id, tag)` explicitly.

4. Event and focus block fields contain the block body without outer braces: `mean_time_to_happen="days = 1"`.

5. `set_state_owner()` writes state history files. Event and focus rewards are runtime effects; use `effects.transfer_state(state_id, tag)` there, not `set_owner = ...`.

6. Never use bare country-scope core effects such as:

   ```text
   add_core_of = TAG
   ```

   Use state-scoped helpers:

   ```python
   from hoi4 import effects

   effects.add_state_core(4, "GER")      # 4 = { add_core_of = GER }
   effects.remove_state_core(4, "AUS")   # 4 = { remove_core_of = AUS }
   ```

   The builders live in the `hoi4.effects` module, not on `Mod`. Each returns
   the script text as a `str`.

7. Prefer SDK helpers over manually assembled Paradox script: `effects.transfer_state_with_core(...)`, `effects.transfer_state(...)`, `effects.swap_idea(...)`, `effects.upgrade_idea_chain(...)`, `effects.add_political_power(...)`, `effects.add_war_support(...)`, `effects.add_manpower(...)`, `effects.add_army_experience(...)`, `effects.add_navy_experience(...)`, `effects.add_equipment(...)`, `effects.set_technology(...)`, `effects.set_politics(...)`, `effects.create_faction(...)`, `effects.add_target_to_faction(...)`, `effects.join_faction(...)`, `effects.white_peace(...)`, `effects.spawn_revolution(...)`, `effects.convert_existing_or_spawn_revolt(...)`, `effects.load_focus_tree(...)`, `effects.spawn_civil_war_with_focus_tree(...)`, `effects.release(...)`, `effects.release_puppet(...)`, `effects.end_puppet(...)`, `effects.set_autonomy(...)`, `effects.convert_puppet_to_ally(...)`, `effects.add_civilian_factory(...)`, `effects.add_bunker(...)`, `effects.add_tech_bonus(...)`, `effects.declare_war_from(...)`, `effects.scope_block(...)`.

8. Search rather than guess:

   ```python
   mod.find_state("Sicily")
   mod.suggest_tags("Sicily", count=1)
   mod.is_country_tag_available("SIC")
   ```

   `find_state()` resolves `STATE_<id>` localization before stale filenames.
   Inspect each result's `display_name`, `file_name`, and `matched` fields.

9. Focus prerequisites use `list[list[str]]`:

   ```python
   prerequisites=[["FOCUS_A", "FOCUS_B"], ["FOCUS_C"]]
   ```

   This means `(FOCUS_A OR FOCUS_B) AND FOCUS_C`.

10. `validate()` is advisory and does not block `save()`. Explicitly stop when errors are present. Use `validate(stage="build")` between multi-script pipeline steps, the default `stage="package"` for complete-country enforcement, and `validate(stage="release", validate_icons=True, strict_localization=True)` before final saves. Release-stage validation also audits focus/event/idea/flag/localization liveness. Known false-positive warnings can be hidden with stable codes from `VALIDATION_WARNING_CODES`, for example `mod.validate(suppress=["country_scope_core_effect"])` or `"code:ID"` for one object; errors cannot be suppressed.
    Every finding has a severity: `error`, `warning` or `info` (advice). `validate()` hides advice unless you pass `advice=True`; `validate_effect()` shows it unless `advice=False`.
    Every code and its severity is listed in [`docs/validation.md`](docs/validation.md).

11. Use idempotent/patch-style helpers when editing existing content: `ensure_idea()`, `ensure_event()`, `ensure_focus_tree()`, `ensure_focus()`, `insert_focus_after()`, `insert_branch()`, `append_to_focus_reward()`, `set_focuses_mutually_exclusive()`. The `ensure_*` helpers work on the mod's own content: for an id that only the game or a base mod defines they raise `ReadOnlyError` instead of putting a new definition on top of it. The exception is `ensure_on_action()`: hooks of all files add up, so it adds a hook of the mod's own.

12. Use `get_country_context(tag)` before generating country-specific content. Pass `copy_states=True` before directly modifying vanilla states.

13. Use `create_event(..., overwrite=True)` only when replacing an existing event. Use `update_event()` or `update_event_option()` for targeted edits.

14. Register startup hooks with `create_on_action()`, not manual `common/on_actions` file edits. Use `effects.schedule_country_event(...)` for delayed event firing.

15. New ideas default to current HOI4 `common/ideas/...` files and `ideas = { country = { ... } }`. Use `create_idea(..., category="political_advisor")` or another explicit category for non-spirit ideas. National spirits and other ideas use `picture = <bare_stem>`, never `icon =` and never `picture = GFX_idea_<stem>`. HOI4 prepends `GFX_idea_` itself, so `picture = generic_political_support` resolves the sprite declaration `GFX_idea_generic_political_support` in `interface/*.gfx`. The compatibility `icon=` API argument accepts either spelling but normalizes to the bare stem. Validate both the assignment shape and sprite resolution before treating the idea as renderable.

    Idea descriptions are localization-only: set `{idea_id}_desc` with
    `set_loc()`. Never write `desc =` inside an idea. Character roles are direct
    `country_leader`, `advisor`, or commander blocks, never `roles = { ... }`.
    Decision-category metadata belongs in `common/decisions/categories`, while
    decision definitions belong in `common/decisions`.

16. Every `create_*()` method (`create_focus_tree()`, `create_decision_category()`, `create_decision()`, `create_idea()`, ...) raises `AlreadyExistsError` on an existing ID unless `overwrite=True`. Use the matching `ensure_*()` for a step that may run twice.

17. Use `Focus(..., requires="FOCUS_ID")` for a single prerequisite instead of manually writing `prerequisites=[["FOCUS_ID"]]`.

18. For generated focus trees, run `auto_layout_branch()`, `place_continuous_focus_below_tree()`, and `assert_no_visual_overlap()` before saving.

19. Use `preview_summary()` alongside `preview()` in long scripts so the agent log contains a compact semantic change list.

20. State edits (`set_state_owner()`, `add_state_core()`, `remove_state_core()`, `update_state()`) change only the statements they touch; `update_state(id, owner=..., add_cores=[...], remove_cores=[...])` sets the owner and several cores in one call, and `set_state_owner()` takes one state id or a list. A state is identified by the `id` directly inside its own `state = { }` block, not by its file name.

21. Avoid bare `add_to_faction = TAG`. Use `effects.add_target_to_faction(leader, target)` or `effects.join_faction(actor, leader)` so faction direction is explicit.

22. State serialization preserves unmodeled vanilla content. Do not rewrite entire state files manually unless specifically required.

23. For civil-war countries that need custom focus trees, use `effects.spawn_civil_war_with_focus_tree(...)`. It nests the tree load inside `start_civil_war`, where HOI4 scopes it to the spawned country. Do not guess dynamic `D01`-style rebel tags.

24. For revolt chains where the tag might already exist or be a puppet, use `effects.convert_existing_or_spawn_revolt(...)` or explicit puppet helpers. Do not assume the target tag is unreleased.

25. Do not use `add_resistance` as generic unrest on states that are cores of their current owner. Use flags, variables, decisions, and events for unrest/revolt progression.

26. Triggered lore events should have at least one gameplay effect in each option, even if it is only a flag. Use `create_decision_chain()` for staged flag/event/cleanup decision chains and `create_recovery_decision()` for live-save repair hooks.

27. Use native `create_dynamic_modifier()` and
    `effects.add_dynamic_modifier()`. `dynamic_country_ideas` is not a HOI4
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
    Every advisor needs a `small` portrait sprite (on any channel: civilian, army or navy), and an air-capable
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
    recognizes that exact safety invariant while still reporting (as advice)
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

36. Never edit mod script files with regular expressions, `str.replace`, or
    by writing the whole file yourself. Use the `Mod` API. `save()` changes
    only the edited statements and keeps each file's encoding, byte-order mark
    and line endings, so do not convert line endings or re-encode files
    afterwards. For an edit the API cannot express, use the span helpers in
    `hoi4.patching` (see `docs/internals/parser.md`).

37. Check `mod.load_diagnostics` (or load with `strict_loading=True`) before
    editing an existing mod. A malformed file in the mod's own folder, such as
    one with an unclosed brace, is not loaded, and `save()` refuses to rewrite
    any mod file that failed to load, or to create a new override of a game
    file in the same folder. Fix the file
    and call `discard()`. Files from the game or a base mod with unbalanced
    braces load the way the game reads them.

38. Top-level `shared_focus`, `joint_focus` and bare `focus` blocks of a file
    load into one container `FocusTree` with `kind == "file"` whose ID is the
    file name without `.txt`. The game has no tree with that ID: never use it
    in `load_focus_tree` or a country selector. Edit those focuses with
    `update_focus(<file stem>, focus_id, ...)`; `Focus.kind` names the block
    key. `Focus.cost` may be fractional (`7.2`).

39. An event title, description or option name can have conditional
    alternatives (`title = { text = KEY trigger = { ... } }`). `Event.title`,
    `Event.description` and `EventOption.name` hold only the unconditional
    key; the others are in `conditional_titles`, `conditional_descriptions`
    and `conditional_names` (`EventText` values). Passing `title=`,
    `description=` or `name=` to an update changes only the plain key; pass
    the conditional list (an empty list removes it) to change the others.

## Country Visual Completeness

A broad request to create, release, restore, or make a country independent
implicitly includes a complete player-facing country package even when the user
does not separately ask for graphics. Do not stop at a tag, state transfer, and
generic portrait. The default package includes:

- one appropriate flag, imported at all three HOI4 sizes;
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
requested art direction, prefer suitable historical flags and portraits from
existing assets or Wikimedia Commons. A photograph is usable source material,
but import conversion does not turn it into a painted HOI4 portrait.
Do not silently omit the graphics or call an incomplete package complete.

## Web-first Image Sourcing

Prefer suitable existing or user-supplied assets, then search Wikimedia Commons
with `CommonsImageClient` before considering paid generation. Broad country
creation authorizes sourcing, downloading, and importing suitable images after
review; no additional approval is needed for that workflow. A missing Gemini
key does not block local or web-sourced graphics.

1. Use `CommonsImageClient.search(query, limit=5, thumbnail_width=1024)` and
   `get_image("File:...")` to inspect candidates. Search and metadata lookup use
   the standard library and require no API key. Check the depicted person's
   identity, date, role, or flag variant against the scenario, plus the source
   page and license. Search ranking alone does not establish historical fit.
2. Download the selected `CommonsImage` with
   `client.download(image, project_root / "assets" / "sources")`. Downloads
   require the `assets` extra and use a raster thumbnail for SVG sources.
   Preserve downloads in durable `assets/sources/`. Keep
   the returned `path`, `metadata_path`, and `sha256` together: the JSON records
   provenance, license, artist, and credit. Retain required attribution with the
   distributed mod. Metadata supports review; it is not automatic legal
   certification.
3. Inspect the full downloaded image and an exact-size preview: 10x7 for flags,
   156x210 for portraits. Check crop, historical identity, and readability.
4. Import the reviewed durable source with `mod.import_flag_to_mod()` or
   `mod.import_portrait_to_mod()` plus `mod.write_portrait_gfx()`. Validate,
   inspect `preview_summary()` and `preview()`, then save with the same `Mod`
   instance and inspect the `SaveResult` as required above.

See `docs/api.md` and `docs/recipes.md` for the Wikimedia Commons API and examples.
If no suitable source is found, report the specific missing asset and continue
other work. Use Gemini only when generation is explicitly desired or necessary
and paid generation is authorized. A failed search or an available key does
not authorize billable fallback.

## Gemini Image Candidate Workflow

Gemini image generation is optional and billable. When generation is requested,
check only whether `GEMINI_API_KEY` or `GOOGLE_API_KEY` is present; never print
its value. If missing, explain that this generation route requires a
billing-enabled Gemini API key from <https://aistudio.google.com/> and which
environment variable to set; continue available local/web sourcing and other
work. Before authorized billable calls, state the planned asset count and that
each asset may require up to three candidates. Install `.[gemini]`, keep the
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
6. When paid generation and asset import are authorized, import immediately
   after review. A broad country-creation request authorizes import, but does
   not by itself authorize paid generation. Otherwise show the candidate and
   wait for approval before touching the mod.

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
- How the `Mod` class is split into layer modules: `docs/internals/mod_layers.md`
- How content loads on first use and what reloads after a save: `docs/internals/mod_sections.md`

Prefer the `Mod` facade. Use the low-level parser only when the public API cannot represent the required modification.
