# How the `Mod` class is organised

`hoi4.Mod` is one class for callers, but its code lives in the `hoi4/mod/`
package, one module per content type. Each module defines one layer class, and
`Mod` inherits from all of them.

## The rule

**A layer class subclasses only the layers whose methods it calls.** Its class
header is its dependency list:

```python
class EventsLayer(CoreLayer): ...                      # events need nothing else
class CharactersLayer(CountriesLayer, LocalisationLayer): ...
class CountryAuthoringLayer(CharactersLayer): ...      # creating a country creates its leader
```

`mypy` enforces this. A method that lives in a layer you did not name does not
exist on your class, so calling it is an `attr-defined` error. The dependencies
therefore form a graph without cycles: country authoring may call character
code, but character code cannot call country authoring.

The full list of layers and what each one uses is in the `hoi4/mod/__init__.py`
docstring. `tests/test_mod_layers.py` checks the structure.

## What the rule does not cover

All shared state (the loaded models, the dirty sets, the caches) is declared in
`CoreLayer`, and every layer can read and write it. The loaded models and their
dirty sets are grouped into sections that load on first use; see
[`mod_sections.md`](mod_sections.md). The layering
controls which methods may call which. It does not yet stop one layer from
touching another content type's data directly.

## Adding or moving code

- Put a new method in the layer of the content type it edits.
- If it needs a method from a layer that is not a base yet, add that layer to
  the class header. If that would create a cycle, move the helper down to a
  layer both can use instead.
- List bases from the most specific to the most general, in the same order the
  other layers use, so Python can build one consistent method resolution order.
- Only `CoreLayer` and `Mod` define `__init__`. Constructors such as
  `from_config` live on `Mod`, so a bare layer class cannot be built half-loaded.
- Do not list a base that another base already provides; a test checks that
  each class header is exactly the reduced set of layers the module calls.
- Module-level helpers used by one layer live in that layer's module. Helpers
  used by several layers live in `hoi4/mod/_shared.py`.

## Patching helpers in tests

Each layer module imports the helpers it uses, so the same helper (for example
`set_block`) is imported by several layer modules. A test that counts calls
must replace it in all of them. Use the `patch_mod_helper` fixture from
`tests/conftest.py`:

```python
def test_scans_once(tmp_path, patch_mod_helper):
    patch_mod_helper(build_state_index, counting_index)
```

It replaces only module-level names bound to that exact function. A helper
imported inside a function body is looked up in its home module at call time,
so patch the home module for those.

## Content layers: what the mod inherits

The word "layer" also names something else: the game and the base mods under
the writable mod (`base_mod_paths`). `hoi4/layers.py` reads them, and nothing
is copied.

`self._base_content` is a `ContentView` of what the mod inherits, or `None`
without a game folder and base mods:

- Without base mods it is `FolderView(hoi4_install)`: the game folder as it is,
  files the mod overrides included. Each call does what direct path access did
  (one `stat` for a file, one folder listing for a listing).
- With base mods it is the `ContentLayers` object (`self._content_layers`),
  which stacks the game, then the base mods in order. Each mod's
  `descriptor.mod` `replace_path` hides those folders in every lower layer
  (the top mod's own `replace_path` included). Only `CONTENT_DIRS` are
  layered. A file the mod has itself is not part of the view: the mod's own
  files are read from the mod folder.

The calls, all with paths relative to the content root:

| Call | Answer |
| --- | --- |
| `file(relative)` | the source file that wins, or `None` |
| `glob(folder, pattern)` | `{relative: source}` of the files directly in a folder, sorted by name |
| `rglob(folder, pattern)` | the same for every file below a folder, in `Path.rglob` order |
| `relative(source)` | a source file's relative path, or `None` |

`ContentLayers` adds `source(relative)` (the winner including the mod's own
files, for `content_source()`) and `inherited(relative)` (the lower file a mod
file hides, used to decide whether deleting the mod's file would expose one).

What the view reads is never kept, so it always matches the disk. The
descriptors' `replace_path` lines are read once; a full reload (`_reload_all()`, which `discard()` runs when a base mod
changed) builds a new view.
The order of a listing matches what the old merged copy gave: a file keeps the
place it got in the lowest layer that has it, and files new in a higher layer
come after. Where order decides a duplicate (two localisation files with the
same key), the later file wins, as before.

Models keep the real source path (`game/history/states/1-Berlin.txt`). Code
that needs the path relative to the content root asks the view (`relative()`),
because a source can be in any layer. Module-level readers that take a game
folder (`read_country`, `load_vanilla_tags`,
`build_state_index`, the map topology helpers, ...) also take a view;
`as_view()` turns a plain folder into a `FolderView`.

Views are read only and never copied (`copy.deepcopy` returns the same
object), so a view used as a dictionary key (the country tag mappings) still
matches after a transaction rollback.

`tests/test_content_view.py` checks precedence, `replace_path`, both
localisation spellings, files in one layer only, and that loading, editing,
validating, saving and discarding never write into read-only game and base
folders.
