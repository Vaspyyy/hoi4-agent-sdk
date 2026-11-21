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
`CoreLayer.__init__`, and every layer can read and write it. The layering
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
