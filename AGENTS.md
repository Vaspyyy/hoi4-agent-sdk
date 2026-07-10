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

9. Focus prerequisites use `list[list[str]]`:

   ```python
   prerequisites=[["FOCUS_A", "FOCUS_B"], ["FOCUS_C"]]
   ```

   This means `(FOCUS_A OR FOCUS_B) AND FOCUS_C`.

10. `validate()` is advisory and does not block `save()`. Explicitly stop when errors are present. Use `validate(validate_icons=True, strict_localization=True)` before final saves when changing focus icons or localization. Known false-positive warnings can be hidden with stable codes from `VALIDATION_WARNING_CODES`, for example `mod.validate(suppress_warnings=["country_scope_core_effect"])`.

11. Use idempotent/patch-style helpers when editing existing content: `ensure_idea()`, `ensure_event()`, `ensure_focus_tree()`, `upsert_focus()`, `insert_focus_after()`, `insert_branch()`, `append_to_focus_reward()`, `set_focuses_mutually_exclusive()`.

12. Use `get_country_context(tag)` before generating country-specific content. Pass `copy_states=True` before directly modifying vanilla states.

13. Use `create_event(..., overwrite=True)` only when replacing an existing event. Use `update_event()` or `update_event_option()` for targeted edits.

14. Register startup hooks with `create_on_action()`, not manual `common/on_actions` file edits. Use `Mod.effect_schedule_country_event(...)` for delayed event firing.

15. New ideas default to current HOI4 `common/ideas/...` files and `ideas = { country = { ... } }`. Use `create_idea(..., category="political_advisor")` or another explicit category for non-spirit ideas.

16. `create_focus_tree()`, `create_decision_category()`, `create_decision()`, and `create_idea()` also raise on existing IDs unless `overwrite=True`.

17. Use `Focus(..., requires="FOCUS_ID")` for a single prerequisite instead of manually writing `prerequisites=[["FOCUS_ID"]]`.

18. For generated focus trees, run `auto_layout_branch()`, `place_continuous_focus_below_tree()`, and `assert_no_visual_overlap()` before saving.

19. Use `preview_summary()` alongside `preview()` in long scripts so the agent log contains a compact semantic change list.

20. Use `patch_state_history()` for owner/core-only changes to fragile vanilla states when preserving exact unrelated text matters.

21. Avoid bare `add_to_faction = TAG`. Use `effect_add_target_to_faction(leader, target)` or `effect_join_faction(actor, leader)` so faction direction is explicit.

22. State serialization preserves unmodeled vanilla content. Do not rewrite entire state files manually unless specifically required.

23. For civil-war countries that need custom focus trees, use `Mod.effect_spawn_civil_war_with_focus_tree(...)` or immediately pair `start_civil_war` with `Mod.effect_load_focus_tree(...)`. Do not rely only on focus tree selectors for dynamic rebel tags.

24. For revolt chains where the tag might already exist or be a puppet, use `Mod.effect_convert_existing_or_spawn_revolt(...)` or explicit puppet helpers. Do not assume the target tag is unreleased.

25. Do not use `add_resistance` as generic unrest on states that are cores of their current owner. Use flags, variables, decisions, and events for unrest/revolt progression.

26. Triggered lore events should have at least one gameplay effect in each option, even if it is only a flag. Use `create_decision_chain()` for staged flag/event/cleanup decision chains and `create_recovery_decision()` for live-save repair hooks.

## Documentation

Read only the documentation relevant to the current task:

- Public methods and signatures: `docs/api.md`
- Examples and common workflows: `docs/recipes.md`
- Dataclass fields: `docs/models.md`
- Effect, modifier, and technology catalogs: `docs/catalogs.md`
- Common AI agent mistakes: `docs/agent_traps.md`
- Low-level parser internals: `docs/internals/parser.md`

Prefer the `Mod` facade. Use the low-level parser only when the public API cannot represent the required modification.
