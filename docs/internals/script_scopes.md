# How the SDK checks effect, trigger and modifier names

This page is for people changing the SDK. It covers the script vocabulary
(`hoi4.script_vocabulary`), the scope-tracking walker (`hoi4.script_scopes`),
the reference reader that rides on it (`hoi4.script_references`) and the file
splitter all of them share (`hoi4._script_corpus`). Read [parser.md](parser.md) first: everything here
reads the lossless syntax tree, never raw text with a regular expression.

The rule that shapes all of it: **the game's own script is valid by
definition.** Every effect, trigger, modifier and weight block of the
installed game must walk without a finding, apart from the real mistakes
listed in `tests/test_corpus_script_tokens.py`.

## The pieces

| Module | Job |
| --- | --- |
| `hoi4._script_corpus` | Split one file into owners (a focus, an event, a decision...) and their effect, trigger, modifier and weight blocks, by where the file sits. The only splitter. |
| `hoi4.script_scopes` | Walk one block through the tree; say what each statement is and which scope it runs in. |
| `hoi4.script_vocabulary` | Know every name the game and the mod define; check the walker's statements; cache what comes from the install. `script_sources()` turns a parsed file into `ScriptSource`s. |
| `hoi4.script_references` | Read the ideas, events, flags and statements each owner names, from the walker's `visit` callback. |
| `Mod._semantic_script_sources` | `script_sources()` of every own script file, as `save()` will write it. |

So the token check, the reference check, the liveness graph and the corpus
test read the same blocks with the same walker.

## The walker

`walk_script(text_or_block, kind, scope=..., from_scope=..., is_command=...,
characters=..., arguments=...)` returns a `ScriptStatement` (name as written,
kind, line, column, scope) for every **command** of the block. `kind` is what
the block holds: `"effect"`, `"trigger"`, `"modifier"` or `"weight"`.

Every statement of an effect or trigger block is one of four things:

1. **A control structure** (`CONTROL`): `if`, `else_if`, `else` (all with
   `limit`; an `else` with a `limit` works like `else_if`), `AND`, `OR`,
   `NOT`, `count_triggers`, `hidden_trigger`, `custom_trigger_tooltip`,
   `custom_override_tooltip`, `hidden_effect`, `effect_tooltip`, `random`
   (with `chance`), `random_list`, `while_loop_effect`, `for_loop_effect`,
   `for_each_loop`, `for_each_scope_loop`, `random_scope_in_array`, `any_of`,
   `all_of`, `any_of_scopes`, `all_of_scopes`, `create_dynamic_country`. Each
   entry says what its block holds, which keys are its arguments, which of
   them are triggers (`limit`) and whether the block runs in a new scope.
2. **An iterator** (`ITERATOR_SCOPES`, or any known name starting with
   `every_`, `random_`, `any_` or `all_`; HOI4 has no `ordered_` iterators):
   its own arguments
   (`ITERATOR_ARGUMENTS`: `limit`, `random_select_amount`,
   `display_individual_scopes`, `tooltip`, `prioritize`,
   `original_tag_to_check`, ...) are data, its body runs in the scope the
   table names (`every_owned_state` leads to `STATE`), or in an unknown scope
   when the table does not know. `NOT_ITERATORS` lists names that only look
   like iterators (`any_province_building_level` takes plain arguments).
3. **A scope change**: `ROOT`, `THIS`, `PREV`, `FROM`, dotted chains
   (`FROM.owner`, `ROOT.OVERLORD`, `PREV.PREV`), scope links (`SCOPE_LINKS`:
   `owner`, `controller`, `capital_scope`, `overlord`, `faction_leader`,
   `character`, `opponent`, ...), prefixed scopes (`SCOPE_PREFIXES`: `var:`,
   `event_target:`, `global_event_target:`, `mio:`, `sp:`, ...), country
   tags, state ids and character ids. Behind `var:`, `event_target:` and the
   like the rest of the key names the variable (`var:ETH.host_nation` is one
   reference, its scope unknown); behind a typed prefix the links still
   count (`state:123.owner` is a country, `country:GER.capital_scope` a
   state), and a word that is no link gives an unknown scope.
4. **A command**, everything else. Its block, if it has one, holds
   **arguments**: `country_event = { id = x days = 3 }` is data and is never
   read as commands.

