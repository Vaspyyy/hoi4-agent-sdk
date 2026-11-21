# The disk cache of game file results

Validation reads thousands of the game's own files: the references of every
script file, division templates, sprites, characters, states, map data and
localisation. Those files do not change between runs. So what one process
reads out of them is stored on disk, and the next process reads the stored
results instead of parsing the files again (`hoi4._disk_cache`).

On a small mod, the first `validate()` of a process took about 40 seconds
before and takes about 3 to 4 seconds with a warm cache (measured 2026-11 on
a busy machine). A later `validate()` in the same process was already fast:
results are also kept in memory (`hoi4._file_cache`).

## What is stored

Only results for files the mod inherits: the game install and base mods.
The mod's own files are never stored; their results stay in memory for the
life of the process. A mod placed inside the game folder counts as the mod.

| Store | What one entry holds |
| --- | --- |
| `file_references` | The owners, references and definitions of one script file of `common`, `events` or `history` |
| `names` | A set of names from one file: scripted effect and trigger names, division template names, character, idea, event and focus ids, name pool tags, sub-unit types, sprite names, dynamic tags, idea categories, event namespaces, localisation keys and `[?name]` values |
| `definitions` | What one script file defines (events, ideas, focus trees, technologies, equipment, tags, organizations); the country tags and tag aliases come from here |
| `shared_focuses` | The prerequisites and the `allow_branch` condition (none, or one that always holds, kept apart) of every top-level shared or joint focus of one focus file |
| `pairs` | `(equipment, technology or special project)` pairs of one file, in order |
| `sprite_textures` | `name -> texturefile` of every sprite of one `.gfx` file |
| `state_index_header` | The id, name and owner of one state file |
| `localisation_entries` | Every entry of one `.yml` file |
| `country_loc_entries` | The entries of one `.yml` file whose key starts with a country tag |
| `province_types` | `province id -> type` of `map/definition.csv` |
| `strategic_regions` | The strategic regions of one file |
| `map_buildings` | The parsed rows of `map/buildings.txt` |
| `province_topology` | Land provinces, land adjacency and coastal land of the map (read from `provinces.bmp`, `definition.csv` and `adjacencies.csv`) |

Nothing that depends on the mod's own content is stored: the checks combine
the stored results with the mod's files on every run.

## Where it lives

`~/.cache/hoi4-sdk/file-results/`, or `$XDG_CACHE_HOME/hoi4-sdk/file-results/`
when `XDG_CACHE_HOME` is set. `HOI4_SDK_CACHE_DIR=/some/folder` moves every
cache of the SDK (this one and the script vocabulary) to
`/some/folder/file-results/` and `/some/folder/script-vocabulary/`.

`HOI4_SDK_DISK_CACHE=off` (or `0`, `false`, `no`) turns every disk cache of
the SDK off: nothing is read from or written to disk, and each process reads
the game again. It is safe to delete the folder at any time.

There is one JSON file per store and code version, named
`<store>-<code stamp>.json`. With the game alone it holds about 40 MB.

## When it is rebuilt

- **A changed file.** Each entry records the file's path, size and
  modification time. An entry whose file now has another size or time is not
  used; the file is read again and its new result replaces the entry. A file
  changed less than three seconds ago is not stored at all: a second change
  in the same tick of the clock could keep the same size and time.
- **A changed SDK.** The code stamp is a hash of every Python source file of
  the `hoi4` package (and the Python version). Any change of the SDK starts
  new store files, so a changed analysis can never read old results. The
  three newest older versions are kept for other checkouts; older ones are
  deleted.
- **A broken file.** A store file that cannot be read, or that has another
  format, is ignored and written again.
- **Deleted files.** Entries of files that are gone or changed are dropped
  whenever a store file is written.

## How it is written

New results are written at the end of `validate()` and when the process
exits. Writing reads the current file first and adds the new entries, so two
processes over the same game keep each other's results. The file is written
to a temporary file and renamed into place: a reader sees the old or the new
file, never half of one. When two processes write at once, the last rename
wins and nothing breaks.

JSON was chosen because reading it never runs code (unlike `pickle`) and it
loads fast. Each store has an encoder and a decoder, and the decoded result
is equal to what the analysis returns (`tests/test_disk_cache.py` checks this
round trip for every stored shape).

## Base mods

With base mods, the SDK reads the inherited layers where they are on disk
(`hoi4.layers.ContentLayers`), so a base mod's file is stored under its own
path, like a file of the game. The base mod being edited is never a layer of
itself: only the mod passed as `mod_root` counts as the mod.

## Adding a stored analysis

1. Write the analysis as a function of one file's text that returns plain
   data (or something that turns into JSON and back without loss).
2. Define a `DiskAnalysis(name, encode, decode)` next to it.
3. In a `Mod` layer, read each inherited file through
   `self._cached_result(purpose, path, compute, analysis)`. It keeps the
   mod's own files in memory only. For a result of several files, use
   `hoi4._file_cache.cached_files_result`.
4. Add the shape to the round-trip test in `tests/test_disk_cache.py`.
