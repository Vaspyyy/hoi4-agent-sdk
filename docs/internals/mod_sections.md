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

`__init__`, `_snapshot`, `_restore`, `discard`, `_reload_all` and the reload check
all read the registry. A new piece of section state goes into the registry,
and its type into the declarations at the top of `CoreLayer`.

## Lazy loading

`CoreLayer.__getattr__` runs only when normal lookup fails. For a name in a
section's `data`, it runs that section's loader and returns the value. The
loader of one section can read another section the same way (the OOB loader
reads `_countries`). The rest of the code reads `self._events` like a plain
attribute.

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
  save or discard, so a broken file fails at construction and after every
  save or discard, never later at a lookup.

## What reloads when

`_discard_changes` (used by `save()` after writing, and by `discard()`) unloads:

1. every section named by a dirty name (`_dirty`). A dirty name no section
   claims unloads everything. Script imports, scripted triggers and assets are
   staged as whole files and map through their paths instead;
2. every section that owns or reads a written, staged, or externally changed
   file (found by fingerprint, see below), and every section whose loader put
   that file into `_original_files`. The descriptor, or a path outside the
   mod, reloads everything (`_reload_all()`);
3. sections changed in memory without a dirty mark (`_reload_on_discard`:
   what a lookup cached that the section's loader did not load, see "The
   reload check");
4. every section that depends on one of these.

One case reads everything again, as `reload()` does: an edit call raised
since the last save or discard (`_edit_failed`). Such a call may have
changed loaded data before it failed, without getting to its dirty mark,
so there is nothing to find the change by. Every public method of `Mod`
that can change something is wrapped for this in `mod/__init__.py`; only
the outermost call counts, so an edit that calls another one, catches its
error and finishes properly is not a failed edit. A `transaction()` that
rolls back undoes the failed edit with the rest. Lookups are not wrapped.

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
`_reload_all()`
clears all of them. With base mods, the inherited content is read where it is (see
[`mod_layers.md`](mod_layers.md)), so nothing needs rebuilding after a save. A
base mod's text files have fingerprints too (`_base_fingerprints`): when one
changed, appeared or went away, `save()` and `discard()` reload everything.

## External changes: fingerprints

`_fingerprints` holds `(size, mtime_ns, racy)` for every text file of the mod
(`.txt .yml .gfx .lua .mod`), taken with `stat` alone. `_read_digests` holds a
digest of the text the SDK read from a file (`_note_read`).

**The first read of a file is what the SDK knows of it.** It takes the file
as it is then, with its fingerprint of that moment. So a file that was
touched, changed or created after `Mod()` and before anything read it is
simply read, as a fresh `Mod` would read it, and `save()` may write it.

That first read stands until the baseline is taken again: by `save()` for
the files it wrote, by `save()` and `discard()` for the files they find
changed on disk (they unload the sections that read them, so those files
are read again), and by a full reload (`_reload_all()`) for all. A later
read never moves it.
What is in memory, or staged for a save, was built from the first text, so
a change behind the SDK's back keeps looking like one and `save()` refuses
to overwrite it.

Who notes a read:

- Every loader, for every mod file it reads (`_read_source`,
  `_keep_source`). That includes files it reads only to learn something
  and that a later edit rewrites: the country tag files, `colors.txt`, the
  header of every state file, the definition and map files.
- An edit that builds on the text of a file, when the edit is made
  (`_read_noted_text`: the mod's copy of an inherited state, country,
  character or OOB file).
- An edit that replaces or patches a file it did not read, when the edit is
  made (`_note_target`): `import_script_file(overwrite=True)`,
  `rebuild_country_color_table()`, `set_country_name_pool()`, the map files.
- An edit that names its target file, when the edit is made
  (`_output_path`, which a `path=` argument goes through; `set_loc` and
  `import_script_file` check their arguments first and note the file they
  end up with, with `_note_target`). The file may be one that no loader
  reads: loaders read the files directly in their folder, not in
  subfolders, and do not follow a linked folder.
- An edit whose target follows from a name, when the edit is made
  (`_note_country_files`): `create_country` for the files of its tag, which
  may be on disk although no tag file registers them, and `update_country`
  for the history file that a new name moves to (`TAG - Name.txt`; the
  save and the note ask the same function, `country_history_target`, and
  the note takes both names the file can get, because the save decides
  between them only when it renders).
- Not the reads made while a save is rendered (`_read_current_text`). They
  happen a moment before the check below and must not become what that
  check compares with.

A loader that fails holds nothing of what it read, so those reads are
forgotten (`_section_reads` tells which are its own) and the next attempt
takes the files as they are then, after a repair for example.

With `HOI4_VERIFY_RELOAD=1`, a file that is about to be overwritten and has
no noted read raises `AssertionError`. The test suite runs with it, so a
reader that forgets to note is found there, whatever the age of its files.

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
  this costs almost nothing. Should a file have no noted read after all, the
  check uses what else is known: the digest the scan took of it (see the
  next point), a text a loaded section holds, or size and time alone.
- File times are coarse: a file rewritten within the same tick keeps its time.
  A file changed less than two seconds before its fingerprint was taken is
  "racy" (git's word). Its fingerprint never proves it unchanged, and its digest
  is taken when the fingerprint is (`_scan_digests`, kept apart from the
  noted reads: a scan is not a read anything was built from).
- After a save, the written files get new fingerprints and digests. A discard
  takes the fingerprints of the whole mod again, which is also how it finds
  files changed on disk.
- A change that only the text shows (same size, time set back) is found by
  the `save()` or `preview()` that refuses it, not by a fingerprint. Those
  files are remembered (`_seen_changed`), and the next `discard()` treats
  them as changed. So after a refusal, `discard()` always reads the changed
  file again, and the edit can be made once more and saved.
- The limit of this: such a change of a file that is NOT being written is
  noticed by nobody. `discard()`, and a `save()` of other files, leave the
  section loaded, so lookups keep answering from the older text until the
  file is written (refused) or the whole mod is read again (`reload()`, or
  a new `Mod`). `stat` cannot do
  better, and reading every file on every save is what this design avoids.

## Threads

Reading one `Mod` from several threads works as it did when everything was
loaded at construction: no thread ever sees a half-filled section.

- One loader runs at a time in the whole process (`_SECTION_LOCK`, a
  re-entrant lock; a loader may load another section).
- While a loader runs, the section's values are kept in `_section_staged`,
  not on the object. The loader's own thread (`_section_thread`) gets them
  through `__getattr__`; for any other thread the attribute is still
  missing, so it lands in `__getattr__` too, waits for the lock and finds
  the section complete. The values become attributes only at the end.
- A loader sets a plain value of its section (a number, not a container)
  with `_stage`, so that it appears with the rest and not before.
- The price is one `__getattr__` call per access while a section loads,
  which is not measurable on a large mod (loading every section of a mod
  with 11,297 files: 6.3 s before and after).

Changing a `Mod` from two threads was never safe and still is not: edits,
`save()`, `discard()` and `validate()` are not guarded. Use one `Mod` per
thread for that (as `docs/api.md` says), or your own lock.

## The reload check

With `HOI4_VERIFY_RELOAD=1`, every `save()`, `discard()` and `_reload_all()` ends
with `_verify_reload()`: it builds a fresh `Mod` on the same folders (sharing
the inherited layers), loads every section of both, lets the fresh one look up
what lookups added to the live caches, and compares every section attribute,
the load diagnostics and `_original_files` by value. A difference raises
`AssertionError` naming the section, the attribute and the first differing id.
Sections the check loaded are unloaded again afterwards, and the reads
their loaders noted are taken back: the check does not change which read of
a file counts as the first.

A lookup may cache something the loader did not load: content of the game
or a base mod (`get_country`, `get_state`, `get_character`, `get_oob`), or
a mod file that appeared after the section loaded. It then puts its section
into `_reload_on_discard`, and `save()` and `discard()` load that section
again. So no such entry is left when the check runs; one that is shows up
as a difference.

`tests/conftest.py` turns the check on for the whole suite. When it fails,
fix the registry (a missing dependency, folder or dirty name), not the check.
