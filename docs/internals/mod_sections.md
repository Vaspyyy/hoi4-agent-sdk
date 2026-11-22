# Sections: lazy loading and partial reload

`Mod(root)` reads no content. Content is split into **sections**, one per
kind of content, and each section loads the first time it is used. After a
save or a discard, only the sections that may have changed are dropped; they
load again on next use.

## The registry

`SECTIONS` in `hoi4/mod/_core.py` is the one list of sections, in load order:
countries, characters, oobs, states, focus, events, on_actions, decisions,
ideas, ideologies, dynamic_modifiers, bookmarks, localisation. Each `Section`
names:

- `loader`: the method that fills the section from disk (`_load_events`, ...).
- `data`: the attributes the loader fills, each with the factory of its empty
  value (`("_events", dict)`). They do not exist until the section loads.
- `pending`: the attributes that hold unsaved edits (dirty sets, staged
  deletions). They always exist and are empty after a load or a discard.
- `dirty_names`: the names in `self._dirty` that mean "this section has edits".
- `folders`: the mod-relative folders its own files live in.
- `reads`: other folders its loader also reads (country names come from
  `localisation`, a country's leader from `common/characters`).
- `depends_on`: sections whose data this one is built from. Unloading one of
  them unloads this one too (OOBs and ideas are matched to countries).
- `caches`: data attributes that lookups fill later (`get_state` adds to
  `_states`), with the lookup. The reload check uses it.

`__init__`, `_snapshot`, `_restore`, `discard`, `reload` and the reload check
all read the registry. A new piece of section state goes into the registry,
and its type into the declarations at the top of `CoreLayer`.

## Lazy loading

`CoreLayer.__getattr__` runs only when normal lookup fails. For a name in a
section's `data`, it runs that section's loader and returns the value. The
loader of one section can read another section the same way (the OOB loader
reads `_countries`). The rest of the code reads `self._events` as before.

- `__getattr__` is hidden from mypy (`if not TYPE_CHECKING`), so mypy still
  rejects unknown attributes and keeps checking the layering.
- Names with two leading underscores, and objects whose `__init__` has not run
  (`copy`, `pickle`), fail with `AttributeError` as usual.
- `_load_section` refuses to start a section that is already loading, with a
  clear error. A loader that fails leaves its section unloaded.
- Each section keeps its own load diagnostics. `load_diagnostics` (and the
  private `_load_diagnostics`) load every section and list them in section
  order, then file order, whatever order the sections loaded in.
  `_loaded_diagnostics()` lists only what is loaded; `save()` uses it after
  loading the sections that own the folders it writes to.
- `strict_loading=True` loads every section in `__init__`, and again after a
  save or discard, so a broken file fails at the same moments as before.

## What reloads when

`_discard_changes` (used by `save()` after writing, and by `discard()`) unloads:

1. every section named by a dirty name (`_dirty`). A dirty name no section
   claims unloads everything. Script imports, scripted triggers and assets are
   staged as whole files and map through their paths instead;
2. every section that owns or reads a written, staged, or externally changed
   file (found by fingerprint, see below), and every section whose loader put
   that file into `_original_files`. The descriptor, or a path outside the
   mod, reloads everything (`reload()`);
3. sections changed in memory without a dirty mark (`_reload_on_discard`:
   inherited content loaded for editing);
4. every section that depends on one of these.

All edit buffers are emptied and `_original_files` keeps only the texts owned
by sections that are still loaded. Derived caches built from several files
(`_scan_cache`, the sprite and state indexes, the country context cache, ...)
are cleared; they are rebuilt from per-file results that `hoi4._file_cache`
keeps, so that is cheap. Caches keyed by a file's exact text check that text
on every use and stay valid: the references of the mod's own files
(`_own_reference_cache`) keep the entries of files that were not written,
staged or changed and that belong to no unloaded section, and the parsed
sprite, scripted-trigger and runtime-activation texts are kept. An entry of
`_own_reference_cache` also records the character ids it was read with
(`_character_scope_names()`, the same frozenset while the ids are the same),
so a character created, deleted or reloaded since reads the file again.
`reload()`
clears all of them. With base mods, the inherited content is read where it is (see
[`mod_layers.md`](mod_layers.md)), so nothing needs rebuilding after a save. A
base mod's text files have fingerprints too (`_base_fingerprints`): when one
changed, appeared or went away, `save()` and `discard()` reload everything.

## External changes: fingerprints

`_fingerprints` holds `(size, mtime_ns, racy)` for every text file of the mod
(`.txt .yml .gfx .lua .mod`), taken with `stat` alone. `_read_digests` holds a
digest of the text the SDK read from a file (`_note_read`).

The first read of a file takes it as it is then, with its fingerprint of
that moment. So a file that was touched, changed or created after `Mod()`
and before its section first loaded is simply read, as a fresh `Mod` would
read it, and `save()` may write it. A later read of the same file does not
move the baseline while a loaded section still holds an older text of it
(`_original_files`): what is in memory was built from that text, so the
change keeps looking like one and `save()` refuses to overwrite it.

Two questions are asked of them, with different care:

- **"What may have changed on disk?"** (which sections `discard()` and
  `save()` drop). Same fingerprint: the file counts as unchanged. Different
  fingerprint: the current text is compared with the digest, and the same
  text (a `touch`) is no change.
- **"May I overwrite this file?"** (`save()` and `preview()`, for every file
  they are about to write). Here the text itself is always compared with the
  digest, whatever the fingerprint says: a rewrite of the same size whose
  time was set back looks unchanged to `stat`, and this is the last check
  before the content is replaced. A different text raises
  `ExternalModificationError`. Only the files being written are read, so
  this costs almost nothing. A file the SDK never read has no digest: for it
  only size and time are known, and a different fingerprint counts as
  changed.
- File times are coarse: a file rewritten within the same tick keeps its time.
  A file changed less than two seconds before its fingerprint was taken is
  "racy" (git's word). Its fingerprint never proves it unchanged, and its digest
  is taken when the fingerprint is.
- After a save, the written files get new fingerprints and digests. A discard
  takes the fingerprints of the whole mod again, which is also how it finds
  files changed on disk.

## Threads

A `Mod` is not safe to share between threads, and lazy loading makes that
matter more than before: while a section's loader runs, the section's
attributes already exist and are half filled, so a second thread that asks
for the same content at that moment gets an incomplete answer without an
error. One lock (`_SECTION_LOCK`) lets only one loader run at a time, which
keeps two threads from running the same loader, and nothing more.

Use one `Mod` per thread (as `docs/api.md` has always said), or guard a
shared one with your own lock. For read-only use from several threads,
`Mod(..., strict_loading=True)` loads every section at construction and
again after each `save()` and `discard()`, so no section loads in the
middle of a read.

## The reload check

With `HOI4_VERIFY_RELOAD=1`, every `save()`, `discard()` and `reload()` ends
with `_verify_reload()`: it builds a fresh `Mod` on the same folders (sharing
the inherited layers), loads every section of both, lets the fresh one look up
what lookups added to the live caches, and compares every section attribute,
the load diagnostics and `_original_files` by value. A difference raises
`AssertionError` naming the section, the attribute and the first differing id.
Sections the check loaded are unloaded again afterwards. Cache entries read
from the game or a base mod are left out: those layers are shared, and the
same entry can be read in more than one way.

`tests/conftest.py` turns the check on for the whole suite. When it fails,
fix the registry (a missing dependency, folder or dirty name), not the check.