A control structure works only in the kind of block the game documents it
for: one whose block holds triggers (`AND`, `OR`, `NOT`, `hidden_trigger`,
`count_triggers`, `any_of`, ...) is a trigger, one whose block holds effects
(`hidden_effect`, `random`, `random_list`, the loops, ...) an effect; `if`,
`else_if`, `else` and `custom_override_tooltip` work in both. In the wrong
kind it is a command the game does not know there, reported once, with its
block as data. A scalar statement whose key is a scope (`GER = 5`) compares
scopes among triggers and is skipped; among effects it is a command.

`walk_script(..., visit=callback)` also passes every assignment it walks to
`callback(item, role, kind, scope, state)`: the role (`command`, `control`,
`iterator`, `scope` or `argument`), the kind of block, the scope and a
`WalkState` with the key that made the scope, the enclosing iterator and
whether the statement is hidden or guarded. Arguments are passed as data.
`walk_data()` passes the parts of a file that hold no script (a focus's
`prerequisite`, a scripted GUI). The reference reader is built on these two.

Keywords are compared in lower case, as the game does: `IF`, `Else_If`,
`ROOT`, `OVERLORD` all work. `NOR` is not a keyword (HOI4 documents `AND`,
`OR` and `NOT` only); it is Norway, and the game's own script uses it so.

Other block kinds:

- **weight** (`ai_will_do`, `mean_time_to_happen`, `ai_chance`): `factor`,
  `base`, `add`, `days`, ... are arguments; each `modifier = { factor = 2
  <triggers> }` holds triggers. Nothing here is read as a modifier.
- **random_list** weights are numbers or variables; each entry is an effect
  block that may hold such a weight `modifier`.
- **modifier**: every scalar key is a modifier, except
  `custom_modifier_tooltip` and a dynamic modifier's own `icon` and
  `attacker_modifier`. `hidden_modifier = { }` holds modifiers too; any other
  nested block is data.

## Scopes

The walker keeps a stack. `ROOT` is the bottom, `THIS` the top, `PREV` the
one below the top (each further `PREV` one more down), `FROM` whatever the
source says (`ScriptSource.from_scope`), and `FROM.FROM` is unknown. Control
structures do not change the scope; scope changes and iterators push one.

A scope the walker cannot be sure of is `None`, and **a scope finding is only
reported when the scope is certain.** `ENTRY_SCOPES` says where each kind of
block starts:

| Block | Entry scope |
| --- | --- |
| focus `completion_reward`, `select_effect`, `available`, `bypass`, `allow_branch`, `ai_will_do` | COUNTRY |
| focus `complete_tooltip` | unknown (only shown as a tooltip, never run) |
| `country_event` / `news_event` blocks | COUNTRY |
| `state_event` blocks | STATE |
| `unit_leader_event` / `operative_leader_event` blocks | unknown (they fire for the owner country; THIS varies) |
| decision blocks | COUNTRY, with `FROM` = STATE for `state_target = yes` |
| idea `allowed`, `ai_will_do` | COUNTRY |
| bookmark country `available` | COUNTRY |
| on-actions, scripted effects and triggers, character roles, dynamic modifiers, bookmark `effect` | unknown |

Supported scopes come from the documentation's `Supported Scopes` lines. A
token documented for `any`, or with no scopes, is never a scope finding.

## The vocabulary

`load_game_script_vocabulary(install)` reads:

- the three documentation files: effects and triggers with their scopes and
  targets, modifiers with their categories;
- the **templated modifier families** of the modifier documentation, such
  as `production_speed_<Building>_factor`, with the members the documentation
  lists. The game's definitions add members the documentation does not
  list; `FAMILY_SOURCES` says where each placeholder's members are defined:

  | Placeholder | Defined in |
  | --- | --- |
  | `Building` | `common/buildings` (`buildings = { x = { } }`) |
  | `Ideology` | `common/ideologies` |
  | `IdeaGroup` | `common/idea_tags`: categories, `slot`, `character_slot` |
  | `IdeaCategory` | `common/idea_tags`: `type` |
  | `Resource` | `common/resources` |
  | `Unit` | `common/units` (`sub_units`) |
  | `Equipment`, `NavalEquipment` | `common/units/equipment` |
  | `EquipmentModule` | `common/units/equipment/modules` |
  | `Technology` | `common/technology_tags` (`technology_categories`) |
  | `Trait` | `common/unit_leader`, `common/country_leader` |
  | `SpecialProject` | `common/special_projects/projects` |
  | `Operation` | `common/operations` |
  | `Doctrine` | `common/doctrines/{folders,grand_doctrines,subdoctrines,tracks}` |
  | `CombatTactic` | `common/combat_tactics.txt` |

  A documented template only learns members from the folders its documented
  members come from, so `<Doctrine>_track_mastery_gain_factor` grows with
  `common/doctrines/tracks` and not with grand doctrines. `BUILTIN_TEMPLATES`
  (`<Ideology>_drift`, `<Ideology>_acceptance`) stand in when an older
  install's documentation has no templates;
