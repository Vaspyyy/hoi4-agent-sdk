# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Ordered directory-backed base mods, descriptor dependencies, effective-file
  provenance, and opt-in inherited focus/event/idea/on-action editing.
- Transactional support-script imports, source-preserving scripted-trigger
  authoring, explicit conversion color-table rebuilding, and impassable states.

- `CommonsImageClient` searches Wikimedia Commons and downloads raster sources
  with creator/license metadata, retrieval time, checksums, and verified local
  cache reuse. SVG flags use Commons raster thumbnails. Sources feed the
  existing transactional flag/portrait import APIs without a Gemini key.

### Changed

- Agent country-creation guidance now prefers suitable existing assets and
  Commons sources. Paid Gemini generation is an explicitly authorized option;
  missing credentials no longer prevent local or web-sourced graphics.

### Fixed

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
