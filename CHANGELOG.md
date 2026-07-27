# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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
