# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed (breaking, 0.7.0)

The public API of `hoi4.Mod` was cleaned up: one error hierarchy, lookups and
changes that raise instead of returning `False` or `None`, return values that
say what you got, no duplicate entry points, and the script builders moved to
`hoi4.effects`. Every row below is one old call and what to write instead.

| Old call (0.6) | New call (0.7) | Why |
|---|---|---|
| `except KeyError` / `except IndexError` around a lookup | `except hoi4.NotFoundError` (still a `KeyError`, no longer an `IndexError`) | One error for "not there"; the message names close matches instead of every id |
| `get_on_action(..., occurrence=n)`, `update_on_action(..., occurrence=n)`, `delete_on_action(..., occurrence=n)` out of range: `IndexError` | `NotFoundError` | One error for "not there" |
| `add_character_role(..., instance_occurrence=n)` and the other role methods with a missing instance: `IndexError` | `NotFoundError` | One error for "not there" |
| `mod.declare_war(target, wg)`, `mod.create_wargoal(target, wg)`, `mod.start_civil_war(ideology, size, capital)` | `effects.declare_war(...)`, `effects.create_wargoal(...)`, `effects.start_civil_war(...)` | Deprecated aliases of the builders |
| `mod.reload()` | `mod.discard()` | `discard()` drops every unsaved edit and picks up changed files; it reloads everything itself when a base mod changed |
| `mod.validate(suppress_warnings=[...])`, `mod.validate_effect(script, suppress_warnings=[...])` | `suppress=[...]` | Older name of the same list |
| `mod.validate(None, False, True, "release")` (options by position) | `mod.validate(validate_icons=False, strict_localization=True, stage="release")` | Every option of `validate()` and `validate_effect()` is keyword-only |
| `mod.create_idea(idea_id, ..., desc="X_desc")` | drop the argument; `mod.set_loc("X_desc", text)` | It was accepted only when it was the fixed key and never written |
| `effects.set_politics(party, elections_frequency=n)` | drop the argument | HOI4's `set_politics` has no such field; the argument only raised |
| `Mod.ruling_parties()` | `mod.available_ruling_parties()` | Hard-coded table; the replacement reads the real ideologies |
| `Mod.leader_ideologies_for_party(party)` | `mod.available_leader_ideologies(party)` | Hard-coded table; the replacement reads the real ideologies |
| `Mod.default_leader_ideology(party)` | `mod.available_leader_ideologies(party)[0]` | Answered `liberalism` for any unknown party |
| `mod.suggest_tag(name)` | `mod.suggest_tags(name, count=1)[0]` | One-line wrapper |
| `mod.suggest_focus_icon(query)` | `mod.suggest_focus_icons(query, count=1)[0]` | One-line wrapper |
| `mod.suggest_idea_icon(query)` | `mod.suggest_idea_icons(query, count=1)[0]` | One-line wrapper |
| `Mod.effect_block(...)`, `Mod.scope_block(...)` | `effects.block(...)`, `effects.scope_block(...)` | Pass-throughs to the module functions |
| `hoi4.import_flag_to_mod(mod_root, ...)`, `hoi4.import_portrait_to_mod(mod_root, ...)`, `hoi4.write_portrait_gfx(mod_root, ...)` | `mod.import_flag_to_mod(...)` and friends (staged), or `hoi4.assets.import_flag_to_mod(...)` (writes at once) | Same names as the `Mod` methods but write at once and bypass transactions; no longer in `hoi4.__all__` |
| `hoi4.version`, `hoi4.PackageNotFoundError` | `importlib.metadata.version`, `importlib.metadata.PackageNotFoundError` | Leaked imports |
| `if mod.delete_x(id): ...` (bookmark, character, country, decision, dynamic_modifier, event, focus_tree, idea, ideology, loc, on_action, oob) returning `bool` | `mod.delete_x(id)` returns `None`; a missing target raises `NotFoundError` | A `False` that nobody checked hid typos |
| `mod.delete_bookmark_country(name, tag, occurrence=n)`, `mod.delete_character_instance(id, n)` returning `False` for a missing entry | return `None`; a missing entry raises `NotFoundError` | Same rule for a missing child as for a missing parent |
| `mod.delete_definition(kind, id)` returning the `Path` | returns `None` | `delete_*` returns nothing |
| `mod.delete_country(tag)` with an invalid tag returning `False` | raises `ValueError` | An invalid tag is a mistake, not "not there" |
| `mod.delete_country("GER")`, `mod.delete_ideology("fascism")`, `mod.delete_oob("GER_1936")` on game content: `False` or `ValueError` | `ReadOnlyError` (a `ValueError`) | Says why: game content is overridden, not deleted |
| `mod.delete_loc(key)` for a key only the game defines: `False` | `ReadOnlyError`; override the text with `set_loc()` | Says why |
| `if mod.update_x(id, **fields): ...` (bookmark, bookmark_country, character, character_instance, character_role, country, decision, decision_category, dynamic_modifier, event, event_option, focus, focus_tree, idea, ideology, on_action, oob) returning `bool` | `obj = mod.update_x(id, **fields)` returns the changed object; a missing target raises `NotFoundError` | The object is what a caller wants next; `assert mod.update_x(...)` keeps passing |
| `mod.update_focus(tree, missing_focus, ...)`, `update_event_option(event, 5, ...)`, `update_bookmark_country(name, missing_tag, ...)`, `update_character_instance(id, 5, ...)`, `update_character_role(id, missing_role, ...)` returning `False` | raise `NotFoundError` | Same rule for a missing child as for a missing parent |
| `mod.update_country("NOPE", ...)` (invalid tag) returning `False` | raises `ValueError` | An invalid tag is a mistake, not "not there" |
| `TypeError("Unknown fields: [...]")` without the method, or raised after part of the change was made | `TypeError("update_x(): Unknown fields: [...]. Valid: [...]")`, raised before anything changes | The message names the method and the valid fields |
| `update_x(touched=..., touched_fields=..., source_index=..., _source_body=..., is_vanilla=...)`, `update_decision_category(paths=...)` | not accepted (`TypeError` or `ValueError`) | Bookkeeping, not content |
| `mod.update_decision(id, path=...)` silently ignored | raises `TypeError`; a decision stays in its file | No silent no-op |
| `mod.add_focus(tree, focus)` returning `None`, adding a second focus with the same id | returns the `Focus`; an id the tree has raises `AlreadyExistsError` | No silent duplicate |
| `mod.insert_focus_after(...)`, `mod.insert_branch(...)` returning `None` | return the inserted `Focus` / `list[Focus]`; a duplicate id raises `AlreadyExistsError` before anything is inserted | Returns say what you got |
| `mod.auto_layout_branch(..., anchor_focus_id=missing)` writing a prerequisite to a focus that does not exist | raises `NotFoundError` | No dangling prerequisite |
| `mod.remove_focus(tree, id)` returning `bool` | returns `None`; a missing focus raises `NotFoundError` | `remove_*` returns nothing |
| `mod.append_to_focus_reward(...)` returning `bool` | returns the `Focus`; a missing focus raises `NotFoundError` | Returns say what you got |
| `mod.set_focuses_mutually_exclusive(...)` returning `bool` | returns `None`; a missing focus raises `NotFoundError` | `set_*` returns nothing |
| `assert mod.assert_no_visual_overlap(tree)` (it returned `True`) | `mod.assert_no_visual_overlap(tree)` returns `None` and raises `ValueError` as before | The `True` carried no information |
| `mod.add_event_option(event, option)` returning `bool`, adding a second option with the same name | returns the `EventOption`; a missing event raises `NotFoundError`, a duplicate name `AlreadyExistsError` | No silent duplicate |
| `mod.set_event_namespace(missing, ns)` silently doing nothing | raises `NotFoundError` | No silent no-op |
| `mod.add_bookmark_country(name, bc)` returning `None`, adding a second entry for the same tag | returns the `BookmarkCountry`; the same tag with the same `required_dlc` raises `AlreadyExistsError` | No silent duplicate |
| `mod.set_bookmark_date_range(...)` returning the `Path` | returns `None` | `set_*` returns nothing |
| `mod.add_supply_node(province)` returning `None` | returns the province id; an existing node raises `AlreadyExistsError` (a `ValueError`, as before) | Returns say what you got |
| `n = mod.remove_map_buildings(...)`, `n = mod.remove_railway(...)`, `n = mod.set_railway_level(...)` returning a count | return `None`; a missing state, a stale entry or no match raises `NotFoundError` | A `0` hid a wrong filter |
| `mod.remove_state_core(id, tag)` returning the `State`, and copying the game's state file into the mod when `tag` was not a core | returns `None`; a tag that is not a core raises `NotFoundError` and nothing is copied | No silent no-op |
| `mod.remove_character_role(...)` returning `bool` | returns `None`; a missing role raises `NotFoundError` | `remove_*` returns nothing |
| `mod.unassign_country_oob(...)` returning `bool` | returns `None`; a missing assignment raises `NotFoundError` | No `False` |
| `mod.assign_country_oob(tag, name)` with an undefined country inventing an empty one | raises `NotFoundError`; with a game install an unknown OOB raises too | No silent invention |
| `mod.create_equipment_variant(tag, v)` with an undefined country inventing an empty one | raises `NotFoundError` | No silent invention |
| `mod.create_decision(category, ...)` with a category the mod does not define writing an empty category definition (which replaced the game's) | extends the category without a definition; with a game install, a category no one defines raises `NotFoundError` ("create it with `create_decision_category()` first") | No silent invention; the game's category keeps its icon and triggers |
| `mod.create_focus_tree(id, tag)`, `mod.create_oob(name, tag)`, `mod.add_state_core(id, tag)`, `mod.set_state_owner(id, tag)`, `mod.set_country_name_pool(tag, ...)` with a tag no one defines | with a game install: `NotFoundError`; without one the tag may be the game's and is accepted (`validate()` checks it) | Catch a typo in a tag at the call |
| `mod.get_focus(tree, id)`, `mod.get_loc(key)`, `mod.get_definition(kind, id)` returning `None` when missing | raise `NotFoundError`; pass `default=None` for the old result: `mod.get_loc(key, default=None)` | A `None` nobody checks turns into an `AttributeError` far from the cause |
| `mod.get_focus(missing_tree, id)` raising while a missing focus returned `None` | both raise `NotFoundError`; `default=` covers both | One rule per lookup |
| `mod.get_country("ZZX")` returning an empty `Country` and caching it (it then showed up in `list_countries()`) | raises `NotFoundError`; `default=` returns the default; nothing is cached | A lookup never invents |
| `mod.get_country_context("ZZX")` answering for a country no one defines | raises `NotFoundError`; `default=` returns the default | A lookup never invents |
| every `get_x(id)` | takes a keyword-only `default=`; `get_x(id, default=None)` returns `None` instead of raising | The `dict.get` convention keeps the "or None" use |
| `mod.create_oob(name, tag)` (it assigns by default) for a country no one defines, inventing that country | raises `NotFoundError`; pass `assign=False` to create the file only | No silent invention |
| `mod.delete_character(id)` of a character whose country no one defines, inventing that country to drop the recruitment | deletes the character; there is no recruitment list to change | No silent invention |
| `mod.upsert_focus(tree, focus)` | `mod.ensure_focus(tree, focus)` | Same idea as every other `ensure_*` |
| `mod.ensure_focus_tree(id, other_tag)`, `mod.ensure_decision(other_category, id)` on an existing object silently ignoring the different parent | raise `ValueError` naming both | No silent no-op |
| `mod.ensure_focus_tree(id, tag, bad_field=1)` creating the tree before raising | raises `TypeError` before anything changes | Atomic |
| `mod.ensure_event/idea/decision/decision_category/on_action(new_id, bad_field=1)` raising `TypeError: EventsLayer.create_event() got an unexpected keyword argument` | `TypeError("ensure_event(): Unknown fields: [...]. Valid: [...]")` on both the create and the update path | The message names the method that was called |
| `mod.create_scripted_trigger(id, body, ...)` | `mod.create_definition("scripted_trigger", id, body, ...)`; its default file is `common/scripted_triggers/00_sdk_generated.txt` (it was `00_generated_triggers.txt`) | A second writer with its own cache and file for the same thing |
| `mod.create_scripted_effect(id, body, ...)`, `create_country_leader_trait`, `create_unit_leader_trait`, `create_technology`, `create_sub_unit` | `mod.create_definition("scripted_effect" / "country_leader_trait" / "unit_leader_trait" / "technology" / "sub_unit", id, body, ...)` | One-line wrappers |
| `path = mod.create_definition(...)` (also `create_static_modifier`, `create_opinion_modifier`, `create_ai_strategy`, `create_scripted_localisation`, `update_definition`) returning the `Path` | return the `ScriptDefinition`; its `source_path` is the file | Returns say what you got |
| `mod.create_definition(kind, id, ...)` on an existing id: `ValueError` | `AlreadyExistsError` (a `ValueError`) | One error for "already there" |
| `mod.create_definition(kind, id, ..., overwrite=True)` for an id defined in two files | raises `ValueError` | Overwriting one copy would leave the other |
| `for d in mod.list_definitions(kind): d.id` (objects) | `mod.list_definitions(kind)` returns sorted ids; `mod.get_definition(kind, id)` returns one | `list_*` returns ids |
| `mod.create_country(tag, name, "Adj", (1, 2, 3), ...)` (options by position) | `mod.create_country(tag, name, adjective="Adj", color=(1, 2, 3), ...)` | Options after the identifying arguments are keyword-only |
| `mod.create_event(event_id, "title", ...)`, `mod.create_idea(idea_id, "icon", {...}, ...)` | `mod.create_event(event_id, title="title", ...)`, `mod.create_idea(idea_id, icon="icon", modifier={...}, ...)` | Keyword-only options |
| `mod.create_focus_tree(id, tag, True)`, `mod.save(True)`, `mod.find_state(q, False, 5)`, `mod.state_index(False)`, `mod.get_state_name_map(False)`, `mod.place_continuous_focus_below_tree(id, 400, 50)`, `mod.add_map_building(..., rotation, 7)`, `mod.suggest_tags(name, 3)` (also `suggest_focus_icons`, `suggest_idea_icons`) | the same options by keyword: `overwrite=`, `require_changes=`, `include_vanilla=`, `limit=`, `padding=`, `x=`, `adjacent_sea_province=`, `count=` | Keyword-only options |
| `mod.create_focus_tree(id, country_tag=...)`, `mod.ensure_focus_tree(id, country_tag=...)`, `mod.create_oob(name, country_tag=...)`, `mod.create_equipment_variant(country_tag=..., variant=...)` | `tag=` | One name per concept (`update_oob(country_tag=)` keeps the model's field name) |
| `mod.set_loc(key, value, "localisation/...yml")`, `set_loc(..., file_path=...)`, `mod.set_focus_loc(id, name, desc, file_path)` | `path=` (keyword-only) | One name per concept |
| `mod.create_state(..., localisation_file=...)`, `mod.create_strategic_region(..., localisation_file=...)` | `loc_path=` | One name per concept |
| `mod.create_country(..., allow_vanilla_override=True)`, `mod.import_flag_to_mod(tag, src, True)` / `vanilla_override=True` (also `hoi4.assets.import_flag_to_mod`), `mod.set_loc(..., replace=True)` | `overwrite_vanilla=True` (keyword-only) | One name for "override the game's content" |
| `except ValueError` around `create_x(id, ...)` for an existing id (country, character, bookmark, decision, decision category, dynamic modifier, event, focus tree, idea, ideology, on-action, OOB, state, strategic region, equipment variant) | `except hoi4.AlreadyExistsError` (still a `ValueError`) | One error for "already there" |
| `state = mod.set_state_owner(id, tag, True)` returning the `State` | `mod.set_state_owner(id, tag, add_core=True)` returns `None`; it takes one id or a list; `mod.get_state(id)` returns the state | One setter for one or many states |
| `mod.batch_set_owner(ids, tag, add_core)` | `mod.set_state_owner(ids, tag, add_core=...)` | A loop over `set_state_owner` |
| `mod.patch_state_history(id, owner=, add_cores=, remove_cores=)` | `mod.update_state(id, owner=, add_cores=, remove_cores=)`; a core in `remove_cores` that is not there raises `NotFoundError` | Same mechanism as the other setters |
| `mod.set_state_properties(id, **fields)` returning `True`, appending to list fields such as `cores` | `mod.update_state(id, **fields)` returns the `State` and replaces list fields; `add_cores=[...]` appends | A `set` that appended was surprising; `update_*` returns the object |
| `set_state_properties(id, _victory_points_requested=...)` | not accepted | Bookkeeping, not content |
| `mod.set_idea_path(tag, path)` | `mod.set_default_idea_file(tag, path)`; with a game install an undefined tag raises `NotFoundError` | Says what it does |
| `mod.ensure_country_states_in_mod(tag)` | `mod.copy_country_states_to_mod(tag)` | Says what it does; it is not an `ensure_*` |
| `mod.content_source(relative)` | `mod.resolve_content_path(relative)` (still returns `None` when no layer has the file) | A path resolver, not a lookup of content |
| `new_id = mod.create_state(...)`, `mod.create_strategic_region(...)`, `mod.split_state(...)` returning the id | return the `State` / `StrategicRegion`; use `.id` | `create_*` returns the object |
| `for region in mod.list_strategic_regions(): region.id` (objects) | `mod.list_strategic_regions()` returns sorted ids; `mod.get_strategic_region(id)` returns one | `list_*` returns ids |
| `hoi4.ValidationError` (also `hoi4.types.ValidationError`) | `hoi4.ValidationIssue` (no alias) | It is a finding, not an exception; `except ValidationError` read like a bug |
| `Mod.effect_<name>(...)` / `mod.effect_<name>(...)` (all 52 builders, e.g. `Mod.effect_add_political_power(50)`, `Mod.effect_transfer_state(115, "SCL")`) | `from hoi4 import effects`, then `effects.<name>(...)` (`effects.add_political_power(50)`, `effects.transfer_state(115, "SCL")`) | The builders never used the mod; as static methods they made `Mod` 52 names longer. Same arguments, same output |
| `hoi4.effect_block(name, fields)`, `hoi4.script.effect_block(...)` | `effects.block(name, fields)` | One home for every builder |
| `hoi4.scope_block(scope, *effects)`, `hoi4.script.scope_block(...)` | `effects.scope_block(scope, *effects)` | One home for every builder |
| `hoi4.bookmarks.load_bookmarks`, `hoi4.decisions.write_decisions_file`, `hoi4.focus.write_focus_tree`, `hoi4.ideologies.load_ideologies`, `hoi4.localisation.delete_localization_keys`, `hoi4.on_actions.write_on_actions_file`, `hoi4.states.ensure_state_in_mod`, `hoi4.states.patch_state_history_owner_cores`, `hoi4.tags.load_all_tags`, `hoi4.validation.collect_character_ids` | the matching `Mod` method (`mod.list_bookmarks()`, `mod.save()`, ...) | Unused module functions that bypassed the `Mod` staging |

### Added

- `hoi4.errors` with `Hoi4Error` (the base of every SDK error),
  `NotFoundError` (also a `KeyError`), `AlreadyExistsError` and
  `ReadOnlyError` (both also a `ValueError`), exported from `hoi4`.
  `NotFoundError` has `kind`, `id` and `suggestions`, and its message names up
  to five close matches and the number of known ids instead of the whole list.
  `ParseError`, `ExternalModificationError`, the image, Commons and Gemini
  errors, `OperationCancelled`, `MapRenderCancelled` and `GenerationCancelled`
  derive from `Hoi4Error` now.
- `scripts/check_removed_api_names.py [paths...]` lists every call or
  mention of a name the 0.7.0 cleanup removed or renamed, and every renamed
  keyword argument, in Python files and Markdown. Run it on your own scripts
  to find what the table above asks you to change.
- A disk cache of what validation reads out of the game's own files (and
  base mods): references, division templates, sprites, characters, states,
  map data and localisation. A new process reads the stored results instead
  of parsing the game again, so the first `validate()` of a process on a
  small mod takes about 3 to 4 seconds instead of about 40. The catalogs'
  per-file scans of the inherited files (definitions, country tags, focus,
  event and idea ids, shared focuses, localisation keys and more) are stored
  too. It lives in
  `~/.cache/hoi4-sdk/file-results/` (or under `$XDG_CACHE_HOME` or
  `$HOI4_SDK_CACHE_DIR`), holds only results for inherited files, and is
  rebuilt for a file whose size or modification time changed and for any new
  version of the SDK. `HOI4_SDK_DISK_CACHE=off` turns it, and the vocabulary
  cache, off. See [docs/internals/disk_cache.md](docs/internals/disk_cache.md).
  The very first `validate()` after an update of the SDK starts with empty
  caches: it builds the script vocabulary again and fills the store, and so
  takes longer than it did before this version (measured on a small mod:
  about 70 s once, where the old version with its vocabulary cache already
  filled took about 40 s). Every run after that is the fast one.
- `hoi4.syntax`: one lossless syntax tree for Paradox script. Every node knows
  its exact position in the source, so reading is a tree query and writing is
  the replacement of one span. It understands typed blocks (`rgb { }`,
  `hsv { }`), quoted keys and values, nested lists, `@variables`, every
  comparison operator and inline maths, and it is as forgiving as the game: a
  block still open at the end of a file is closed there and a stray closing
  brace is skipped, each recorded as a `Problem`. `require_well_formed` and
  `repair` build on that.
- `hoi4.textio`: byte-exact file reading and writing. A saved file keeps its
  encoding (UTF-8, Windows-1252 or any other bytes), its byte-order mark and
  its line endings, and every line that did not change keeps its exact bytes,
  also in a file that mixes line endings.
- Shared and joint focuses: a file's top-level `focus`, `shared_focus` and
  `joint_focus` blocks load into a per-file container tree (`FocusTree.kind ==
  "file"`), with `Focus.kind` naming the block type. 51 of the game's 82 focus
  files use them. Also `Focus.icon_blocks`, `Focus.icon_names()` and fractional
  `Focus.cost`.
- Conditional event text: `EventText`, `Event.conditional_titles`,
  `Event.conditional_descriptions` and `EventOption.conditional_names`.
- `Country.color_ui`, and colours read from `rgb { }`, plain `{ r g b }`,
  `hsv`/`HSV` and `hsv360`.
- `hoi4.patching.sync_body`: rewrites a block body statement by statement.
  Statements the old and the new body share keep their exact bytes, a list
  gets one more entry without losing the comments next to the others, and a
  nested block that changed is edited the same way one level down.
  `set_block` uses it for every block edit. Also `same_block_body`,
  `statement_spans` and `one_statement_per_line`.
- An opt-in corpus test (`pytest -m corpus` with `HOI4_INSTALL_DIR` set) that
  checks the script core against every script file of a real game install.

- Ordered directory-backed base mods, descriptor dependencies, effective-file
  provenance, and opt-in inherited focus/event/idea/on-action editing.
- Transactional support-script imports, source-preserving scripted-trigger
  authoring, explicit conversion color-table rebuilding, and impassable states.
- Generic keyed `common/` definitions (`list/get/create/update/delete_definition`)
  for 36 domains, including scripted effects and localisation, leader traits,
  technologies, sub-units, equipment, AI strategies, opinion and static
  modifiers, wargoals, autonomy states, game rules, scripted GUIs, unit name
  lists, balance of power, MIOs, special projects, occupation laws and
  doctrines, with typed helpers,
  container-aware byte-preserving patches, duplicate-ID and unknown-trait
  validation, and a wider `import_script_file()` allow-list.

- Map editing for the existing map: `create_state`, `move_provinces`, and
  `split_state` move provinces with their victory points and province-level
  history buildings; strategic-region listing, moves, and creation with copied
  weather; supply-node, railway, and `buildings.txt` position editing with
  optional reassignment of positions inside moved provinces. Edits are
  transactional, copy inherited files into the mod, and touch only the affected
  lines or province IDs.
- Validation checks that land provinces belong to exactly one state, land and
  sea provinces to exactly one strategic region, and that supply-node, railway,
  and building-position references resolve.

- `CommonsImageClient` searches Wikimedia Commons and downloads raster sources
  with creator/license metadata, retrieval time, checksums, and verified local
  cache reuse. SVG flags use Commons raster thumbnails. Sources feed the
  existing transactional flag/portrait import APIs without a Gemini key.
- Transactional `Mod` importers for custom focus icons (with standard shine
  sprites), idea icons, decision and decision-category icons, decision-category
  pictures, and report/news event pictures. Sprites are merged into mod-scoped
  `.gfx` files by name without disturbing other entries, and staged sprites are
  visible to icon validation before save.
- Multi-language localisation: `get_loc`, `set_loc`, `delete_loc`,
  `search_loc`, and `all_loc` take `language=` (default `"english"`) for all
  ten HOI4 languages, indexing inherited and mod files per language without
  mixing them. `set_loc(..., replace=True)` writes vanilla overrides to
  `localisation/<language>/replace/`, and `copy_loc_to_languages()` fills
  missing translations from English. Validation reports header/folder
  language mismatches and English keys missing from a shipped language.
- Decision missions and targeted decisions: `Decision` models mission fields
  (`days_mission_timeout`, `activation`, `timeout_effect`, `cancel_trigger`,
  `cancel_effect`, `is_good`, `selectable_mission`), targeted fields
  (`target_trigger`, `targets`, `target_array`, `target_root_trigger`,
  `state_target`, `targets_dynamic`, `target_non_existing`, `on_map_mode`),
  and general fields (`allowed`, `days_re_enable`, `war_with_on_remove`,
  `custom_cost_trigger`, `custom_cost_text`, `cancel_if_not_visible`,
  `modifier`, `remove_trigger`, `highlight_states`, `priority`). They are
  parsed, rendered, and patched in place by `update_decision`; edits now patch
  only changed fields, so unmodeled spellings such as `cost = @constant`
  survive. New `Mod.create_mission()` and `Mod.create_targeted_decision()`
  helpers, plus `mission_with_cost` and `mission_activation_without_timeout`
  validation.

- **Three severities for validation findings.** Every validation code has
  exactly one severity: `"error"` (the game rejects the content or behaves
  wrongly), `"warning"` (probably a mistake) or `"info"` (advice: the
  content is valid, but often not what was meant). The registry is exported
  from `hoi4`: `VALIDATION_SEVERITIES` maps each code to its severity, and
  `VALIDATION_ERROR_CODES`, `VALIDATION_WARNING_CODES` and
  `VALIDATION_INFO_CODES` map the codes of one severity to a description.
  Every finding the SDK builds has a registered code and that code's
  severity, also when a check such as `validate_event` or `validate_oob` is
  called on its own. [`docs/validation.md`](docs/validation.md) lists every
  code.
- `Mod.validate(advice=False)`: advice (`"info"`) is hidden unless
  `advice=True`. `Mod.validate_effect(advice=True)` looks at one script, so
  it shows advice unless `advice=False`.
- `Mod.validate(suppress=[...])` hides warnings and advice by code: `"code"`
  hides every finding with that code, `"code:ID"` only the findings about
  one focus, event, idea, decision, country, state or other object.
- New validation codes. Many of these findings were reported before without
  a code.
  - Errors: `duplicate_definition_id`, `duplicate_identifier`,
    `duplicate_strategic_region_id`, `equipment_variant_chassis_not_unlocked`,
    `invalid_country_color`, `invalid_country_tag`, `invalid_event_type`,
    `invalid_focus_kind`, `invalid_railway`, `invalid_ruling_party`,
    `invalid_supply_node`, `malformed_map_building`, `malformed_railway`,
    `map_building_unknown_province`, `map_building_unknown_state`,
    `missing_character_small_portrait`, `missing_country_name_pool`,
    `missing_event_id`, `missing_event_options`, `missing_focus_id`,
    `missing_focus_mutually_exclusive`, `missing_focus_prerequisite`,
    `missing_idea_id`, `missing_state_id`, `non_land_state_province`,
    `province_in_multiple_states`, `province_in_multiple_strategic_regions`,
    `runtime_recruit_character`, `state_load_error`,
    `strategic_region_parse_error`, `undeclared_event_namespace`,
    `unknown_idea_category`, `unknown_state_province` and
    `unknown_strategic_region_province`.
  - Warnings: `country_popularity_sum`, `empty_strategic_region`,
    `missing_country_name`, `missing_event_description`,
    `missing_event_title`, `missing_state_owner`, `province_missing_state`,
    `province_missing_strategic_region`, `unknown_character_trait`,
    `unknown_focus_search_filter`, `unknown_state_core`,
    `unknown_state_owner`, `unknown_state_reference` and
    `unloaded_missing_oob_reference`.
  - Advice: `continuous_focus_position_high`, `idea_without_effect`,
    `missing_optional_localization`, `triggered_only_with_mtth` and
    `war_declaration_in_immediate`.
- `hoi4.script_scopes` walks script blocks and knows, for every statement,
  whether it is a command, a scope change, a control block or an argument,
  and which scope it runs in. See
  [`docs/internals/script_scopes.md`](docs/internals/script_scopes.md).
- `hoi4.script_references` reads every idea, event, flag, technology,
  equipment, focus tree and country tag that script names, with its line and
  scope. It rides on the scope walker of `hoi4.script_scopes` (through
  `walk_script(..., visit=...)`), so the reference check and the token check
  agree on what every statement is and where it runs.
- `validate_effect(..., suppress=[...])`, like `validate()`.
- `validate_oob(oob, known_templates=...)` accepts division templates that
  the country defines in another OOB file or in script.

### Changed

- **The first `validate()` of a large mod is about twice as fast.** On a mod
  with 397 countries the warm first `validate()` went from about 65 s to
  about 34 s, and a second `validate()` in the same process from about 45 s
  to about 14 s (rough, on a busy machine). The country package check no
  longer repeats work for every country: the runtime activation evidence of
  all effects, the province ownership of the whole map and the special
  project unlocks are built once per `validate()` run, and the enclosed
  territory search starts at the country's own border. A section's first
  load reads and parses each file once instead of up to three reads and two
  parses. `discard()` keeps the cached references of the mod's files that
  did not change.
- **`Mod(root)` is fast, and `save()` no longer reloads the whole mod.**
  Content now loads one section at a time (events, states, countries,
  localisation and so on) the first time it is used, so an agent that edits
  one event reads only the events. After `save()` and `discard()`, only the
  sections that had edits, whose files were written or changed on disk, or
  that are built from those load again on next use; everything else stays in
  memory. On a large mod (11,297 files, 157 MB) construction went from about
  15 s to 0.2 s, and saving one event edit from about 15 s to 0.3 s.
  `strict_loading=True` still loads everything at construction, and
  `load_diagnostics` loads what is missing and lists diagnostics in the same
  order as before. `reload()` still reloads everything.
- **Base mods are read where they are, not copied.** With `base_mod_paths`,
  `Mod` used to clone every content file of the game and the base mods into a
  temporary folder (about 39,000 files and 4.3 GB for the game) on every
  `Mod()`, `discard()` and `reload()`. Now `hoi4.layers.ContentLayers` answers
  each read from the winning source file, with the same precedence,
  `replace_path` and results. With a mod on top of a base mod: construction
  went from about 8 s to 0.03 s, `discard()` from about 9.5 s to 0.15 s, the
  first `validate()` from about 22 s to 18 s, and nothing is copied. `discard()` and `save()` reload only what changed, as
  without base mods, unless a text file of a base mod changed on disk. Paths
  in models, reports and `get_country_context()` now name the real game or
  base-mod file instead of a file in the temporary copy.
- **Much less memory for the external-change check.** `Mod` no longer keeps a
  copy of the bytes of every text file of the mod. It records each file's size
  and modification time, and a digest of the text it read. A file whose time
  changed but whose text did not (a `touch`) can still be overwritten; any
  other change behind the SDK's back still raises `ExternalModificationError`
  (before a file is overwritten, its text is compared, whatever its size
  and time say).
- A file is read when its section is first used, not when `Mod(...)` is
  built. A file that was changed, touched or created in between is read as
  it is then, which is what a fresh `Mod` would see, and can be edited and
  saved. A change after the SDK first read the file is still refused by
  `save()`, whether it happens before or after the SDK's own edit. An edit
  that replaces a file without reading it (`import_script_file(...,
  overwrite=True)`, `rebuild_country_color_table()`,
  `set_country_name_pool()`, the map files) takes the file as it is when
  the edit is made: a change of it between the edit and `save()` is refused.
- Reading one `Mod` from several threads keeps working, although content
  now loads on first use: a thread that asks for a section while another
  thread loads it waits until the section is complete. (Changing a `Mod`
  from two threads was never safe and still is not.)
- With base mods, the files below a folder are read in one fixed order on
  every Python version and file system. Before, the order was that of
  `Path.rglob` of the running Python and of the temporary copy's file
  system. On Python 3.11 and 3.12 this can change which of two definitions
  wins when an inherited layer defines the same key twice in nested
  folders (on 3.13 and later the order is the same as before).
- A symlink in a base mod that leaves the base mod's folder is refused when
  it is read, not when the `Mod` is built.
- `copy.copy(mod)` gives a copy that keeps its own account of which
  sections are loaded. It is still a shallow copy: the two share the
  content loaded at that moment and the list of unsaved edits, but not
  what either loads later. Use `copy.deepcopy(mod)` or a second
  `Mod(root)` for an independent one.
- The release gate's localisation probe edits a text the mod itself
  defines. With base mods it could pick a text of a base mod before.
- A load failure in a file that was changed or broken after `Mod(...)` was
  built, but before its section was first used, is now reported as a load
  diagnostic (`load_failure` in `validate()`), like any file that fails to load.
- `transaction()` copies only the sections that are loaded; a section first
  loaded inside the transaction is unloaded again on rollback.
- `get_loc`, `search_loc` and `all_loc` return the game's own text for a key
  the mod does not define, also when only `hoi4_install` is set (base mods
  already worked this way). The mod's entry wins, the game is indexed on the
  first lookup the mod misses (once per language), and saving never writes
  the game's entries into the mod.
- The script vocabulary cache follows `$XDG_CACHE_HOME` when it is set and
  `$HOI4_SDK_CACHE_DIR` is not.
- **An edit to one field now changes only that field.** Focus trees, events,
  on-actions, ideas, ideologies, dynamic modifiers, decisions, bookmarks,
  countries, characters, country tags, states and orders of battle are read
  from the syntax tree and written by replacing only the spans that differ
  from the source. Comments, unknown keys, repeated blocks, conditional blocks
  and the spelling of every untouched value survive.
- Files inherited from the game or a base mod are read the way the game reads
  them. The game ships files with unbalanced braces (Norway's history,
  France's 1936 army, the Soviet ideas file); they load now. When such a file
  becomes a mod override it is written out well formed.
- A malformed file in the mod's own folder is still a load failure, reported
  in `load_diagnostics` and by `validate()`, and `save()` now refuses to
  rewrite any mod file that failed to load instead of replacing it from an
  incomplete model.
- `hoi4.parser` and `hoi4.patching` are rebuilt on the syntax tree.
  `parse_pdx(strict=False)` reads the forgiving way. `PdxNode` gained
  `key_quoted` and `tag`; `AssignmentSpan` gained `operator` and `tag`;
  `top_level_assignments(comparisons=True)` also returns comparison
  statements. `find_assignment_block` no longer fails on a block that is
  open at the end of the text.
- A block edit changes only the statements that differ, instead of writing
  the whole block again. Comments count as soon as the new text has any: a
  changed, added or dropped comment is written. A text without comments
  leaves the comments in the file alone (pass `comments=True` to
  `set_block` to remove them). A statement that is removed takes the
  comment on its line with it.
  Two limits: a statement that moves and gets a new value in the same edit
  loses its comments, and so can one of several statements with the same
  key when one is removed and another changed.
- Setting an event's plain `title` or `description`, or an option's plain
  `name`, no longer removes the conditional alternatives. Pass
  `conditional_titles=[]` (or `conditional_descriptions=[]`,
  `conditional_names=[]`) to remove them.
- While a file in the mod's own folder fails to load, `save()` also refuses
  to create a new override of a game file in that folder: the broken file
  may already hold the mod's version of that content.
- `preview()` shows the repaired form of a game file with unbalanced braces
  that becomes a mod override, which is what `save()` writes.
- `preview()` and `save()` refuse to write a script file that would get a
  syntax problem it does not have now (an unclosed brace in script text
  passed to an update, for example). Nothing is written in that case.
- Text handed to a writer cannot land outside the place it is written to.
  Script text for a block (an effect, a trigger, a reward) that is not well
  formed on its own, for example `} x = 1 {`, which would end the block
  early, is refused with a `ParseError`. A value that is not one word (it
  has a space, a brace, a `#` or a quote in it) is written as one quoted
  string. A name that is written as a key (a modifier, an event type, an
  idea category, an equipment type) has to be one word and is refused
  otherwise. List entries are quoted where needed.
  A state's victory points have to be numbers (any spelling the game
  reads, such as `.5` or `1e3`) or script constants (`@points`).
  A value with a byte-order mark inside, or with text right after inline
  maths (`@[x]y`), is written in quotes too: the reader ends a word there.
  The effect builders `effect_add_building` and `effect_add_tech_bonus` and
  the flags of `create_decision_chain` follow the same rule. `effect_block`
  and `scope_block` check their name, keys and scope, and `scope_block`,
  `effect_start_civil_war` and `effect_division_template` check the script
  they wrap. A comment at the end of an effect given to `scope_block`, or
  at the end of the regiments of a division template, no longer swallows
  the closing brace or the effect after it: what follows goes on a new
  line. `pdx_value` spells any object (a path, a decimal) as one scalar.
  `tests/test_block_body_guard.py` tries thirteen attack texts on every text
  field of every model, on new and on loaded objects.
- A state edit that only adds quote characters or a space at an end of a
  name, a category or a resource value is written. It used to be taken for
  "no change", because the new text was compared the way file text is
  compared. Two spellings of one number (`2000` and `2000.0`, `.5` and
  `0.5`) are still no change in a field that holds a number; a name such
  as `001` is text.
- In an on-action's `random_events`, a statement that is not a
  `weight = event` pair (such as `chance_to_fire = 25`) reads as one list
  entry holding the whole statement, and in `events` any assignment does.
- A comment that moves from one statement to another, with the same words,
  counts as a change and is written, also inside nested blocks.
- A decision edit writes only the file that holds the decision. Category
  presentation that an old layout kept in the decisions file moves to
  `common/decisions/categories/` only when the category itself is edited. A
  category may be reopened in one file or spread over several files, and its
  decisions are editable in place.
- An edit to a vanilla country writes only the files it changed, and renaming
  a vanilla country keeps its history file name.
- `Idea.category_raw_block` holds only the category's own settings. Loading
  every vanilla ideas file now peaks at 35 MB instead of 182 MB.
- `hoi4/mod.py` (12,992 lines) is now the `hoi4/mod/` package: one module per
  content type, each defining one layer class, with `Mod` inheriting from all
  of them. A layer subclasses only the layers whose methods it calls, so its
  class header is its dependency list and mypy rejects a call to any other
  layer. Every method moved with its body unchanged, apart from function-local
  relative imports gaining a dot, the last two calls of `__init__` moving into
  the top class, and `from_config` returning `Self` (it lives on `Mod` itself).
  `from hoi4 import Mod` and `from hoi4.mod import Mod` work as before. See
  `docs/internals/mod_layers.md`.
- `hoi4.mod` now exports exactly `Mod` and `ExternalModificationError`. The old
  module had no `__all__`, so every name it defined or imported for its own
  use (for example `Focus`, `State`, `set_block`, `COMMON_FOCUS_ICONS`, even
  `os`) could be imported from it by accident. Import models from `hoi4` and
  helpers from their home module. Method `__qualname__`s now name the layer
  (`CountryAuthoringLayer.create_country`), and
  `ExternalModificationError.__module__` is `hoi4.mod._shared`.
- Agent country-creation guidance now prefers suitable existing assets and
  Commons sources. Paid Gemini generation is an explicitly authorized option;
  missing credentials no longer prevent local or web-sourced graphics.

- Finding severities changed to fit the three-level model (see Added).
  Errors that are now warnings: `duplicate_oob_grid_position`,
  `duplicate_oob_ship` and `oob_ship_name`. Errors that are now advice:
  `invalid_idea_picture_prefix`, `mission_activation_without_timeout`,
  `oob_location_not_owned`, `oob_naval_location_not_owned` and
  `oob_air_location_not_owned`. Warnings that are now advice:
  `bad_idea_tooltip_pattern`, `civil_war_focus_tree_missing`,
  `civil_war_scope_footgun`, `event_option_no_effect`,
  `faction_scope_footgun`, `idea_mutation_collision`, `idea_not_addable`,
  `leader_party_mismatch`, `resistance_on_core_state`,
  `revolt_state_already_owned`, `unseen_effect_token`,
  `unseen_modifier_token` and `unseen_trigger_token`. Advice is hidden by
  `validate()` unless `advice=True`.
- Suppression works by code only. An entry in `suppress=` or
  `suppress_warnings=` (the older name of the same list) that is not a
  known code, such as a piece of a message, raises `ValueError`, so a typo
  no longer hides nothing in silence. Errors cannot be suppressed.
- The check of effect, trigger and modifier names follows scopes. It reads
  every block through the syntax tree, knows control blocks, scope changes,
  iterators and their arguments, `random_list` weights and weight blocks
  (`ai_will_do`, `mean_time_to_happen`), and folds case like the game. A
  scope finding is reported only when the scope is certain. On a mod made
  of the game's own files it went from about 13,000 false alarms to the 17
  real mistakes in the game. What it reads from the install is cached under
  `~/.cache/hoi4-sdk/script-vocabulary`.
- Five findings about what a complete country needs are warnings now, not
  errors: `missing_country_leader`, `insufficient_political_advisors`,
  `insufficient_military_commanders`, `missing_land_oob` and
  `capital_not_cored` (`hoi4.COUNTRY_PACKAGE_REQUIREMENT_CODES`). The game
  loads a country without them, and an error cannot be hidden. They still
  decide completeness: `validate_country_package(tag).complete` stays
  `False` (see the new `unmet_requirements`), and the release gate fails
  (`incomplete_country_count`).
- `invalid_country_color` is reported for a country definition file whose
  colour has a channel outside 0 to 255. The reader clamps such a value, so
  the check never saw it. A colour set with `update_country(color=...)`
  replaces the file's, so the finding goes before `save()`.
- A trigger used where an effect belongs (or the other way round) is named
  as such, instead of being answered with a look-alike name.
- `docs/validation.md` says what each stage checks. The "is this ever
  used?" findings (`unfired_event`, `flag_set_never_read` and the like)
  belong to `stage="release"`, as before.
- A strategic region without provinces is reported only when the mod's own
  file holds it. The game ships three such regions, and every mod used to
  get three warnings for them.
- Reference and liveness checks read the syntax tree instead of searching
  text with regular expressions. Comments and quoted text no longer count
  as references, catalogs read definitions where the game keeps them (tag
  aliases, equipment from `duplicate_archetypes`, events in sub folders),
  and a flag read in localisation text (`[?flag:days_left]`) counts as read.
- Order-of-battle and country checks no longer fire on valid game content.
  States, provinces and OOB files are also read from the game install,
  division templates from the country's other OOB files and from script
  count, a country that owns no state at the start does not load its OOB
  files, units may start outside the country's territory, a ship may use
  `ordered_name`, and DLC conditions of equipment variants are read from the
  real `if`/`else` blocks. Before, these checks raised about 6,000 errors
  and warnings on a copy of the game's own files.
- Content checks look localisation keys up in the mod, its base mods and
  the game. A plain-text value or a bracket command (`[GetName]`) needs no
  key, hidden events need no text, any `add_namespace` in any event file
  declares a namespace, and a character named by a key of the game
  (`name = KEY`) has its name.
- `validate_focus_tree` and `validate_event` no longer take `known_tags`.
  Unknown country tags are reported by the reference checks of
  `Mod.validate()`, which know the scope. `validate_event` takes
  `namespaces=` and `validate_focus_tree` takes `known_search_filters=`.
- `assert_no_visual_overlap` and `validate()` use the same rule: two focuses
  may share a position only when the game never draws both there, because
  an `offset` block moves one and not the other (its own, or one on a focus
  it is placed relative to) while `allow_branch` can hide at least one of
  them, or each needs an `allow_branch` condition the other does not need
  (two focuses with the same `allow_branch` text appear together, and a
  focus whose conditions the other needs as well is drawn whenever the
  other is). What a focus needs follows the HOI4 wiki: a focus with
  `allow_branch` checks only its own condition, a focus without one is
  hidden as soon as one of its prerequisite focuses is. Mutually exclusive
  focuses are both drawn, so they must not share a position.
- `validate()` and `validate_effect()` load a catalog of the game (ideas,
  events, technologies, equipment) only when a name is not among the mod's
  own objects, and then only once.

### Fixed

- `unknown_assigned_idea` no longer reports an advisor's `idea_token` that a
  country history hires with `add_ideas`, and the finding names the history
  file and line.
- `tag_definition` no longer reports a tag whose country file the mod
  inherits from the game or a base mod (`replace_path` is respected).
- A mod's focus may require, exclude or anchor to a focus of the game or a
  base mod (shared and joint focuses included) without
  `missing_focus_prerequisite` or `unresolved_focus_position`. The
  inherited focus files are scanned once, and only when a name is not the
  mod's own.
- The catalogs read from a folder of the game and the mod (advisor idea
  tokens, technologies, equipment, name pools, scripted effects and
  triggers) skip the inherited files that the mod's `replace_path` hides.
- A second `validate()` sees unsaved API edits: a deleted event, focus tree,
  character or idea is unknown at once, and a created character and its
  advisor idea token are known before `save()`. Every catalog reads the
  mod's own content from the loaded models and keeps only per-file scans,
  which follow the files on disk, so `save()` and `discard()` leave none
  stale. A file's top-level focus container is no longer taken for a focus
  tree id.
- `capital_ref` and `character_ref` read a country history with its unsaved
  edits, not only the file on disk.
- `validate(suppress="code")` with one string instead of a list raises
  "takes a list of codes, not one string" instead of complaining about the
  code `'v'`.
- A focus id that is defined twice in one tree is one `duplicate_focus_id`
  finding. Before, it was reported twice, the focus "overlapped" itself
  (`visual_overlap`), and its missing texts were listed twice.
- The country tags a script may name follow the game: a mod's
  `replace_path = "common/country_tags"` hides the game's tags, and a
  country removed with `delete_country()` is an unknown tag before
  `save()` (`unknown_country_scope`, `unknown_state_owner`, the order of
  battle checks).
- `load_focus_tree` cannot name the container the SDK keeps a file's
  top-level shared focuses in (`unknown_focus_tree_reference`).
- A focus of a tree may be placed relative to the shared focuses the tree
  pulls in (`shared_focus = ID`, and the shared focuses below `ID`) without
  `unresolved_focus_position`, and a focus below such a shared focus is
  hidden with it (its `allow_branch` counts for the position check).
  `validate_focus_tree` takes `shared_focus_parents=` and
  `shared_focus_conditions=` for that.
- `ALL` and `ANY` are country tags when a mod defines them, not keywords.
- The reference checks know the characters, so what stands inside a
  character scope keeps its scope: `GER_erwin_rommel = { owner = {
  add_core_of = GER } }` is reported (`country_scope_core_effect`), and so
  is an unknown tag in there. Before, such a block was read as data.
- The roles inside a character's `instance = { ... }` blocks and the
  `available` block of a bookmark country are checked like other script.
- `unused_localization` no longer reports a key that another text of the
  mod pulls in as `$KEY$` or `$KEY|H$`.
- The namespace of an event id is everything before its last dot, as the
  HOI4 wiki describes it: `my_mod.news.1` needs
  `add_namespace = my_mod.news`. Before, only the text before the first dot
  counted, so `undeclared_event_namespace` was reported for a declared
  `my_mod.news` and stayed quiet when only `my_mod` was declared.
  `create_event("my_mod.news.1")` now declares `my_mod.news` (it declared
  `my_mod`), and `set_event_namespace` takes a namespace with dots.
- A character of a base mod used as a scope (`BAS_hero = { ... }`) is known
  to the token check, as it already was to the reference check.
- Validation reads each script file through one splitter
  (`hoi4._script_corpus`) and one scope walker. The token check reads the
  mod's own files as `save()` will write them, with exact file lines; the
  corpus test checks the same path. Fixed on the way: `NOR` is Norway, not a
  keyword; `PREV`, `THIS` and `FROM` scope references; a bare top-level
  `focus` is a focus; an iterator's scope comes from the table, not from
  words in its name; event `after`, title and option-name triggers, idea
  `on_add` and focus `cancel` are checked.
- `state:123.owner`, `country:GER.capital_scope` and other links behind a
  typed prefix lead where they say instead of to the prefix's scope.
- A block holds statements of its own kind: `OR`, `NOT`, `hidden_trigger` or
  `any_of` among effects, `hidden_effect` or `for_each_loop` among triggers,
  `GER = 5` among effects, and a scripted trigger used as an effect (or the
  other way round) are reported.
- An event's namespace is matched without regard to case in the reference
  check and in `unfired_event`, as the game does (`PER_SSB_events.8` fires
  `PER_ssb_events.8`).
- A definition made with `create_definition()` counts for the token check
  before `save()`, and a deleted one stops counting.
- `unused_localization` no longer reports keys the game or a base mod
  defines too (overrides in `localisation/english/replace`), nor keys the
  mod's own script files or interface files name.
- A core effect in a country scope is one finding
  (`country_scope_core_effect`), not also `unsupported_effect_scope`.
- `ordered_*` names are not iterators: HOI4 has none.
- Project flags (`set_project_flag`, `has_project_flag`, ...) are tracked by
  the flag liveness checks.
- Adding an event to an on-action merged its repeated `events` blocks into
  one and dropped the comments inside them. Event lists are now edited entry
  by entry.
- Editing an event option's effect copied the option's comments, and the
  copies doubled with every edit.
- A comment-only change to an effect or trigger was silently ignored.
- Adding a trait, an idea, a technology or any other entry to a list or
  mapping block dropped the comments inside the block.
- A multi-line statement that replaced another one was indented only on its
  first line, and the first statement put into an empty multi-line block was
  glued to the opening brace.
- Removing a statement that was followed by spaces left a line of spaces.
- A broken state file in the mod made the SDK edit the game's state of the
  same id and write it as a second file for that state.
- An air base's `ace = { ... }` block was read as an air wing named `ace`.
- Putting a comment or a statement of several lines into a one-line event
  option (`option = { name = x }`) wrote the closing brace into the comment,
  and the event was gone after a reload. The option is now laid out with
  one statement per line.
- Changing the weight of a `random_events` entry dropped the comment on its
  line, and inserting an event in the middle of a list merged the hook's
  repeated blocks.
- A `random_events` block with a statement that is not a `weight = event`
  pair lost its pairing on the next edit: every entry after it was written
  with the wrong weight.
- Editing a decision in one legacy category and the presentation of another
  category in the same file wrote a second definition of the first category
  into `common/decisions/categories/`.
- A localisation file without a byte-order mark gets one on save. A line of
  it that mixed valid UTF-8 with other bytes was then read differently, so
  its text changed although nobody edited it. Such a line is now written
  again from its text.
- Changing a state's `buildings` rewrote the whole block and lost its
  comments, and a change of only a comment was not written.
- A new event statement placed after one that shares its line with another
  statement and a comment (`id = a.1 desc = a.1.d # note`) went between the
  statement and its comment.
- A statement added before one that shares the line of the opening brace
  moved that statement to a new line. Reordering statements lost the
  comments on their lines.
- A block whose layout could not hold an edit in place (a reorder next to
  the opening brace, a comment for a one-line block) was written again as
  a whole, which dropped its comments. It is now laid out with one
  statement per line first, each with its bytes and its comment, and then
  edited.
- Editing one decision category wrote a definition for every other category
  of the same decisions file into `common/decisions/categories/`.
- A copy of a loaded division, template, ship or air wing that was passed
  to `update_oob` next to its original replaced the original. It is now
  appended after the existing items, wherever it sits in the passed list and
  whether or not the original was changed in the same call: the source
  block stays with the original.
- A focus statement added after one that sits in the middle of a line with
  a trailing comment was put between that statement and its comment. It
  now gets its own line below.
- An event `picture` with a space or `#` was written without quotes.
- `PdxNode.set_value` kept a comparison operator (`a > 1` became `a > 2`).
- The map renderer read country colours and tag files with a regular
  expression: a commented-out colour counted, and `hsv` colours did not.
  The known-sprite list counted commented-out sprites.
- Editing one field of a focus deleted its conditional `icon = { ... }` blocks
  and truncated `cost = 7.2` to `7`.
- Editing the `icon = { ... }` blocks of a focus that had no plain `icon`
  added a stale `icon = GFX_...` line. The icon a block-only focus reports is
  now never written back; a plain `icon` is written only when you set it, and
  setting it to `""` removes it.
- Adding a prerequisite, mutually exclusive group, icon block or
  `shared_focus` after a line such as `prerequisite = { focus = A } # note`
  moved the comment (or the rest of the line) onto the new statement. The new
  statement now gets its own line below.
- A `focus_tree` without its own `id` was written twice on save. Unnamed trees
  are now known by the file name (`name`, then `name_2`, ...) both when
  loading and when saving, and are patched in place.
- Focus and focus-tree strings (icons, ids, `relative_position_id`,
  `will_lead_to_war_with`, prerequisite and search filter entries,
  `shared_focus`) were written without quotes, so a value with a space or a
  `#` produced broken script. They are now quoted when they need it.
- An event with conditional `title = { ... }` blocks read its title as `{`,
  and any edit deleted the first block and duplicated other lines.
- An idea with only `equipment_bonus`, `targeted_modifier`, `rule` or
  `research_bonus` was not recognised and was duplicated when a sibling idea
  was edited. A repeated category block in an ideas file lost its earlier
  copy on any edit.
- A decision took `cost` or `icon` from a nested block. A category reopened in
  the same file lost its later decisions and blocked every save. Saving one
  file of a category spread over several files deleted that category's
  decisions from the saved file. A generated category file could take the name
  of a game file and replace it.
- A decision category spread over several `common/decisions` files kept only
  the decisions of the last file, and a category edit left the old inline
  `icon`, `allowed` and `visible` in the earlier files. The category now holds
  the decisions of every file in load order, lists those files in the new
  `DecisionCategory.paths`, and a category edit removes the inline copies from
  each of them.
- Decision and category strings were written without quotes, so
  `update_decision("x", icon="a b")` wrote the invalid `icon = a b`. Every
  string the decision code writes is now quoted when it needs quotes, and a
  quoted category `icon` reads without its quotes.
- A comment inside `traits = { ... }` became trait ids. Editing a typed colour
  (`color = rgb { ... }`) left a dangling block. `colors.txt` values written
  as `HSV { }` were ignored.
- A state was found by any `id = N` in its file, including one in a comment.
  A state file with a byte-order mark loaded empty. A one-field state edit
  could produce broken indentation.
- `update_oob` ignored edits made to copies of loaded divisions, templates and
  air wings (and to the objects `get_oob` returned) and saved the file
  unchanged. Each item is now compared with its source block and every field
  that differs is written, and only those fields. Removing one division or ship
  and adding another in the same call could overwrite a kept one.
- Changing a state's owner split a `victory_points = { 1 5 2 10 }` block into
  one block per pair. Victory points are now rewritten only when their value
  changed or the caller passed them explicitly.
- `set_state_properties(..., manpower=2000)` crashed at save with an integer or
  float. `manpower`, `local_supplies`, `buildings_max_level_factor` and resource
  amounts now accept `int` and `float`, and a number equal to the file's value
  (`1.0` for `1.000`) keeps the file's spelling.
- `validate()` crashed with `ValueError: Unbalanced braces` once a vanilla
  country with an unbalanced order of battle was in the mod.
- Saving rewrote Windows line endings as Unix ones in every line of the file,
  dropped the byte-order mark, and silently deleted bytes that were not valid
  UTF-8.
- `serialize_pdx` dropped the quotes of bare list values and quoted keys,
  flattened nested lists and typed blocks, and `PdxNode.set_value` on a block
  wrote an empty block. `set_block` on an inline statement duplicated the
  script before it, a `@variable` was mistaken for the field of the same name,
  and `==` was scanned as an assignment.
- On-actions: repeated `effect` blocks after the first were ignored, and
  editing weighted `random_events` wrote `20 daily.2` instead of `20 = daily.2`.
- Inherited state lookups now honor same-named mod files and the mod
  descriptor's `replace_path` when no base mods are configured, matching how
  the game resolves files. Province-list changes to existing state files now
  remove or append IDs in place instead of re-rendering the block.
- Country-package validation now accepts HOI4's effective `default` name pool
  as a fallback for countries with air wings, while still rejecting packages
  that have neither a country-specific nor default pool.
- Pending support scripts now participate in effect validation, custom-token
  resolution, and technology/equipment catalogs before saving. Replacements
  hide obsolete definitions and transaction rollback clears derived catalogs.
- Inherited-file loading rejects duplicate IDs before mutating the facade;
  scripted-trigger bodies cannot escape their enclosing definition.
- Event edits preserve conditional descriptions and unmodeled fields; victory
  points serialize as separate province/value pairs.
- Localization destination checks reject unsupported paths before mutation;
  transaction rollback restores state-name lookup results. Inherited localization
  is excluded from authored-content liveness warnings.
- Logical trigger blocks preserve their enclosing scope. Repeated state,
  sprite, runtime-activation, name-pool and trigger lookups avoid redundant scans.
- CI uses patched pip 26.2.1 instead of the vulnerable 26.1.2 pin.

- `update_country(..., technologies=...)` now moves the scenario-start
  technology block ahead of equipment variants that already exist in country
  history, keeping pre-save, previewed, serialized, and reloaded validation in
  agreement.
- Chassis-path validation now tracks mixed DLC and non-DLC predicates through
  nested `AND`, `OR`, `NOT`, and exhaustive `if`/`else` branches. Each
  conditional occurrence remains distinct, so an unknown runtime condition
  cannot hide an uncovered path or invalidate a correctly guarded variant.
- Runtime `recruit_character` validation now reports every occurrence in an
  event or scripted effect, with separate source locations and without counting
  quoted or commented decoys.
- Structurally malformed mod or vanilla `common/countries/colors.txt` files now
  produce exact-path load diagnostics. Non-strict loading continues with
  unaffected country data instead of reparsing and crashing; strict loading
  still fails immediately.
- One byte that was not valid UTF-8 made a whole file read as Windows-1252
  (`München` became `MÃ¼nchen`), and an edit that added a character outside
  the code page then re-encoded every line. Files are now read line by line,
  and only edited lines are re-encoded.

- A localisation value may hold quotes of its own (`KEY:0 "The "Plan""`).
  The reader stopped at the first inner quote and dropped the line, and
  saving another key of the same file then deleted it. Such lines are read
  now, and a line that cannot be read is kept as it is on save.

### Removed

- Module-level regular expressions that read script from raw text and are no
  longer used: `decisions.SCALAR_RE`, `countries.COLOR_RE`, `COLOR_FIELD_RE`,
  `CAPITAL_RE`, `RESEARCH_SLOTS_RE`, `tags.TAG_FILE_RE`, `states.STATE_ID_RE`,
  `STATE_NAME_RE` and `OWNER_RE`.
- The automatic move of category presentation out of a decisions file on any
  decision edit. It now happens only when the category itself is edited.
- `parser.extract_braced_block`, a second brace scanner that nothing used any
  more. Use `parser.find_assignment_block` or the syntax tree.

- `parser.strip_comments` and the comment masking behind it. Nothing reads
  script as text with blanked comments any more; use the syntax tree.
- Validation codes `assigned_idea_not_country_category` (country history may
  assign ideas of any category; `unknown_idea_category` checks the category
  of an idea instead), `duplicate_oob_air_wing` (two equal air wings are
  two wings for the game) and `unsupported_modifier_scope` (it was
  registered, and nothing ever reported it).

## [0.6.5] - 2026-08-01

### Fixed

- `update_country(..., technologies=...)` now materializes pending technology
  grants before a subsequently created equipment variant, so pre-save and
  reloaded validation agree with serialized source order.
- Chassis-path validation now preserves Boolean `AND`, `OR`, and `NOT` DLC
  predicates instead of treating `OR` alternatives as simultaneous
  requirements.
- Runtime `recruit_character` detection ignores quoted string content while
  retaining accurate source offsets for genuine script effects.
- `verify_real_mod.py MOD_ROOT` now discovers the HOI4 installation from a
  `.hoi4.json` colocated with the explicit mod root unless an explicit
  `--hoi4-install` overrides it.

## [0.6.4] - 2026-08-01

### Fixed

- Modular chassis validation now follows country-history source order and DLC
  conditions. A later technology grant or one confined to a mutually exclusive
  DLC branch can no longer validate an engine-rejected equipment variant.
- Runtime `recruit_character` validation now scans mod-defined scripted effects,
  including nested directories, instead of only modeled events, focuses,
  decisions, and on-actions.
- Chassis-order and runtime-recruitment diagnostics now include source line and
  column locations.

## [0.6.3] - 2026-08-01

### Added

- Transactional `Mod.import_flag_to_mod()`, `Mod.import_portrait_to_mod()`, and
  `Mod.write_portrait_gfx()` methods. Binary assets now participate in
  `preview()`, `transaction()`, atomic `save()`, and external-change guards.
- `set_country_name_pool()` for typed `common/names` authoring and package
  validation for air-capable countries that would otherwise fail dynamic
  character-name generation in game.
- Date-aware OOB references and repeatable `date=` assignment/removal support.

### Fixed

- `Mod.from_config(..., strict_loading=True)` now forwards strict loading.
- Mod-owned overrides of vanilla country history/definitions load before
  validation, eliminating context-priming workarounds and a dictionary-size
  mutation crash during lazy country validation.
- Filtered OOB updates retain source coordinates instead of moving comments or
  raw blocks onto unrelated surviving units.
- Complete-country validation requires the small civilian portrait used by
  advisor cards, not merely any large portrait.
- Runtime `recruit_character` is rejected statically; HOI4 only executes that
  effect from scenario history.
- Modular equipment variants now require their chassis technology before
  creation. `allow_without_tech=yes` no longer incorrectly bypasses this gate.
- Game-log attribution recognizes bare `path:line:` effect messages, quoted
  equipment-effect locations, and country-name-only character generation
  failures.
- Installed scripted effects/triggers participate in vocabulary validation,
  and conditional `limit` blocks are no longer misreported as trigger names.
- Content liveness recognizes flags supplied or consumed by installed vanilla
  content, eliminating false cross-content read/write warnings.

## [0.6.2] - 2026-07-31

### Added

- DLC-aware OOB assignment metadata and public `assign_country_oob()` /
  `unassign_country_oob()` APIs. Land, naval, and air files now use the
  engine's `set_oob`, `set_naval_oob`, and `set_air_oob` effects separately.
- Public `EquipmentVariant` and `OOBReference` models plus
  `create_equipment_variant()` for source-preserving country-history variant
  definitions.
- Man the Guns validation that rejects ungated legacy fleets, missing hull
  `version_name` values, and hull designs without a compatible
  `create_equipment_variant`. These diagnostics reproduce the real engine
  failure that skipped all thirteen Empire ships.
- By Blood Alone validation for ungated airframes, missing air-wing
  `version_name` values, and unresolved airframe variants.
- `find_enclosed_foreign_states()` and the
  `enclosed_foreign_territory` package warning for land components completely
  surrounded by the target country.
- Installed and mod-defined ideology IDs now synthesize valid
  `<ideology>_drift` modifier vocabulary entries.

### Fixed

- Game-log attribution now accepts every observed quoted and unquoted `file:`
  emitter without hard-coding content directories. In particular,
  `taskforce.cpp` equipment-variant failures under `history/units` are no
  longer discarded as unscoped.
- Conditional OOB and equipment-variant readers now retain simple `IF`/`ELSE`
  DLC semantics, including inversion of `has_dlc` for the fallback branch.
- New country histories write `set_oob` instead of the legacy `oob` spelling,
  while reading and source-preserving edits remain backward compatible.
- OOB deletion now removes matching land, naval, and air assignments instead
  of leaving stale references.

### Changed

- New OOB authoring rejects a mixed land/naval/air assignment and directs
  callers to separate domain files. Existing mixed files remain readable and
  produce the `mixed_oob_kinds` migration warning.
- `CountryPackageReport` and `ContentLivenessReport` now expose
  `analysis_scope="structural"` and
  `proves_dynamic_achievability=False`. Their completeness/cleanliness results
  do not claim to solve popularity arithmetic, variable thresholds, or
  player-state reachability.
- Agent guidance now requires DLC-aware naval fallbacks, enclosed-state checks,
  fresh engine-log review, and live testing of important dynamic branches.

### Migration

- Replace combined starting-force files with separate land, naval, and air
  OOBs. For Man the Guns, gate the hull OOB and matching `EquipmentVariant`
  records with `required_dlc=("Man the Guns",)` and provide a legacy naval OOB
  with `excluded_dlc=("Man the Guns",)`.

## [0.6.1] - 2026-07-30

### Fixed

- Installed-game effect and trigger scopes are now enforced instead of merely
  exposed as metadata. Validation follows explicit country tags, numeric state
  scopes, character scopes, and typed iterators, with stable
  `unsupported_effect_scope` and `unsupported_trigger_scope` warnings.
- `validate_effect()` now accepts an explicit root `scope=` and defaults to
  country scope; passing `None` still validates every explicit nested scope.
- `idea_mutation_collision` now recognizes a runtime
  `NOT = { has_idea = X }` guard around `add_ideas = X`, while retaining the
  warning for unguarded, mismatched, removal, and swap mutations.
- Refreshed the private Empire verification baseline to the current completed
  build. The integration test now requires `DSR` to validate as a complete
  runtime country activated by `empire.88`, rather than freezing the obsolete
  missing-activation state into the SDK suite.

## [0.6.0] - 2026-07-29

### Added

- Installed-game `GameScriptVocabulary` support for documented effects,
  triggers, and modifiers, including supported scopes/categories, usage counts
  by script domain, high-frequency typo suggestions, mod-defined scripted
  extensions, and explicit kind-qualified allowlists.
- Source-preserving naval and air OOB authoring with `Fleet`, `TaskForce`,
  `Ship`, `ShipEquipment`, and `AirWing`, including modern/legacy equipment
  shapes, public `create_oob()`/`update_oob()` integration, and validation of
  structure, equipment, locations, ownership, and country tags.
- `ContentLivenessReport` and `Mod.analyze_content_liveness()` for focus
  reachability, triggered-event incoming references, idea grants, flag
  read/write asymmetry, and unused localization.
- `build`, `package`, and `release` validation stages. Build pipelines can
  defer complete-country checks without maintaining local error filters;
  release validation adds semantic liveness.

### Changed

- The release gate now runs package/vocabulary validation plus semantic
  liveness on its immutable baseline. Rollback-only source-preservation probes
  intentionally use package validation so temporary probe content cannot
  create false liveness failures.
- Agent guidance now requires modeled naval/air starting forces, build-stage
  validation between pipeline steps, and release-stage vocabulary/liveness
  review before declaring a mod ready.
- OOB documentation and examples now cover land, naval, and air authoring
  while retaining production blocks, comments, ordering, and unknown fields.

### Known audit status

- The pinned private Empire corpus snapshot has zero HOI4 1.19.2 compatibility findings, all
  ten populated source-preservation probes pass, its 37 divisions, one fleet,
  and five air wings validate through the modeled OOB, and the corpus remains
  byte-identical after audit. In that historical snapshot, the pre-existing
  `DSR` content gap remains intentionally visible: the tag has definitions but
  neither scenario-start territory nor any runtime activation path. This does
  not describe newer Empire builds.

## [0.5.1] - 2026-07-29

### Fixed

- Gemini flag and portrait guidance now stores paid, non-reproducible PNG
  candidates under the durable mod-project `assets/candidates/` directory
  instead of an operating-system temporary directory that may be memory-backed
  and wiped on reboot.
- Agent instructions now require unique filenames for billable attempts and
  retain the accepted source PNG beside the mod project before importing it.
  Disposable one-off automation remains under `/tmp/hoi4-agent-scripts/`.

### Migration

- Existing generator calls remain API-compatible because candidate destinations
  are caller-controlled. Change `/tmp/hoi4-agent-assets/...` destinations to
  `<project>/assets/candidates/...` and keep the accepted PNG as a build input.

## [0.5.0] - 2026-07-29

### Added

- Full source-preserving character CRUD with direct and repeated DLC
  `instance` variants, multi-role characters, explicit occurrence-selected
  role/instance mutations, automatic recruitment, and character localization.
- Land OOB models and CRUD for division templates, battalion grids, and
  starting divisions, including assignment to country history and validation
  of templates, unit types, province type, ownership, and factor ranges.
- `CountryPackageReport` and `Mod.validate_country_package()` for enforcing
  flags, portraits/GFX, roster size, recruitment, localization, owned/cored
  capitals, and valid land OOBs.
- Optional-map `find_disconnected_states()` topology analysis with capital
  anchoring, explicit adjacency links, minimum-size filtering, and state
  allowlists for legitimate islands or overseas holdings.

### Changed

- `validate()` now treats countries registered through
  `00_generated_tags.txt` or created in memory as complete-country packages.
  Lifecycle-aware validation checks scenario-start countries against starting
  ownership/OOB data, checks later focus/event releases against their runtime
  activation path, and rejects generated tags with neither. `save()` remains
  advisory-compatible, while release gates fail on package errors.
- The compatibility `Leader` API now operates alongside the richer
  `Character` model instead of being the only character-authoring surface.

### Known audit status

- The private Empire corpus passes HOI4 1.19.2 compatibility and all required
  source-preservation probes. It retains one intentional release-time
  diagnostic for investigation: generated tag `DSR` has neither scenario-start
  territory nor a detected focus, event, decision, or on-action activation
  path. Runtime-released `ILL` is correctly recognized from
  `AUH_crown_of_zvonimir` and does not produce a starting-country error.

## [0.4.3] - 2026-07-28

### Changed

- Idea `icon` values are now canonical bare `picture` stems. Existing
  `GFX_idea_`-prefixed API inputs remain accepted but are normalized on create,
  update, and serialization.
- `suggest_idea_icon()` and `suggest_idea_icons()` now return bare stems that
  can be passed directly back to `create_idea()`.
- The default idea picture is now the real vanilla stem
  `generic_political_support`, replacing the nonexistent
  `GFX_idea_generic` sprite key.

### Fixed

- Idea validation now resolves a bare `picture` value as
  `GFX_idea_<picture>` against both mod and configured vanilla interface
  sprites. It no longer rejects correct stems or recommends the prefixed value
  that HOI4 silently double-prefixes and renders as a question mark.
- Prefixed on-disk `picture = GFX_idea_<stem>` values are validation errors,
  and touching an affected idea source-preservingly removes the prefix.
- Agent and API guidance now explains the engine's implicit `GFX_idea_`
  lookup rather than describing the `picture` field as a full sprite name.

### Migration

- Prefer `icon="generic_political_support"` rather than
  `icon="GFX_idea_generic_political_support"`. Both API forms work in 0.4.3,
  but saved files always contain `picture = generic_political_support`.
- Re-save prefixed idea definitions through the SDK or manually remove
  `GFX_idea_` from each `picture` value. Keep the prefix on the matching
  `interface/*.gfx` sprite declaration.

## [0.4.2] - 2026-07-28

### Added

- A HOI4 `error.log` parser and `Mod.validate_game_log()` API that filter
  records to files owned by the target mod, group engine-error classes, support
  timestamps and byte offsets, and integrate optional freshness/error checks
  into the real-mod and installed-game release gates.
- Static validation errors for engine-rejected idea descriptions, character
  `roles`, combined decision-category files, and unsupported
  `set_politics.elections_frequency`.

### Changed

- Decision-category metadata now writes to
  `common/decisions/categories/*.txt`, separately from decision content.
  Touching pre-0.4.2 combined output migrates it source-preservingly.
- Strict localization validation now requires both an idea's display name and
  its conventional `{idea_id}_desc` description.
- `00_generated_tags.txt` is canonicalized by country tag so identical builds
  produce identical bytes.

### Fixed

- Ideas no longer serialize the unsupported top-level `desc` assignment; HOI4
  derives descriptions from `{idea_id}_desc` localization.
- Generated country leaders no longer contain the unsupported top-level
  `roles = { country_leader }` field, and touching an affected leader removes
  the legacy field.
- `effect_set_politics()` no longer emits `elections_frequency`, which current
  HOI4 rejects.

### Migration

- Replace idea-level `desc = SOME_KEY` with localization at
  `{idea_id}_desc`. The compatibility `desc` argument is accepted only when it
  names that fixed key and is never serialized.
- Remove `elections_frequency=` from `effect_set_politics()` calls; supplying it
  now raises instead of generating dead script.
- Edit and save legacy combined decisions once to move category metadata into
  `common/decisions/categories`, or recreate them through the facade.

## [0.4.1] - 2026-07-27

### Fixed

- Newly created ideas now serialize their sprite with HOI4's supported
  `picture` assignment instead of the ignored `icon` assignment.
- Validation now warns about legacy idea-level `icon` assignments even when
  their sprite name resolves, and editing an affected idea migrates the key to
  `picture` without rebuilding the surrounding source.

## [0.4.0] - 2026-07-27

### Added

- Optional Gemini Developer API generation of reviewable 3:2 flag and 3:4
  leader-portrait PNG candidates, with HOI4-specific prompt presets, explicit
  reference images, model/resolution validation, immutable result metadata,
  and atomic failure behavior.
- Mocked Python 3.11/3.14 Gemini-extra CI coverage and an explicitly authorized
  billable live-smoke example.

### Changed

- Gemini's Python Interactions JPEG output is validated and converted to the
  promised PNG candidate, small provider aspect-ratio variance is normalized,
  and live-tested agent prompts now use exhaustive flag inventories and
  positive-only historical portrait descriptions.
- Agent guidance now treats broad country creation, release, restoration, and
  independence requests as complete visual packages with a three-size flag,
  leader, advisor, and commander portraits. Missing billing-enabled Gemini
  credentials must be disclosed with a Google AI Studio setup link rather than
  silently producing an incomplete country.

## [0.3.1] - 2026-07-27

### Added

- Deterministic Python 3.11-3.14 CI, minimum/latest optional-dependency jobs,
  branch-coverage enforcement, property tests, grouped Dependabot updates, and
  a gated tag-release workflow with install smoke tests and checksums.
- A read-only installed-HOI4 compatibility audit that validates generated
  effect/modifier documentation, exact technology categories, strict loading,
  assets, localization, source-churn budgets, filesystem fingerprints, and
  required real-mod probes.
- Release-gate support for explicitly required probe names and machine-readable
  reporting of missing required probes.

### Changed

- The curated effect and modifier catalogs now contain only exact entries
  present in HOI4 1.19.2's generated documentation.

### Fixed

- Experience effect helpers and catalogs now emit the current HOI4
  `army_experience`, `navy_experience`, and `air_experience` effects instead of
  obsolete `add_*_experience` names.
- DDS exports now reject older Pillow builds that silently ignore the requested
  DXT compression instead of accepting a valid-looking but incompatible file.

## [0.3.0] - 2026-07-27

### Added

- Native, source-preserving `common/dynamic_modifiers` models, CRUD methods,
  and add/remove/update effect helpers.
- Country history support for `elections_allowed`, `set_stability`,
  `set_war_support`, `set_technology`, and `oob`.
- Idea-icon validation and close-match suggestions.
- Validation for truncated mod `common/countries/colors.txt` files, unsupported
  `dynamic_country_ideas`, and unknown civil-war capital state IDs.

### Changed

- State search now resolves `STATE_<id>` localization and ranks the localized
  name ahead of stale filenames. Results expose `file_name`, `display_name`,
  and the field that matched.
- Localization values accept real line breaks and round-trip HOI4 `\n` escapes.
- `effect_spawn_civil_war_with_focus_tree()` loads the tree inside
  `start_civil_war`, where the game scopes execution to the spawned country.
- The technology-category catalog includes the 27 doctrine categories present
  in HOI4 1.19.
- Numeric and date-shaped Paradox keys are recognized by the fast top-level
  assignment scanner.

### Fixed

- Creating a new country no longer writes a partial global `colors.txt` that
  removes vanilla map colors. Vanilla color overrides seed the complete
  configured vanilla table before applying changes.
- Replaced the nonexistent `research_time_factor` catalog entry with
  `research_speed_factor`.
- Removed the nonfunctional `rebel_tag` civil-war focus-tree API.
- Replaced the non-HOI4 `dynamic_country_ideas` API with native dynamic
  modifiers.

## [0.2.0] - 2026-07-22

### Added

- Source-preserving ideology, dynamic-idea, and bookmark models.
- Project scaffolding, launcher descriptor creation, and safe mod discovery.
- Optional flag and portrait conversion helpers.
- Political-map rendering and procedural map-generation services.
- Duplicate identifier diagnostics across single-definition content.
- Progress and cancellation hooks for long validation and map operations.
- Real-mod source-stability and packaging release gates.
- Optimistic source-concurrency checks that reject preview/save when another
  writer changed a target after the `Mod` instance loaded.

### Changed

- Existing focus, event, decision, idea, on-action, localisation, and state
  serializers now patch changed values locally instead of re-rendering whole
  definitions.
- State parsing preserves legal nesting and repeated history blocks.
- Country politics now retain custom ideology-group popularities and choose
  leader sub-ideologies from loaded custom definitions.
- Idea modifier updates replace by default; `merge_modifier=True` opts into
  key-by-key merging, and an empty replacement removes the modifier block.
- Event loading and validation now include unit-leader and operative-leader
  event types.
- Bookmark mutations target repeated country variants by zero-based occurrence,
  and bookmark texture plus `.gfx` import commits atomically.
- Repeated on-action hooks are modeled compositionally and can be selected by
  occurrence or source path for exact edits and deletion.

### Fixed

- State edits no longer move history-level buildings to the state root.
- Repeated victory-point blocks no longer collapse during unrelated edits.
- Duplicate event and state IDs no longer silently overwrite earlier entries.
- Existing country edits follow recruited character references and patch the
  exact leader block without changing custom IDs or neighboring characters.
- Malformed ideology colors retain their original channel count for validation
  instead of being silently truncated.
- Country color reads and edits now honor mod-over-vanilla and `colors.txt`
  precedence while preserving unrelated color definitions.

## [0.1.0] - 2026-06-24

### Added

- Initial SDK facade, core content models, parser, validation, transactions,
  previews, and atomic saves.