- `common/modifier_definitions`: modifiers a game or mod defines itself;
- **family triggers** (`TRIGGER_FAMILIES`): every building is a state trigger
  (`infrastructure > 3`), every ideology a popularity trigger
  (`fascism > 0.3`), every resource a state trigger (`steel > 10`);
- scripted effects and triggers, which work in any scope, each only among
  statements of its own kind;
- character ids (in lower case), which may stand as scopes;
- usage counts: every statement key of `common`, `events` and `history`,
  in lower case, in total and per folder.

Names are looked up without regard to case (`GameScriptVocabulary.lookup`).

`GameScriptVocabulary.extended(roots, ..., pending=...)` adds what mods
define: their family definitions and `common/modifier_definitions` (read
from the mod folders, with the `pending` texts of files `save()` will write
in place of the disk), plus names the caller passes (scripted effects and
triggers, ideologies, including pending edits). `characters=` is the
whole set of character ids and replaces the install's: a mod can hide a
character of the game (`replace_path`, a file of the same name,
`delete_character()`), and then its id is no scope.
`Mod.game_script_vocabulary()` caches the install's vocabulary per `Mod` and
extends it on every call, so a building made with `create_definition()`
counts before `save()`.

### The disk cache

What comes from the install (the documentation, definitions, characters and
usage counts) is stored as JSON under `~/.cache/hoi4-sdk/script-vocabulary/`
(or `$HOI4_SDK_CACHE_DIR/script-vocabulary/`). The file name is a hash of
the cache schema (`_CACHE_SCHEMA`), the SDK version, the install path and the
size and modification time of every documentation and script file, so a game
update or a new SDK reads the install again. A missing, broken or stale file
is simply read again; the twelve most recently used files are kept.

Building the cache reads the whole install through the tree (about 80 MB of
script; 35 to 40 seconds on a busy machine, measured 2026-11). It runs in the
calling process on purpose: worker processes would re-import a caller's
script that has no `if __name__ == "__main__"` guard. A warm load takes about
0.3 seconds. Bump `_CACHE_SCHEMA` whenever what is stored, or what it means,
changes.

## Findings

`validate_script_sources(sources, vocabulary, ...)` walks every source and
checks each statement (`check_statement`):

| Code | Severity | Meaning |
| --- | --- | --- |
| `unknown_effect_token`, `unknown_trigger_token`, `unknown_modifier_token` | warning | No game, definition or mod knows the name. The message suggests a close, often-used name. |
| `unsupported_effect_scope`, `unsupported_trigger_scope` | warning | A documented name in a certain scope its documentation does not list. |
| `unseen_effect_token`, `unseen_trigger_token`, `unseen_modifier_token` | info | A documented name the game's own script never uses: legal, but untried. |

Every finding has a file and a line. `Mod._semantic_script_sources` parses
each own script file (its pending text when `save()` would change it) and
hands the walker the parsed blocks, so lines are file lines. A source
without a file (`validate_effect`) reports lines of its own text.

`add_core_of` and `remove_core_of` in a certain country scope are left to
the reference check, which reports them once as `country_scope_core_effect`
with the state-scoped fix.

## Testing

- `tests/test_script_scopes.py`: the walker and vocabulary on a small
  generated install, including the true alarms (a misspelled effect, trigger
  and modifier, and a state effect in a certain country scope).
- `tests/test_corpus_script_tokens.py` (marker `corpus`, needs
  `HOI4_INSTALL_DIR`): every block of the real install walks without a
  finding outside `KNOWN_GAME_MISTAKES`. It runs `script_sources()` and
  `validate_script_sources()`, the path `validate()` uses on a mod's files.

When the corpus test reports a new name after a game update, decide which of
the four kinds the statement is and teach the table that covers it; do not
add the name to an allowlist.
