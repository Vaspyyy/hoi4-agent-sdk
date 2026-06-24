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

7. Prefer SDK helpers over manually assembled Paradox script: `Mod.effect_transfer_state_with_core(...)`, `Mod.effect_transfer_state(...)`, `Mod.effect_add_political_power(...)`, `Mod.effect_add_war_support(...)`, `Mod.effect_add_manpower(...)`, `Mod.effect_add_equipment(...)`, `Mod.effect_set_technology(...)`, `Mod.effect_add_civilian_factory(...)`, `Mod.effect_add_bunker(...)`, `Mod.effect_add_tech_bonus(...)`, `Mod.effect_declare_war_from(...)`, `Mod.scope_block(...)`.

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

10. `validate()` is advisory and does not block `save()`. Explicitly stop when errors are present. Known false-positive warnings can be hidden with stable codes from `VALIDATION_WARNING_CODES`, for example `mod.validate(suppress_warnings=["country_scope_core_effect"])`.

11. Use patch-style focus helpers when editing existing trees: `insert_focus_after()`, `insert_branch()`, `append_to_focus_reward()`, `set_focuses_mutually_exclusive()`.

12. Use `get_country_context(tag)` before generating country-specific content. Pass `copy_states=True` before directly modifying vanilla states.

13. Use `create_event(..., overwrite=True)` only when replacing an existing event. Use `update_event()` or `update_event_option()` for targeted edits.

14. `create_focus_tree()`, `create_decision_category()`, `create_decision()`, and `create_idea()` also raise on existing IDs unless `overwrite=True`.

15. Use `Focus(..., requires="FOCUS_ID")` for a single prerequisite instead of manually writing `prerequisites=[["FOCUS_ID"]]`.

16. State serialization preserves unmodeled vanilla content. Do not rewrite entire state files manually unless specifically required.

## Documentation

Read only the documentation relevant to the current task:

- Public methods and signatures: `docs/api.md`
- Examples and common workflows: `docs/recipes.md`
- Dataclass fields: `docs/models.md`
- Effect, modifier, and technology catalogs: `docs/catalogs.md`
- Low-level parser internals: `docs/internals/parser.md`

Prefer the `Mod` facade. Use the low-level parser only when the public API cannot represent the required modification.
